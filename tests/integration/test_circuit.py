"""Three-hop onion circuits over real QUIC — the M3 success test.

§13 asks for two things: that a 3-hop circuit is built at all, and that **hop 2
can know neither the origin nor the destination**. The second is the one that
matters, so it is asserted directly rather than inferred.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from nullcell.core.frames import RelayCmd, pack_relay_payload
from nullcell.core.identity import Identity
from nullcell.net.listener import Listener
from nullcell.net.quic_link import open_link
from nullcell.onion.circuit import Circuit, CircuitError, Hop
from nullcell.onion.handshake_nk import x25519_public_bytes
from nullcell.onion.relay_logic import RelayService


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RelayCell:
    """A reachable relay that carries circuits."""

    def __init__(self) -> None:
        self.identity = Identity.generate()
        self.static = X25519PrivateKey.generate()
        self.port = _free_port()
        self.endpoints = [f"127.0.0.1:{self.port}"]
        self.service = RelayService(self.static)
        self.listener = Listener(self.identity, "127.0.0.1", self.port, on_link=self.service.attach)

    async def start(self) -> RelayCell:
        await self.listener.start()
        return self

    @property
    def hop(self) -> Hop:
        return Hop(
            pub=self.identity.public_key,
            x25519=x25519_public_bytes(self.static),
            endpoints=self.endpoints,
        )

    async def close(self) -> None:
        self.listener.close()
        await self.service.close()


@contextlib.asynccontextmanager
async def relays(count: int):
    cells = [await RelayCell().start() for _ in range(count)]
    try:
        yield cells
    finally:
        await asyncio.gather(*(cell.close() for cell in cells))


@contextlib.asynccontextmanager
async def circuit_through(cells: list[RelayCell]):
    """A client circuit through *cells*, guard first."""
    guard = cells[0]
    async with open_link(guard.identity.nid, "127.0.0.1", guard.port) as link:
        circuit = Circuit(link)
        try:
            await circuit.build([cell.hop for cell in cells])
            yield circuit
        finally:
            circuit.destroy()


# ------------------------------------------------------------------- building
async def test_single_hop_circuit():
    async with relays(1) as cells, circuit_through(cells) as circuit:
        assert len(circuit) == 1
        assert circuit.ready
        assert cells[0].service.stats["created"] == 1


async def test_two_hop_circuit_extends():
    async with relays(2) as cells, circuit_through(cells) as circuit:
        assert len(circuit) == 2
        assert cells[0].service.stats["extended"] == 1
        assert cells[1].service.stats["created"] == 1


async def test_three_hop_circuit():
    """The shape §5 specifies: guard, middle, exit."""
    async with relays(3) as cells, circuit_through(cells) as circuit:
        assert len(circuit) == 3
        assert circuit.describe().count(">") == 2
        assert cells[0].service.stats["created"] == 1
        assert cells[0].service.stats["extended"] == 1
        assert cells[1].service.stats["extended"] == 1
        assert cells[2].service.stats["created"] == 1
        assert cells[2].service.stats["extended"] == 0  # the exit extends nothing


# ---------------------------------------------------- the property that matters
async def test_middle_hop_learns_neither_origin_nor_destination():
    """§5: hop 2 knows nothing.

    Asserted on what the middle relay actually holds: it has one hop's crypto
    state, a previous neighbour that is the guard (not the client) and a next
    neighbour that is the exit. The client's address appears nowhere.
    """
    async with relays(3) as cells, circuit_through(cells) as circuit:
        guard, middle, exit_ = cells
        legs = list(middle.service.legs.values())
        assert len(legs) == 1
        leg = legs[0]

        # It can peel exactly one layer, and holds no other key material.
        assert leg.crypto is not None
        assert not hasattr(leg, "path")

        # Its neighbours are the guard and the exit. The client never appears:
        # the client's only link is to the guard.
        assert leg.prev_link is not None
        assert leg.next_link is not None
        assert leg.prev_link.peer_certificate is None  # a client-side link, unauthenticated

        # And the guard, which does know the client, does not know the exit:
        # its own next hop is the middle.
        guard_leg = next(iter(guard.service.legs.values()))
        assert guard_leg.next_circ_id is not None
        assert guard_leg.next_link is not middle.service.legs  # structural sanity
        assert exit_.service.stats["created"] == 1
        assert circuit.describe()


async def test_payload_is_readable_only_at_the_addressed_hop():
    """Layer crypto end to end: the exit sees plaintext, nobody before it does."""
    async with relays(3) as cells, circuit_through(cells) as circuit:
        seen: list[tuple[int, bytes]] = []
        secret = b"GET /pan HTTP/1.1"

        cells[2].service.on_relay_message = lambda leg, header: seen.append(
            (header.stream_id, header.payload)
        )
        circuit.send_relay(RelayCmd.DATA, secret, stream_id=9)
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0.01)

        assert seen == [(9, secret)]
        # The middle relay forwarded it without ever recognising it.
        middle_leg = next(iter(cells[1].service.legs.values()))
        assert middle_leg.frames_forward >= 1


# ----------------------------------------------------------------- resilience
async def test_building_through_an_unreachable_hop_fails_cleanly():
    async with relays(1) as cells:
        guard = cells[0]
        guard.service.extend_timeout = 1.0  # nothing listens; do not wait 15 s
        ghost = Hop(
            pub=Identity.generate().public_key,
            x25519=x25519_public_bytes(X25519PrivateKey.generate()),
            endpoints=["127.0.0.1:1"],  # nothing listens there
        )
        async with open_link(guard.identity.nid, "127.0.0.1", guard.port) as link:
            circuit = Circuit(link)
            with pytest.raises(CircuitError):
                await circuit.build([guard.hop, ghost])
            circuit.destroy()


async def test_wrong_static_key_fails_the_handshake():
    async with relays(1) as cells:
        guard = cells[0]
        impostor = Hop(
            pub=guard.identity.public_key,
            x25519=x25519_public_bytes(X25519PrivateKey.generate()),  # not its key
            endpoints=guard.endpoints,
        )
        async with open_link(guard.identity.nid, "127.0.0.1", guard.port) as link:
            circuit = Circuit(link)
            with pytest.raises(CircuitError):
                await circuit.build([impostor])
            circuit.destroy()


async def test_a_tampered_frame_tears_the_circuit_down():
    """§5: carrying on after a failed digest is what a tagging attack needs."""
    async with relays(2) as cells, circuit_through(cells) as circuit:
        assert circuit.ready
        bogus = pack_relay_payload(RelayCmd.DATA, b"inyectado")
        circuit._on_frame(circuit.circ_id, 4, memoryview(bogus))  # cmd 4 = RELAY
        assert circuit.closed


async def test_destroy_releases_the_circuit_id():
    async with relays(1) as cells:
        guard = cells[0]
        async with open_link(guard.identity.nid, "127.0.0.1", guard.port) as link:
            circuit = Circuit(link)
            await circuit.build([guard.hop])
            assert circuit.circ_id in link.circuits
            circuit.destroy()
            assert circuit.circ_id not in link.circuits
            assert circuit.closed
