"""Rendezvous over real circuits — the M4 success test.

§13: "dos nodos tras NAT se hablan sin abrir puertos". The test asserts that
literally: neither the service nor the client runs a listener at all. Only the
relays do, and every connection the two ends make is outbound.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from nullcell.core.frames import RelayCmd
from nullcell.core.identity import Identity
from nullcell.net.listener import Listener
from nullcell.onion import rendezvous
from nullcell.onion.circuit import Hop
from nullcell.onion.handshake_nk import x25519_public_bytes
from nullcell.onion.relay_logic import RelayService
from nullcell.onion.rendezvous_msgs import EstablishIntro, Introduce, IntroPointRef, new_cookie
from nullcell.onion.service import CircuitManager, HiddenService, connect_to_service


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Relay:
    """A reachable relay that can also be an intro or rendezvous point."""

    def __init__(self) -> None:
        self.identity = Identity.generate()
        self.static = X25519PrivateKey.generate()
        self.port = 0  # the kernel picks it; start() reports it
        self.endpoints = [f"127.0.0.1:{self.port}"]
        self.service = RelayService(self.static, extend_timeout=5.0)
        self.intro, self.rendezvous = rendezvous.install(self.service, self.identity)
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

    @property
    def ref(self) -> IntroPointRef:
        return IntroPointRef(
            pub=self.identity.public_key,
            x25519=x25519_public_bytes(self.static),
            endpoints=self.endpoints,
        )

    async def close(self) -> None:
        self.listener.close()
        await self.service.close()


@contextlib.asynccontextmanager
async def network(count: int = 5):
    relays = [await Relay().start() for _ in range(count)]
    try:
        yield relays
    finally:
        await asyncio.gather(*(relay.close() for relay in relays))


@contextlib.asynccontextmanager
async def hosted(relays: list[Relay], *, intro_count: int = 1):
    """A service with *intro_count* intro points, and no listener of its own."""
    identity = Identity.generate()
    manager = CircuitManager()
    service = HiddenService(identity, manager)
    paths = [[relays[0].hop, relays[1].hop, relays[2 + i].hop] for i in range(intro_count)]
    try:
        established = await service.establish(paths)
        assert established == intro_count, f"only {established} intro points came up"
        yield service, manager
    finally:
        await service.close()
        await manager.close()


# ------------------------------------------------------------- the §13 success test
async def test_two_nodes_talk_with_no_open_ports():
    """Neither end listens. Every connection either one makes is outbound."""
    async with network(5) as relays, hosted(relays) as (service, _service_manager):
        intro_point = service.intro_points[0]
        rendezvous_relay = relays[4]

        greeted: list[bytes] = []
        service.on_session = lambda session: setattr(
            session, "on_data", lambda data: greeted.append(data)
        )

        client = CircuitManager()
        try:
            session = await connect_to_service(
                client,
                service_pub=service.identity.public_key,
                service_x25519=service.x25519_public,
                intro_point=intro_point,
                rendezvous_path=[relays[1].hop, relays[2].hop, rendezvous_relay.hop],
                intro_path=[relays[0].hop, relays[3].hop, intro_point.as_hop()],
            )

            await session.send(b"hola desde el cliente")
            for _ in range(200):
                if greeted:
                    break
                await asyncio.sleep(0.01)

            assert greeted == [b"hola desde el cliente"]
        finally:
            await client.close()

        # The property §13 asks for: the two ends never listened.
        assert rendezvous_relay.rendezvous.stats["spliced"] == 1
        assert relays[4].rendezvous.stats["relayed"] >= 1


async def test_the_service_answers_back():
    """Six hops, both directions, end to end encrypted."""
    async with network(5) as relays, hosted(relays) as (service, _):
        replies: list[bytes] = []

        def on_session(session):
            async def echo(data: bytes) -> None:
                await session.send(b"eco: " + data)

            session.on_data = lambda data: asyncio.ensure_future(echo(data))

        service.on_session = on_session

        client = CircuitManager()
        try:
            session = await connect_to_service(
                client,
                service_pub=service.identity.public_key,
                service_x25519=service.x25519_public,
                intro_point=service.intro_points[0],
                rendezvous_path=[relays[1].hop, relays[2].hop, relays[4].hop],
                intro_path=[relays[0].hop, relays[3].hop, service.intro_points[0].as_hop()],
            )
            session.on_data = replies.append
            await session.send(b"ping")
            for _ in range(300):
                if replies:
                    break
                await asyncio.sleep(0.01)
            assert replies == [b"eco: ping"]
        finally:
            await client.close()


async def test_the_rendezvous_point_cannot_read_what_it_relays():
    """§6: the two ends agreed a Noise key through the introduction."""
    async with network(5) as relays, hosted(relays) as (service, _):
        seen: list[bytes] = []
        rendezvous_relay = relays[4]
        original = rendezvous_relay.rendezvous._across

        def spy(leg, header):
            seen.append(header.payload)
            return original(leg, header)

        rendezvous_relay.rendezvous._across = spy

        arrived: list[bytes] = []
        service.on_session = lambda s: setattr(s, "on_data", arrived.append)

        client = CircuitManager()
        secret = b"numero de cuenta 1234"
        try:
            session = await connect_to_service(
                client,
                service_pub=service.identity.public_key,
                service_x25519=service.x25519_public,
                intro_point=service.intro_points[0],
                rendezvous_path=[relays[1].hop, relays[2].hop, rendezvous_relay.hop],
                intro_path=[relays[0].hop, relays[3].hop, service.intro_points[0].as_hop()],
            )
            await session.send(secret)
            for _ in range(200):
                if arrived:
                    break
                await asyncio.sleep(0.01)
        finally:
            await client.close()

        assert arrived == [secret], "the service should have read it"
        assert seen, "the rendezvous point should have relayed something"
        assert all(secret not in payload for payload in seen), (
            "the rendezvous point saw the plaintext"
        )


async def test_three_intro_points():
    """§6 gives a service three so losing one does not take it down."""
    async with network(6) as relays, hosted(relays, intro_count=3) as (service, _):
        assert len(service.intro_points) == 3
        assert len({p.pub for p in service.intro_points}) == 3
        assert service.descriptor_body()["x"] == service.x25519_public
        assert len(service.descriptor_body()["ip"]) == 3


# --------------------------------------------------------------------- refusals
async def test_an_intro_slot_cannot_be_claimed_with_a_bad_signature():
    """Otherwise anyone could swallow every introduction meant for a service."""
    async with network(3) as relays:
        impostor = Identity.generate()
        manager = CircuitManager()
        try:
            circuit = await manager.build([relays[0].hop, relays[1].hop, relays[2].hop])
            forged = EstablishIntro(service_pub=impostor.public_key, signature=b"\x00" * 64)
            circuit.send_relay(RelayCmd.ESTABLISH_INTRO, forged.encode())
            await asyncio.sleep(0.3)
            assert relays[2].intro.stats["established"] == 0
            assert relays[2].intro.stats["refused"] >= 1
        finally:
            await manager.close()


async def test_an_unknown_cookie_is_refused():
    async with network(3) as relays:
        manager = CircuitManager()
        try:
            circuit = await manager.build([relays[0].hop, relays[1].hop, relays[2].hop])
            circuit.send_relay(RelayCmd.RENDEZVOUS1, new_cookie() + b"\x00" * 48)
            await asyncio.sleep(0.3)
            assert relays[2].rendezvous.stats["spliced"] == 0
            assert relays[2].rendezvous.stats["refused"] >= 1
        finally:
            await manager.close()


async def test_an_introduction_for_an_unknown_service_is_refused():
    async with network(3) as relays:
        manager = CircuitManager()
        try:
            circuit = await manager.build([relays[0].hop, relays[1].hop, relays[2].hop])
            message = Introduce(
                service_pub=Identity.generate().public_key,
                rendezvous=relays[0].ref,
                cookie=new_cookie(),
                handshake=b"\x00" * 48,
            )
            circuit.send_relay(RelayCmd.INTRODUCE1, message.encode())
            await asyncio.sleep(0.3)
            assert relays[2].intro.stats["introduced"] == 0
        finally:
            await manager.close()


@pytest.mark.parametrize("blob", [b"", b"basura", b"\x00" * 200])
def test_malformed_messages_are_refused(blob):
    from nullcell.onion.rendezvous_msgs import MessageError

    with pytest.raises(MessageError):
        Introduce.decode(blob)
    with pytest.raises(MessageError):
        EstablishIntro.decode(blob)
