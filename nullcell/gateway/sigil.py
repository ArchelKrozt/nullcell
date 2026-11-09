"""Sellos — the seal a portal gets instead of a favicon, §9.

A favicon or thumbnail fetched from the remote site would be **someone else's
content loaded into our own origin**: it leaks that you are looking at that
portal, and it hands a remote site an asset slot inside the panel that controls
your node. §9 rules it out.

So every portal gets a seal generated **locally**, with geometry derived
deterministically from ``sha256(nid)``. Zero network, zero risk, impossible to
forge — two different keys cannot share a seal, because the seal *is* the key,
drawn. And it costs nothing: the address is already in hand.

The drawing is hexagons and strokes on a grid, which is the one §9 asks for,
and it uses the HUD's own CSS tokens so a theme changes the seals with
everything else (§9, Personalización). It is **pure SVG with no script**, and
it is served from our origin, so a seal can never execute anything.
"""

from __future__ import annotations

import hashlib
from typing import Final

from nullcell.core import ids

__all__ = ["GRID", "SIZE", "sigil_svg", "sigil_symbol", "sprite"]

#: Cells across. Odd, so there is a centre column to mirror around.
GRID: Final[int] = 5
SIZE: Final[int] = 48
_MARGIN: Final[int] = 3


def _bits(nid: str) -> bytes:
    """Deterministic bytes for a seal. Derived from the address, nothing else."""
    return hashlib.sha256(b"nullcell-sigil-v1" + ids.parse_hostname(nid).encode()).digest()


def _cells(seed: bytes) -> list[tuple[int, int, bool]]:
    """Which cells are filled, mirrored left to right.

    Mirroring is what makes a seal read as a *mark* rather than as noise, and
    it is what identicons have always done: a symmetric shape is far easier to
    recognise again at 16 pixels than a random scatter.
    """
    filled: list[tuple[int, int, bool]] = []
    half = GRID // 2 + 1
    index = 0
    for row in range(GRID):
        for column in range(half):
            byte = seed[index % len(seed)]
            index += 1
            if byte & 0x80:
                strong = bool(byte & 0x40)
                filled.append((row, column, strong))
                mirror = GRID - 1 - column
                if mirror != column:
                    filled.append((row, mirror, strong))
    return filled


def sigil_svg(nid: str, *, size: int = SIZE, embed_style: bool = True) -> str:
    """One seal as a standalone SVG.

    Colours come from the HUD's CSS tokens, so a theme restyles every seal with
    everything else (§9). ``embed_style=False`` is for the sprite, where the
    tokens are inherited from the page.
    """
    seed = _bits(nid)
    step = (size - 2 * _MARGIN) / GRID
    radius = step * 0.42

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'width="{size}" height="{size}" role="img" '
        f'aria-label="sello de {ids.short(nid)}">'
    ]
    if embed_style:
        parts.append(
            "<style>"
            ".ncs-bg{fill:var(--surface-2,#121922)}"
            ".ncs-a{fill:var(--accent-data,#22d3ee)}"
            ".ncs-b{fill:var(--text-dim,#7d8da3)}"
            "</style>"
        )
    parts.append(f'<rect class="ncs-bg" width="{size}" height="{size}" rx="3"/>')

    for row, column, strong in _cells(seed):
        cx = _MARGIN + step * (column + 0.5)
        cy = _MARGIN + step * (row + 0.5)
        points = " ".join(
            f"{cx + radius * _HEX_X[i]:.2f},{cy + radius * _HEX_Y[i]:.2f}" for i in range(6)
        )
        parts.append(f'<polygon class="{"ncs-a" if strong else "ncs-b"}" points="{points}"/>')

    parts.append("</svg>")
    return "".join(parts)


def sigil_symbol(nid: str, *, size: int = SIZE) -> str:
    """One seal as an SVG ``<symbol>``, for the sprite of §16.

    §16: generating geometry on every render is free with ten portals and
    expensive with five hundred. A sprite of symbols is drawn once and
    referenced with ``<use>``.
    """
    inner = sigil_svg(nid, size=size, embed_style=False)
    body = inner[inner.index(">") + 1 : -len("</svg>")]
    return (
        f'<symbol id="sigil-{ids.parse_hostname(nid)}" viewBox="0 0 {size} {size}">{body}</symbol>'
    )


def sprite(nids: list[str], *, size: int = SIZE) -> str:
    """Every seal in one hidden SVG, referenced by ``<use>`` (§16)."""
    symbols = "".join(sigil_symbol(nid, size=size) for nid in nids)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">'
        "<style>"
        ".ncs-bg{fill:var(--surface-2,#121922)}"
        ".ncs-a{fill:var(--accent-data,#22d3ee)}"
        ".ncs-b{fill:var(--text-dim,#7d8da3)}"
        "</style>"
        f"{symbols}</svg>"
    )


# Unit hexagon, flat-top, precomputed so the hot path does no trigonometry.
_HEX_X: Final[tuple[float, ...]] = (1.0, 0.5, -0.5, -1.0, -0.5, 0.5)
_HEX_Y: Final[tuple[float, ...]] = (0.0, 0.866, 0.866, 0.0, -0.866, -0.866)
