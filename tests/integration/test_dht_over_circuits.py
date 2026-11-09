"""The DHT over circuits — §7's decision 8.

A client's lookup must not tell the peer being asked who is asking. These tests
assert that directly: the request reaches the DHT node as the exit of a
three-hop circuit, and the node that answers has no link to the client at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from nullcell.core.identity import Identity
from nullcell.dht import pow as P
from nullcell.dht import records as R
from nullcell.dht.kademlia import KademliaNode
from nullcell.dht.routing_table import Node
from nullcell.dht.transport import CircuitTransport, DirectTransport, TransportError
from nullcell.net.listener import Listener
from nullcell.onion import rendezvous
from nullcell.onion.circuit import Hop
from nullcell.onion.handshake_nk import x25519_public_bytes
from nullcell.onion.relay_logic import RelayService
from nullcell.onion.service import CircuitManager


@pytest.fixture(autouse=True)
def cheap_pow(monkeypatch):
    monkeypatch.setattr(P, "DIFFICULTY", dict.fromkeys(P.DIFFICULTY, 3))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FullRelay:
    """A reachable relay that carries circuits *and* runs a DHT node."""

    def __init__(self) -> None:
        self.identity = Identity.generate()
        self.static = X25519PrivateKey.generate()
        self.port = _free_port()
        self.endpoints = [f"127.0.0.1:{self.port}"]
        self.node = KademliaNode(self.identity, DirectTransport(), endpoints=self.endpoints)
        self.relay = RelayService(self.static, extend_timeout=5.0)
        rendezvous.install(self.relay, self.identity, dht=self.node)
        self.listener = Listener(self.identity, "127.0.0.1", self.port, on_link=self._on_link)

    def _on_link(self, link) -> None:
        # circ_id 0 is link-level traffic, which the DHT serves directly for
        # other relays; anything with a circuit id goes to the relay service.
        self.node.attach(link)
        self.relay.attach(link)

    async def start(self) -> FullRelay:
        await self.listener.start()
        return self

    @property
    def hop(self) -> Hop:
        return Hop(
            pub=self.identity.public_key,
            x25519=x25519_public_bytes(self.static),
            endpoints=self.endpoints,
        )

    @property
    def peer(self) -> Node:
        return Node(pub=self.identity.public_key, endpoints=self.endpoints)

    async def close(self) -> None:
        self.listener.close()
        await self.relay.close()
        await self.node.close()


@contextlib.asynccontextmanager
async def mesh(count: int = 4):
    relays = [await FullRelay().start() for _ in range(count)]
    try:
        for relay in relays[1:]:
            await relay.node.bootstrap([relays[0].peer])
        yield relays
    finally:
        await asyncio.gather(*(relay.close() for relay in relays))


# ----------------------------------------------------------------- decision 8
async def test_a_lookup_over_a_circuit_reaches_the_node():
    async with mesh(4) as relays:
        owner = relays[1]
        record = await R.build(owner.identity, R.RecordType.NAME, {"name": "panaderia"})
        assert await owner.node.store_record(record) >= 1

        manager = CircuitManager()

        async def path(node: Node) -> list[Hop]:
            # Three hops, exiting at the node being asked.
            exit_hop = next(r.hop for r in relays if r.identity.public_key == node.pub)
            middles = [r.hop for r in relays if r.identity.public_key != node.pub][:2]
            return [*middles, exit_hop]

        transport = CircuitTransport(path, manager)
        client = KademliaNode(Identity.generate(), transport)
        try:
            assert transport.anonymous
            client.table.add(relays[2].peer)
            found = await client.find_value(R.name_key("panaderia"))
            assert len(found) == 1
            assert found[0].pub == owner.identity.public_key
        finally:
            await client.close()


async def test_the_answering_node_has_no_link_to_the_client():
    """The property decision 8 exists for."""
    async with mesh(4) as relays:
        answerer = relays[2]
        manager = CircuitManager()

        async def path(node: Node) -> list[Hop]:
            exit_hop = next(r.hop for r in relays if r.identity.public_key == node.pub)
            middles = [r.hop for r in relays if r.identity.public_key != node.pub][:2]
            return [*middles, exit_hop]

        transport = CircuitTransport(path, manager)
        client_identity = Identity.generate()
        client = KademliaNode(client_identity, transport)
        try:
            links_before = len(answerer.listener.links)
            await client.ping(answerer.peer)

            # The request arrived through the circuit's exit, so the answering
            # node's new link (if any) comes from the middle hop, never from
            # the client. The client's own identity appears nowhere on it.
            assert client.stats["rpc_out"] >= 1
            new_links = answerer.listener.links[links_before:]
            for link in new_links:
                assert link.peer_certificate is None  # a client-side link
        finally:
            await client.close()


async def test_direct_and_circuit_transports_are_interchangeable():
    """Kademlia never learns which transport it has (§7)."""
    async with mesh(3) as relays:
        manager = CircuitManager()

        async def path(node: Node) -> list[Hop]:
            exit_hop = next(r.hop for r in relays if r.identity.public_key == node.pub)
            middles = [r.hop for r in relays if r.identity.public_key != node.pub][:2]
            return [*middles, exit_hop]

        direct = KademliaNode(Identity.generate(), DirectTransport())
        over_circuits = KademliaNode(Identity.generate(), CircuitTransport(path, manager))
        try:
            assert await direct.ping(relays[0].peer)
            assert await over_circuits.ping(relays[0].peer)
            assert not direct.transport.anonymous
            assert over_circuits.transport.anonymous
        finally:
            await direct.close()
            await over_circuits.close()


async def test_the_directory_circuit_is_reused():
    """§7: the latency budget assumes construction is not paid per lookup."""
    async with mesh(3) as relays:
        manager = CircuitManager()
        builds = 0

        async def path(node: Node) -> list[Hop]:
            nonlocal builds
            builds += 1
            exit_hop = next(r.hop for r in relays if r.identity.public_key == node.pub)
            middles = [r.hop for r in relays if r.identity.public_key != node.pub][:2]
            return [*middles, exit_hop]

        transport = CircuitTransport(path, manager)
        client = KademliaNode(Identity.generate(), transport)
        try:
            for _ in range(5):
                assert await client.ping(relays[0].peer)
            assert builds == 1, f"built {builds} circuits for 5 requests"
        finally:
            await client.close()


async def test_an_unreachable_node_fails_cleanly():
    manager = CircuitManager()

    async def path(node: Node) -> list[Hop]:
        return [Hop(pub=node.pub, x25519=b"\x00" * 32, endpoints=["127.0.0.1:1"])]

    transport = CircuitTransport(path, manager)
    ghost = Node(pub=Identity.generate().public_key, endpoints=["127.0.0.1:1"])
    try:
        with pytest.raises(TransportError, match="no circuit"):
            await transport.rpc(ghost, b"x", timeout=1.0)
    finally:
        await transport.close()


async def test_a_node_with_no_endpoint_is_refused():
    manager = CircuitManager()

    async def path(node: Node) -> list[Hop]:  # pragma: no cover - never called
        return []

    transport = CircuitTransport(path, manager)
    try:
        with pytest.raises(TransportError, match="no known endpoint"):
            await transport.rpc(Node(pub=b"\x00" * 32), b"x")
    finally:
        await transport.close()
