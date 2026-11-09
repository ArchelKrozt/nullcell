"""The local UI — §9's hard rules, and §16's limits.

The security tests here are the point of the file. `127.0.0.1:9180` is
reachable by any page the user has open, so each rule stops a specific attack
and each test names it.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from nullcell.gateway.events import BUFFER_SIZE, EventBus, Level, collapse, drain
from nullcell.gateway.localui import COOKIE_NAME, RESERVED_NAMES, TOKEN_HEADER, LocalUI

HUD_ROOT = Path(__file__).resolve().parents[2] / "nullcell" / "hud"


def snapshot() -> dict:
    return {"nid": "a" * 56, "phase": "listo", "peers": 7, "circuits": []}


@pytest.fixture
async def client():
    ui = LocalUI(snapshot, EventBus(), port=9180)
    server = TestServer(ui.build_app())
    async with TestClient(server) as test_client:
        test_client.ui = ui  # type: ignore[attr-defined]
        yield test_client


def loopback(port: int) -> dict[str, str]:
    return {"Host": f"127.0.0.1:{port}"}


# ----------------------------------------------------------------- §9 security
async def test_a_page_without_the_token_is_refused(client):
    """Without this, any site could call the API and publish in your name."""
    response = await client.get("/api/state", headers=loopback(9180))
    assert response.status == 401


async def test_a_wrong_token_is_refused(client):
    response = await client.get(
        "/api/state", headers={**loopback(9180), TOKEN_HEADER: "no-es-el-token"}
    )
    assert response.status == 401


async def test_the_right_token_is_accepted(client):
    response = await client.get(
        "/api/state", headers={**loopback(9180), TOKEN_HEADER: client.ui.token}
    )
    assert response.status == 200
    assert (await response.json())["state"]["peers"] == 7


async def test_a_foreign_host_header_is_refused(client):
    """DNS rebinding: a hostile site points its own name at 127.0.0.1 and the
    browser treats the result as same-origin (§9)."""
    for host in ("evil.example.com:9180", "attacker.test", "127.0.0.1:9999", ""):
        response = await client.get(
            "/api/state", headers={"Host": host, TOKEN_HEADER: client.ui.token}
        )
        assert response.status == 403, f"accepted Host: {host!r}"


async def test_loopback_literals_are_accepted(client):
    for host in (f"127.0.0.1:{9180}", f"localhost:{9180}", f"[::1]:{9180}"):
        response = await client.get(
            "/api/state", headers={"Host": host, TOKEN_HEADER: client.ui.token}
        )
        assert response.status == 200, f"refused Host: {host!r}"


async def test_csp_forbids_everything_external(client):
    """§9: one <link> to a CDN would tell the ordinary internet a node is here."""
    response = await client.get(
        "/", headers={**loopback(9180), "token": client.ui.token}, params={"token": client.ui.token}
    )
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp  # nothing embeds our origin
    assert "base-uri 'none'" in csp
    assert "https://" not in csp


async def test_the_token_moves_from_the_url_into_a_strict_cookie(client):
    response = await client.get("/", headers=loopback(9180), params={"token": client.ui.token})
    assert response.status == 200
    cookie = response.cookies[COOKIE_NAME]
    assert cookie.value == client.ui.token
    assert cookie["httponly"]
    assert cookie["samesite"].lower() == "strict"


async def test_static_files_cannot_escape_the_hud_directory(client):
    for name in ("../pyproject.toml", "..%2Fpyproject.toml", ".hidden", "a/b"):
        response = await client.get(
            f"/static/{name}", headers={**loopback(9180), TOKEN_HEADER: client.ui.token}
        )
        assert response.status == 404, f"served {name!r}"


async def test_reserved_names_are_declared():
    """§9: or somebody registers the name of your control panel in the DHT."""
    assert {"hud.null", "search.null", "me.null"} == RESERVED_NAMES


# ------------------------------------------------------------- §9 frontend rules
#: The SVG namespace is an *identifier*, not a resource: no browser ever
#: fetches it, and `createElementNS` needs it literally. Allowlisted by exact
#: string so it cannot widen into a loophole for anything else.
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"


def _external_urls(text: str) -> list[str]:
    """Every URL in *text* that a browser could actually fetch.

    Compared to the allowlist by **exact equality**, not by stripping a prefix:
    ``http://www.w3.org/2000/svg2/evil.example/x.png`` starts with the allowed
    namespace and is a completely different URL, and a prefix-stripping check
    let it through.
    """
    urls = re.findall(r"https?://[^\s\"'()<>]*", text)
    urls += re.findall(r"(?<![:\w])//[^\s\"'()<>]+", text)
    return [url for url in urls if url != _SVG_NAMESPACE]


def test_the_hud_makes_no_external_requests():
    """§9: one <link> to a CDN would tell the ordinary internet a node is here."""
    for path in HUD_ROOT.iterdir():
        found = _external_urls(path.read_text(encoding="utf-8"))
        assert not found, f"{path.name} reaches outside: {found}"


@pytest.mark.parametrize(
    "sneaky",
    [
        '<script src="https://cdn.example/x.js">',
        '<link href="http://fonts.googleapis.com/css">',
        '<img src="//cdn.example/pixel.png">',
        f'<img src="{_SVG_NAMESPACE}2/evil.example/x.png">',  # namespace-ish prefix
    ],
)
def test_the_allowlist_does_not_hide_a_real_url(sneaky):
    """The allowlist is one exact string and must not widen into a loophole."""
    assert _external_urls(sneaky), f"an external URL slipped past the check: {sneaky}"


def test_the_svg_namespace_itself_is_allowed():
    assert _external_urls(f'<svg xmlns="{_SVG_NAMESPACE}">') == []


def test_colours_live_only_in_root_tokens():
    """What makes ~/.nullcell/theme.css possible (§9, Personalización)."""
    css = (HUD_ROOT / "style.css").read_text(encoding="utf-8")
    without_root = re.sub(r":root[^{]*\{[^}]*\}", "", css)
    literals = re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(", without_root)
    assert not literals, f"literal colours outside :root: {literals}"


def test_reduced_motion_is_respected():
    css = (HUD_ROOT / "style.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css


def test_the_console_is_capped():
    """§16: an uncapped console grows the DOM without limit."""
    js = (HUD_ROOT / "app.js").read_text(encoding="utf-8")
    assert "CONSOLE_MAX = 500" in js
    assert "firstElementChild.remove()" in js


def test_nothing_is_embedded_in_an_iframe():
    """§9: a remote site inside our origin could call our own API."""
    html = (HUD_ROOT / "index.html").read_text(encoding="utf-8")
    assert "<iframe" not in html.lower()


# ------------------------------------------------------------------ event bus
def test_history_is_bounded():
    bus = EventBus(buffer_size=10)
    for i in range(50):
        bus.emit("test", f"evento {i}")
    assert len(bus) == 10
    assert bus.recent()[0]["text"] == "evento 40"


def test_repeated_events_collapse_into_a_count():
    """§16: a busy relay emits the same line thousands of times."""
    bus = EventBus()
    events = [bus.emit("relay.forward", "reenviado") for _ in range(1843)]
    folded = collapse(events)
    assert len(folded) == 1
    assert folded[0].count == 1843


def test_distinct_events_are_not_collapsed():
    bus = EventBus()
    folded = collapse([bus.emit("a", "x"), bus.emit("b", "y"), bus.emit("a", "x")])
    assert [e.kind for e in folded] == ["a", "b", "a"]


def test_a_slow_subscriber_loses_events_instead_of_stalling_the_protocol():
    bus = EventBus()
    bus.subscribe()  # never drained
    for i in range(500):
        bus.emit("flood", f"{i}")
    assert bus.dropped > 0
    assert len(bus) == min(500, BUFFER_SIZE)


async def test_drain_batches_what_piled_up():
    bus = EventBus()
    queue = bus.subscribe()
    for _ in range(5):
        bus.emit("x", "same")
    batch = await drain(queue, period=0.01)
    assert len(batch) == 1
    assert batch[0].count == 5


def test_levels_round_trip_as_strings():
    bus = EventBus()
    event = bus.emit("k", "t", level=Level.WARN, extra=1)
    assert event.as_dict()["level"] == "warn"
    assert event.as_dict()["data"] == {"extra": 1}


async def test_the_ui_starts_and_stops():
    ui = LocalUI(snapshot, EventBus(), port=0)
    url = await ui.start()
    assert "token=" in url
    await ui.close()
    assert isinstance(web.Application(), web.Application)  # sanity
    await asyncio.sleep(0)


def test_collapse_does_not_touch_the_events_it_is_given():
    """The events handed to a subscriber are the ones the bus keeps in its
    history. Folding in place corrupted that history for every other reader,
    and folding the same run twice double-counted it."""
    bus = EventBus()
    events = [bus.emit("relay.forward", "reenviado") for _ in range(5)]

    folded = collapse(events)
    assert folded[0].count == 5
    assert [e.count for e in events] == [1] * 5, "collapse mutated its input"

    again = collapse(events)
    assert again[0].count == 5, "folding twice double-counted"

    # And the bus's own history is intact and folds to the same thing.
    assert bus.recent()[0]["count"] == 5


def test_history_and_live_push_fold_the_same_way():
    bus = EventBus()
    for _ in range(4):
        bus.emit("relay.forward", "reenviado")
    history = bus.recent()
    assert len(history) == 1
    assert history[0]["count"] == 4
