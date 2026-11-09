"""The desktop surface — §9 without a terminal.

A command line is a bad place for a network you *live* in: it is not
persistent, it shows a wall of text instead of state, and every change of mind
means stopping the process and starting it again with a different flag. This
package is the same cell with a face: an icon in the menu bar, a panel with
switches, and a browser whose address bar understands `.null`.

Nothing here is required to run Nullcell. It is an optional extra
(`pip install nullcell[desktop]`) because a relay on a server must not have to
download a browser engine to forward frames.
"""

from __future__ import annotations

__all__ = ["MISSING_QT"]

#: Shown when PySide6 is not installed, as advice rather than a traceback.
MISSING_QT = (
    "el escritorio necesita PySide6 (núcleo Chromium):\n"
    "    pip install 'nullcell[desktop]'\n"
    "y queda en 6.11.3 a propósito: Qt dejó de publicar QtWebEngine para macOS "
    "en 6.12, así que una versión más nueva se instala sin navegador."
)
