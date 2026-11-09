"""Hosting and reaching a service over a rendezvous — §6.

This is where §0's "unexpected gift of decision 1" becomes concrete: **a
service hosted on Nullcell needs no public IP, no UPnP and no open port.**
Every connection it makes is outbound. It builds a circuit to each of its
introduction points and keeps it open; a client that wants to reach it arranges
a meeting at a third relay. NAT, which was the enemy of the earlier design,
stops being a problem for whoever publishes. The price is paid by the relays,
which do have to be reachable.

Six hops, and what each party knows:

* the client only ever talked to its own guard and to the rendezvous point,
* the service only ever talked to its own guard, its intro points and the
  rendezvous point,
* the rendezvous point forwards bytes it cannot read, because the two ends
  completed a Noise NK handshake through the introduction,
* and no relay on either side knows both ends.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Final

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from nullcell.core import ids
from nullcell.core.frames import RELAY_DATA_SIZE, RelayCmd
from nullcell.core.identity import Identity
from nullcell.net.link_manager import LinkManager
from nullcell.net.quic_link import open_link
from nullcell.onion.circuit import Circuit, CircuitError, Hop
from nullcell.onion.handshake_nk import (
    TRANSPORT_OVERHEAD,
    HandshakeError,
    Initiator,
    Responder,
    x25519_public_bytes,
)
from nullcell.onion.rendezvous_msgs import (
    COOKIE_LEN,
    EstablishIntro,
    Introduce,
    IntroPointRef,
    MessageError,
    new_cookie,
)

__all__ = [
    "MAX_E2E_PAYLOAD",
    "CircuitManager",
    "HiddenService",
    "Session",
    "connect_to_service",
]

#: End-to-end data per frame: the relay data field minus Noise's AEAD tag.
MAX_E2E_PAYLOAD: Final[int] = RELAY_DATA_SIZE - TRANSPORT_OVERHEAD
_TIMEOUT: Final[float] = 30.0

log = logging.getLogger("nullcell.service")


class CircuitManager:
    """Builds and holds circuits. Opens one link per guard and reuses it."""

    def __init__(self, links: LinkManager | None = None) -> None:
        self.links = links if links is not None else LinkManager()
        self.circuits: list[Circuit] = []
        self._stack = contextlib.AsyncExitStack()

    async def build(self, hops: list[Hop]) -> Circuit:
        """Build a circuit through *hops*, guard first."""
        if not hops:
            raise CircuitError("a circuit needs at least one hop")
        guard = hops[0]
        if not guard.endpoints:
            raise CircuitError(f"{guard.short} has no known endpoint")
        from nullcell.net.link_manager import parse_endpoint

        host, port = parse_endpoint(guard.endpoints[0])
        link = await self._stack.enter_async_context(open_link(guard.nid, host, port))
        circuit = await Circuit(link).build(hops)
        self.circuits.append(circuit)
        return circuit

    async def close(self) -> None:
        for circuit in self.circuits:
            circuit.destroy()
        self.circuits.clear()
        await self._stack.aclose()
        await self.links.close()


class Session:
    """An end-to-end conversation over a spliced pair of circuits (§6).

    Everything written here is encrypted with the Noise transport keys the two
    ends agreed through the introduction, so the rendezvous point in the middle
    moves bytes it cannot read.
    """

    def __init__(
        self, circuit: Circuit, noise: Initiator | Responder, *, stream_id: int = 1
    ) -> None:
        self.circuit = circuit
        self._noise = noise
        self.stream_id = stream_id
        self.on_data: Callable[[bytes], None] | None = None
        self.received: list[bytes] = []
        self.closed = False

    async def send(self, data: bytes) -> None:
        if self.closed:
            raise CircuitError("session is closed")
        for offset in range(0, max(len(data), 1), MAX_E2E_PAYLOAD):
            chunk = data[offset : offset + MAX_E2E_PAYLOAD]
            await self.circuit.send_data(self._noise.encrypt(chunk), stream_id=self.stream_id)

    def _deliver(self, ciphertext: bytes) -> None:
        try:
            plaintext = self._noise.decrypt(ciphertext)
        except Exception as exc:
            log.debug("end-to-end decryption failed: %s", exc)
            return
        self.received.append(plaintext)
        if self.on_data is not None:
            self.on_data(plaintext)

    def close(self) -> None:
        self.closed = True


class HiddenService:
    """A service reachable only through its introduction points (§6)."""

    def __init__(
        self,
        identity: Identity,
        manager: CircuitManager,
        *,
        static: X25519PrivateKey | None = None,
    ) -> None:
        self.identity = identity
        self.manager = manager
        self.static = static or X25519PrivateKey.generate()
        self.intro_circuits: dict[bytes, Circuit] = {}
        self.intro_points: list[IntroPointRef] = []
        self.sessions: list[Session] = []
        self.on_session: Callable[[Session], None] | None = None
        self.stats = {"established": 0, "introductions": 0, "rendezvous": 0, "failed": 0}

    @property
    def x25519_public(self) -> bytes:
        return x25519_public_bytes(self.static)

    # ----------------------------------------------------------- publishing
    async def establish(self, intro_hops: list[list[Hop]]) -> int:
        """Build a circuit to each intro point and claim a slot on it.

        *intro_hops* is one path per intro point, each ending at the relay that
        is to be the intro point.
        """
        for path in intro_hops:
            with contextlib.suppress(CircuitError, MessageError, TimeoutError):
                await self._establish_one(path)
        return len(self.intro_points)

    async def _establish_one(self, path: list[Hop]) -> None:
        intro = path[-1]
        circuit = await self.manager.build(path)
        circuit.on_data = lambda stream_id, rcmd, data: self._on_relay(circuit, rcmd, data)

        request = EstablishIntro(
            service_pub=self.identity.public_key,
            signature=self.identity.sign(
                EstablishIntro.binding(self.identity.public_key, intro.pub)
            ),
        )
        waiter = circuit._expect(RelayCmd.INTRO_ESTABLISHED)  # noqa: SLF001
        circuit.send_relay(RelayCmd.ESTABLISH_INTRO, request.encode())
        await asyncio.wait_for(waiter, _TIMEOUT)

        self.intro_circuits[intro.pub] = circuit
        self.intro_points.append(
            IntroPointRef(pub=intro.pub, x25519=intro.x25519, endpoints=list(intro.endpoints))
        )
        self.stats["established"] += 1
        log.info("intro point established: %s", intro.short)

    def descriptor_body(self) -> dict:
        """The ``intro_points`` part of a service descriptor (§8)."""
        return {
            "x": self.x25519_public,
            "ip": [point.as_dict() for point in self.intro_points],
        }

    # -------------------------------------------------------- introductions
    def _on_relay(self, circuit: Circuit, rcmd: int, data: bytes) -> None:
        if rcmd == RelayCmd.INTRODUCE2:
            self.stats["introductions"] += 1
            asyncio.ensure_future(self._on_introduction(data))

    async def _on_introduction(self, payload: bytes) -> None:
        """Somebody wants to meet. Build our own circuit to the rendezvous."""
        try:
            request = Introduce.decode(payload)
        except MessageError as exc:
            log.debug("bad introduction: %s", exc)
            self.stats["failed"] += 1
            return
        if request.service_pub != self.identity.public_key:
            return  # not for us

        responder = Responder(self.static)
        try:
            reply, _ = responder.respond(request.handshake)
        except HandshakeError as exc:
            log.debug("introduction handshake failed: %s", exc)
            self.stats["failed"] += 1
            return

        path = self._rendezvous_path(request.rendezvous)
        try:
            circuit = await self.manager.build(path)
        except (CircuitError, ValueError) as exc:
            log.debug("could not reach the rendezvous point: %s", exc)
            self.stats["failed"] += 1
            return

        session = Session(circuit, responder)
        circuit.on_data = lambda stream_id, rcmd, data: (
            session._deliver(data) if rcmd == RelayCmd.DATA else None  # noqa: SLF001
        )
        circuit.send_relay(RelayCmd.RENDEZVOUS1, request.cookie + reply)
        self.sessions.append(session)
        self.stats["rendezvous"] += 1
        if self.on_session is not None:
            self.on_session(session)

    def _rendezvous_path(self, rendezvous: IntroPointRef) -> list[Hop]:
        """Three hops of our own, ending at the rendezvous point.

        The middle hops come from the paths we already use; a real
        implementation draws them from :mod:`nullcell.onion.path_select`.
        """
        existing = [c.hops[0] for c in self.intro_circuits.values()]
        middle = [h for h in existing if h.pub != rendezvous.pub][:2]
        return [*middle, rendezvous.as_hop()]

    async def close(self) -> None:
        for session in self.sessions:
            session.close()
        self.sessions.clear()
        self.intro_circuits.clear()
        self.intro_points.clear()


# ------------------------------------------------------------------- client
async def connect_to_service(
    manager: CircuitManager,
    *,
    service_pub: bytes,
    service_x25519: bytes,
    intro_point: IntroPointRef,
    rendezvous_path: list[Hop],
    intro_path: list[Hop],
) -> Session:
    """Reach a service through a rendezvous (§6).

    Two circuits of our own: one to the rendezvous point where we leave a
    cookie, and one to an introduction point to ask the service to come. We
    never learn where the service is, and it never learns where we are.
    """
    cookie = new_cookie()
    rendezvous = rendezvous_path[-1]

    rendezvous_circuit = await manager.build(rendezvous_path)
    waiter = rendezvous_circuit._expect(RelayCmd.RENDEZVOUS_ESTABLISHED)  # noqa: SLF001
    rendezvous_circuit.send_relay(RelayCmd.ESTABLISH_RENDEZVOUS, cookie)
    await asyncio.wait_for(waiter, _TIMEOUT)

    initiator = Initiator(service_x25519)
    introduction = Introduce(
        service_pub=service_pub,
        rendezvous=IntroPointRef(
            pub=rendezvous.pub, x25519=rendezvous.x25519, endpoints=list(rendezvous.endpoints)
        ),
        cookie=cookie,
        handshake=initiator.create(),
    )

    spliced = rendezvous_circuit._expect(RelayCmd.RENDEZVOUS2)  # noqa: SLF001
    intro_circuit = await manager.build(intro_path)
    ack = intro_circuit._expect(RelayCmd.INTRODUCE_ACK)  # noqa: SLF001
    intro_circuit.send_relay(RelayCmd.INTRODUCE1, introduction.encode())
    await asyncio.wait_for(ack, _TIMEOUT)

    reply = await asyncio.wait_for(spliced, _TIMEOUT)
    initiator.created(reply[:48])

    session = Session(rendezvous_circuit, initiator)
    rendezvous_circuit.on_data = lambda stream_id, rcmd, data: (
        session._deliver(data) if rcmd == RelayCmd.DATA else None  # noqa: SLF001
    )
    log.info("session open with %s over six hops", ids.short(ids.encode_nid(service_pub)))
    assert len(cookie) == COOKIE_LEN
    return session
