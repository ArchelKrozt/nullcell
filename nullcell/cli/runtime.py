"""Assembling a running cell — the glue the commands share.

Every command that touches the network needs the same pieces wired the same
way: identity, a DHT node, a directory of relays it has *observed*, pinned
guards, and a path builder that is the only thing allowed to choose hops.

That last point is the reason this module exists rather than each command
assembling its own. §7's guard pinning and prefix diversity are properties of
*every* circuit or they are properties of none, and the way they quietly become
none is two code paths, one of which builds a path by hand.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from nullcell.core import ids
from nullcell.core.identity import Identity
from nullcell.dht.kademlia import KademliaNode
from nullcell.dht.records import RecordType
from nullcell.dht.routing_table import Node
from nullcell.dht.transport import DirectTransport
from nullcell.net.listener import Listener
from nullcell.onion import rendezvous
from nullcell.onion.circuit import Hop
from nullcell.onion.handshake_nk import x25519_public_bytes
from nullcell.onion.path_select import (
    CIRCUIT_LENGTH,
    GuardSet,
    PathBuilder,
    RelayDirectory,
)
from nullcell.onion.relay_logic import RelayService
from nullcell.onion.service import CircuitManager
from nullcell.services.publisher import Publisher
from nullcell.services.resolver import Resolver

__all__ = ["MIN_RELAY_POW", "Runtime"]

#: Relays below this are not candidates at all (§10).
MIN_RELAY_POW: Final[int] = 9

log = logging.getLogger("nullcell.runtime")


@dataclass
class Runtime:
    """One assembled cell."""

    identity: Identity
    home: Path
    node: KademliaNode
    relay: RelayService
    directory: RelayDirectory
    guards: GuardSet
    paths: PathBuilder
    circuits: CircuitManager
    publisher: Publisher
    resolver: Resolver
    listener: Listener | None = None
    indexer: object | None = None
    search: object | None = None
    gossip: object | None = None
    crawler: object | None = None
    _portals: list[dict] | None = None
    _tasks: list[asyncio.Task] = field(default_factory=list)
    _direct_transport: object | None = None

    # ------------------------------------------------------------------ build
    @classmethod
    def assemble(
        cls,
        identity: Identity,
        home: Path,
        *,
        endpoints: list[str] | None = None,
        min_pow_bits: int = MIN_RELAY_POW,
        hops: int | str = CIRCUIT_LENGTH,
        min_age: float | None = None,
    ) -> Runtime:
        node = KademliaNode(identity, DirectTransport(), endpoints=endpoints or [])
        relay = RelayService(identity.x25519_private)
        rendezvous.install(relay, identity, dht=node)
        directory = RelayDirectory()
        # Lo observado en sesiones anteriores: el reloj de §7 no debe volver a
        # cero cada vez que alguien cierra el programa.
        if recalled := directory.recall(home / "observed.json"):
            log.info("recordados %d relevos vistos antes", recalled)
        guards = GuardSet(home / "guards.json")
        return cls(
            identity=identity,
            home=home,
            node=node,
            relay=relay,
            directory=directory,
            guards=guards,
            paths=PathBuilder(
                directory,
                guards,
                min_pow_bits=min_pow_bits,
                hops=hops,
                **({} if min_age is None else {"min_age": min_age}),
            ),
            circuits=CircuitManager(),
            publisher=Publisher(identity, node),
            resolver=Resolver(node, hosts_file=home / "hosts"),
        )

    # ------------------------------------------------- §7, decisión 8
    def go_anonymous(self) -> None:
        """Send every DHT request over a three-hop circuit (§7, decision 8).

        §7 is explicit — *"un cliente puro lo manda todo por circuito, sin
        excepciones"*, the only exception being routing-table upkeep by a relay
        that is publicly reachable anyway. `CircuitTransport` was built and
        tested in M4 and then **never wired to a command**: `assemble` gave
        every cell a `DirectTransport`, whose own docstring says it is "wrong
        for a client".

        What that cost, concretely: a cell that only browses sent its lookups
        straight to the DHT peers holding them, so each of those peers learned
        this node's address *together with the name it was asking for* — which
        is the exact link the three hops exist to break, handed over before the
        first circuit was built.

        Must be called after the directory can build paths; `PathBuilder.ready`
        says when.
        """
        from nullcell.dht.transport import CircuitTransport

        async def path_to(node: Node) -> list[Hop]:
            """A path whose **exit** is the peer being asked."""
            candidate = self.directory.get(node.pub)
            if candidate is None:
                # No descriptor means no x25519 key, so there is no handshake
                # that could make this peer the last hop. Refusing sends the
                # lookup to another peer (`_rpc` penalises and moves on); the
                # alternative would be falling back to a direct request, which
                # is the leak this method exists to close.
                raise LookupError(f"no descriptor for {node.short}, cannot exit there")
            return self.paths.build(ending_at=candidate.as_hop())

        previous = self.node.transport
        self.node.transport = CircuitTransport(path_to, self.circuits)
        self._direct_transport = previous
        log.info("DHT over circuits (§7, decisión 8)")

    @property
    def anonymous(self) -> bool:
        """Whether DHT requests leave this cell over a circuit."""
        return bool(getattr(self.node.transport, "anonymous", False))

    async def listen(self, host: str, port: int) -> None:
        """Become reachable, so this cell can carry circuits for others."""

        def on_link(link) -> None:
            self.node.attach(link)
            self.relay.attach(link)

        self.listener = Listener(self.identity, host, port, on_link=on_link)
        await self.listener.start()
        # Para que la celda pueda ser el primer salto de sus propios circuitos
        # (red de un solo relevo): se alcanza a sí misma por el loopback.
        local = "127.0.0.1" if host in ("0.0.0.0", "::", "") or ":" not in host else host
        self.circuits.self_pub = self.identity.public_key
        self.circuits.self_endpoint = f"{local}:{port}" if ":" not in local else f"[{local}]:{port}"

    def self_hop(self):
        """Esta celda como salto, con sus direcciones públicas.

        Para el portal de una red pequeña: si no hay otro relevo que funcione,
        él mismo es su punto de enlace. Las direcciones son las anunciadas
        (las que verán los clientes); el circuito propio va por el loopback.
        """
        from nullcell.onion.circuit import Hop

        if self.listener is None:
            return None
        endpoints = [e for e in getattr(self.node, "endpoints", []) if e]
        if not endpoints:
            return None
        return Hop(
            pub=self.identity.public_key,
            x25519=self.identity.x25519_public,
            endpoints=list(endpoints),
        )

    # -------------------------------------------------------------- directory
    async def learn_relays(self, peers: list[Node] | None = None) -> int:
        """Fetch relay descriptors for the peers we know and observe them.

        Observation is recorded **here**, locally: §7 asks for observed age,
        and a descriptor's own timestamp is the publisher's claim.
        """
        candidates = peers if peers is not None else self.node.table.all_nodes()
        learned = 0
        for peer in candidates:
            try:
                found = await self.node.find_value(ids.routing_key(peer.pub), want=RecordType.RELAY)
            except Exception as exc:
                log.debug("could not fetch a descriptor for %s: %s", peer.short, exc)
                continue
            for record in found:
                if record.type is RecordType.RELAY and self.directory.ingest(
                    record, pow_bits=record.measure_pow_bits()
                ):
                    learned += 1
        self.absorb_observations()
        return learned

    def absorb_observations(self) -> int:
        """Tell the directory where relays actually answered (§7).

        Every link we opened knows the address it is sending to, and a relay
        announced by DNS name can only be checked for diversity once somebody
        has seen its address. Nothing extra leaks: these are connections that
        already happened.
        """
        noted = 0
        for transport in (self.node.transport, self.relay):
            links = getattr(transport, "links", None)
            observed = getattr(links, "observed", None) if links is not None else None
            if not observed:
                continue
            for pub, address in observed.items():
                self.directory.note_observed(pub, address)
                noted += 1
        return noted

    async def keep_learning(self, interval: float = 120.0) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.learn_relays()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("relay discovery round failed: %s", exc)

    def open_index(self) -> None:
        """Attach the index, the search engine and the gossip service (§8, §9)."""
        from nullcell.search.gossip import GossipService
        from nullcell.search.indexer import Indexer
        from nullcell.search.query import Search

        indexer = Indexer(self.home / "index.db")
        indexer.start()
        self.indexer = indexer
        self.search = Search(indexer, self.node)
        self.gossip = GossipService(self.identity, indexer=indexer)
        # The DHT serves the gossip op from here, so a peer asking us gets
        # answered from the same store the HUD reads.
        self.node.gossip = self.gossip

    async def gossip_round(self, limit: int = 4) -> int:
        """Exchange observations with a few peers (§8 mechanism 3).

        This is the mechanism §8 calls primary: a new cell fills its panel in
        minutes from what the network already saw, instead of crawling six-hop
        circuits for days.
        """
        if self.gossip is None:
            return 0
        kept = 0
        for peer in self.node.table.closest(self.identity.routing_key, limit):
            try:
                kept += await self.node.exchange_gossip(peer)
            except Exception as exc:
                log.debug("gossip with %s failed: %s", peer.short, exc)
        return kept

    async def keep_gossiping(self, interval: float = 180.0) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                if self.gossip is not None:
                    self.gossip.expire()
                await self.gossip_round()
                await self.refresh_portals()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("gossip round failed: %s", exc)

    async def refresh_portals(self) -> int:
        """Re-read the index so the HUD's portal panel is current (§9)."""
        if self.indexer is None:
            return 0
        hits = await self.indexer.recent(limit=64)
        portals = []
        for hit in hits:
            entry = {
                "nid": hit.nid,
                "kind": hit.kind,
                "title": hit.title or hit.nid[:8],
                "tags": hit.tags,
                "age": round(hit.age),
                "confirmed": hit.confirmed,
                "seen": "",
            }
            # §9's liveness, in order of cost: a signed observation from
            # another cell beats our own index entry, because it is evidence
            # from somebody else rather than a note to ourselves.
            if self.gossip is not None:
                try:
                    pub = ids.decode_nid(hit.nid).pub
                except ids.AddressError:
                    pub = None
                if pub is not None:
                    confirmations = self.gossip.confirmations(pub)
                    seen = self.gossip.last_seen(pub)
                    if confirmations:
                        entry["confirmed"] = confirmations
                        entry["seen"] = self.gossip.describe(pub)
                    if seen is not None:
                        entry["age"] = max(0, round(time.time() - seen))
            portals.append(entry)
        self._portals = portals
        return len(self._portals)

    async def keep_portals_fresh(self, interval: float = 15.0) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.refresh_portals()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("portal refresh failed: %s", exc)

    def spawn(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._tasks.append(task)
        return task

    # ------------------------------------------------------------------ paths
    @property
    def own_hop(self):
        from nullcell.onion.circuit import Hop

        return Hop(
            pub=self.identity.public_key,
            x25519=x25519_public_bytes(self.identity.x25519_private),
            endpoints=list(self.node.endpoints),
        )

    def topology(self) -> dict:
        """Bucket occupancy of the Kademlia table (§9's topology panel).

        Mostly empty near buckets is not a fault: bucket *i* covers
        2^(255-i) of the keyspace, so the close ones stay empty until the
        network is enormous (§7).
        """
        table = self.node.table
        buckets = [len(bucket) for bucket in table._buckets]  # noqa: SLF001
        used = [(index, count) for index, count in enumerate(buckets) if count]
        return {
            "cells": len(table),
            "buckets_in_use": len(used),
            "buckets": used[-24:],
            "k": table.k,
        }

    def portals(self, limit: int = 24) -> list[dict]:
        """Portals this cell knows about, with §9's liveness, not uptime.

        §9 is explicit that the HUD must not invent metrics: this reports
        *"visto hace 4 m, confirmado por 7 celdas"*, which is what we actually
        know, and never a percentage nobody can verify.
        """
        if self._portals is None:
            return []
        return self._portals[:limit]

    def status(self) -> dict:
        """What the HUD shows (§9), from real sources only."""
        circuits = [
            {
                "id": circuit.circ_id,
                "state": "up" if circuit.ready else "down",
                "hops": [hop.nid for hop in circuit.hops],
                "frames_sent": circuit.frames_sent,
                "frames_received": circuit.frames_received,
                "rtt_ms": circuit.hops[0].rtt_ms if circuit.hops else None,
            }
            for circuit in self.circuits.circuits
        ]
        return {
            "nid": self.identity.nid,
            "phase": "listo" if self.paths.ready else (self.paths.why_not_ready() or "conectando"),
            "peers": len(self.node.table),
            "relays": len(self.directory.usable(min_pow_bits=self.paths.min_pow_bits)),
            "guards": [ids.encode_nid(pub) for pub in self.guards.pubs],
            "records": self.node.store.stats["records"],
            "legs": len(self.relay.legs),
            "anonymous_dht": self.node.transport.anonymous,
            "circuits": circuits,
            "portals": self.portals(),
            "topology": self.topology(),
            "observations": len(self.gossip) if self.gossip is not None else 0,
        }

    # --------------------------------------------------------------- teardown
    def remember_observations(self) -> int:
        """Anotar lo que esta celda ha visto, para el próximo arranque."""
        return self.directory.remember(self.home / "observed.json")

    async def close(self) -> None:
        self.remember_observations()

        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        await self.circuits.close()
        await self.relay.close()
        await self.node.close()
        if self.indexer is not None:
            self.indexer.close()
            self.indexer = None
