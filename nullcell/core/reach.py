"""Where this cell is actually reachable — §7, §10.

A relay is only a relay if somebody else can open a connection to it, and the
difference between the three answers is not a detail:

* **loopback** — nobody but this machine. Publishing a relay descriptor that
  points here is worse than not publishing one: it is an unreachable entry in
  everyone's directory, and a seed line nobody can use.
* **lan** — the machines in this house or office. Enough to build a real
  network with friends in the same place, and genuinely useful; useless to the
  internet.
* **internet** — a globally routable address. The only one that makes this
  cell a relay for strangers, and it usually needs a port forwarded.

The detection deliberately makes **no network request**. Asking a "what is my
IP" service would tell that service there is a Nullcell node here, which is
precisely the thing §9 refuses to do for a web font. The UDP trick below sends
nothing: connecting a datagram socket only makes the kernel pick the source
address it *would* use, which is the answer we want and is purely local.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Final

log = logging.getLogger(__name__)

__all__ = ["Reach", "classify", "describe", "lan_address", "best_endpoint"]

#: A documentation address that is never routed. Nothing is sent to it.
_PROBE: Final[tuple[str, int]] = ("192.0.2.1", 9)

Reach = str  # "loopback" | "lan" | "internet" | "unknown"


def _host_of(endpoint: str) -> str:
    """The host part of ``host:port``, ``[v6]:port``, or a bare address.

    A bare IPv6 address is all colons, so splitting on the last one turns
    `2606:4700::1111` into `2606:4700:` and the address stops being an address.
    """
    text = endpoint.strip()
    if text.startswith("["):
        return text[1:].partition("]")[0]
    if text.count(":") > 1:  # bare IPv6, no port
        return text
    return text.rsplit(":", 1)[0] if ":" in text else text


def classify(endpoint: str) -> Reach:
    """Who could reach *endpoint*: this machine, the LAN, or the internet."""
    host = _host_of(endpoint)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A name. It resolves to something, and we do not resolve it here —
        # §7 does not resolve names it is merely weighing up. Announcing a
        # name is the normal way to run a relay, so it counts as routable.
        return "internet" if "." in host else "unknown"
    if address.is_loopback:
        return "loopback"
    if address.is_global:
        return "internet"
    return "lan"


def describe(reach: Reach) -> str:
    """One sentence an operator can act on."""
    return {
        "loopback": (
            "Sólo tu propia máquina puede alcanzar esta celda, así que todavía no puede "
            "ser relevo de nadie ni darle a nadie una semilla que funcione."
        ),
        "lan": (
            "Alcanzable desde tu red local: sirve para montar una red con los equipos de "
            "tu casa u oficina, pero no desde internet."
        ),
        "internet": (
            "Alcanzable desde internet. Hace falta que el puerto UDP esté abierto en el "
            "router para que sea cierto desde fuera."
        ),
        "unknown": "No se puede saber por dónde es alcanzable esta celda.",
    }[reach]


def lan_address() -> str | None:
    """This machine's address on its own network, or None.

    Sends nothing: connecting a UDP socket just asks the kernel which source
    address it would use for that destination.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.2)
            probe.connect(_PROBE)
            host = probe.getsockname()[0]
    except OSError as exc:
        log.debug("no se pudo averiguar la dirección de red local: %s", exc)
        return None
    return None if classify(host) == "loopback" else host


def best_endpoint(port: int, *, announce: str | None = None) -> tuple[str, Reach]:
    """The endpoint to announce, and how far it reaches.

    Prefers what the operator said. Otherwise the LAN address, because a cell
    that can at least be reached by the machine next to it is a cell somebody
    can actually join — and falls back to loopback, which is honest about
    reaching nobody rather than inventing an address.
    """
    if announce:
        return announce, classify(announce)
    host = lan_address()
    if host:
        return f"{host}:{port}", "lan"
    return f"127.0.0.1:{port}", "loopback"
