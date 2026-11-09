"""Rendezvous points, and the splice — §6.

The rendezvous point is where the two halves meet. A client builds a three-hop
circuit to it and leaves a cookie; the service, told about the cookie through an
intro point, builds its *own* three-hop circuit to the same relay and presents
it. The relay matches the two and from then on hands bytes from one leg to the
other.

That is the whole trick, and it is what gets both ends anonymity at once:

* **Six hops.** Three from the client, three from the service.
* Neither end learns the other's address. Each one only ever talked to the
  rendezvous point.
* The rendezvous point forwards bytes it **cannot read**: the client and the
  service complete a Noise NK handshake through the introduction, so everything
  after the splice is encrypted end to end, underneath the onion layers.
* And the service never needed a public address, an open port or UPnP. Every
  connection it makes is outbound (§0, the unexpected gift of decision 1).
"""

from __future__ import annotations

import logging
from typing import Final

from nullcell.core.frames import RelayCmd, RelayHeader
from nullcell.onion.relay_logic import CircuitLeg, RelayService
from nullcell.onion.rendezvous_msgs import COOKIE_LEN

__all__ = ["MAX_PENDING", "RendezvousPointService"]

#: Cookies waiting for a service to show up. Bounded, or anyone could make a
#: relay hold state for free by establishing rendezvous points it never uses.
MAX_PENDING: Final[int] = 1024

log = logging.getLogger("nullcell.rendezvous")


class RendezvousPointService:
    """The rendezvous point role, layered on top of a relay."""

    def __init__(self, relay: RelayService, *, max_pending: int = MAX_PENDING) -> None:
        self.relay = relay
        self.max_pending = max_pending
        #: cookie -> the client's circuit, until the service arrives
        self.waiting: dict[bytes, CircuitLeg] = {}
        self.stats = {"established": 0, "spliced": 0, "relayed": 0, "refused": 0}

    def handle(self, leg: CircuitLeg, header: RelayHeader) -> bool:
        """Handle a §6 message, or relay across an existing splice."""
        if header.rcmd == RelayCmd.ESTABLISH_RENDEZVOUS:
            self._establish(leg, header.payload)
            return True
        if header.rcmd == RelayCmd.RENDEZVOUS1:
            self._splice(leg, header.payload)
            return True
        if leg.spliced_to is not None and header.rcmd in (RelayCmd.DATA, RelayCmd.END):
            self._across(leg, header)
            return True
        return False

    # ------------------------------------------------------------- establish
    def _establish(self, leg: CircuitLeg, payload: bytes) -> None:
        cookie = payload[:COOKIE_LEN]
        if len(cookie) != COOKIE_LEN:
            self.stats["refused"] += 1
            self.relay.send_back(leg, RelayCmd.TRUNCATED, b"bad cookie")
            return
        if len(self.waiting) >= self.max_pending:
            self.stats["refused"] += 1
            self.relay.send_back(leg, RelayCmd.TRUNCATED, b"rendezvous point is full")
            return
        if cookie in self.waiting:
            # A cookie is 20 random bytes; a collision is somebody reusing one.
            self.stats["refused"] += 1
            self.relay.send_back(leg, RelayCmd.TRUNCATED, b"cookie in use")
            return

        self.waiting[cookie] = leg
        self.stats["established"] += 1
        self.relay.send_back(leg, RelayCmd.RENDEZVOUS_ESTABLISHED)

    # ----------------------------------------------------------------- splice
    def _splice(self, service_leg: CircuitLeg, payload: bytes) -> None:
        cookie, handshake = payload[:COOKIE_LEN], payload[COOKIE_LEN:]
        client_leg = self.waiting.pop(cookie, None)
        if client_leg is None:
            self.stats["refused"] += 1
            self.relay.send_back(service_leg, RelayCmd.TRUNCATED, b"no such cookie")
            return

        client_leg.spliced_to = service_leg
        service_leg.spliced_to = client_leg
        self.stats["spliced"] += 1
        log.info("spliced two circuits: six hops end to end")

        # The service's handshake reply goes to the client, which completes the
        # end-to-end encryption we are not party to.
        self.relay.send_back(client_leg, RelayCmd.RENDEZVOUS2, handshake)

    def _across(self, leg: CircuitLeg, header: RelayHeader) -> None:
        other = leg.spliced_to
        if other is None:  # pragma: no cover - guarded by the caller
            return
        self.stats["relayed"] += 1
        self.relay.send_back(other, header.rcmd, header.payload, stream_id=header.stream_id)

    # ---------------------------------------------------------------- cleanup
    def forget(self, leg: CircuitLeg) -> None:
        for cookie, held in list(self.waiting.items()):
            if held is leg:
                del self.waiting[cookie]
        other = leg.spliced_to
        if other is not None:
            other.spliced_to = None
            leg.spliced_to = None
            self.relay.send_back(other, RelayCmd.END, b"the other side went away")


def install(relay: RelayService, identity, *, dht=None) -> tuple:
    """Give *relay* the roles it can play for other people's circuits.

    Intro point and rendezvous point are both relay roles (§6), so one relay
    can be either or both, and a relay that also runs a DHT node can answer
    lookups arriving over a circuit (§7, decision 8). All three are chained
    onto the single ``on_relay_message`` seam, splice traffic first because it
    is the hot path.
    """
    from nullcell.onion.intro_point import IntroPointService

    intro = IntroPointService(relay, identity)
    rendezvous = RendezvousPointService(relay)
    dht_handler = dht.circuit_handler(relay) if dht is not None else None

    def dispatch(leg: CircuitLeg, header: RelayHeader) -> None:
        if rendezvous.handle(leg, header):
            return
        if intro.handle(leg, header):
            return
        if dht_handler is not None and dht_handler(leg, header):
            return
        log.debug("unhandled relay cmd %s", header.rcmd)

    relay.on_relay_message = dispatch
    return intro, rendezvous
