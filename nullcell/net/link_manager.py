"""Long-lived outbound links, cached by peer — §4, §7.

The DHT needs links that outlive a single request: a lookup touches the same
peers repeatedly, and §16 says the construction cost must not be paid per
lookup. Every link is verified against the address it claims (§4) before it
enters the cache.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Final

from nullcell.core import ids
from nullcell.net.link import FrameProtocol
from nullcell.net.quic_link import open_link
from nullcell.net.stream import StreamMux

__all__ = ["LinkManager", "parse_endpoint", "peer_address"]

MAX_LINKS: Final[int] = 64


def parse_endpoint(endpoint: str) -> tuple[str, int]:
    """Split ``host:port``, accepting ``[::1]:4433`` for IPv6."""
    endpoint = endpoint.strip()
    if endpoint.startswith("["):
        host, _, rest = endpoint[1:].partition("]")
        port = rest.lstrip(":")
    else:
        host, _, port = endpoint.rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"not a host:port endpoint: {endpoint!r}")
    return host, int(port)


def peer_address(link: FrameProtocol) -> str | None:
    """``host:port`` a link is actually sending to, or None.

    Dual-stack sockets report an IPv4 peer as ``::ffff:1.2.3.4``; that is
    normalised back, or every IPv4 relay would look like one enormous IPv6
    prefix and §7's diversity rule would stop discriminating.
    """
    quic = getattr(link, "_quic", None)
    address = quic.get_peer_address() if quic is not None else None
    if not address:
        return None
    host, port = str(address[0]), int(address[1])
    if host.startswith("::ffff:") and "." in host:
        host = host[len("::ffff:") :]
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


@dataclass(slots=True)
class _Held:
    link: FrameProtocol
    mux: StreamMux
    stack: AsyncExitStack


class LinkManager:
    """Opens, verifies, caches and closes links to peers."""

    def __init__(self, *, max_links: int = MAX_LINKS) -> None:
        self._links: dict[bytes, _Held] = {}
        self._locks: dict[bytes, asyncio.Lock] = {}
        self.max_links = max_links
        #: Where each peer actually answered, as ``host:port``. Recorded only
        #: for connections that succeeded, so it leaks nothing: we had to know
        #: the address to send the packet. §7 refuses to *resolve* a relay we
        #: are merely weighing up; this is the opposite situation, and it is
        #: what lets a relay announced by DNS name still be checked for
        #: diversity (§7).
        self.observed: dict[bytes, str] = {}

    def __len__(self) -> int:
        return len(self._links)

    def get(self, pub: bytes) -> StreamMux | None:
        held = self._links.get(pub)
        return held.mux if held is not None else None

    async def connect(self, pub: bytes, endpoints: list[str], *, timeout: float = 5.0) -> StreamMux:
        """Return a verified link to *pub*, opening one if needed."""
        held = self._links.get(pub)
        if held is not None:
            return held.mux

        lock = self._locks.setdefault(pub, asyncio.Lock())
        async with lock:
            held = self._links.get(pub)  # another waiter may have opened it
            if held is not None:
                return held.mux

            nid = ids.encode_nid(pub)
            last: Exception | None = None
            for endpoint in endpoints:
                try:
                    host, port = parse_endpoint(endpoint)
                except ValueError as exc:
                    last = exc
                    continue
                stack = AsyncExitStack()
                try:
                    link = await asyncio.wait_for(
                        stack.enter_async_context(open_link(nid, host, port)), timeout
                    )
                except Exception as exc:
                    last = exc
                    await stack.aclose()
                    continue

                if len(self._links) >= self.max_links:
                    await self._evict_one()
                mux = StreamMux(link)
                self._links[pub] = _Held(link=link, mux=mux, stack=stack)
                address = peer_address(link)
                if address is not None:
                    self.observed[pub] = address
                return mux

            raise ConnectionError(f"could not reach {ids.short(nid)}: {last}")

    async def _evict_one(self) -> None:
        pub = next(iter(self._links))
        await self.disconnect(pub)

    async def disconnect(self, pub: bytes) -> None:
        held = self._links.pop(pub, None)
        self._locks.pop(pub, None)
        if held is None:
            return
        held.mux.close()
        await held.stack.aclose()

    async def close(self) -> None:
        for pub in list(self._links):
            await self.disconnect(pub)
