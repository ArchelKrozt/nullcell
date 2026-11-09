"""Choosing the three hops — §7, the most delicate part of the design.

§7 is explicit that this is **an open research problem**, not something solved
here. Tor picks relays from a consensus signed by nine directory authorities;
this project refuses to have authorities, and nobody has a demonstrated answer
for doing it without them. What follows is the pragmatic mitigation §7
describes, and it is documented as a mitigation rather than a solution.

Four rules, each against a specific failure:

* **Pinned guards.** The first hop stays fixed for weeks. Picking a fresh first
  hop for every circuit means that, over enough circuits, *somebody* you chose
  was hostile and saw you — rotation is what deanonymises you in the long run,
  not what protects you.
* **Prefix diversity.** The three hops must sit in different /16 (IPv4) or /48
  (IPv6) prefixes, so one operator or one observer does not hold two hops of
  the same circuit.
* **Minimum observed age.** A relay has to have been in our routing table for a
  while before we route through it. Flooding the DHT with fresh identities is
  cheap in volume; waiting is not.
* **Proof of work floor.** Descriptors below the required work are not
  candidates at all (§10).

What is **not** here, and matters: ASN-level diversity. §7 asks for distinct
ASNs where resolvable, and resolving an ASN needs either an external dataset or
a network query — the first is a dependency and a staleness problem, the second
is a disclosure. Prefix diversity is a weaker proxy and is labelled as such.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from nullcell.core import ids
from nullcell.dht.records import Record, RecordType
from nullcell.net.link_manager import parse_endpoint
from nullcell.onion.circuit import Hop

__all__ = [
    "CIRCUIT_LENGTH",
    "GUARD_COUNT",
    "GUARD_LIFETIME",
    "MIN_OBSERVED_AGE",
    "GuardSet",
    "PathError",
    "RelayCandidate",
    "endpoint_prefix",
    "select_path",
]

CIRCUIT_LENGTH: Final[int] = 3
GUARD_COUNT: Final[int] = 3
#: §7 says weeks. Long enough that rotation is not what identifies you, short
#: enough that a guard that quietly went hostile does not keep you forever.
GUARD_LIFETIME: Final[float] = 30 * 24 * 3600
#: How long a relay must have been known before we route through it.
MIN_OBSERVED_AGE: Final[float] = 600.0

_IPV4_PREFIX: Final[int] = 16
_IPV6_PREFIX: Final[int] = 48

log = logging.getLogger("nullcell.path")


class PathError(Exception):
    """Not enough usable, diverse relays to build a circuit."""


@dataclass(slots=True)
class RelayCandidate:
    """A relay we could route through, from its signed descriptor (§7)."""

    pub: bytes
    x25519: bytes
    endpoints: list[str] = field(default_factory=list)
    bandwidth: int = 0
    first_seen: float = field(default_factory=time.time)
    pow_bits: int = 0

    @classmethod
    def from_record(cls, record: Record, *, pow_bits: int = 0) -> RelayCandidate | None:
        """Build a candidate from a verified relay descriptor, or None.

        The record must already have passed
        :func:`nullcell.dht.records.verify`; this only checks that the body has
        the shape a relay descriptor needs.
        """
        if record.type is not RecordType.RELAY:
            return None
        body = record.body
        x25519, endpoints = body.get("x"), body.get("ep")
        if not (isinstance(x25519, bytes) and len(x25519) == 32):
            return None
        if not (isinstance(endpoints, list) and endpoints):
            return None
        clean = [e for e in endpoints if isinstance(e, str)][:4]
        if not clean:
            return None
        return cls(
            pub=record.pub,
            x25519=x25519,
            endpoints=clean,
            bandwidth=int(body.get("bw", 0) or 0),
            first_seen=float(record.ts),
            pow_bits=pow_bits,
        )

    @property
    def nid(self) -> str:
        return ids.encode_nid(self.pub)

    @property
    def short(self) -> str:
        return ids.short(self.nid)

    @property
    def prefixes(self) -> set[str]:
        return {p for p in (endpoint_prefix(e) for e in self.endpoints) if p}

    def as_hop(self) -> Hop:
        return Hop(pub=self.pub, x25519=self.x25519, endpoints=list(self.endpoints))

    def observed_for(self, now: float | None = None) -> float:
        return max(0.0, (time.time() if now is None else now) - self.first_seen)


def endpoint_prefix(endpoint: str) -> str | None:
    """Network prefix of an endpoint: /16 for IPv4, /48 for IPv6.

    Returns None for a hostname, which cannot be compared without resolving it
    — and resolving it would leak which relays we are weighing up.
    """
    try:
        host, _ = parse_endpoint(endpoint)
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    bits = _IPV4_PREFIX if address.version == 4 else _IPV6_PREFIX
    return str(ipaddress.ip_network(f"{host}/{bits}", strict=False))


class GuardSet:
    """The pinned first hops, persisted across sessions (§7)."""

    def __init__(self, path: Path | None = None, *, count: int = GUARD_COUNT) -> None:
        self.path = path
        self.count = count
        self._guards: dict[bytes, float] = {}  # pub -> pinned_at
        if path is not None:
            self.load()

    def __len__(self) -> int:
        return len(self._guards)

    @property
    def pubs(self) -> list[bytes]:
        return list(self._guards)

    def is_guard(self, pub: bytes) -> bool:
        return pub in self._guards

    def expired(self, now: float | None = None) -> list[bytes]:
        now = time.time() if now is None else now
        return [pub for pub, pinned in self._guards.items() if now - pinned > GUARD_LIFETIME]

    def retire(self, pub: bytes) -> None:
        """Drop a guard: it expired, or stopped answering."""
        if self._guards.pop(pub, None) is not None:
            log.info("guard retired: %s", ids.short(ids.encode_nid(pub)))
            self.save()

    def ensure(self, candidates: list[RelayCandidate], *, now: float | None = None) -> list[bytes]:
        """Top the guard set up from *candidates*, keeping what is already pinned."""
        now = time.time() if now is None else now
        for pub in self.expired(now):
            self.retire(pub)

        if len(self._guards) >= self.count:
            return self.pubs

        usable = [
            c
            for c in candidates
            if c.pub not in self._guards and c.observed_for(now) >= MIN_OBSERVED_AGE
        ]
        # Diverse from the guards we already have: two guards behind the same
        # operator is one guard with extra steps.
        taken = {p for pub in self._guards for p in _prefixes_of(candidates, pub)}
        random.shuffle(usable)
        for candidate in usable:
            if len(self._guards) >= self.count:
                break
            if candidate.prefixes & taken:
                continue
            self._guards[candidate.pub] = now
            taken |= candidate.prefixes
            log.info("guard pinned: %s", candidate.short)

        self.save()
        return self.pubs

    # ------------------------------------------------------------- persistence
    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("%s unreadable, choosing new guards: %s", self.path, exc)
            return
        if not isinstance(data, dict):
            return
        for nid, pinned in data.items():
            try:
                self._guards[ids.decode_nid(nid).pub] = float(pinned)
            except (ids.AddressError, TypeError, ValueError):
                continue

    def save(self) -> None:
        if self.path is None:
            return
        data = {ids.encode_nid(pub): pinned for pub, pinned in self._guards.items()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self.path)


def _prefixes_of(candidates: list[RelayCandidate], pub: bytes) -> set[str]:
    for candidate in candidates:
        if candidate.pub == pub:
            return candidate.prefixes
    return set()


def select_path(
    candidates: list[RelayCandidate],
    guards: GuardSet,
    *,
    length: int = CIRCUIT_LENGTH,
    min_pow_bits: int = 0,
    now: float | None = None,
    rng: random.Random | None = None,
) -> list[Hop]:
    """Pick *length* hops: a pinned guard first, then diverse relays.

    Raises :class:`PathError` rather than quietly returning a path that breaks
    one of the rules. A circuit that silently shares two hops with one operator
    is worse than no circuit, because the user believes they have one.
    """
    now = time.time() if now is None else now
    rng = rng or random.Random()

    usable = [
        c
        for c in candidates
        if c.pow_bits >= min_pow_bits and c.observed_for(now) >= MIN_OBSERVED_AGE and c.prefixes
    ]
    if len(usable) < length:
        raise PathError(
            f"only {len(usable)} relays meet the age and work floors; "
            f"{length} needed. A new node has to wait "
            f"{MIN_OBSERVED_AGE / 60:.0f} min before it can build a circuit"
        )

    guards.ensure(usable, now=now)
    guard_pool = [c for c in usable if guards.is_guard(c.pub)]
    if not guard_pool:
        raise PathError("no usable pinned guard: none of them is reachable right now")

    chosen = [rng.choice(guard_pool)]
    taken = set(chosen[0].prefixes)
    pool = [c for c in usable if c.pub != chosen[0].pub]
    rng.shuffle(pool)

    for candidate in pool:
        if len(chosen) >= length:
            break
        if candidate.prefixes & taken:
            continue  # §7: distinct prefixes, no exceptions
        chosen.append(candidate)
        taken |= candidate.prefixes

    if len(chosen) < length:
        raise PathError(
            f"found {len(chosen)} hops in distinct prefixes, {length} needed. "
            "The network is too small or too concentrated for a diverse path"
        )
    return [c.as_hop() for c in chosen]
