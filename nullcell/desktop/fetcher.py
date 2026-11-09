"""HTTP over a circuit, for the `null://` scheme — §8.

§8's SOCKS proxy pipes an encrypted TLS conversation through a stream and
never looks at it. Our own browser does not need that TLS at all, so this
speaks HTTP/1.1 over the stream directly.

The session cache is the part that is not optional. A page is not one request:
it is the document plus its stylesheet, its script and its images, and
building a six-hop rendezvous for each would make every page unusable — in
§8's own measurements six parallel requests without the cache simply time out.
So one session per address, shared, with one stream per request.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nullcell.core import ids

log = logging.getLogger(__name__)

__all__ = ["Fetched", "NullFetcher"]

#: Refuse a response larger than this rather than grow without bound.
MAX_BODY: int = 32 * 1024 * 1024
#: How long one request may take, end to end over six hops.
TIMEOUT: float = 45.0


class Fetched:
    """A parsed HTTP response."""

    __slots__ = ("body", "headers", "status")

    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "application/octet-stream")


class NullFetcher:
    """Fetches `null://<address>/path` through circuits."""

    def __init__(self, open_session: Any) -> None:
        self.open_session = open_session
        self._sessions: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.stats = {"requests": 0, "sessions": 0, "failed": 0}

    async def _session_for(self, bare: str) -> Any:
        """One session per service, built once (§8)."""
        lock = self._locks.setdefault(bare, asyncio.Lock())
        async with lock:
            session = self._sessions.get(bare)
            if session is not None and not getattr(session, "closed", False):
                return session
            session = await self.open_session(bare)
            self._sessions[bare] = session
            self.stats["sessions"] += 1
            return session

    async def fetch(self, host: str, path: str, *, method: str = "GET") -> Fetched:
        bare = ids.parse_hostname(host)
        if not bare:
            raise ValueError(f"{host!r} no es una dirección .null válida")

        self.stats["requests"] += 1
        session = await self._session_for(bare)
        stream = await session.open()
        try:
            request = (
                f"{method} {path or '/'} HTTP/1.1\r\n"
                f"Host: {bare}{ids.SUFFIX}\r\n"
                "Connection: close\r\n"
                "Accept-Encoding: identity\r\n"
                "\r\n"
            ).encode()
            await stream.write(request)
            raw = await asyncio.wait_for(self._read_all(stream), timeout=TIMEOUT)
        finally:
            await _quietly_close(stream)
        return _parse(raw)

    async def _read_all(self, stream: Any) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                chunk = await stream.read()
            except Exception as exc:  # StreamClosed and friends mean EOF here
                log.debug("stream ended: %s", exc)
                break
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_BODY:
                raise ValueError(f"la respuesta pasa de {MAX_BODY // 1024 // 1024} MiB")
        return b"".join(chunks)

    async def close(self) -> None:
        for session in self._sessions.values():
            close = getattr(session, "close", None)
            if close is None:
                continue
            result = close()
            if asyncio.iscoroutine(result):
                await result
        self._sessions.clear()


async def _quietly_close(stream: Any) -> None:
    try:
        await stream.close()
    except Exception as exc:  # noqa: BLE001 - closing must not mask the answer
        log.debug("closing the stream failed: %s", exc)


def _parse(raw: bytes) -> Fetched:
    """Split an HTTP/1.1 response.

    Deliberately strict about one thing: a response with no status line is an
    error with a message, not a zero status handed to the renderer. A blank
    page with no explanation was the single most confusing failure while
    building §8.
    """
    head, _, body = raw.partition(b"\r\n\r\n")
    if not head:
        raise ValueError("el portal no contestó nada")
    lines = head.split(b"\r\n")
    parts = lines[0].split(b" ", 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise ValueError(f"respuesta sin línea de estado: {lines[0][:60]!r}")

    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, sep, value = line.partition(b":")
        if sep:
            headers[name.decode("latin-1").strip().lower()] = value.decode("latin-1").strip()

    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = _dechunk(body)
    return Fetched(int(parts[1]), headers, body)


def _dechunk(body: bytes) -> bytes:
    """Reassemble a chunked body.

    Needed because we ask for `Connection: close` but a portal may still
    answer chunked, and handing the renderer the chunk sizes as if they were
    content renders them as text at the top of the page.
    """
    out: list[bytes] = []
    rest = body
    while True:
        line, sep, rest = rest.partition(b"\r\n")
        if not sep:
            break
        try:
            size = int(line.split(b";")[0], 16)
        except ValueError:
            return body  # not chunked after all; better raw than mangled
        if size == 0:
            break
        out.append(rest[:size])
        rest = rest[size:].lstrip(b"\r\n")
    return b"".join(out)
