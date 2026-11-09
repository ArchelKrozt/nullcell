"""SOCKS5 on 127.0.0.1:9150 — how a browser reaches the network (§8).

§8 decided against writing a browser: *"ni el navegador normal del usuario
(mezcla su identidad real), ni un navegador escrito por nosotros (más trabajo
que la red entera y toda su superficie de ataque a nuestro cargo)."* So an
existing browser, pointed at this proxy, with a dedicated profile.

Only one SOCKS5 address type is accepted: **a domain name**. Refusing literal
addresses is not a limitation, it is the point — §8 calls
``socks_remote_dns`` critical, because a browser that resolves ``.null``
itself leaks every name you visit to your ISP's resolver and does not work
anyway. A request that arrives with an IP literal is a misconfigured browser,
and saying so loudly beats failing quietly.

What happens per connection:

1. SOCKS5 handshake, no authentication (anything reaching 127.0.0.1 is already
   on this machine).
2. The hostname is decoded as an address (§3). An invalid one is refused
   locally, with no network traffic at all.
3. A session to that service is opened, or an existing one reused: a page's
   several connections become several **streams** on one six-hop splice
   (§8, and §16's tactic 7 one layer down).
4. For port 443 the proxy terminates TLS with a certificate from the local CA,
   so the browser grants the origin secure-context permissions (§8). The
   plaintext is then piped over the stream — the circuit is what provides
   confidentiality, twice over.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Final

from nullcell.core import ids
from nullcell.gateway.localca import LocalCA
from nullcell.onion.streams import MultiplexedSession, Stream

__all__ = ["DEFAULT_PORT", "HTTPS_PORT", "Socks5Error", "Socks5Proxy"]

DEFAULT_PORT: Final[int] = 9150
HTTPS_PORT: Final[int] = 443

_VERSION: Final[int] = 0x05
_CMD_CONNECT: Final[int] = 0x01
_ATYP_IPV4: Final[int] = 0x01
_ATYP_DOMAIN: Final[int] = 0x03
_ATYP_IPV6: Final[int] = 0x04

_REPLY_OK: Final[int] = 0x00
_REPLY_GENERAL_FAILURE: Final[int] = 0x01
_REPLY_HOST_UNREACHABLE: Final[int] = 0x04
_REPLY_CMD_UNSUPPORTED: Final[int] = 0x07
_REPLY_ATYP_UNSUPPORTED: Final[int] = 0x08

_CHUNK: Final[int] = 32 * 1024

log = logging.getLogger("nullcell.socks")

#: Called with a bare NID; returns a session to that service.
SessionFactory = Callable[[str], Awaitable[MultiplexedSession]]


class Socks5Error(Exception):
    """A malformed or unsupported SOCKS5 request."""

    def __init__(self, message: str, reply: int = _REPLY_GENERAL_FAILURE) -> None:
        super().__init__(message)
        self.reply = reply


class Socks5Proxy:
    """The local SOCKS5 proxy."""

    def __init__(
        self,
        open_session: SessionFactory,
        ca: LocalCA,
        *,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
    ) -> None:
        self.open_session = open_session
        self.ca = ca
        self.host = host
        self.port = port
        self._server: asyncio.AbstractServer | None = None
        #: One session per service, shared by every connection to it. This is
        #: the point of §8's design and it belongs here, not in the caller: a
        #: browser opens several connections per page, and without this each
        #: one would build its own six-hop rendezvous. Measured the hard way —
        #: six parallel requests without it simply time out.
        self._sessions: dict[str, MultiplexedSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.stats = {"accepted": 0, "opened": 0, "refused": 0, "sessions": 0}

    # -------------------------------------------------------------- lifecycle
    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        log.info("SOCKS5 on %s:%d", self.host, self.port)
        return self.port

    async def close(self) -> None:
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()
        self._locks.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ---------------------------------------------------------------- handling
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.stats["accepted"] += 1
        try:
            host, port = await self._negotiate(reader, writer)
        except Socks5Error as exc:
            log.debug("socks refused: %s", exc)
            self.stats["refused"] += 1
            await self._reply(writer, exc.reply)
            writer.close()
            return
        except (asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return
        except Exception as exc:
            # Anything unexpected must still become a SOCKS failure. Letting it
            # escape kills this task silently and leaves the browser waiting
            # for a reply that will never come — which is exactly how a bad
            # hostname decode first presented itself as a hang.
            log.exception("socks negotiation failed unexpectedly: %s", exc)
            self.stats["refused"] += 1
            await self._reply(writer, _REPLY_GENERAL_FAILURE)
            writer.close()
            return

        try:
            bare = ids.parse_hostname(host)
            ids.decode_nid(bare)  # local, no network traffic
        except ids.AddressError as exc:
            log.debug("not a .null address: %s", exc)
            self.stats["refused"] += 1
            await self._reply(writer, _REPLY_HOST_UNREACHABLE)
            writer.close()
            return

        try:
            session = await self._session_for(bare)
            stream = await session.open()
        except Exception as exc:
            log.info("could not reach %s: %s", ids.short(bare), exc)
            self.stats["refused"] += 1
            await self._reply(writer, _REPLY_HOST_UNREACHABLE)
            writer.close()
            return

        await self._reply(writer, _REPLY_OK)
        self.stats["opened"] += 1

        if port == HTTPS_PORT:
            # §8: not for confidentiality — for the browser to treat the origin
            # as secure and allow service workers, WebCrypto and the rest.
            try:
                await writer.start_tls(self.ca.ssl_context(host))
            except Exception as exc:
                log.debug("TLS with the browser failed: %s", exc)
                writer.close()
                await stream.close()
                return

        await self._pipe(reader, writer, stream)

    async def _session_for(self, bare_nid: str) -> MultiplexedSession:
        """A session to this service, built once and shared.

        The lock matters: a page's requests arrive together, and without it the
        first six would each start their own rendezvous before any of them
        finished.
        """
        session = self._sessions.get(bare_nid)
        if session is not None and not session.closed:
            return session

        lock = self._locks.setdefault(bare_nid, asyncio.Lock())
        async with lock:
            session = self._sessions.get(bare_nid)
            if session is not None and not session.closed:
                return session
            session = await self.open_session(bare_nid)
            self._sessions[bare_nid] = session
            self.stats["sessions"] += 1
            return session

    async def _negotiate(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> tuple[str, int]:
        greeting = await reader.readexactly(2)
        if greeting[0] != _VERSION:
            raise Socks5Error(f"not SOCKS5 (version {greeting[0]})")
        await reader.readexactly(greeting[1])  # offered auth methods
        writer.write(bytes([_VERSION, 0x00]))  # no authentication
        await writer.drain()

        header = await reader.readexactly(4)
        if header[0] != _VERSION:
            raise Socks5Error("bad request version")
        if header[1] != _CMD_CONNECT:
            raise Socks5Error(f"only CONNECT is supported, got {header[1]}", _REPLY_CMD_UNSUPPORTED)

        atyp = header[3]
        if atyp == _ATYP_DOMAIN:
            length = (await reader.readexactly(1))[0]
            raw = await reader.readexactly(length)
            try:
                # A .null address is base32, so plain ASCII. The idna codec
                # would be wrong here and, worse, it rejects errors="replace"
                # by raising UnicodeError — which is how this line first
                # hung a connection instead of refusing it.
                host = raw.decode("ascii")
            except UnicodeDecodeError as exc:
                raise Socks5Error(
                    "hostname is not ASCII, so it is not a .null address",
                    _REPLY_HOST_UNREACHABLE,
                ) from exc
        elif atyp in (_ATYP_IPV4, _ATYP_IPV6):
            await reader.readexactly(4 if atyp == _ATYP_IPV4 else 16)
            raise Socks5Error(
                "the browser resolved the name itself: set "
                "network.proxy.socks_remote_dns = true (§8)",
                _REPLY_ATYP_UNSUPPORTED,
            )
        else:
            raise Socks5Error(f"unknown address type {atyp}", _REPLY_ATYP_UNSUPPORTED)

        port = int.from_bytes(await reader.readexactly(2), "big")
        return host, port

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, code: int) -> None:
        # Bound address 0.0.0.0:0 — a circuit has no address to report, and
        # inventing one would put a lie in the browser's view of the world.
        try:
            writer.write(bytes([_VERSION, code, 0x00, _ATYP_IPV4, 0, 0, 0, 0, 0, 0]))
            await writer.drain()
        except (ConnectionError, RuntimeError):
            pass

    # ------------------------------------------------------------------- piping
    async def _pipe(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, stream: Stream
    ) -> None:
        async def browser_to_circuit() -> None:
            try:
                while True:
                    chunk = await reader.read(_CHUNK)
                    if not chunk:
                        break
                    await stream.write(chunk)
            except Exception as exc:
                log.debug("browser -> circuit ended: %s", exc)

        async def circuit_to_browser() -> None:
            try:
                while True:
                    chunk = await stream.read()
                    if not chunk:
                        break
                    writer.write(chunk)
                    await writer.drain()
            except Exception as exc:
                log.debug("circuit -> browser ended: %s", exc)

        up = asyncio.create_task(browser_to_circuit())
        down = asyncio.create_task(circuit_to_browser())
        try:
            await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (up, down):
                task.cancel()
            await stream.close()
            writer.close()
