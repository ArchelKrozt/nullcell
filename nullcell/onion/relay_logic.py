"""Relay side of a circuit — §5, §10.

A relay holds, per circuit it carries, exactly one hop's crypto state and at
most two neighbours. That is the whole of its knowledge: it can peel its own
layer and nothing more, so it cannot read what it forwards, and refusing on
content is not a capability it has (§10 — a relay can only decline on volume).

Three jobs:

* **CREATE** — complete a Noise NK handshake as the responder and answer
  ``CREATED``.
* **EXTEND** — open a link onward, relay the client's handshake for the next
  hop, and return the answer as ``EXTENDED``. The relay cannot read that
  handshake; it carries it.
* **Forward** — peel a layer going outward, add one coming back, and pass the
  frame along unchanged otherwise.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from nullcell.core.frames import (
    DIGEST_OFFSET,
    DIGEST_SIZE,
    Cmd,
    RelayCmd,
    RelayHeader,
    new_circ_id,
    pack_relay_payload,
    parse_relay_payload,
)
from nullcell.net.link import FrameProtocol
from nullcell.net.link_manager import LinkManager
from nullcell.onion.circuit import CircuitError, unpack_extend
from nullcell.onion.handshake_nk import HANDSHAKE_LEN, HandshakeError, Responder
from nullcell.onion.layers import HopCrypto

__all__ = ["MAX_CIRCUITS", "CircuitLeg", "RelayService"]

#: Per-link cap. A relay operator limits volume, not content (§10).
MAX_CIRCUITS: Final[int] = 512
_EXTEND_TIMEOUT: Final[float] = 15.0

log = logging.getLogger("nullcell.relay")


@dataclass(slots=True)
class CircuitLeg:
    """One circuit as this relay sees it."""

    crypto: HopCrypto
    prev_link: FrameProtocol
    prev_circ_id: int
    next_link: FrameProtocol | None = None
    next_circ_id: int | None = None
    frames_forward: int = 0
    frames_backward: int = 0
    pending_extend: asyncio.Future[bytes] | None = field(default=None, repr=False)

    @property
    def extended(self) -> bool:
        return self.next_link is not None


class RelayService:
    """Carries circuits for other people."""

    def __init__(
        self,
        static_x25519: X25519PrivateKey,
        *,
        links: LinkManager | None = None,
        max_circuits: int = MAX_CIRCUITS,
        extend_timeout: float = _EXTEND_TIMEOUT,
    ) -> None:
        self.static = static_x25519
        self.links = links if links is not None else LinkManager()
        self.max_circuits = max_circuits
        self.extend_timeout = extend_timeout
        #: Where relay commands addressed to this hop and not handled here are
        #: delivered — ``(leg, header) -> None``. This is the seam the exit and
        #: stream logic of M5 plugs into; today nothing consumes ``DATA``.
        self.on_relay_message: Callable[[CircuitLeg, RelayHeader], None] | None = None
        self.legs: dict[tuple[int, int], CircuitLeg] = {}
        self.draining = False
        self.stats = {"created": 0, "extended": 0, "forwarded": 0, "refused": 0}

    # ------------------------------------------------------------------ wiring
    def attach(self, link: FrameProtocol) -> None:
        """Handle link-level circuit frames arriving on *link*.

        Registered as the link's fallback handler, so frames for circuits this
        relay does not yet know about (a fresh ``CREATE``) reach us.
        """
        link.on_frame = lambda circ_id, cmd, payload: self._on_frame(link, circ_id, cmd, payload)

    def _key(self, link: FrameProtocol, circ_id: int) -> tuple[int, int]:
        return (id(link), circ_id)

    # --------------------------------------------------------------- dispatch
    def _on_frame(self, link: FrameProtocol, circ_id: int, cmd: int, payload: memoryview) -> None:
        if circ_id == 0:
            return  # link-level traffic, not ours
        key = self._key(link, circ_id)
        leg = self.legs.get(key)

        if cmd == Cmd.CREATE:
            if leg is None:
                # A frame is zero-padded to 512 B (§5); the handshake is the
                # first HANDSHAKE_LEN bytes of the payload, not all of it.
                self._on_create(link, circ_id, bytes(payload[:HANDSHAKE_LEN]))
            return
        if leg is None:
            return  # unknown circuit; silently dropped, as Tor does
        if cmd == Cmd.DESTROY:
            self._destroy(key, "DESTROY received")
            return
        if cmd == Cmd.RELAY:
            self._on_relay(key, leg, link, bytes(payload))

    # ----------------------------------------------------------------- CREATE
    def _on_create(self, link: FrameProtocol, circ_id: int, handshake: bytes) -> None:
        if self.draining or len(self.legs) >= self.max_circuits:
            self.stats["refused"] += 1
            with contextlib.suppress(Exception):
                link.send_frame(circ_id, Cmd.DESTROY, b"")
            return
        try:
            created, keys = Responder(self.static).respond(handshake)
        except HandshakeError as exc:
            log.debug("CREATE refused: %s", exc)
            self.stats["refused"] += 1
            with contextlib.suppress(Exception):
                link.send_frame(circ_id, Cmd.DESTROY, b"")
            return

        self.legs[self._key(link, circ_id)] = CircuitLeg(
            crypto=HopCrypto(keys), prev_link=link, prev_circ_id=circ_id
        )
        self.stats["created"] += 1
        link.send_frame(circ_id, Cmd.CREATED, created)

    # ------------------------------------------------------------------ RELAY
    def _on_relay(
        self, key: tuple[int, int], leg: CircuitLeg, link: FrameProtocol, payload: bytes
    ) -> None:
        inbound_from_next = leg.extended and link is leg.next_link

        if inbound_from_next:
            # Coming back towards the client: add our layer and pass it on. We
            # do not and cannot inspect it.
            leg.frames_backward += 1
            self.stats["forwarded"] += 1
            leg.prev_link.send_frame(
                leg.prev_circ_id, Cmd.RELAY, leg.crypto.crypt_backward(payload)
            )
            return

        peeled = leg.crypto.crypt_forward(payload)
        if not leg.crypto.check_forward(peeled):
            # Not addressed to us: forward it outward, already one layer lighter.
            if leg.extended:
                leg.frames_forward += 1
                self.stats["forwarded"] += 1
                leg.next_link.send_frame(leg.next_circ_id, Cmd.RELAY, peeled)
            else:
                self._destroy(key, "frame for a hop that does not exist yet")
            return

        header = parse_relay_payload(peeled)
        if header.rcmd == RelayCmd.EXTEND:
            asyncio.ensure_future(self._on_extend(key, leg, header.payload))
            return
        if header.rcmd == RelayCmd.EXTENDED:
            return  # only a client consumes these
        if self.on_relay_message is not None:
            self.on_relay_message(leg, header)
            return
        log.debug("circuit %08x: unhandled relay cmd %s", leg.prev_circ_id, header.rcmd)

    # ----------------------------------------------------------------- EXTEND
    async def _on_extend(self, key: tuple[int, int], leg: CircuitLeg, data: bytes) -> None:
        """Open the next link and relay a handshake we cannot read."""
        if leg.extended:
            self._destroy(key, "already extended")
            return
        try:
            hop, handshake = unpack_extend(data)
        except CircuitError as exc:
            self._destroy(key, f"malformed EXTEND: {exc}")
            return

        try:
            mux = await self.links.connect(hop.pub, hop.endpoints, timeout=self.extend_timeout)
        except Exception as exc:
            log.debug("extend to %s failed: %s", hop.short, exc)
            self._send_back(leg, RelayCmd.TRUNCATED, b"")
            return

        next_link = mux.link
        next_circ_id = new_circ_id(link_initiator=next_link.is_client)
        waiter: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

        def on_next_frame(circ_id: int, cmd: int, payload: memoryview) -> None:
            if cmd == Cmd.CREATED and not waiter.done():
                waiter.set_result(bytes(payload[:HANDSHAKE_LEN]))
            elif cmd == Cmd.RELAY:
                self._on_relay(key, leg, next_link, bytes(payload))
            elif cmd == Cmd.DESTROY:
                self._destroy(key, "next hop sent DESTROY")

        next_link.route(next_circ_id, on_next_frame)
        next_link.send_frame(next_circ_id, Cmd.CREATE, handshake)

        try:
            created = await asyncio.wait_for(waiter, self.extend_timeout)
        except TimeoutError:
            next_link.unroute(next_circ_id)
            self._send_back(leg, RelayCmd.TRUNCATED, b"")
            return

        leg.next_link = next_link
        leg.next_circ_id = next_circ_id
        self.stats["extended"] += 1
        self._send_back(leg, RelayCmd.EXTENDED, created)

    def _send_back(self, leg: CircuitLeg, rcmd: RelayCmd, data: bytes) -> None:
        """Send one of *our own* messages back to the client."""
        # Tag written in place on one mutable copy, as in OnionPath.wrap: a
        # set_digest() call would allocate a second payload per frame (§16).
        buf = bytearray(pack_relay_payload(rcmd, data))
        buf[DIGEST_OFFSET : DIGEST_OFFSET + DIGEST_SIZE] = leg.crypto.tag_backward(buf)
        leg.prev_link.send_frame(leg.prev_circ_id, Cmd.RELAY, leg.crypto.crypt_backward(bytes(buf)))

    # ---------------------------------------------------------------- teardown
    def _destroy(self, key: tuple[int, int], reason: str) -> None:
        leg = self.legs.pop(key, None)
        if leg is None:
            return
        log.debug("circuit %08x torn down: %s", leg.prev_circ_id, reason)
        for link, circ_id in (
            (leg.prev_link, leg.prev_circ_id),
            (leg.next_link, leg.next_circ_id),
        ):
            if link is None or circ_id is None:
                continue
            with contextlib.suppress(Exception):
                link.send_frame(circ_id, Cmd.DESTROY, b"")
                link.unroute(circ_id)

    def drain(self) -> None:
        """Stop accepting new circuits, keep the live ones (§10)."""
        self.draining = True

    async def close(self) -> None:
        for key in list(self.legs):
            self._destroy(key, "relay shutting down")
        await self.links.close()
