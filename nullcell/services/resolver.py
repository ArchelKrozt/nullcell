"""Turning what someone typed into three introduction points — §8.

Three forms arrive here, and §8 is specific about the difference:

* ``<56 chars>.null`` — **always works and cannot be stolen.** The address *is*
  the key (§3), so there is nothing to hijack: a wrong answer cannot be signed.
* ``panaderia.null`` — a first-come registration in the DHT. Convenient, and
  weaker by construction: it is a name somebody holds, not a key.
* a local alias from ``~/.nullcell/hosts`` — checked first, so a user's own
  shorthand beats whatever the network says.

Reserved names (§9) resolve locally and never reach the DHT: ``hud.null``,
``search.null`` and ``me.null`` are this node's own, and letting the network
answer for them would hand somebody your control panel.

**Nothing here trusts the DHT.** A name lookup yields a key; the descriptor is
then verified against *that* key. A lying cell can refuse to answer, which is
visible, but it cannot answer wrongly.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Final

from nullcell.core import ids
from nullcell.dht import private as privmod
from nullcell.dht import records as rec
from nullcell.dht.kademlia import KademliaNode
from nullcell.dht.records import RecordType
from nullcell.onion import blinding
from nullcell.services.descriptor import ServiceDescriptor

__all__ = ["CACHE_TTL", "RESERVED", "ResolveError", "Resolver"]

#: §9 — resolved locally, and refused in the DHT.
RESERVED: Final[frozenset[str]] = frozenset({"hud", "search", "me"})
CACHE_TTL: Final[float] = 300.0

log = logging.getLogger("nullcell.resolver")


class ResolveError(Exception):
    """Nothing in the directory answers for this name."""


class Resolver:
    """Name or address to service descriptor."""

    def __init__(
        self,
        dht: KademliaNode,
        *,
        hosts_file: Path | None = None,
        cache_ttl: float = CACHE_TTL,
    ) -> None:
        self.dht = dht
        self.hosts_file = hosts_file
        self.cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, ServiceDescriptor]] = {}
        self._aliases: dict[str, str] = {}
        if hosts_file is not None:
            self.load_hosts()

    # ---------------------------------------------------------------- aliases
    def load_hosts(self) -> int:
        """Read ``~/.nullcell/hosts``: ``<alias> <nid>`` per line (§8)."""
        if self.hosts_file is None or not self.hosts_file.exists():
            return 0
        self._aliases.clear()
        for line in self.hosts_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 2:
                continue
            alias, target = parts
            try:
                ids.decode_nid(target)
            except ids.AddressError as exc:
                log.warning("hosts: skipping %s: %s", alias, exc)
                continue
            self._aliases[ids.parse_hostname(alias)] = ids.parse_hostname(target)
        return len(self._aliases)

    # -------------------------------------------------------------- resolving
    async def resolve(self, target: str, *, private: bool = False) -> ServiceDescriptor:
        """Resolve a name or address to a descriptor with intro points."""
        bare = ids.parse_hostname(target)
        if not bare:
            raise ResolveError("empty name")
        if bare in RESERVED:
            raise ResolveError(
                f"{bare}.null is this node's own and never resolves through the network (§9)"
            )

        bare = self._aliases.get(bare, bare)
        cached = self._cache.get(bare)
        if cached is not None and time.monotonic() - cached[0] < self.cache_ttl:
            return cached[1]

        pub = await self._key_for(bare)
        descriptor = await self._descriptor_for(pub, private=private)
        self._cache[bare] = (time.monotonic(), descriptor)
        return descriptor

    async def _key_for(self, bare: str) -> bytes:
        """The public key behind a name or address."""
        try:
            return ids.decode_nid(bare).pub
        except ids.AddressError:
            pass  # not an address, so try it as a registered name

        found = await self.dht.find_value(rec.name_key(bare))
        if not found:
            raise ResolveError(f"{bare}.null is not registered in the directory")
        # First come wins and the store enforces one holder per name (§8), so
        # taking the freshest is the right tie-break if several ever appear.
        return max(found, key=lambda record: record.ts).pub

    async def _descriptor_for(self, pub: bytes, *, private: bool) -> ServiceDescriptor:
        if private:
            return await self._private_descriptor(pub)

        found = await self.dht.find_value(ids.routing_key(pub))
        for record in sorted(found, key=lambda r: r.ts, reverse=True):
            if record.type is not RecordType.SERVICE:
                continue
            descriptor = ServiceDescriptor.from_record(record)
            if descriptor is not None and descriptor.pub == pub:
                return descriptor
        raise ResolveError(
            f"{ids.short(ids.encode_nid(pub))} has no usable service descriptor: "
            "it may be offline, or it may be a private service (§6)"
        )

    async def _private_descriptor(self, pub: bytes) -> ServiceDescriptor:
        """Try every period the service could currently be filed under (§6)."""
        last: Exception | None = None
        for period in blinding.periods_for(time.time()):
            index = blinding.index_key(pub, period)
            for record in await self.dht.find_value(index):
                try:
                    body = privmod.open_private(record, pub)
                except Exception as exc:
                    last = exc
                    continue
                descriptor = ServiceDescriptor.from_body(pub, body)
                if descriptor is not None:
                    return descriptor
        raise ResolveError(
            f"no private descriptor for {ids.short(ids.encode_nid(pub))}"
            + (f": {last}" if last else "")
        )

    # ------------------------------------------------------------------ cache
    def forget(self, target: str) -> None:
        self._cache.pop(ids.parse_hostname(target), None)

    @property
    def cached(self) -> int:
        return len(self._cache)
