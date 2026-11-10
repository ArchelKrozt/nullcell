"""How the browser looks — §9's skin on Qt's widgets.

The address bar is the whole interface, so it carries the one distinction a
browser for this network has to make and an ordinary one does not: whether what
you are looking at is **a portal of the network** or **this node's own panel**.
Both are legitimate and they are not the same thing, and a single grey bar that
treats them alike is how someone ends up believing a local page is public.
"""

from __future__ import annotations

from typing import Final

#: The HUD's tokens, as a Qt stylesheet. Same palette, declared once there and
#: mirrored here because Qt widgets are not styled by the page's CSS.
STYLE: Final[str] = """
QWidget { background: #07090d; color: #e6edf3;
          font-family: "SF Mono", Menlo, monospace; font-size: 12px; }
QLineEdit {
  background: #0d1117; border: 1px solid #1d2733; border-radius: 3px;
  padding: 6px 10px; color: #e6edf3; selection-background-color: #22d3ee;
  selection-color: #07090d;
}
QLineEdit:focus { border-color: #22d3ee; }
QPushButton {
  background: #121922; border: 1px solid #1d2733; border-radius: 3px;
  padding: 5px 10px; color: #e6edf3; min-width: 28px;
}
QPushButton:hover { border-color: #22d3ee; color: #22d3ee; }
QPushButton:disabled { color: #7d8da3; border-color: #121922; }
QLabel#kind {
  padding: 3px 9px; border: 1px solid #1d2733; border-radius: 10px;
  color: #7d8da3; font-size: 10px;
}
QLabel#kind[kind="portal"] { color: #34d399; border-color: #34d399; }
QLabel#kind[kind="local"]  { color: #22d3ee; border-color: #22d3ee; }
QLabel#status { color: #7d8da3; font-size: 10px; padding: 3px 10px; }
"""


def is_destination(text: str) -> str | None:
    """The portal *text* names, or None if it should be searched for instead.

    `ids.parse_hostname` only strips the suffix and lowercases — it does not
    validate — so using it alone to decide made the address bar treat
    "recetas de pan" as a portal and left the search box as dead code.

    Two things count as naming a portal, and nothing else does:

    * an explicit `.null` suffix, which is somebody saying where they want to
      go rather than what they want to find;
    * a bare 56-character address, which cannot be mistaken for words.
    """
    from nullcell.core import ids

    cleaned = text.strip().rstrip(".")
    if not cleaned or " " in cleaned:
        return None

    bare = ids.parse_hostname(cleaned)
    if cleaned.lower().endswith((ids.SUFFIX_ALT, ids.SUFFIX)):
        return bare or None
    try:
        ids.decode_nid(bare)
    except Exception:  # noqa: BLE001 - cualquier fallo significa "no es una dirección"
        return None
    return bare


def kind_of(url: str) -> tuple[str, str]:
    """What the address bar should say about *url*: (kind, label)."""
    if url.startswith("null://"):
        return "portal", "portal de la red"
    if url.startswith(("http://127.0.0.1", "http://localhost")):
        return "local", "este nodo"
    return "otro", "fuera de la red"
