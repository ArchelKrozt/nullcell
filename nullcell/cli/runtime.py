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
from nullcell.onion.handshake_nk import x25519_public_bytes
from nullcell.onion.path_select import GuardSet, PathBuilder, RelayDirectory
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
    _tasks: list[asyncio.Task] = field(default_factory=list)

    # ------------------------------------------------------------------ build
    @classmethod
    def assemble(
        cls,
        identity: Identity,
        home: Path,
        *,
        endpoints: list[str] | None = None,
        min_pow_bits: int = MIN_RELAY_POW,
    ) -> Runtime:
        node = KademliaNode(identity, DirectTransport(), endpoints=endpoints or [])
        relay = RelayService(identity.x25519_private)
        rendezvous.install(relay, identity, dht=node)
        directory = RelayDirectory()
        guards = GuardSet(home / "guards.json")
        return cls(
            identity=identity,
            home=home,
            node=node,
            relay=relay,
            directory=directory,
            guards=guards,
            paths=PathBuilder(directory, guards, min_pow_bits=min_pow_bits),
            circuits=CircuitManager(),
            publisher=Publisher(identity, node),
            resolver=Resolver(node, hosts_file=home / "hosts"),
        )

    async def listen(self, host: str, port: int) -> None:
        """Become reachable, so this cell can carry circuits for others."""

        def on_link(link) -> None:
            self.node.attach(link)
            self.relay.attach(link)

        self.listener = Listener(self.identity, host, port, on_link=on_link)
        await self.listener.start()

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
                found = await self.node.find_value(ids.routing_key(peer.pub))
            except Exception as exc:
                log.debug("could not fetch a descriptor for %s: %s", peer.short, exc)
                continue
            for record in found:
                if record.type is RecordType.RELAY and self.directory.ingest(
                    record, pow_bits=record.measure_pow_bits()
                ):
                    learned += 1
        return learned

    async def keep_learning(self, interval: float = 120.0) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.learn_relays()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("relay discovery round failed: %s", exc)

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
        }

    # --------------------------------------------------------------- teardown
    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        await self.circuits.close()
        await self.relay.close()
        await self.node.close()
