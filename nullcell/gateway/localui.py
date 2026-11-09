"""The local UI server — §9's hard rules, enforced here.

`https://hud.null` is served from `127.0.0.1:9180`. That address is reachable by
**any web page the user has open**, so this file is a security boundary, not
plumbing. §9's rules, and what each one stops:

* **Session token.** Without one, any site could call this API and publish in
  your name, read your state or shut your node down (CSRF).
* **`Host` validation.** A hostile site can point a name it controls at
  127.0.0.1 and have the browser treat it as same-origin — DNS rebinding. The
  only accepted hosts are the loopback literals.
* **`Content-Security-Policy: default-src 'self'`.** No CDN, no fonts from
  elsewhere, no telemetry. A single external request would tell the ordinary
  internet that there is a Nullcell node at this address.
* **`frame-ancestors 'none'`.** Nothing embeds this page, and this page embeds
  nothing (§9: a remote site inside our origin could call our own API).
* **WebSocket, not polling**, with the batching of §16.

Reserved names: `hud.null`, `search.null` and `me.null` resolve locally and are
refused in the DHT (§9), or somebody could register the name of your control
panel.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Final

from aiohttp import WSMsgType, web

from nullcell.gateway.events import EventBus, drain

__all__ = ["COOKIE_NAME", "DEFAULT_PORT", "RESERVED_NAMES", "LocalUI"]

DEFAULT_PORT: Final[int] = 9180
COOKIE_NAME: Final[str] = "ncell_session"
TOKEN_HEADER: Final[str] = "X-Nullcell-Token"

#: §9 — resolved locally, and refused in the DHT.
RESERVED_NAMES: Final[frozenset[str]] = frozenset({"hud.null", "search.null", "me.null"})

_CSP: Final[str] = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)
_HUD_ROOT: Final[Path] = Path(__file__).resolve().parent.parent / "hud"

log = logging.getLogger("nullcell.hud")

Snapshot = Callable[[], dict[str, Any]]


class LocalUI:
    """Serves the HUD and pushes live events."""

    def __init__(
        self,
        snapshot: Snapshot,
        bus: EventBus,
        *,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        token: str | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.bus = bus
        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(32)
        self._runner: web.AppRunner | None = None
        self._allowed_hosts = {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            f"[::1]:{port}",
        }

    @property
    def url(self) -> str:
        """The address to open, carrying the token once."""
        return f"http://127.0.0.1:{self.port}/?token={self.token}"

    # ------------------------------------------------------------ middleware
    @web.middleware
    async def _guard(
        self, request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
    ) -> web.StreamResponse:
        # DNS rebinding: the browser will happily send any hostname that
        # resolves to 127.0.0.1, so only the loopback literals are accepted.
        host = request.headers.get("Host", "")
        if host not in self._allowed_hosts:
            log.warning("rejected a request with Host: %r", host)
            raise web.HTTPForbidden(text="bad Host header")

        if not self._authorised(request):
            raise web.HTTPUnauthorized(text="missing or wrong session token")

        response = await handler(request)
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    def _authorised(self, request: web.Request) -> bool:
        for candidate in (
            request.cookies.get(COOKIE_NAME),
            request.headers.get(TOKEN_HEADER),
            request.query.get("token"),
        ):
            if candidate and secrets.compare_digest(candidate, self.token):
                return True
        return False

    # --------------------------------------------------------------- handlers
    async def _index(self, request: web.Request) -> web.StreamResponse:
        body = (_HUD_ROOT / "index.html").read_bytes()
        response = web.Response(body=body, content_type="text/html", charset="utf-8")
        if request.query.get("token"):
            # Move the token out of the URL so it stops appearing in the
            # address bar, in history and in any Referer.
            response.set_cookie(
                COOKIE_NAME,
                self.token,
                httponly=True,
                samesite="Strict",
                path="/",
            )
        return response

    async def _static(self, request: web.Request) -> web.StreamResponse:
        name = request.match_info["name"]
        if "/" in name or ".." in name or name.startswith("."):
            raise web.HTTPNotFound()
        path = _HUD_ROOT / name
        if not path.is_file():
            raise web.HTTPNotFound()
        types = {".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
        return web.Response(
            body=path.read_bytes(),
            content_type=types.get(path.suffix, "application/octet-stream"),
            charset="utf-8" if path.suffix in (".js", ".css", ".svg") else None,
        )

    async def _state(self, request: web.Request) -> web.StreamResponse:
        return web.json_response({"state": self.snapshot(), "events": self.bus.recent(100)})

    async def _ws(self, request: web.Request) -> web.StreamResponse:
        socket = web.WebSocketResponse(heartbeat=30.0)
        await socket.prepare(request)
        queue = self.bus.subscribe()
        try:
            await socket.send_json({"type": "state", "state": self.snapshot()})
            await socket.send_json({"type": "events", "events": self.bus.recent(100)})

            import asyncio

            async def push_events() -> None:
                while not socket.closed:
                    batch = await drain(queue)
                    await socket.send_json(
                        {"type": "events", "events": [e.as_dict() for e in batch]}
                    )

            async def push_state() -> None:
                while not socket.closed:
                    await asyncio.sleep(1.0)
                    await socket.send_json({"type": "state", "state": self.snapshot()})

            pushers = [asyncio.create_task(push_events()), asyncio.create_task(push_state())]
            try:
                async for message in socket:
                    if message.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                        break
            finally:
                for task in pushers:
                    task.cancel()
        finally:
            self.bus.unsubscribe(queue)
        return socket

    # -------------------------------------------------------------- lifecycle
    def build_app(self) -> web.Application:
        app = web.Application(middlewares=[self._guard])
        app.add_routes(
            [
                web.get("/", self._index),
                web.get("/static/{name}", self._static),
                web.get("/api/state", self._state),
                web.get("/ws", self._ws),
            ]
        )
        return app

    async def start(self) -> str:
        self._runner = web.AppRunner(self.build_app(), access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        log.info("HUD on %s", self.url)
        return self.url

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
