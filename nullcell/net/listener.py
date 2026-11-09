"""Reachable node: accept QUIC links and serve frames — §4.

A listening node proves who it is with its certificate (§4, one-sided
authentication): it presents an identity, the client presents nothing. That
asymmetry is what keeps a client anonymous at the link layer already.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from aioquic.asyncio import serve
from aioquic.quic.configuration import QuicConfiguration

from nullcell.core.identity import Identity
from nullcell.net.cert import make_cert
from nullcell.net.link import PROTOCOL_VERSIONS, FrameProtocol

__all__ = ["ALPN", "Listener", "server_configuration"]

ALPN: Final[list[str]] = ["nullcell/1"]


def server_configuration(identity: Identity) -> QuicConfiguration:
    """QUIC configuration for a reachable node."""
    cert, key = make_cert(identity)
    config = QuicConfiguration(
        is_client=False,
        alpn_protocols=ALPN,
        # A client never presents a certificate; asking for one would be the
        # single worst thing we could do to its anonymity (§4).
        idle_timeout=60.0,
    )
    config.certificate = cert
    config.private_key = key
    return config


class Listener:
    """A listening Nullcell node."""

    def __init__(
        self,
        identity: Identity,
        host: str = "::",
        port: int = 4433,
        *,
        on_link: Callable[[FrameProtocol], None] | None = None,
    ) -> None:
        self.identity = identity
        self.host = host
        self.port = port
        self._server = None
        self.links: list[FrameProtocol] = []
        #: Called for every accepted link, so a service (the DHT in M2) can
        #: attach its own message handling without this class knowing about it.
        self.on_link = on_link

    def _factory(self, *args, **kwargs) -> FrameProtocol:
        proto = FrameProtocol(*args, **kwargs)
        self.links.append(proto)
        if self.on_link is not None:
            self.on_link(proto)
        # No proactive greeting: the side that *initiated* the link opens the
        # stream and sends VERSIONS first, and we answer on that same stream
        # (§5). Greeting first would have both ends open a stream of their own.
        return proto

    async def start(self) -> int:
        """Bind and start accepting. Returns the port actually bound.

        ``port=0`` asks the kernel for a free one and :attr:`port` is updated
        to what it gave. That closes a race the test harness used to hit:
        picking a port by binding a throwaway socket, closing it and then
        binding for real leaves a window where somebody else takes it, and the
        failure shows up much later as a connection that times out.
        """
        self._server = await serve(
            self.host,
            self.port,
            configuration=server_configuration(self.identity),
            create_protocol=self._factory,
        )
        transport = getattr(self._server, "_transport", None)
        socket_object = transport.get_extra_info("socket") if transport is not None else None
        if socket_object is not None:
            self.port = socket_object.getsockname()[1]
        return self.port

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None

    @property
    def versions(self) -> tuple[int, ...]:
        return PROTOCOL_VERSIONS
