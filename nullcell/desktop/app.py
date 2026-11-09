"""The menu-bar shell — §9 with a face.

One icon that says NULL, and a panel under it with the switches. The panel is
a web page because §9's panel already is one, and serving it from the node's
own HTTP API keeps it **same-origin with `/api`**: it inherits the token, the
`Host` check and the CSP instead of needing CORS, and CORS on our own scheme
is measured in `scheme.py` as the exact flag that breaks portal isolation.

The three things the page cannot do by itself — open a native folder chooser,
open a browser window, quit the app — go through a navigation bridge: the page
sets `location.href = "ncell:<action>"` and this shell intercepts it. No extra
dependency, nothing injected into the page, and a bridge that can only carry
the verbs listed in `_ACTIONS`.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from nullcell.desktop.node_thread import NodeThread

log = logging.getLogger(__name__)

__all__ = ["run"]

#: The only verbs the page may ask the shell for.
_ACTIONS = frozenset({"pick-folder", "open-browser", "quit"})

PANEL_WIDTH = 360
PANEL_HEIGHT = 470


def _icon(size: int = 44):
    """The menu-bar mark: NULL, drawn rather than shipped as a file.

    A template image so macOS inverts it with the menu bar instead of leaving
    a bright square in a light theme.
    """
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

    pixmap = QPixmap(size * 3, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    font = QFont()
    font.setPixelSize(int(size * 0.62))
    font.setWeight(QFont.Weight.DemiBold)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, size * 0.07)
    painter.setFont(font)
    painter.setPen(QColor(0, 0, 0))
    painter.drawText(QRectF(0, 0, size * 3, size), Qt.AlignmentFlag.AlignCenter, "NULL")
    painter.end()
    icon = QIcon(pixmap)
    icon.setIsMask(True)  # template: the system recolours it
    return icon


def _build_panel(node: NodeThread, shell: Any):
    """A frameless window holding the panel page."""
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtWebEngineCore import QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    class Page(QWebEnginePage):
        """Intercepts the `ncell:` bridge and refuses anything else to leave."""

        def acceptNavigationRequest(self, url: QUrl, kind, is_main_frame: bool) -> bool:  # noqa: N802
            if url.scheme() == "ncell":
                action = (url.path() or url.host() or "").strip("/")
                if action in _ACTIONS:
                    shell.perform(action)
                else:
                    log.warning("el panel pidió una acción desconocida: %r", action)
                return False
            # The panel is a control surface, not a browser: it may only ever
            # be our own loopback origin.
            if url.scheme() in ("http", "https") and url.host() not in ("127.0.0.1", "localhost"):
                log.warning("el panel intentó salir a %s", url.toString())
                return False
            return True

    window = QWidget()
    window.setWindowFlags(
        Qt.WindowType.Popup
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.NoDropShadowWindowHint
    )
    window.setFixedSize(PANEL_WIDTH, PANEL_HEIGHT)
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)

    view = QWebEngineView()
    view.setPage(Page(view))
    view.setFixedSize(PANEL_WIDTH, PANEL_HEIGHT)
    layout.addWidget(view)
    window.view = view  # type: ignore[attr-defined]
    return window


class Shell:
    """Holds the tray icon, the panel and the node."""

    def __init__(self, node: NodeThread) -> None:
        self.node = node
        self.panel: Any = None
        self.tray: Any = None
        self.browser: Any = None

    # -------------------------------------------------------------- bridge
    def perform(self, action: str) -> None:
        if action == "quit":
            self.quit()
        elif action == "pick-folder":
            self.pick_folder()
        elif action == "open-browser":
            self.open_browser()

    def pick_folder(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(None, "Carpeta a publicar como portal .null")
        if not folder:
            return
        from nullcell.gateway.control import ControlError

        try:
            result = self.node.call(lambda: self.node.panel.publish_folder(folder))
        except ControlError as refused:
            result = {"error": str(refused)}
        except Exception as exc:  # noqa: BLE001 - shown, never swallowed
            result = {"error": str(exc)}
        self._tell_panel("ncellPublished", result)

    def open_browser(self) -> None:
        from nullcell.desktop.browser import BrowserWindow

        if self.browser is None:
            self.browser = BrowserWindow(self.node)
        self.browser.show()
        self.browser.raise_()

    def _tell_panel(self, function: str, payload: dict) -> None:
        import json

        if self.panel is None:
            return
        self.panel.view.page().runJavaScript(f"window.{function}({json.dumps(payload)})")

    # ------------------------------------------------------------ lifecycle
    def toggle_panel(self, reason: Any = None) -> None:
        from PySide6.QtGui import QCursor

        if self.panel is None:
            return
        if self.panel.isVisible():
            self.panel.hide()
            return
        point = QCursor.pos()
        self.panel.move(max(0, point.x() - PANEL_WIDTH // 2), point.y() + 8)
        self.panel.show()
        self.panel.activateWindow()

    def quit(self) -> None:
        from PySide6.QtWidgets import QApplication

        log.info("cerrando el nodo")
        self.node.stop()
        QApplication.quit()


def run(**options: Any) -> int:
    """Start the desktop shell. Returns a process exit code."""
    try:
        from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
    except ImportError:
        from nullcell.desktop import MISSING_QT

        print(MISSING_QT, file=sys.stderr)
        return 2

    from nullcell.desktop import scheme

    # Must happen before QApplication exists, or Chromium never sees it.
    scheme.register()

    app = QApplication(sys.argv)
    app.setApplicationName("Nullcell")
    app.setQuitOnLastWindowClosed(False)  # closing the panel is not quitting

    node = NodeThread(**options)
    shell = Shell(node)

    try:
        url = node.start()
    except Exception as exc:  # noqa: BLE001
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(None, "Nullcell", f"El nodo no arrancó:\n\n{exc}")
        return 1

    shell.panel = _build_panel(node, shell)
    from PySide6.QtCore import QUrl

    shell.panel.view.load(QUrl(url))

    tray = QSystemTrayIcon(_icon())
    tray.setToolTip(f"Nullcell · {node.identity.short}")
    menu = QMenu()
    menu.addAction("Panel", shell.toggle_panel)
    menu.addAction("Navegador", shell.open_browser)
    menu.addSeparator()
    menu.addAction("Salir", shell.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(shell.toggle_panel)
    tray.show()
    shell.tray = tray

    return app.exec()
