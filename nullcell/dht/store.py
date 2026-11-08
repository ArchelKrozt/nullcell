"""Local DHT storage with quotas — §7, §8, §16.

Storing other people's data for free is an obvious way to be abused, so every
limit here exists against a specific attack:

* **Entries per key.** A popular keyword concentrates all its storage on the
  same 20 nodes (the Kademlia hot-spot problem). Capped, with eviction by proof
  of work and age, so occupying a word costs sustained work rather than one
  burst (§8, §16).
* **One entry per publisher per key.** Republishing refreshes in place instead
  of accumulating, so a publisher cannot fill a key alone.
* **Global key and byte quotas.** Bounds total memory regardless of how the
  traffic is shaped.

Expiry is driven by each record's own TTL, which is also the liveness signal the
HUD reads (§9): a descriptor still inside its TTL means the service is up,
because republishing requires working intro points.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

from nullcell.dht.records import Record, RecordType

__all__ = [
    "AGE_BONUS_BITS",
    "AGE_BONUS_HOURS",
    "MAX_BYTES",
    "MAX_ENTRIES_PER_KEY",
    "MAX_KEYS",
    "Store",
    "StoreFull",
]

MAX_ENTRIES_PER_KEY: Final[int] = 256
MAX_KEYS: Final[int] = 100_000
MAX_BYTES: Final[int] = 64 * 1024 * 1024

#: How much being established is worth, in proof-of-work bits, and over how
#: long it accrues. This is not a free parameter: measured work is
#: ``required_bits + Geometric``, so roughly half of fresh proofs at the same
#: nominal difficulty measure higher than any given one by luck alone. Without
#: an age bonus that exceeds that noise, an attacker displaces established
#: entries for free about half the time. At 3 bits over 24 h, evicting a
#: day-old entry costs 8x the work it paid.
AGE_BONUS_BITS: Final[float] = 3.0
AGE_BONUS_HOURS: Final[float] = 24.0

#: Only these types may have many entries under one key. A relay or service
#: descriptor is keyed by its own routing key, so there is exactly one.
_MULTI_VALUE: Final[frozenset[RecordType]] = frozenset({RecordType.KEYWORD})


class StoreFull(Exception):
    """A global quota is exhausted."""


@dataclass(slots=True)
class _Entry:
    record: Record
    blob: bytes
    stored_at: float
    pow_bits: int

    def score_at(self, now: float) -> float:
        """Eviction score at instant *now* — higher survives.

        Scores the work **actually done** on the record, not the difficulty its
        type requires: the required value lives in a global table that a
        protocol update could change, and that would retroactively rewrite the
        standing of everything already stored.

        Work dominates, with a bounded bonus for having been around a while
        (:data:`AGE_BONUS_BITS`). An established entry therefore outranks a
        newcomer that merely got a lucky digest, and displacing it costs real
        extra work rather than a coin flip.

        *now* is passed in rather than read here because every score in one
        comparison has to come from the same instant. Reading the clock per
        entry gave a brand-new candidate a few microseconds of age bonus, which
        was enough to win ties it should have lost.
        """
        age_hours = min(max(0.0, (now - self.stored_at) / 3600.0), AGE_BONUS_HOURS)
        return self.pow_bits + AGE_BONUS_BITS * age_hours / AGE_BONUS_HOURS


class Store:
    """Key to records, with TTL and quotas."""

    __slots__ = ("_by_key", "_bytes", "max_entries_per_key", "max_keys", "max_bytes")

    def __init__(
        self,
        *,
        max_entries_per_key: int = MAX_ENTRIES_PER_KEY,
        max_keys: int = MAX_KEYS,
        max_bytes: int = MAX_BYTES,
    ) -> None:
        self._by_key: dict[bytes, dict[bytes, _Entry]] = {}
        self._bytes = 0
        self.max_entries_per_key = max_entries_per_key
        self.max_keys = max_keys
        self.max_bytes = max_bytes

    # ------------------------------------------------------------------ write
    def put(
        self,
        record: Record,
        *,
        blob: bytes | None = None,
        pow_bits: int | None = None,
    ) -> bool:
        """Store a *validated* record. Returns False if it lost an eviction race.

        The caller must have run :func:`nullcell.dht.records.verify` first; the
        store does not re-validate, and never accepts anything on trust from a
        caller that skipped it. Pass the ``pow_bits`` that ``verify`` returned
        to avoid paying for a second argon2id hash.
        """
        blob = record.encode() if blob is None else blob
        if pow_bits is None:
            pow_bits = record.measure_pow_bits()
        now = time.time()
        entries = self._by_key.get(record.key)

        if entries is None:
            if len(self._by_key) >= self.max_keys:
                raise StoreFull(f"key quota reached ({self.max_keys})")
            entries = self._by_key[record.key] = {}

        previous = entries.get(record.pub)
        delta = len(blob) - (len(previous.blob) if previous else 0)
        if previous is None and self._bytes + delta > self.max_bytes:
            raise StoreFull(f"byte quota reached ({self.max_bytes})")

        new_entry = _Entry(record=record, blob=blob, stored_at=now, pow_bits=pow_bits)

        if previous is not None:
            # Republication by the same publisher: refresh in place, but never
            # let a replay of an older record overwrite a newer one.
            if record.ts < previous.record.ts:
                return False
            new_entry.stored_at = previous.stored_at  # keep the age it earned
            entries[record.pub] = new_entry
            self._bytes += delta
            return True

        if record.type not in _MULTI_VALUE and entries:
            # Single-value key with a different publisher: only possible for a
            # name, where first come wins until it expires (§8).
            return False

        if len(entries) >= self.max_entries_per_key:
            weakest_pub, weakest = min(entries.items(), key=lambda kv: kv[1].score_at(now))
            if new_entry.score_at(now) <= weakest.score_at(now):
                return False
            del entries[weakest_pub]
            self._bytes -= len(weakest.blob)

        entries[record.pub] = new_entry
        self._bytes += len(blob)
        return True

    # ------------------------------------------------------------------- read
    def get(self, key: bytes, *, now: int | None = None) -> list[Record]:
        """Live records under *key*, freshest first."""
        now = int(time.time()) if now is None else now
        entries = self._by_key.get(key)
        if not entries:
            return []
        live = [e.record for e in entries.values() if e.record.expires_at > now]
        return sorted(live, key=lambda r: r.ts, reverse=True)

    def blobs(self, key: bytes, *, now: int | None = None) -> list[bytes]:
        """Encoded form of :meth:`get`, to forward without re-encoding."""
        now = int(time.time()) if now is None else now
        entries = self._by_key.get(key)
        if not entries:
            return []
        return [e.blob for e in entries.values() if e.record.expires_at > now]

    def __contains__(self, key: object) -> bool:
        return bool(self.get(key)) if isinstance(key, bytes) else False

    def __len__(self) -> int:
        return sum(len(e) for e in self._by_key.values())

    # ------------------------------------------------------------ maintenance
    def expire(self, *, now: int | None = None) -> int:
        """Drop expired records. Returns how many went."""
        now = int(time.time()) if now is None else now
        dropped = 0
        for key in list(self._by_key):
            entries = self._by_key[key]
            for pub in [p for p, e in entries.items() if e.record.expires_at <= now]:
                self._bytes -= len(entries.pop(pub).blob)
                dropped += 1
            if not entries:
                del self._by_key[key]
        return dropped

    def _age_entries(self, key: bytes, hours: float) -> None:
        """Backdate every entry under *key*. Test seam, not protocol."""
        for entry in self._by_key.get(key, {}).values():
            entry.stored_at -= hours * 3600.0

    @property
    def stats(self) -> dict[str, int]:
        return {
            "keys": len(self._by_key),
            "records": len(self),
            "bytes": self._bytes,
        }

    def __repr__(self) -> str:  # pragma: no cover
        s = self.stats
        return f"<Store {s['records']} records, {s['keys']} keys, {s['bytes']} B>"
