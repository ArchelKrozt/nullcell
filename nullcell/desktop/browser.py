"""The network's own browser — Chromium core, our interface (§8, §9).

Two threads meet here and the handover is the whole design. Chromium asks for
a URL on Qt's thread; the circuits live on the node's asyncio loop. So the
handler schedules the work on that loop with `run_coroutine_threadsafe` and
delivers the answer back through a Qt signal, which is the only safe way to
touch a `QWebEngineUrlRequestJob` again. Replying from the wrong thread is the
kind of mistake that shows up as a crash in Chromium's internals with no
mention of our code.

What is deliberately absent: a certificate authority. §8 needed one so an
ordinary browser would accept `https://<address>.null`; here the renderer gets
the bytes straight from the circuit, so there is nothing to sign, nothing to
install and nothing to trust. The isolation between portals still comes from
the browser, because `null://<address>` is a real origin — see `scheme.py` for
the flags that make that true and the measured flag that broke it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

from PySide6.QtCore import QBuffer, QByteArray, QObject, Qt, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineUrlSchemeHandler
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nullcell.desktop import browser_chrome as chrome
from nullcell.desktop import failures
from nullcell.desktop.scheme import SCHEME

log = logging.getLogger(__name__)

__all__ = ["BrowserWindow", "NullSchemeHandler"]


def _error_page(title: str, detail: str) -> bytes:
    """A failure the operator can act on, in the project's own skin.

    §9 forbids the interface from inventing state, and a blank page is the
    interface inventing "nothing is wrong".
    """
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<style>
  :root {{ --bg:#07090d; --text:#e6edf3; --dim:#7d8da3; --alert:#ff00ff; }}
  body {{ background:var(--bg); color:var(--text); font:13px/1.6 ui-monospace,Menlo,monospace;
          margin:0; display:grid; place-items:center; min-height:100vh; }}
  .card {{ max-width:40em; padding:22px 26px; border-left:2px solid var(--alert);
           background:#0d1117; }}
  h1 {{ font-size:14px; letter-spacing:.14em; margin:0 0 10px; color:var(--alert); }}
  p {{ margin:0; color:var(--dim); white-space:pre-wrap; word-break:break-word; }}
</style></head><body><div class="card"><h1>{title}</h1><p>{detail}</p></div></body></html>
""".encode()


class NullSchemeHandler(QWebEngineUrlSchemeHandler):
    """Serves `null://` by fetching through the node's circuits."""

    #: Carries (job, status, content_type, body) back to the Qt thread.
    answered = Signal(object, int, bytes, bytes)

    def __init__(self, node: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.node = node
        self._fetcher: Any = None
        self._live: set[Any] = set()
        self.answered.connect(self._reply, Qt.ConnectionType.QueuedConnection)

    # ------------------------------------------------------------- Qt thread
    def requestStarted(self, job) -> None:  # noqa: N802 - Qt's name
        url = job.requestUrl()
        host, path = url.host(), url.path() or "/"
        if url.hasQuery():
            path = f"{path}?{url.query()}"
        method = bytes(job.requestMethod()).decode("latin-1").upper()
        if method != "GET":
            self._fail(job, "Método no permitido", f"{method} no está soportado todavía.")
            return

        self._live.add(job)
        job.destroyed.connect(lambda *_: self._live.discard(job))

        loop = getattr(self.node, "_loop", None)
        if loop is None:
            self._fail(job, "Nodo parado", "El nodo no está corriendo.")
            return

        async def work() -> None:
            try:
                # Tu propio portal no necesita circuito: eres los dos extremos.
                panel = getattr(self.node, "panel", None)
                local = panel.local_url_for(host) if panel is not None else None
                if local:
                    fetched = await self._fetcher_for().fetch_local(local, path)
                else:
                    fetched = await self._fetcher_for().fetch(host, path)
            except Exception as exc:  # noqa: BLE001 - every failure is shown
                log.info("null://%s%s falló: %s", host[:10], path, exc)
                title, detail = failures.explain(exc, host)
                self.answered.emit(job, 502, b"text/html", _error_page(title, detail))
                return
            self.answered.emit(
                job, fetched.status, fetched.content_type.encode("latin-1"), fetched.body
            )

        asyncio.run_coroutine_threadsafe(work(), loop)

    def _fetcher_for(self) -> Any:
        if self._fetcher is None:
            from nullcell.desktop.fetcher import NullFetcher

            self._fetcher = NullFetcher(self.node.open_session)
        return self._fetcher

    def _fail(self, job, title: str, detail: str) -> None:
        self.answered.emit(job, 502, b"text/html", _error_page(title, detail))

    def _reply(self, job, status: int, content_type: bytes, body: bytes) -> None:
        """Back on Qt's thread, where touching the job is legal."""
        if job not in self._live and status != 502:
            return  # the renderer gave up first
        try:
            buffer = QBuffer(job)
            buffer.setData(QByteArray(body))
            buffer.open(QBuffer.OpenModeFlag.ReadOnly)
            job.reply(content_type, buffer)
        except Exception as exc:  # noqa: BLE001
            log.debug("no se pudo contestar al renderizador: %s", exc)


class BrowserWindow(QWidget):
    """A window whose address bar only understands this network."""

    def __init__(self, node: Any) -> None:
        super().__init__()
        self.node = node
        self.setWindowTitle("Nullcell")
        self.resize(1100, 760)
        self.setStyleSheet(chrome.STYLE)

        profile = QWebEngineProfile("nullcell", self)
        # §8: nothing from this browsing survives on disk by itself.
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.handler = NullSchemeHandler(node, self)
        profile.installUrlSchemeHandler(SCHEME, self.handler)
        self.profile = profile

        from PySide6.QtWebEngineCore import QWebEnginePage

        self.view = QWebEngineView()
        self.view.setPage(QWebEnginePage(profile, self))
        self.view.urlChanged.connect(self._url_changed)
        self.view.loadFinished.connect(self._load_finished)

        self.kind = QLabel("", objectName="kind")
        self.bar = QLineEdit()
        self.bar.setPlaceholderText("dirección .null, o escribe para buscar en la red")
        self.bar.returnPressed.connect(self._go)
        self.status = QLabel("", objectName="status")

        back = QPushButton("←")
        back.clicked.connect(self.view.back)
        forward = QPushButton("→")
        forward.clicked.connect(self.view.forward)
        home = QPushButton("⌂")
        home.setToolTip("el tablero de este nodo: buscador, circuitos y portales")
        home.clicked.connect(self.go_home)

        top = QHBoxLayout()
        top.setContentsMargins(10, 9, 10, 6)
        top.setSpacing(6)
        for widget in (back, forward, home):
            top.addWidget(widget)
        top.addWidget(self.bar, 1)
        top.addWidget(self.kind)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(top)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.status)
        self.go_home()

    def shutdown(self) -> None:
        """Soltar la página antes que el perfil.

        Qt destruye padres e hijos en un orden que no promete nada, y si el
        perfil se va primero aparece «Release of profile requested but
        WebEnginePage still not deleted» — que en el mejor caso es un aviso y
        en el peor cuelga el cierre.
        """
        page = self.view.page()
        self.view.setPage(None)
        if page is not None:
            page.deleteLater()

    # ------------------------------------------------------------- navegar
    def _dashboard(self) -> str | None:
        """This node's own panel: search, circuits, portals (§9).

        Not the menu-bar popover, which is a control surface for the node. The
        browser's home is the dashboard, which is the page that behaves like a
        portal — and loading the popover here was simply the wrong URL.
        """
        url = getattr(self.node, "url", None)
        if not url:
            return None
        base, _, query = url.partition("?")
        return f"{base.removesuffix('/panel')}/?{query}"

    def go_home(self) -> None:
        home = self._dashboard()
        if home:
            self.view.load(QUrl(home))

    def _go(self) -> None:
        text = self.bar.text().strip()
        if not text:
            return
        if destination := chrome.is_destination(text):
            self.view.load(QUrl(f"null://{destination}/"))
            return
        # Not an address, so it is a search. §8's Oráculo answers it, on this
        # node's own origin.
        home = self._dashboard()
        if home:
            self.view.load(QUrl(f"{home}&q={quote(text)}"))

    # -------------------------------------------------------------- estado
    def _url_changed(self, url: QUrl) -> None:
        text = url.toString()
        kind, label = chrome.kind_of(text)
        # Tu propio portal servido sin red es legítimo y no es lo mismo que
        # alcanzarlo por la red: decirlo evita creer que ya es visitable.
        panel = getattr(self.node, "panel", None)
        if kind == "portal" and panel is not None and panel.local_url_for(url.host()):
            kind, label = "local", "tu portal · local"
        self.kind.setText(label)
        self.kind.setProperty("kind", kind)
        self.kind.style().unpolish(self.kind)
        self.kind.style().polish(self.kind)
        # The token is ours and belongs in no address bar — but that is about
        # the dashboard's http URL, not about a null:// portal. Clearing on
        # "local" blanked the very address the person had just typed.
        carries_token = url.scheme() in ("http", "https")
        self.bar.setText("" if carries_token else text)

    def _load_finished(self, ok: bool) -> None:
        stats = getattr(self.handler, "_fetcher", None)
        counts = getattr(stats, "stats", {}) if stats else {}
        if counts:
            self.status.setText(
                f"  {counts.get('requests', 0)} peticiones · "
                f"{counts.get('sessions', 0)} rendezvous reutilizados"
            )
        elif not ok:
            self.status.setText("  no se pudo cargar")
