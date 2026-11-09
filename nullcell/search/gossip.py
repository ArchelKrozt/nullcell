"""Gossip de observaciones firmadas — §8 mechanism 3, §9 liveness.

Two problems, one mechanism.

**§9 asked: how do we know a portal is alive?** The tempting answer — every
cell probes every service — is *"un ataque de denegación de servicio construido
dentro del propio diseño"*: N cells × M services × a six-hop circuit each. And
probing tells the intro points what interests you. So the model is, cheapest
first: descriptor freshness (free), then **this**, and only then an on-demand
probe the user asked for.

An observation is a cell saying, under its own signature, *"I visited this
portal at this time and it worked"*. The HUD then shows **"visto hace 4 m ·
confirmado por 7 celdas"** — distributed reputation, not a measurement of our
own, and never a *"99,8 % uptime"* nobody can verify.

**§8 asked: how does a new cell learn what the network knows?** Recrawling it
over six-hop circuits does not scale. So cells exchange a **Bloom filter** of
what they already hold and send back only the deltas — which is why a fresh
cell fills its panel in minutes instead of days.

What this does **not** do is make observations trustworthy. Anyone can sign a
claim about anything; what a signature buys is knowing *who* claims it, so
counting distinct observers means something. A Sybil swarm can still inflate a
count, which is exactly why §10's ceiling exists in the ranking.
"""

from __future__ import annotations

import hashlib
import logging
import math
import struct
import time
from dataclasses import dataclass
from typing import Any, Final

import cbor2

from nullcell.core import ids
from nullcell.core.identity import Identity

__all__ = [
    "MAX_DELTAS",
    "OBSERVATION_TTL",
    "BloomFilter",
    "GossipService",
    "Observation",
]

#: An observation older than this says nothing useful about now (§9).
OBSERVATION_TTL: Final[int] = 6 * 3600
#: Per exchange. Bounds what one peer can make us verify in one round.
MAX_DELTAS: Final[int] = 64
#: Accepted clock skew, matching §16's window for records.
MAX_SKEW: Final[int] = 2 * 3600

_BINDING: Final[bytes] = b"nullcell-observation-v1"
_HEADER = struct.Struct("!32s32sQB")  # portal, observer, seen_at, ok

log = logging.getLogger("nullcell.gossip")


@dataclass(frozen=True, slots=True)
class Observation:
    """One cell's signed statement that it reached a portal (§9)."""

    portal: bytes
    observer: bytes
    seen_at: int
    ok: bool
    sig: bytes = b""

    # ------------------------------------------------------------------ bind
    def binding(self) -> bytes:
        """What the signature covers.

        Includes the observer, so an observation cannot be re-attributed, and
        the timestamp, so an old one cannot be presented as fresh.
        """
        return _BINDING + _HEADER.pack(
            self.portal, self.observer, self.seen_at, 1 if self.ok else 0
        )

    def signed_by(self, identity: Identity) -> Observation:
        return Observation(
            portal=self.portal,
            observer=identity.public_key,
            seen_at=self.seen_at,
            ok=self.ok,
            sig=identity.sign(
                Observation(
                    portal=self.portal,
                    observer=identity.public_key,
                    seen_at=self.seen_at,
                    ok=self.ok,
                ).binding()
            ),
        )

    def verify(self, *, now: int | None = None) -> bool:
        """Signature, shape and clock. Everything here is hostile input."""
        now = int(time.time()) if now is None else now
        if len(self.portal) != 32 or len(self.observer) != 32 or len(self.sig) != 64:
            return False
        if self.seen_at > now + MAX_SKEW:
            return False  # a sighting from the future is not a sighting
        if self.seen_at < now - OBSERVATION_TTL - MAX_SKEW:
            return False
        return Identity.verify(self.observer, self.binding(), self.sig)

    # ------------------------------------------------------------------ wire
    @property
    def key(self) -> bytes:
        """Identity of this claim: one observer, one portal.

        A later sighting by the same cell replaces the earlier one rather than
        adding to the count, or a single cell could confirm a portal a thousand
        times by visiting it a thousand times.
        """
        return self.portal + self.observer

    @property
    def portal_nid(self) -> str:
        return ids.encode_nid(self.portal)

    def as_dict(self) -> dict[str, Any]:
        return {
            "p": self.portal,
            "o": self.observer,
            "t": self.seen_at,
            "ok": self.ok,
            "s": self.sig,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Observation | None:
        if not isinstance(raw, dict):
            return None
        try:
            portal, observer, sig = raw["p"], raw["o"], raw["s"]
            seen_at, ok = int(raw["t"]), bool(raw["ok"])
        except (KeyError, TypeError, ValueError):
            return None
        if not all(isinstance(v, bytes) for v in (portal, observer, sig)):
            return None
        return cls(portal=portal, observer=observer, seen_at=seen_at, ok=ok, sig=sig)


class BloomFilter:
    """What a cell already holds, in a few hundred bytes (§8).

    Sized for the number of items expected at a 1 % false-positive rate. A
    false positive costs one observation not sent, which is harmless: the next
    exchange with a different filter will carry it.
    """

    __slots__ = ("bits", "hashes", "_data")

    def __init__(self, bits: int = 4096, hashes: int = 4, data: bytes | None = None) -> None:
        if bits % 8 or bits <= 0 or bits > 1 << 20:
            raise ValueError("bits must be a positive multiple of 8, at most 2^20")
        if not 1 <= hashes <= 16:
            raise ValueError("hashes must be 1..16")
        self.bits = bits
        self.hashes = hashes
        self._data = bytearray(data or bits // 8)
        if len(self._data) != bits // 8:
            raise ValueError("data length does not match the bit count")

    @classmethod
    def sized_for(cls, items: int, *, rate: float = 0.01) -> BloomFilter:
        items = max(items, 1)
        bits = max(64, int(-items * math.log(rate) / (math.log(2) ** 2)))
        bits += (-bits) % 8
        hashes = max(1, min(16, round(bits / items * math.log(2))))
        return cls(bits=bits, hashes=hashes)

    def _positions(self, item: bytes):
        digest = hashlib.sha256(item).digest()
        for index in range(self.hashes):
            # Two 64-bit halves combined, the standard way to get k hashes
            # from one digest without hashing k times.
            a = int.from_bytes(digest[:8], "big")
            b = int.from_bytes(digest[8:16], "big")
            yield (a + index * b) % self.bits

    def add(self, item: bytes) -> None:
        for position in self._positions(item):
            self._data[position // 8] |= 1 << (position % 8)

    def __contains__(self, item: object) -> bool:
        if not isinstance(item, bytes):
            return False
        return all(
            self._data[position // 8] & (1 << (position % 8)) for position in self._positions(item)
        )

    def as_dict(self) -> dict[str, Any]:
        return {"b": self.bits, "k": self.hashes, "d": bytes(self._data)}

    @classmethod
    def from_dict(cls, raw: Any) -> BloomFilter | None:
        """Parse a peer's filter. Refuses anything that would allocate wildly."""
        if not isinstance(raw, dict):
            return None
        try:
            return cls(bits=int(raw["b"]), hashes=int(raw["k"]), data=raw["d"])
        except (KeyError, TypeError, ValueError) as exc:
            log.debug("rejected a peer's bloom filter: %s", exc)
            return None

    def __len__(self) -> int:
        return sum(bin(byte).count("1") for byte in self._data)


class GossipService:
    """Keeps and exchanges signed observations."""

    def __init__(self, identity: Identity, *, indexer=None) -> None:
        self.identity = identity
        self.indexer = indexer
        self._observations: dict[bytes, Observation] = {}
        self.stats = {"observed": 0, "sent": 0, "received": 0, "rejected": 0}

    def __len__(self) -> int:
        return len(self._observations)

    # ----------------------------------------------------------- our own eyes
    def observe(self, portal_pub: bytes, *, ok: bool = True) -> Observation:
        """Record that **we** reached a portal. Signed, so it can travel."""
        observation = Observation(
            portal=portal_pub, observer=self.identity.public_key, seen_at=int(time.time()), ok=ok
        ).signed_by(self.identity)
        self._observations[observation.key] = observation
        self.stats["observed"] += 1
        self._reindex(portal_pub)
        return observation

    # -------------------------------------------------------------- exchange
    def digest(self) -> BloomFilter:
        """A filter of what we hold, to send to a peer (§8)."""
        bloom = BloomFilter.sized_for(max(len(self._observations), 16))
        for key in self._observations:
            bloom.add(key)
        return bloom

    def deltas(self, peer: BloomFilter | None, *, limit: int = MAX_DELTAS) -> list[Observation]:
        """Observations the peer appears to lack, freshest first."""
        self.expire()
        candidates = sorted(self._observations.values(), key=lambda o: o.seen_at, reverse=True)
        chosen = [o for o in candidates if peer is None or o.key not in peer][:limit]
        self.stats["sent"] += len(chosen)
        return chosen

    def ingest(self, observations: list[Observation], *, now: int | None = None) -> int:
        """Verify and store what a peer sent. Returns how many were kept."""
        kept = 0
        for observation in observations[:MAX_DELTAS]:
            if not observation.verify(now=now):
                self.stats["rejected"] += 1
                continue
            if observation.observer == self.identity.public_key:
                continue  # our own claim, round-tripped
            known = self._observations.get(observation.key)
            if known is not None and known.seen_at >= observation.seen_at:
                continue
            self._observations[observation.key] = observation
            kept += 1
            self._reindex(observation.portal)
        self.stats["received"] += kept
        return kept

    # ------------------------------------------------------------- answering
    def confirmations(self, portal_pub: bytes, *, now: int | None = None) -> int:
        """How many **distinct cells** say this portal worked (§9)."""
        now = int(time.time()) if now is None else now
        return len(
            {
                observation.observer
                for observation in self._observations.values()
                if observation.portal == portal_pub
                and observation.ok
                and observation.seen_at > now - OBSERVATION_TTL
            }
        )

    def last_seen(self, portal_pub: bytes) -> int | None:
        times = [
            observation.seen_at
            for observation in self._observations.values()
            if observation.portal == portal_pub and observation.ok
        ]
        return max(times) if times else None

    def describe(self, portal_pub: bytes) -> str:
        """§9's wording, from what we actually know."""
        seen = self.last_seen(portal_pub)
        if seen is None:
            return "sin observaciones"
        age = max(0, int(time.time()) - seen)
        minutes = age // 60
        when = f"{minutes} m" if minutes else f"{age} s"
        return f"visto hace {when} · confirmado por {self.confirmations(portal_pub)} celdas"

    # ------------------------------------------------------------ maintenance
    def expire(self, *, now: int | None = None) -> int:
        now = int(time.time()) if now is None else now
        stale = [
            key
            for key, observation in self._observations.items()
            if observation.seen_at < now - OBSERVATION_TTL
        ]
        for key in stale:
            del self._observations[key]
        return len(stale)

    def _reindex(self, portal_pub: bytes) -> None:
        """Push the confirmation count into the local index, for the ranking."""
        if self.indexer is None:
            return
        self.indexer.upsert(
            ids.encode_nid(portal_pub), confirmed=max(1, self.confirmations(portal_pub))
        )

    # ------------------------------------------------------------------- wire
    def encode_request(self) -> bytes:
        return cbor2.dumps({"op": "gossip", "bloom": self.digest().as_dict()}, canonical=True)

    def encode_reply(self, peer_bloom: BloomFilter | None) -> bytes:
        return cbor2.dumps(
            {
                "ok": 1,
                "obs": [o.as_dict() for o in self.deltas(peer_bloom)],
                "bloom": self.digest().as_dict(),
            },
            canonical=True,
        )

    @staticmethod
    def decode_observations(raw: Any) -> list[Observation]:
        """Parse a list of observations, skipping anything malformed."""
        if not isinstance(raw, list):
            return []
        parsed = []
        for entry in raw[:MAX_DELTAS]:
            observation = Observation.from_dict(entry)
            if observation is not None:
                parsed.append(observation)
        return parsed
