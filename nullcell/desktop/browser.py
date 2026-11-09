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

from PySide6.QtCore import QBuffer, QByteArray, QObject, Qt, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineUrlSchemeHandler
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

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
                fetched = await self._fetcher_for().fetch(host, path)
            except Exception as exc:  # noqa: BLE001 - every failure is shown
                log.info("null://%s%s falló: %s", host[:10], path, exc)
                self.answered.emit(
                    job, 502, b"text/html", _error_page("No se pudo abrir", str(exc))
                )
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
    """A window with one address bar that only understands `.null`."""

    def __init__(self, node: Any) -> None:
        super().__init__()
        self.node = node
        self.setWindowTitle("Nullcell")
        self.resize(1100, 760)

        profile = QWebEngineProfile("nullcell", self)
        # §8: nothing from this browsing survives on disk by itself.
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.handler = NullSchemeHandler(node, self)
        profile.installUrlSchemeHandler(SCHEME, self.handler)
        self.profile = profile

        self.view = QWebEngineView()
        page = self.view.page()
        self.view.setPage(self._page_for(profile))
        del page

        self.bar = QLineEdit()
        self.bar.setPlaceholderText("dirección .null, o una búsqueda")
        self.bar.returnPressed.connect(self._go)
        back = QPushButton("←")
        back.clicked.connect(self.view.back)
        home = QPushButton("⌂")
        home.clicked.connect(self.go_home)

        top = QHBoxLayout()
        top.setContentsMargins(8, 8, 8, 4)
        top.addWidget(back)
        top.addWidget(home)
        top.addWidget(self.bar, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.view, 1)
        self.go_home()

    def _page_for(self, profile: QWebEngineProfile):
        from PySide6.QtWebEngineCore import QWebEnginePage

        return QWebEnginePage(profile, self)

    def go_home(self) -> None:
        """The dashboard and the search box, served by our own node."""
        url = getattr(self.node, "url", None)
        if url:
            self.view.load(QUrl(url))
            self.bar.setText("inicio")

    def _go(self) -> None:
        from nullcell.core import ids

        text = self.bar.text().strip()
        if not text:
            return
        if "://" in text:
            self.view.load(QUrl(text))
            return
        bare = ids.parse_hostname(text)
        if bare:
            self.view.load(QUrl(f"null://{bare}/"))
            return
        # Not an address: treat it as a search, which is what §8's Oráculo is
        # for. The panel's own origin serves the results.
        url = getattr(self.node, "url", "")
        if url:
            base = url.split("/panel")[0]
            token = url.split("token=")[-1]
            self.view.load(QUrl(f"{base}/?token={token}#q={text}"))
