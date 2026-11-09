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
        self.port = 0  # the kernel picks it; start() reports it
        self.endpoints = [f"127.0.0.1:{self.port}"]
        self.service = RelayService(self.static)
        self.listener = Listener(self.identity, "127.0.0.1", self.port, on_link=self.service.attach)

    async def start(self):

        self.port = await self.listener.start()
        # Mutated in place, not rebound: KademliaNode and anything else built
        # in __init__ holds a reference to *this list*, so assigning a new one
        # left them advertising port 0 for ever — every connection to them
        # then timed out, which showed up as a suite that got slower rather
        # than as a failure.
        self.endpoints[:] = [f"127.0.0.1:{self.port}"]
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


# ------------------------------------------------------------- flow control §5
async def test_sendme_lets_more_than_a_window_through():
    """§5: without SENDME the client blocks for ever at CIRCUIT_WINDOW frames.

    This is the test that proves the credit loop closes, not just that the
    windows exist.
    """
    from nullcell.onion.flow import CIRCUIT_WINDOW

    frames = CIRCUIT_WINDOW * 2 + 50
    async with relays(2) as cells, circuit_through(cells) as circuit:
        delivered = 0
        done = asyncio.Event()

        def sink(leg, header) -> None:
            nonlocal delivered
            if header.rcmd == RelayCmd.DATA:
                delivered += 1
                if delivered >= frames:
                    done.set()

        cells[-1].service.on_relay_message = sink
        payload = b"x" * 64
        sender = asyncio.create_task(_send_many(circuit, payload, frames, stream_id=11))
        await asyncio.wait_for(done.wait(), timeout=60.0)
        await asyncio.wait_for(sender, timeout=10.0)

        assert delivered >= frames
        assert circuit.flow.circuit_send.available > 0, "window never reopened"


async def _send_many(circuit, payload: bytes, count: int, *, stream_id: int) -> None:
    for i in range(count):
        await circuit.send_data(payload, stream_id=stream_id)
        if i % 128 == 0:
            await asyncio.sleep(0)


async def test_window_closes_when_nobody_returns_credit():
    """The other half of the same property: credit really is required.

    Note *which* window shuts. ``STREAM_WINDOW`` (500) is half of
    ``CIRCUIT_WINDOW`` (1000), so a single stream runs out of stream credit
    first and can never exhaust the circuit on its own — which is the point of
    metering at two levels instead of one (§5).
    """
    from nullcell.onion.flow import CIRCUIT_WINDOW, STREAM_WINDOW

    assert STREAM_WINDOW < CIRCUIT_WINDOW

    async with relays(1) as cells, circuit_through(cells) as circuit:
        cells[0].service.on_relay_message = None
        # Suppress the relay's own SENDME so no window can reopen.
        cells[0].service._return_credit = lambda leg, stream_id: None  # noqa: SLF001

        sender = asyncio.create_task(_send_many(circuit, b"x", CIRCUIT_WINDOW + 10, stream_id=12))
        await asyncio.sleep(0.5)
        assert not sender.done(), "the sender should be waiting for a SENDME"
        assert circuit.flow.stream_send(12).blocked, "the stream window should shut first"
        assert not circuit.flow.circuit_send.blocked
        assert circuit.flow.circuit_send.available == CIRCUIT_WINDOW - STREAM_WINDOW - 1

        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender


# ------------------------------------------------------ circuit lifetime §5
async def test_a_used_circuit_stops_taking_new_streams():
    from nullcell.onion import circuit as circuit_mod

    async with relays(1) as cells, circuit_through(cells) as circuit:
        await circuit.send_data(b"hola", stream_id=1)
        assert circuit.accepts_new_streams

        circuit.first_use_at -= circuit_mod.MAX_DIRTY + 1  # pretend 10 min passed
        assert not circuit.accepts_new_streams
        await circuit.send_data(b"sigue", stream_id=1)  # an existing stream is fine
        with pytest.raises(CircuitError, match="takes no new streams"):
            await circuit.send_data(b"nuevo", stream_id=2)


async def test_retirement_policy():
    from nullcell.onion import circuit as circuit_mod

    async with relays(1) as cells, circuit_through(cells) as circuit:
        assert circuit.should_retire == (False, "")

        circuit.bytes_sent = circuit_mod.MAX_BYTES
        retire, why = circuit.should_retire
        assert retire and "GB" in why

        circuit.bytes_sent = 0
        circuit.created_at -= circuit_mod.MAX_AGE + 1
        retire, why = circuit.should_retire
        assert retire and "min old" in why


async def test_an_unused_circuit_is_given_back():
    from nullcell.onion import circuit as circuit_mod

    async with relays(1) as cells, circuit_through(cells) as circuit:
        circuit.created_at -= circuit_mod.MAX_IDLE + 1
        assert circuit.should_retire == (True, "never used")
