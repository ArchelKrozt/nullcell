"""The DHT over real QUIC links — the M2 success test.

§13: "30 nodos Docker se encuentran solos". Run in one process as asyncio tasks
rather than containers: it exercises the same mesh, finishes in seconds and can
live in CI. Docker Compose is the M8 variant, for testing the packaging and real
network conditions rather than the protocol.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket

import pytest

from nullcell.core import ids
from nullcell.core.identity import Identity
from nullcell.dht import pow as P
from nullcell.dht import records as R
from nullcell.dht.kademlia import KademliaNode
from nullcell.dht.routing_table import Node, distance
from nullcell.dht.transport import DirectTransport
from nullcell.discovery import pex
from nullcell.net.listener import Listener


@pytest.fixture(autouse=True)
def cheap_pow(monkeypatch):
    monkeypatch.setattr(P, "DIFFICULTY", dict.fromkeys(P.DIFFICULTY, 3))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Cell:
    """One running node plus its listener, for tests."""

    def __init__(self) -> None:
        self.identity = Identity.generate()
        self.port = _free_port()
        self.endpoints = [f"127.0.0.1:{self.port}"]
        self.node = KademliaNode(self.identity, DirectTransport(), endpoints=self.endpoints)
        self.listener = Listener(self.identity, "127.0.0.1", self.port, on_link=self.node.attach)

    async def start(self) -> Cell:
        await self.listener.start()
        return self

    @property
    def as_peer(self) -> Node:
        return Node(pub=self.identity.public_key, endpoints=self.endpoints)

    async def close(self) -> None:
        self.listener.close()
        await self.node.close()


@contextlib.asynccontextmanager
async def mesh(count: int, *, converge: int = 0):
    """*count* cells, all bootstrapped from the first one.

    ``converge`` runs that many PEX rounds afterwards. Bootstrap alone leaves
    the routing tables at whatever the ordering happened to produce, which made
    the thirty-cell assertion depend on timing; PEX is the mechanism §7 already
    specifies for widening a table, so converging with it tests convergence
    instead of luck.
    """
    cells = [await Cell().start() for _ in range(count)]
    try:
        seed = cells[0].as_peer
        for cell in cells[1:]:
            await cell.node.bootstrap([seed])
        for _ in range(converge):
            for cell in cells:
                await pex.exchange(cell.node, rounds=1)
        yield cells
    finally:
        await asyncio.gather(*(cell.close() for cell in cells))


# --------------------------------------------------------------------- basics
async def test_two_cells_ping_over_the_dht():
    async with mesh(2) as cells:
        assert await cells[1].node.ping(cells[0].as_peer)
        assert len(cells[1].node.table) == 1


async def test_bootstrap_from_one_seed_finds_everyone():
    async with mesh(8) as cells:
        for cell in cells:
            assert len(cell.node.table) >= 4, f"{cell.identity.short} is isolated"


async def test_a_record_published_by_one_cell_is_found_by_another():
    async with mesh(8) as cells:
        publisher, seeker = cells[3], cells[7]
        body = R.RelayBody(
            endpoints=publisher.endpoints, x25519=publisher.identity.x25519_public
        ).as_dict()
        record = await R.build(publisher.identity, R.RecordType.RELAY, body)

        assert await publisher.node.store_record(record) >= 1

        seeker.node.store = type(seeker.node.store)()  # forget any local copy
        found = await seeker.node.find_value(publisher.identity.routing_key)
        assert len(found) == 1
        assert found[0].pub == publisher.identity.public_key
        assert found[0].body["ep"] == publisher.endpoints


async def test_readable_name_resolves_across_the_mesh():
    async with mesh(6) as cells:
        owner, visitor = cells[1], cells[4]
        record = await R.build(owner.identity, R.RecordType.NAME, {"name": "panaderia"})
        await owner.node.store_record(record)

        visitor.node.store = type(visitor.node.store)()
        found = await visitor.node.find_value(R.name_key("panaderia"))
        assert len(found) == 1
        assert found[0].pub == owner.identity.public_key


async def test_lookup_of_an_absent_key_returns_nothing():
    async with mesh(5) as cells:
        assert await cells[2].node.find_value(ids.routing_key(b"x" * 32)) == []


async def test_a_forged_record_is_refused_by_the_storing_node():
    """§7: nothing is stored on trust."""
    async with mesh(3) as cells:
        liar, honest = cells[0], cells[1]
        good = await R.build(
            liar.identity, R.RecordType.RELAY, R.RelayBody(endpoints=liar.endpoints).as_dict()
        )
        forged = R.Record(
            good.type,
            good.key,
            good.pub,
            good.ts,
            good.ttl,
            {"ep": ["evil:1"]},
            good.nonce,
            good.sig,
        )

        reply = await liar.node._rpc(
            honest.as_peer,
            {"op": "store", "rec": forged.encode()},
        )
        assert reply is None  # the honest node answered with an error
        assert honest.node.stats["rejected"] >= 1
        assert honest.node.store.get(good.key) == []


async def test_pex_widens_the_routing_table():
    async with mesh(10) as cells:
        node = cells[9].node
        before = len(node.table)
        after = await pex.exchange(node, rounds=4)
        assert after >= before


# ------------------------------------------------------------- the M2 success test
@pytest.mark.slow
async def test_thirty_cells_find_each_other_and_route():
    """§13: thirty cells bootstrap from one seed and resolve each other.

    The meaningful part is the last assertion: a record is fetched by a cell
    that does **not** hold it locally, so the lookup really had to route.
    """
    async with mesh(30, converge=1) as cells:
        for cell in cells:
            assert len(cell.node.table) >= 10, (
                f"{cell.identity.short} knows only {len(cell.node.table)} cells"
            )

        publisher = cells[11]
        body = R.RelayBody(
            endpoints=publisher.endpoints, x25519=publisher.identity.x25519_public
        ).as_dict()
        record = await R.build(publisher.identity, R.RecordType.RELAY, body)
        accepted = await publisher.node.store_record(record)
        assert accepted >= 3, f"only {accepted} cells accepted the descriptor"

        key = publisher.identity.routing_key
        # A cell far from the key in XOR terms, which therefore did not receive
        # the STORE and has to route for it.
        far = max(
            (c for c in cells if c is not publisher),
            key=lambda c: distance(c.identity.routing_key, key),
        )
        assert far.node.store.get(key) == [], "picked a cell that already had it"

        found = await far.node.find_value(key)
        assert len(found) == 1
        assert found[0].pub == publisher.identity.public_key
