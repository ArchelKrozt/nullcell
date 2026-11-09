"""HTTP proxy on 127.0.0.1:9151 — §8, for clients without SOCKS.

§8 lists it beside the SOCKS proxy for exactly one reason: some clients speak
HTTP proxying and not SOCKS5. It is **not** the preferred path, and the
difference matters:

* ``CONNECT`` is handled the same way as SOCKS5 — a tunnel to the service,
  with TLS terminated here so the browser treats the origin as secure (§8).
* A **plain** (non-``CONNECT``) request is **refused**. Absolute-form HTTP
  through a proxy means the client resolved nothing and expects us to fetch a
  URL, and answering it would make this an open proxy onto whatever the client
  typed. §8's rule is one network, not a bridge to the other one.

The same ``Host`` and token rules do not apply here, because a proxy is not an
API: what protects it is that it only ever reaches ``.null`` addresses, and
refuses everything else before any traffic happens.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Final

from nullcell.core import ids
from nullcell.gateway.socks5 import SessionFactory, Socks5Proxy

__all__ = ["DEFAULT_PORT", "HttpProxy"]

DEFAULT_PORT: Final[int] = 9151
_MAX_REQUEST_LINE: Final[int] = 8 * 1024

log = logging.getLogger("nullcell.httpproxy")


class HttpProxy:
    """``CONNECT``-only HTTP proxy, sharing the SOCKS proxy's machinery."""

    def __init__(
        self,
        open_session: SessionFactory,
        ca,
        *,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
    ) -> None:
        # The session cache, TLS termination and piping are all the same, so
        # this borrows them rather than growing a second copy that can drift.
        self._socks = Socks5Proxy(open_session, ca, host=host, port=port)
        self.host = host
        self.port = port
        self._server: asyncio.AbstractServer | None = None
        self.stats = {"accepted": 0, "opened": 0, "refused": 0}

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        log.info("HTTP proxy on %s:%d (CONNECT only)", self.host, self.port)
        return self.port

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        await self._socks.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.stats["accepted"] += 1
        try:
            request = await asyncio.wait_for(reader.readline(), timeout=30.0)
        except (TimeoutError, ConnectionError):
            writer.close()
            return
        if not request or len(request) > _MAX_REQUEST_LINE:
            await self._refuse(writer, 400, "petición malformada")
            return

        try:
            method, target, _ = request.decode("ascii", errors="replace").split(maxsplit=2)
        except ValueError:
            await self._refuse(writer, 400, "petición malformada")
            return

        # Drain the headers; we need none of them.
        try:
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10.0)
                if line in (b"\r\n", b"\n", b""):
                    break
        except (TimeoutError, ConnectionError):
            writer.close()
            return

        if method.upper() != "CONNECT":
            # §8: answering this would make us an open proxy onto the ordinary
            # internet, which is the opposite of what this gateway is for.
            await self._refuse(
                writer,
                501,
                "sólo CONNECT. Un proxy HTTP en modo absoluto nos convertiría "
                "en un proxy abierto hacia el internet normal (§8); usa SOCKS5 "
                "en :9150 con resolución remota",
            )
            return

        host, _, port_text = target.rpartition(":")
        host = host.strip("[]") or target
        try:
            port = int(port_text) if port_text.isdigit() else 443
            bare = ids.parse_hostname(host)
            ids.decode_nid(bare)
        except (ValueError, ids.AddressError) as exc:
            await self._refuse(writer, 502, f"no es una dirección .null: {exc}")
            return

        try:
            session = await self._socks._session_for(bare)  # noqa: SLF001
            stream = await session.open()
        except Exception as exc:
            log.info("could not reach %s: %s", ids.short(bare), exc)
            await self._refuse(writer, 502, "no se pudo alcanzar el portal")
            return

        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        self.stats["opened"] += 1

        if port == 443:
            try:
                await writer.start_tls(self._socks.ca.ssl_context(host))
            except Exception as exc:
                log.debug("TLS with the client failed: %s", exc)
                writer.close()
                await stream.close()
                return

        await self._socks._pipe(reader, writer, stream)  # noqa: SLF001

    async def _refuse(self, writer: asyncio.StreamWriter, status: int, reason: str) -> None:
        self.stats["refused"] += 1
        body = reason.encode("utf-8")
        try:
            writer.write(
                f"HTTP/1.1 {status} Nullcell\r\n"
                f"Content-Type: text/plain; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        except (ConnectionError, RuntimeError):
            pass
        writer.close()
