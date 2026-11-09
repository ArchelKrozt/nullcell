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

import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Final

from aiohttp import WSMsgType, web

from nullcell.gateway import control as control_mod
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


async def _json_body(request: web.Request) -> dict[str, Any]:
    """The request's JSON object, or an empty one. Never raises."""
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


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
        search: object = None,
        control: object = None,
        panel_root: Path | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.bus = bus
        #: A :class:`~nullcell.search.query.Search`, if this node has one. The
        #: HUD is also el Oráculo (§9), so the search box is part of the panel
        #: rather than a separate page.
        self.search = search
        #: A :class:`~nullcell.gateway.control.ControlPanel`, if this node lets
        #: its panel act as well as look (§9). Without one the HUD is read-only
        #: and every control route answers 503.
        self.control = control
        #: Where the desktop panel's files live, if a desktop shell is driving
        #: this node. Served **from here on purpose**: the panel then shares an
        #: origin with `/api`, so it inherits §9's token, Host check and CSP
        #: instead of needing CORS — and CORS on a custom scheme is measured
        #: in `desktop/scheme.py` as the thing that breaks portal isolation.
        self.panel_root = panel_root
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

        # A state-changing request needs the token in a *header*. Reading
        # accepts a cookie, because that is how a page keeps a session — but a
        # hostile cross-site form can make the browser attach that cookie,
        # and it cannot set a header. `SameSite=Strict` already stops the
        # cookie travelling cross-site and the `Host` check already stops DNS
        # rebinding; this is the fence that does not depend on the browser
        # honouring an attribute.
        if request.method in control_mod.MUTATING and not self._has_header_token(request):
            log.warning("refused a %s without the header token", request.method)
            raise web.HTTPForbidden(text="state-changing requests need the token header")

        response = await handler(request)
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    def _has_header_token(self, request: web.Request) -> bool:
        """The token presented where only our own script can put it."""
        offered = request.headers.get(TOKEN_HEADER)
        return bool(offered) and secrets.compare_digest(offered, self.token)

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
            self._set_cookie(response)
        return response

    def _set_cookie(self, response: web.StreamResponse) -> None:
        """Move the token out of the URL so it stops appearing in the address
        bar, in history and in any Referer."""
        response.set_cookie(COOKIE_NAME, self.token, httponly=True, samesite="Strict", path="/")

    async def _panel_index(self, request: web.Request) -> web.StreamResponse:
        if self.panel_root is None:
            raise web.HTTPNotFound()
        body = (self.panel_root / "index.html").read_bytes()
        response = web.Response(body=body, content_type="text/html", charset="utf-8")
        self._set_cookie(response)
        return response

    async def _panel_static(self, request: web.Request) -> web.StreamResponse:
        if self.panel_root is None:
            raise web.HTTPNotFound()
        name = request.match_info["name"]
        if name == "config.js":
            return self._panel_config()
        return self._serve_from(self.panel_root, name)

    def _panel_config(self) -> web.StreamResponse:
        """Where to call and with what, as a served script rather than inline.

        It started as an inline `<script>` and **§9's own CSP refused it**:
        `script-src 'self'` exists precisely to stop a page running script that
        arrived inside the markup. Loosening it to `unsafe-inline` to deliver
        our own token would have traded a real defence for a convenience, so
        the token comes back as a file instead — generated per request, never
        written to disk.
        """
        body = f"window.NCELL_API='';window.NCELL_TOKEN={json.dumps(self.token)};\n"
        return web.Response(text=body, content_type="application/javascript", charset="utf-8")

    async def _static(self, request: web.Request) -> web.StreamResponse:
        return self._serve_from(_HUD_ROOT, request.match_info["name"])

    def _serve_from(self, root: Path, name: str) -> web.StreamResponse:
        """One file from *root*, and nothing outside it.

        Shared by the HUD and the desktop panel on purpose: two copies of a
        path check is how one of them quietly stops refusing `..`.
        """
        if "/" in name or ".." in name or name.startswith("."):
            raise web.HTTPNotFound()
        path = root / name
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

    async def _search(self, request: web.Request) -> web.StreamResponse:
        """Run a query (§8) and answer with results and their explanations.

        Every score comes back **broken into parts** (§8's ranking), because
        §9 forbids the interface from inventing metrics and one opaque number
        is how a ranking stops being explainable.
        """
        if self.search is None:
            return web.json_response({"error": "este nodo no tiene índice"}, status=503)
        query = (request.query.get("q") or "").strip()
        if not query:
            return web.json_response({"query": "", "results": [], "progress": ""})

        progress: list[str] = []
        results = await self.search.run(query, on_progress=lambda p: progress.append(p.describe()))
        return web.json_response(
            {
                "query": query,
                "progress": progress[-1] if progress else "",
                "results": [
                    {
                        "nid": ranked.hit.nid,
                        "kind": ranked.hit.kind,
                        "title": ranked.hit.title,
                        "desc": ranked.hit.descr,
                        "tags": ranked.hit.tags,
                        "age": round(ranked.hit.age),
                        "confirmed": ranked.hit.confirmed,
                        "score": round(ranked.score, 3),
                        "why": ranked.explain(),
                    }
                    for ranked in results
                ],
            }
        )

    async def _sigils(self, request: web.Request) -> web.StreamResponse:
        """Every known portal's seal, in one sprite (§9, §16).

        Generated locally from ``sha256(nid)``: no request leaves this machine
        to draw them, which is the whole reason §9 refuses remote favicons.
        """
        from nullcell.gateway.sigil import sprite

        state = self.snapshot()
        nids = [portal["nid"] for portal in state.get("portals", []) if portal.get("nid")]
        nids += [
            circuit_hop for c in state.get("circuits", []) for circuit_hop in c.get("hops", [])
        ]
        return web.Response(
            body=sprite(sorted(set(nids))).encode("utf-8"),
            content_type="image/svg+xml",
            charset="utf-8",
        )

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

    # --------------------------------------------------------------- control
    def _panel(self) -> Any:
        if self.control is None:
            raise web.HTTPServiceUnavailable(text="este nodo es de sólo lectura")
        return self.control

    async def _control_state(self, request: web.Request) -> web.StreamResponse:
        return web.json_response(self._panel().describe())

    async def _set_sharing(self, request: web.Request) -> web.StreamResponse:
        panel = self._panel()
        body = await _json_body(request)
        wanted = body.get("on")
        if not isinstance(wanted, bool):
            return web.json_response({"error": "falta 'on': true o false"}, status=400)
        try:
            result = await panel.set_sharing(wanted)
        except control_mod.ControlError as refused:
            return web.json_response({"error": str(refused)}, status=409)
        self.bus.emit(
            "control.sharing",
            f"compartir ancho de banda: {'activado' if wanted else 'desactivado'}"
            f" — {result.get('detail', '')}",
        )
        return web.json_response(result)

    async def _publish_folder(self, request: web.Request) -> web.StreamResponse:
        panel = self._panel()
        body = await _json_body(request)
        folder = body.get("folder")
        if not isinstance(folder, str) or not folder.strip():
            return web.json_response({"error": "falta 'folder'"}, status=400)
        try:
            result = await panel.publish_folder(folder, name=body.get("name"))
        except control_mod.ControlError as refused:
            return web.json_response({"error": str(refused)}, status=409)
        self.bus.emit("control.portal", f"portal publicado desde {folder}")
        return web.json_response(result)

    # -------------------------------------------------------------- lifecycle
    def build_app(self) -> web.Application:
        app = web.Application(middlewares=[self._guard])
        app.add_routes(
            [
                web.get("/", self._index),
                web.get("/static/{name}", self._static),
                web.get("/api/state", self._state),
                web.get("/api/search", self._search),
                web.get("/api/sigils.svg", self._sigils),
                web.get("/ws", self._ws),
                web.get("/panel", self._panel_index),
                web.get("/panel/{name}", self._panel_static),
                web.get("/api/control", self._control_state),
                web.post("/api/control/sharing", self._set_sharing),
                web.post("/api/control/portal", self._publish_folder),
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
