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
    "PathBuilder",
    "RelayDirectory",
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
    #: The address we saw when we actually connected, if we ever did. It wins
    #: over anything derived from the descriptor: the descriptor is what the
    #: relay *claims*, this is what we observed.
    observed: str | None = None

    @classmethod
    def from_record(cls, record: Record, *, pow_bits: int = 0) -> RelayCandidate | None:
        """Build a candidate from a verified relay descriptor, or None.

        The record must already have passed
        :func:`nullcell.dht.records.verify`; this only checks that the body has
        the shape a relay descriptor needs.

        ``first_seen`` is left at *now*, deliberately. §7 asks for **observed**
        age, and a record's own timestamp is the publisher's claim: taking it
        at face value let an attacker backdate a flood of fresh identities by
        hours and clear the age floor instantly, which is the whole of the
        mitigation gone. :class:`RelayDirectory` keeps the real first sighting.
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
        """Diversity keys for this relay.

        An observed address replaces the claimed ones entirely. A relay that
        announces a hostname and resolves to an address we have already seen on
        another hop is the same machine, and only the observation can tell us.
        """
        if self.observed is not None:
            observed = endpoint_prefix(self.observed)
            if observed is not None:
                return {observed}
        return {p for p in (endpoint_prefix(e) for e in self.endpoints) if p}

    def note_observed(self, address: str) -> None:
        """Record the address this relay actually answered on.

        Called after a connection succeeds, so it leaks nothing: we had to
        know the address to send the packet.
        """
        self.observed = address

    def as_hop(self) -> Hop:
        return Hop(pub=self.pub, x25519=self.x25519, endpoints=list(self.endpoints))

    def observed_for(self, now: float | None = None) -> float:
        return max(0.0, (time.time() if now is None else now) - self.first_seen)


def endpoint_prefix(endpoint: str) -> str | None:
    """A diversity key for an endpoint: /16 for IPv4, /48 for IPv6.

    For a **hostname**, the key is its parent domain — ``vps1.example.com``
    becomes ``dns:example.com`` — because resolving it would leak which relays
    we are weighing up, and §7 will not trade that for convenience.

    That is deliberately **conservative**: two relays under one domain are
    treated as one operator, which they usually are. It refuses some paths that
    would have been fine (two strangers behind the same dynamic-DNS provider),
    and §7 says refusing beats fudging — a circuit the user believes is diverse
    and is not is worse than no circuit.

    A hostname key is still **weaker** than an IP one, because an unresolved
    name cannot be compared against an IP: ``relay.alice.com`` and
    ``203.0.113.9`` might be the same machine and we cannot tell. That gap
    closes as soon as we actually connect, which is what
    :meth:`RelayCandidate.note_observed` is for — once a connection exists we
    already know the address, so recording it leaks nothing new.
    """
    try:
        host, _ = parse_endpoint(endpoint)
    except ValueError:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return _domain_key(host)
    bits = _IPV4_PREFIX if address.version == 4 else _IPV6_PREFIX
    return str(ipaddress.ip_network(f"{host}/{bits}", strict=False))


def _domain_key(host: str) -> str | None:
    """``vps1.example.com`` -> ``dns:example.com``.

    Two labels, which is wrong for ``example.co.uk`` — it would key on
    ``co.uk`` and treat every British relay as one operator. Wrong in the
    conservative direction: it refuses paths rather than allowing a
    concentrated one. A public-suffix list would fix it and is a dependency
    and a staleness problem, the same trade §7 already made for ASNs.
    """
    labels = [label for label in host.strip(".").lower().split(".") if label]
    if len(labels) < 2:
        return None  # a bare name says nothing about who runs it
    return "dns:" + ".".join(labels[-2:])


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
        # Say which floor was not met. The first version blamed age and work
        # for everything, which sent an operator deploying with a DNS name
        # looking in entirely the wrong place.
        total = len(candidates)
        no_prefix = sum(1 for c in candidates if not c.prefixes)
        too_young = sum(1 for c in candidates if c.observed_for(now) < MIN_OBSERVED_AGE)
        too_cheap = sum(1 for c in candidates if c.pow_bits < min_pow_bits)
        raise PathError(
            f"{len(usable)} of {total} relays are usable, {length} needed. "
            f"Excluded: {too_young} too recently observed "
            f"(§7 requires {MIN_OBSERVED_AGE / 60:.0f} min), "
            f"{too_cheap} below the proof-of-work floor, "
            f"{no_prefix} with no comparable endpoint"
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


class RelayDirectory:
    """Relays this node has actually seen, and when it first saw each (§7).

    Separate from the DHT store on purpose. The store holds records; this holds
    **observations**, and the difference is the security property: §7's age
    floor is meaningless if a publisher can assert its own age, so the first
    sighting recorded here is local, monotonic and unforgeable by anyone else.

    Re-publication refreshes a relay's keys and endpoints without resetting the
    clock, which is what makes waiting the cost it is meant to be.
    """

    def __init__(self) -> None:
        self._relays: dict[bytes, RelayCandidate] = {}

    def __len__(self) -> int:
        return len(self._relays)

    def ingest(self, record: Record, *, pow_bits: int = 0) -> RelayCandidate | None:
        """Note a verified relay descriptor. Returns the candidate, or None."""
        fresh = RelayCandidate.from_record(record, pow_bits=pow_bits)
        if fresh is None:
            return None
        known = self._relays.get(fresh.pub)
        if known is None:
            self._relays[fresh.pub] = fresh
            return fresh
        # Refresh what it says, keep when we first saw it.
        known.x25519 = fresh.x25519
        known.endpoints = fresh.endpoints
        known.bandwidth = fresh.bandwidth
        known.pow_bits = max(known.pow_bits, fresh.pow_bits)
        return known

    def candidates(self, *, now: float | None = None) -> list[RelayCandidate]:
        return list(self._relays.values())

    def get(self, pub: bytes) -> RelayCandidate | None:
        """The candidate for *pub*, if this node has its descriptor.

        Needed to turn a DHT peer into a circuit's exit hop: a `Node` carries
        endpoints but no x25519 key, and without that key there is no handshake
        to make it the last hop (§7, decision 8).
        """
        return self._relays.get(pub)

    def usable(self, *, min_pow_bits: int = 0, now: float | None = None) -> list[RelayCandidate]:
        """Candidates past the age and work floors of §7."""
        now = time.time() if now is None else now
        return [
            relay
            for relay in self._relays.values()
            if relay.pow_bits >= min_pow_bits
            and relay.observed_for(now) >= MIN_OBSERVED_AGE
            and relay.prefixes
        ]

    def note_observed(self, pub: bytes, address: str) -> None:
        """Record where a relay actually answered (see ``note_observed``)."""
        relay = self._relays.get(pub)
        if relay is not None:
            relay.note_observed(address)

    def forget(self, pub: bytes) -> None:
        self._relays.pop(pub, None)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RelayDirectory {len(self)} relays, {len(self.usable())} usable>"


class PathBuilder:
    """Turns a directory of relays into circuit paths (§7).

    The single place that decides which relays a circuit goes through. Every
    circuit in the node should come from here, or the guard pinning and prefix
    diversity of §7 are documentation rather than behaviour.
    """

    def __init__(
        self,
        directory: RelayDirectory,
        guards: GuardSet,
        *,
        min_pow_bits: int = 0,
        rng: random.Random | None = None,
    ) -> None:
        self.directory = directory
        self.guards = guards
        self.min_pow_bits = min_pow_bits
        self.rng = rng or random.Random()

    def build(self, *, length: int = CIRCUIT_LENGTH, ending_at: Hop | None = None) -> list[Hop]:
        """A path of *length* hops, optionally ending at a given relay.

        ``ending_at`` is how a path to a specific intro point or rendezvous
        point is built: the last hop is fixed, the rest still obey the guard
        and diversity rules.
        """
        if ending_at is None:
            return select_path(
                self.directory.candidates(),
                self.guards,
                length=length,
                min_pow_bits=self.min_pow_bits,
                rng=self.rng,
            )

        prefix = select_path(
            [c for c in self.directory.candidates() if c.pub != ending_at.pub],
            self.guards,
            length=length - 1,
            min_pow_bits=self.min_pow_bits,
            rng=self.rng,
        )
        taken = {p for hop in prefix for p in _hop_prefixes(hop)}
        if taken & _hop_prefixes(ending_at):
            raise PathError(
                f"{ending_at.short} shares a prefix with the path to it; "
                "§7 does not allow two hops of one circuit in one prefix"
            )
        return [*prefix, ending_at]

    @property
    def ready(self) -> bool:
        return len(self.directory.usable(min_pow_bits=self.min_pow_bits)) >= CIRCUIT_LENGTH

    def missing(self, *, now: float | None = None) -> dict:
        """What stands between this cell and its first circuit.

        An interface that can only say "not ready" leaves the operator
        guessing whether to wait or to act, and those are opposite responses.
        The two answers are genuinely different: a relay that is merely *young*
        becomes usable by waiting, while a network with one operator in it
        never will, however long you stare at it.
        """
        now = time.time() if now is None else now
        known = self.directory.candidates(now=now)
        usable = self.directory.usable(min_pow_bits=self.min_pow_bits, now=now)
        waiting = sorted(
            MIN_OBSERVED_AGE - c.observed_for(now)
            for c in known
            if c.pow_bits >= self.min_pow_bits
            and c.prefixes
            and c.observed_for(now) < MIN_OBSERVED_AGE
        )

        # Enough *operators*, not enough relays: §7 counts distinct prefixes,
        # so five relays behind one provider are one hop, not five.
        prefixes = {p for c in known if c.pow_bits >= self.min_pow_bits for p in c.prefixes}

        ready_in: float | None = None
        still_needed = CIRCUIT_LENGTH - len(usable)
        if still_needed <= 0:
            ready_in = 0.0
        elif len(waiting) >= still_needed:
            ready_in = waiting[still_needed - 1]

        return {
            "known": len(known),
            "usable": len(usable),
            "needed": CIRCUIT_LENGTH,
            "operators": len(prefixes),
            "waiting": len(waiting),
            "ready_in": ready_in,
            "why": self.why_not_ready(),
        }

    def why_not_ready(self, *, now: float | None = None) -> str:
        """Qué falta, y sobre todo **si esperar sirve de algo**.

        El mensaje anterior culpaba siempre al suelo de antigüedad, también
        cuando lo que faltaban eran operadores. Eso le decía a alguien que
        esperara en el único caso en el que esperar no va a cambiar nada
        jamás, y esperar y actuar son reacciones opuestas.
        """
        now = time.time() if now is None else now
        total = len(self.directory)
        if total == 0:
            return "no se conoce ningún relevo todavía: hace falta una semilla (§7)"

        usable = len(self.directory.usable(min_pow_bits=self.min_pow_bits, now=now))
        if usable >= CIRCUIT_LENGTH:
            return ""

        # §7 cuenta operadores, no máquinas: diez relevos del mismo proveedor
        # son un salto, no diez.
        operators = len(
            {
                prefix
                for c in self.directory.candidates(now=now)
                if c.pow_bits >= self.min_pow_bits
                for prefix in c.prefixes
            }
        )
        if operators < CIRCUIT_LENGTH:
            faltan = CIRCUIT_LENGTH - operators
            return (
                f"{total} relevos en {operators} operador"
                f"{'' if operators == 1 else 'es'} distinto"
                f"{'' if operators == 1 else 's'}. §7 exige {CIRCUIT_LENGTH} operadores "
                f"distintos, así que esperar no basta: faltan {faltan} más, "
                "y tienen que ser de otra gente o de otro proveedor"
            )
        return (
            f"{total} relevos conocidos, {usable} usables. §7 exige "
            f"{MIN_OBSERVED_AGE / 60:.0f} min de antigüedad observada, "
            "así que ya sólo hay que esperar"
        )


def _hop_prefixes(hop: Hop) -> set[str]:
    return {p for p in (endpoint_prefix(e) for e in hop.endpoints) if p}
