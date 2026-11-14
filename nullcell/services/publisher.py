"""Keeping a descriptor in the directory — §7, §8, §9.

Publication is not a one-off. §9 builds the whole liveness model on it:
*"Si el descriptor se renovó en el DHT hace menos de su TTL, el servicio está
vivo: publicar exige tener intro points funcionando."* A portal that stops
republishing is a portal that has gone away, and that is the signal the HUD
reads — so republication before the TTL expires is the heartbeat of the network,
not housekeeping.

Renewal is also why the proof of work of §10 is priced where it is: at 9 bits
(~0,5 s) a relay can afford it hourly, and a flood of fake services cannot.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Final

from nullcell.core.identity import Identity
from nullcell.dht import private as privmod
from nullcell.dht import records as rec
from nullcell.dht.kademlia import KademliaNode
from nullcell.dht.records import RecordType
from nullcell.onion import blinding
from nullcell.services.descriptor import ServiceDescriptor

__all__ = ["DEFAULT_TTL", "RENEW_MARGIN", "Publisher"]

DEFAULT_TTL: Final[int] = 3600
#: Republish this long before the TTL runs out. A descriptor that lapses makes
#: the service look dead to every HUD on the network (§9).
RENEW_MARGIN: Final[float] = 0.25

log = logging.getLogger("nullcell.publisher")


class Publisher:
    """Publishes and renews everything this node puts in the directory."""

    def __init__(self, identity: Identity, dht: KademliaNode, *, ttl: int = DEFAULT_TTL) -> None:
        self.identity = identity
        self.dht = dht
        self.ttl = ttl
        self.stats = {"relay": 0, "service": 0, "name": 0, "keyword": 0, "failed": 0}

    # ----------------------------------------------------------------- relay
    async def publish_relay(self, endpoints: list[str], *, bandwidth: int = 0) -> int:
        """Announce this node as a relay. Returns how many cells accepted it."""
        body = rec.RelayBody(
            endpoints=endpoints, x25519=self.identity.x25519_public, bandwidth=bandwidth
        ).as_dict()
        record = await rec.build(self.identity, RecordType.RELAY, body, ttl=self.ttl)
        stored = await self.dht.store_record(record)
        self.stats["relay"] += 1
        log.info("relay descriptor published to %d cells", stored)
        return stored

    # --------------------------------------------------------------- service
    async def publish_service(self, descriptor: ServiceDescriptor, *, private: bool = False) -> int:
        """Publish a portal, public or private (§6).

        A private service is filed under a blinded index for **every period it
        is currently valid in**, which is what stops a period boundary from
        making it briefly unreachable.
        """
        if private:
            return await self._publish_private(descriptor)

        record = await rec.build(self.identity, RecordType.SERVICE, descriptor.body(), ttl=self.ttl)
        stored = await self.dht.store_record(record)
        self.stats["service"] += 1
        log.info("service descriptor published to %d cells", stored)
        return stored

    async def _publish_private(self, descriptor: ServiceDescriptor) -> int:
        stored = 0
        for period, _index in privmod.publish_keys(self.identity.public_key):
            record = await privmod.build_private(
                self.identity, descriptor.body(), period=period, ttl=self.ttl
            )
            stored += await self.dht.store_record(record)
        self.stats["service"] += 1
        log.info("private descriptor published to %d cells", stored)
        return stored

    # ------------------------------------------------------------------ name
    async def publish_name(self, name: str) -> int:
        """Register a readable name, first come (§8).

        Returns 0 if nobody accepted it, which usually means somebody already
        holds it — the caller should say so rather than report success.
        """
        record = await rec.build(self.identity, RecordType.NAME, {"name": name}, ttl=self.ttl)
        stored = await self.dht.store_record(record)
        self.stats["name"] += 1
        log.info("name %s.null published to %d cells", name, stored)
        return stored

    # --------------------------------------------------------------- keywords
    async def publish_keywords(self, descriptor: ServiceDescriptor, *, limit: int = 12) -> int:
        """Index a portal under its own words (§8, mechanism 1).

        This is what makes search work from day one with no crawler. Each word
        costs its own proof of work — 11 bits, the dearest of the routine ones,
        because keywords are the direct spam vector (§10).
        """
        stored = 0
        for word in descriptor.keywords()[:limit]:
            shard = int.from_bytes(self.identity.public_key[:2], "big") % rec.KEYWORD_SHARDS
            record = await rec.build(
                self.identity,
                RecordType.KEYWORD,
                {"id": descriptor.nid, "title": descriptor.title, "kind": descriptor.kind},
                key=rec.keyword_key(word, shard),
                ttl=self.ttl,
            )
            stored += await self.dht.store_record(record)
            self.stats["keyword"] += 1
        return stored

    # ---------------------------------------------------------------- renewal
    async def renew_forever(
        self,
        *,
        endpoints: list[str] | None = None,
        descriptor: ServiceDescriptor | None = None,
        private: bool = False,
        name: str | None = None,
        keywords: bool = False,
    ) -> None:
        """Republish before the TTL lapses, for as long as the node runs (§9).

        ``keywords`` renueva también las palabras clave del portal: tienen el
        mismo TTL que el descriptor, y sin renovarlas el portal desaparecía del
        buscador a la hora de publicarse aunque siguiera en línea.
        """
        interval = max(60.0, self.ttl * (1.0 - RENEW_MARGIN))
        while True:
            await asyncio.sleep(interval)
            started = time.monotonic()
            try:
                if endpoints:
                    await self.publish_relay(endpoints)
                if descriptor is not None:
                    await self.publish_service(descriptor, private=private)
                if name:
                    await self.publish_name(name)
                if keywords and descriptor is not None and not private:
                    await self.publish_keywords(descriptor)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A failed renewal is not fatal — the TTL has margin left — but
                # it must be visible, because silent failure here is how a
                # service disappears from every HUD without anyone noticing.
                self.stats["failed"] += 1
                log.warning("renewal failed: %s", exc)
            else:
                log.debug("renewed in %.1f s", time.monotonic() - started)


def blinded_index(pub: bytes) -> bytes:
    """Current blinded DHT index for a private service (§6). Convenience."""
    return blinding.index_key(pub, blinding.periods_for(time.time())[0])
