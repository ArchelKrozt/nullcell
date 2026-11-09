"""Sellos — §9, identity without touching the network."""

from __future__ import annotations

import re

import pytest

from nullcell.core.identity import Identity
from nullcell.gateway.sigil import GRID, sigil_svg, sigil_symbol, sprite


@pytest.fixture
def nids() -> list[str]:
    return [Identity.generate().nid for _ in range(6)]


def test_a_seal_is_deterministic(nids):
    assert sigil_svg(nids[0]) == sigil_svg(nids[0])


def test_two_keys_never_share_a_seal(nids):
    """§9: the seal *is* the key, drawn, so it cannot be forged."""
    assert len({sigil_svg(nid) for nid in nids}) == len(nids)


def test_the_bare_nid_and_the_hostname_give_the_same_seal(nids):
    assert sigil_svg(nids[0]) == sigil_svg(nids[0] + ".null")


def test_a_seal_carries_no_script(nids):
    """It is served from our own origin, so it must be unable to execute."""
    svg = sigil_svg(nids[0])
    for dangerous in ("<script", "onload", "onerror", "javascript:", "<foreignObject"):
        assert dangerous not in svg.lower()


def test_a_seal_makes_no_request(nids):
    """§9 refuses remote favicons precisely so nothing is fetched."""
    svg = sigil_svg(nids[0])
    assert "href" not in svg
    assert "http" not in svg.replace('xmlns="http://www.w3.org/2000/svg"', "")


def test_a_seal_uses_the_theme_tokens(nids):
    """So a theme restyles every seal with everything else (§9)."""
    svg = sigil_svg(nids[0])
    assert "var(--accent-data" in svg
    assert "var(--surface-2" in svg


def test_a_seal_is_symmetric(nids):
    """Mirrored so it reads as a mark rather than noise at 16 pixels."""
    from nullcell.gateway.sigil import _bits, _cells

    for nid in nids:
        rows: dict[int, set[int]] = {}
        for row, column, _strong in _cells(_bits(nid)):
            rows.setdefault(row, set()).add(column)
        for columns in rows.values():
            for column in columns:
                assert (GRID - 1 - column) in columns


def test_a_seal_is_not_empty(nids):
    for nid in nids:
        assert sigil_svg(nid).count("<polygon") >= 1


def test_a_sprite_holds_one_symbol_per_portal(nids):
    """§16: generated once and referenced with <use>, not redrawn per render."""
    svg = sprite(nids)
    assert svg.count("<symbol") == len(nids)
    for nid in nids:
        assert f'id="sigil-{nid}"' in svg
    assert "display:none" in svg


def test_a_symbol_has_no_nested_svg(nids):
    """A <symbol> containing an <svg> would not render through <use>."""
    symbol = sigil_symbol(nids[0])
    assert symbol.startswith("<symbol")
    assert "<svg" not in symbol


def test_the_sprite_defines_the_tokens_once(nids):
    svg = sprite(nids)
    assert svg.count("<style>") == 1


def test_a_seal_scales(nids):
    small = sigil_svg(nids[0], size=16)
    assert 'width="16"' in small
    assert re.search(r'viewBox="0 0 16 16"', small)
