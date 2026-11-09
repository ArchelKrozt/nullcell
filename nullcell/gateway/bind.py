"""Where the local proxy may listen — §8.

§8 puts SOCKS on `127.0.0.1` and says anything reaching loopback is already
inside the trust boundary. That holds on one machine. It stops holding the
moment someone runs the proxy on a VPS and wants a phone to use it, which is a
reasonable thing to want and the reason this module exists.

The proxy is **the one place in Nullcell that handles plaintext**: it
terminates the local TLS and hands the bytes to a circuit. So whoever can
reach it does not merely use the network — they read everything that goes
through it, and they get a .null exit that answers in this node's name. That
makes the bind address a security decision, not a convenience flag:

* **Loopback** — the §8 case, one machine.
* **Any address that is not globally routable** — the VPN case. A WireGuard
  interface lives here, so a phone on the tunnel reaches the proxy and nobody
  else does. The tunnel, not the proxy, does the authenticating.
* **Anything public, and the wildcards `0.0.0.0` / `::`** — refused. Not
  warned about: refused. A wildcard bind on a VPS is an open proxy on the
  public internet within minutes, and the traffic from the phone would cross
  the internet unencrypted on its way in, which undoes the entire point of
  what it then enters.

The wildcard is refused even though a relay binds `::` quite happily. A relay
forwards frames it cannot read; this reads them.
"""

from __future__ import annotations

import ipaddress

#: Suggested when the refusal is a wildcard, which is the common mistake.
_HINT = (
    "Use 127.0.0.1 for this machine, or the address of a VPN interface "
    "(e.g. 10.8.0.1 on WireGuard) to let your own devices in. "
    "See docs/VPS.md."
)


class BindRefused(ValueError):
    """The requested address would expose the plaintext side of the gateway."""


def check_bind(host: str) -> str:
    """Return *host* if the proxy may listen there, or raise `BindRefused`.

    Names are refused rather than resolved: a name can point anywhere, and it
    can point somewhere else tomorrow, so accepting one would mean this check
    passes on a reading of the DNS that no longer applies when it matters.
    """
    text = host.strip().strip("[]")
    if not text:
        raise BindRefused(f"empty bind address. {_HINT}")

    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        raise BindRefused(
            f"{host!r} is not an IP address. The gateway handles plaintext, so it binds "
            f"an address you can point at, never a name that could resolve elsewhere. {_HINT}"
        ) from None

    if address.is_unspecified:
        raise BindRefused(
            f"{host} is every interface, which on a server means an open proxy on the "
            f"public internet — and anyone reaching it reads the plaintext of everything "
            f"it carries and exits in your node's name. {_HINT}"
        )
    # `is_global` and not a hand-written list of private ranges. The list I
    # wrote first said RFC 1918 + link-local + ULA and used `is_private`, and
    # measuring it showed the idea was wrong twice over: `100.64.0.1` — the
    # CGNAT range Tailscale hands out, exactly the case this flag is for — is
    # not `is_private`, while `203.0.113.9` and `2001:db8::1` are. One
    # predicate for "reachable from the internet" gets all four right.
    if not address.is_global:
        return text
    raise BindRefused(
        f"{host} is a globally routable address. Traffic to it would cross the "
        f"internet unencrypted to reach the one component that sees plaintext. "
        f"Put a VPN in front and bind the "
        f"tunnel's private address instead. {_HINT}"
    )


def is_loopback(host: str) -> bool:
    """Whether *host* keeps the gateway inside §8's original trust boundary."""
    try:
        return ipaddress.ip_address(host.strip().strip("[]")).is_loopback
    except ValueError:
        return False
