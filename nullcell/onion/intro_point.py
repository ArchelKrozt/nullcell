"""Introduction points — §6.

A service keeps a circuit open to each of three intro points and publishes them
in its descriptor. A client that wants to reach the service does not contact it;
it asks an intro point to pass a message down that standing circuit.

What an intro point learns, and what it does not: it knows a service's
long-term public key, because the service proves ownership of it to stop anyone
from squatting the slot. It does **not** learn where the service is — the
circuit to it is three hops — nor what any introduction says, because the part
meant for the service is a Noise handshake message the intro point only carries.
"""

from __future__ import annotations

import logging
from typing import Final

from nullcell.core import ids
from nullcell.core.frames import RelayCmd, RelayHeader
from nullcell.core.identity import Identity
from nullcell.onion.relay_logic import CircuitLeg, RelayService
from nullcell.onion.rendezvous_msgs import EstablishIntro, Introduce, MessageError

__all__ = ["MAX_SERVICES", "IntroPointService"]

#: Per-relay cap. Holding a circuit open for a service is a standing cost, so
#: an operator limits how many it will carry (§10 — volume, not content).
MAX_SERVICES: Final[int] = 256

log = logging.getLogger("nullcell.intro")


class IntroPointService:
    """The intro point role, layered on top of a relay."""

    def __init__(
        self, relay: RelayService, identity: Identity, *, max_services: int = MAX_SERVICES
    ) -> None:
        self.relay = relay
        self.identity = identity
        self.max_services = max_services
        #: service public key -> the service's standing circuit
        self.services: dict[bytes, CircuitLeg] = {}
        self.stats = {"established": 0, "introduced": 0, "refused": 0}

    def handle(self, leg: CircuitLeg, header: RelayHeader) -> bool:
        """Handle a §6 message. Returns True if it was ours."""
        if header.rcmd == RelayCmd.ESTABLISH_INTRO:
            self._establish(leg, header.payload)
            return True
        if header.rcmd == RelayCmd.INTRODUCE1:
            self._introduce(leg, header.payload)
            return True
        return False

    # ------------------------------------------------------------- establish
    def _establish(self, leg: CircuitLeg, payload: bytes) -> None:
        if len(self.services) >= self.max_services:
            self.stats["refused"] += 1
            self.relay.send_back(leg, RelayCmd.TRUNCATED, b"intro point is full")
            return
        try:
            request = EstablishIntro.decode(payload)
        except MessageError as exc:
            log.debug("bad ESTABLISH_INTRO: %s", exc)
            self.stats["refused"] += 1
            self.relay.send_back(leg, RelayCmd.TRUNCATED, b"malformed")
            return

        binding = EstablishIntro.binding(request.service_pub, self.identity.public_key)
        if not Identity.verify(request.service_pub, binding, request.signature):
            # Without this check anyone could claim any service's slot and
            # silently swallow every introduction meant for it.
            log.debug("ESTABLISH_INTRO signature did not verify")
            self.stats["refused"] += 1
            self.relay.send_back(leg, RelayCmd.TRUNCATED, b"bad signature")
            return

        self.services[request.service_pub] = leg
        self.stats["established"] += 1
        log.info("intro point for %s", ids.short(ids.encode_nid(request.service_pub)))
        self.relay.send_back(leg, RelayCmd.INTRO_ESTABLISHED)

    # ------------------------------------------------------------- introduce
    def _introduce(self, leg: CircuitLeg, payload: bytes) -> None:
        try:
            request = Introduce.decode(payload)
        except MessageError as exc:
            log.debug("bad INTRODUCE1: %s", exc)
            self.relay.send_back(leg, RelayCmd.TRUNCATED, b"malformed")
            return

        service_leg = self.services.get(request.service_pub)
        if service_leg is None:
            self.relay.send_back(leg, RelayCmd.TRUNCATED, b"unknown service")
            return

        # Forwarded byte for byte. We are a postbox, not a party to this.
        self.relay.send_back(service_leg, RelayCmd.INTRODUCE2, payload)
        self.stats["introduced"] += 1
        self.relay.send_back(leg, RelayCmd.INTRODUCE_ACK)

    # ---------------------------------------------------------------- cleanup
    def forget(self, leg: CircuitLeg) -> None:
        for pub, held in list(self.services.items()):
            if held is leg:
                del self.services[pub]
