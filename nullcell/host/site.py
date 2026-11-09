"""Serving a site over circuits — §8.

``ncell host`` runs a real HTTP server whose transport is a Nullcell stream
instead of a TCP socket. §8 is explicit about what that buys: *"El servidor HTTP
interno escucha sobre circuitos, nunca en un puerto público. Cero port
forwarding."*

The HTTP itself is **aiohttp's**, not ours. An asyncio transport that writes
into a stream is about forty lines; a correct HTTP/1.1 server with keep-alive,
chunked encoding, ranges and conditional requests is not something to reinvent
for a milestone whose point is somewhere else.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
from pathlib import Path
from typing import Any, Final

from aiohttp import web

from nullcell.onion.streams import Stream

__all__ = ["SiteHost", "static_app", "StreamTransport"]

_MAX_READ: Final[int] = 1 << 20

log = logging.getLogger("nullcell.host")


class StreamTransport(asyncio.Transport):
    """An asyncio transport whose other end is a Nullcell stream.

    This is the whole adapter: aiohttp believes it is talking to a socket, and
    the bytes travel over six hops instead.
    """

    def __init__(self, stream: Stream, loop: asyncio.AbstractEventLoop | None = None) -> None:
        super().__init__()
        self.stream = stream
        self._loop = loop or asyncio.get_running_loop()
        self._closing = False
        # A FIFO with one drain task, not a task per write. aiohttp writes the
        # headers and the body separately, and spawning a task per write lets
        # them reach the stream out of order — which looks like a response that
        # never arrives, because the client is parsing a body as a status line.
        # A small page hid it by fitting in a single write.
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._drain = self._loop.create_task(self._drain_queue())

    def write(self, data: bytes) -> None:
        if self._closing or not data:
            return
        self._queue.put_nowait(bytes(data))

    async def _drain_queue(self) -> None:
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                # Closing the stream is what carries `Connection: close` to the
                # other end. Without it the client waits for an EOF that never
                # comes, which looks exactly like a hung response.
                with contextlib.suppress(Exception):
                    await self.stream.close()
                return
            try:
                await self.stream.write(chunk)
            except Exception as exc:
                # At warning, not debug. A failure here means a response that
                # never arrives, and the first time it happened — a one-byte
                # frame budget error — it was invisible because the exception
                # died inside a task nobody awaited.
                log.warning("stream write failed, response truncated: %s", exc)
                return

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        # aiohttp asks for these; a circuit has no peer address to give, which
        # is the point. Reporting a plausible fake would put a wrong address in
        # access logs.
        if name == "peername":
            return ("nullcell", 0)
        if name in ("sslcontext", "ssl_object", "socket"):
            return None
        return default

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._queue.put_nowait(None)  # let the drain task finish in order

    def abort(self) -> None:
        self._closing = True
        self._drain.cancel()


class SiteHost:
    """Serves an aiohttp application to whoever reaches us over a circuit."""

    def __init__(self, app: web.Application) -> None:
        self.app = app
        self._server: web.Server | None = None
        self.stats = {"streams": 0, "bytes_in": 0}

    async def start(self) -> None:
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        self._server = runner.server

    def serve(self, stream: Stream) -> None:
        """Attach a new stream to the HTTP server. Use as ``on_stream``."""
        if self._server is None:
            raise RuntimeError("call start() before serving streams")
        self.stats["streams"] += 1
        asyncio.ensure_future(self._serve(stream))

    async def _serve(self, stream: Stream) -> None:
        assert self._server is not None
        protocol = self._server()
        transport = StreamTransport(stream)
        protocol.connection_made(transport)
        try:
            while True:
                chunk = await stream.read()
                if not chunk:
                    break
                self.stats["bytes_in"] += len(chunk)
                protocol.data_received(chunk)
                await asyncio.sleep(0)  # let the handler run
        except Exception as exc:
            log.debug("serving a stream failed: %s", exc)
        finally:
            protocol.connection_lost(None)
            transport.close()


def wsgi_app(target: str) -> web.Application:
    """Wrap a WSGI callable so ``ncell host --wsgi app:application`` works (§8).

    §8 promises Flask and FastAPI, and both speak WSGI or ASGI. This takes the
    ``module:attribute`` form and serves it over circuits like anything else.

    The callable runs in a **thread executor**, because WSGI is synchronous by
    definition: calling it inline would block the event loop for the whole
    request and, per §16, freeze every circuit on the node.
    """
    import asyncio
    import importlib
    import io

    module_name, _, attribute = target.partition(":")
    if not module_name or not attribute:
        raise ValueError("expected module:attribute, as in app:application")
    module = importlib.import_module(module_name)
    application = getattr(module, attribute)

    app = web.Application()

    async def handler(request: web.Request) -> web.StreamResponse:
        body = await request.read()
        environ = {
            "REQUEST_METHOD": request.method,
            "SCRIPT_NAME": "",
            "PATH_INFO": request.path,
            "QUERY_STRING": request.query_string,
            "CONTENT_TYPE": request.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
            # A circuit has no client address, which is the point (§6). An
            # application that logs REMOTE_ADDR gets a name, not a lie.
            "REMOTE_ADDR": "nullcell",
            "SERVER_NAME": request.host.split(":")[0],
            "SERVER_PORT": "443",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "https",
            "wsgi.input": io.BytesIO(body),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": True,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        for key, value in request.headers.items():
            environ["HTTP_" + key.upper().replace("-", "_")] = value

        captured: dict[str, object] = {}

        def start_response(status, headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = headers
            return lambda data: None

        def call() -> bytes:
            chunks = application(environ, start_response)
            try:
                return b"".join(chunks)
            finally:
                close = getattr(chunks, "close", None)
                if close is not None:
                    close()

        loop = asyncio.get_running_loop()
        payload = await loop.run_in_executor(None, call)

        status = str(captured.get("status", "200 OK"))
        response = web.Response(
            body=payload,
            status=int(status.split()[0]),
            headers={
                key: value
                for key, value in captured.get("headers", [])  # type: ignore[union-attr]
                if key.lower() not in ("content-length", "transfer-encoding")
            },
        )
        return response

    app.router.add_route("*", "/{tail:.*}", handler)
    return app


def static_app(root: Path, *, index: str = "index.html") -> web.Application:
    """An application serving *root* as a static site (§8, ``ncell host``)."""
    root = root.resolve()
    app = web.Application()

    async def handler(request: web.Request) -> web.StreamResponse:
        relative = request.path.lstrip("/") or index
        target = (root / relative).resolve()

        # A site must not be able to serve the rest of the filesystem.
        if not target.is_relative_to(root):
            raise web.HTTPForbidden(text="outside the site root")
        if target.is_dir():
            target = target / index
        if not target.is_file():
            raise web.HTTPNotFound(text="not found")

        guessed, _ = mimetypes.guess_type(target.name)
        return web.Response(
            body=target.read_bytes(),
            content_type=guessed or "application/octet-stream",
        )

    app.router.add_get("/{tail:.*}", handler)
    return app
