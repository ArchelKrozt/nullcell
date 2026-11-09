"""The `null://` URL scheme — §8's isolation, without a certificate authority.

§8 reaches `.null` sites through a SOCKS proxy that terminates a local TLS
connection with a certificate minted by our own CA, because an ordinary
browser refuses to show `https://` without one. That CA is the most
uncomfortable thing in the whole design: it has to be trusted *somewhere*, and
on a phone that somewhere is the system store.

When we are the browser, none of that is necessary. A registered scheme hands
bytes straight from the circuit to the renderer: no TLS inside the circuit, no
certificate, no CA, nothing to install and nothing to trust. The content is
already end-to-end encrypted by the onion layer; the inner TLS existed only to
satisfy an address bar we did not control.

------------------------------------------------------------------- the flags
What a custom scheme is *allowed* to do decides whether §8's isolation holds,
and this was measured rather than assumed, because the wrong answer is silent:

    SecureScheme | CorsEnabled | FetchApiAllowed   site A read site B  ← fuga
    SecureScheme                                   blocked ✓
    SecureScheme | FetchApiAllowed                 blocked ✓

`CorsEnabled` does not mean "enforce CORS here". It means *"this scheme may be
fetched across origins"*, which is the opposite of what a network of mutually
distrusting portals needs: with it set, every `.null` site could read every
other `.null` site the operator had open. That is the same-origin collapse
that makes a web gateway unacceptable (§8), arriving through a flag name that
reads like a security feature.

`Syntax.Host` is what gives each address its own origin — measured as
`null://<56 chars>`, a secure context, with `localStorage` partitioned per
address.
"""

from __future__ import annotations

from typing import Final

#: The scheme, without `://`.
SCHEME: Final[bytes] = b"null"

__all__ = ["SCHEME", "register"]

_registered = False


def register() -> None:
    """Declare `null://` to Chromium. Must run before the app is created."""
    global _registered
    if _registered:
        return
    from PySide6.QtWebEngineCore import QWebEngineUrlScheme

    scheme = QWebEngineUrlScheme(SCHEME)
    # Host syntax: every address becomes its own origin, so the browser's own
    # same-origin policy does the isolating (§8).
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setDefaultPort(0)
    # SecureScheme so pages are a secure context (crypto.subtle, workers) and
    # are not treated as mixed content. FetchApiAllowed so a page can fetch
    # its *own* resources. Deliberately **no CorsEnabled**: see above.
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme | QWebEngineUrlScheme.Flag.FetchApiAllowed
    )
    QWebEngineUrlScheme.registerScheme(scheme)
    _registered = True
