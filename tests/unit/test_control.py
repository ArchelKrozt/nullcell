"""The actions the panel can take — §9, the control half.

Everything here is reachable over loopback by a page in a browser, so each
test names the attack it blocks. The read-only HUD could afford a cookie; a
switch that turns this cell into a public relay in your name cannot.
"""

from __future__ import annotations

import pathlib

import pytest
from aiohttp.test_utils import TestClient, TestServer

from nullcell.gateway.control import ControlError, ControlPanel
from nullcell.gateway.events import EventBus
from nullcell.gateway.localui import TOKEN_HEADER, LocalUI


class FakePaths:
    def __init__(self, ready: bool = True, *, ready_in: float | None = None) -> None:
        self.ready = ready
        self.ready_in = ready_in

    def why_not_ready(self) -> str:
        return "" if self.ready else "hacen falta 3 relevos usables (§7)"

    def missing(self) -> dict:
        return {
            "known": 3 if self.ready else 0,
            "usable": 3 if self.ready else 0,
            "needed": 3,
            "operators": 3 if self.ready else 0,
            "waiting": 0,
            "ready_in": 0.0 if self.ready else self.ready_in,
            "why": self.why_not_ready(),
        }

    def build(self, **kwargs):
        return ["hop1", "hop2", "hop3"]


class FakePublisher:
    ttl = 3600

    def __init__(self) -> None:
        self.published: list[list[str]] = []

    async def publish_relay(self, endpoints):
        self.published.append(list(endpoints))
        return 7

    async def renew_forever(self, *, endpoints=None, **kwargs):
        import asyncio

        while True:  # pragma: no cover - cancelled by the test
            await asyncio.sleep(3600)


class FakeNode:
    def __init__(self, endpoints=None) -> None:
        self.endpoints = endpoints if endpoints is not None else []


class FakeIdentity:
    nid = "a" * 56
    hostname = "a" * 56 + ".null"
    public_key = b"\x01" * 32


class FakeDirectory:
    """Los relevos que esta celda conoce. Vacío salvo que un test diga otra cosa."""

    def __init__(self, relays=None) -> None:
        self._relays = relays or []

    def candidates(self, **kwargs):
        return list(self._relays)


class FakeRuntime:
    def __init__(self, *, endpoints=None, ready: bool = True, relays=None) -> None:
        self.directory = FakeDirectory(relays)
        self.node = FakeNode(endpoints)
        self.publisher = FakePublisher()
        self.paths = FakePaths(ready)
        self.identity = FakeIdentity()
        self.tasks: list = []

    def spawn(self, coroutine):
        import asyncio

        task = asyncio.ensure_future(coroutine)
        self.tasks.append(task)
        return task


def panel(**kwargs) -> ControlPanel:
    import tempfile

    return ControlPanel(FakeRuntime(**kwargs), home=pathlib.Path(tempfile.mkdtemp()))


# ------------------------------------------------------------ §10: compartir
async def test_sharing_starts_off():
    assert panel(endpoints=["203.0.113.9:4433"]).sharing is False


async def test_turning_sharing_on_publishes_and_keeps_renewing():
    p = panel(endpoints=["203.0.113.9:4433"])
    result = await p.set_sharing(True)
    assert result["sharing"] and result["changed"]
    assert result["stored_on"] == 7
    assert p.sharing
    assert p.runtime.publisher.published == [["203.0.113.9:4433"]]
    await p.set_sharing(False)


async def test_a_cell_nobody_can_reach_is_refused_with_the_reason():
    """Offering bandwidth from an address nobody can dial is not a relay, and
    silently publishing a useless descriptor is worse than refusing."""
    with pytest.raises(ControlError, match="alcanzable"):
        await panel(endpoints=[]).set_sharing(True)


async def test_turning_sharing_off_admits_what_it_cannot_undo():
    """§10's honesty: switching off stops *announcing*. A descriptor already in
    the DHT stays there until its TTL runs out, and showing a tick would be a
    lie about how long you remain a relay."""
    p = panel(endpoints=["203.0.113.9:4433"])
    await p.set_sharing(True)
    result = await p.set_sharing(False)

    assert result["sharing"] is False and result["changed"]
    assert "caduca" in result["detail"]
    assert "60 min" in result["detail"]
    assert "vacían" in result["detail"]  # circuits drained, not cut
    assert not p.sharing


async def test_switching_twice_the_same_way_is_not_a_change():
    p = panel(endpoints=["203.0.113.9:4433"])
    await p.set_sharing(True)
    again = await p.set_sharing(True)
    assert again["changed"] is False
    await p.set_sharing(False)


# ----------------------------------------------------------------- portales
async def test_publishing_a_folder_that_is_not_there_is_refused(tmp_path):
    with pytest.raises(ControlError, match="no es una carpeta"):
        await panel().publish_folder(tmp_path / "no-existe")


async def test_publishing_an_empty_folder_is_refused(tmp_path):
    with pytest.raises(ControlError, match="vacía"):
        await panel().publish_folder(tmp_path)


async def test_a_folder_without_an_index_is_published_with_a_warning(tmp_path):
    (tmp_path / "datos.txt").write_text("hola")
    result = await panel().publish_folder(tmp_path)
    assert result["warning"] == "la carpeta no tiene index.html"
    assert result["address"].endswith(".null")


async def test_publishing_without_routes_still_serves_it_locally(tmp_path):
    """The behaviour this replaced: publishing used to be refused outright
    until the network was healthy, which meant that on the day you install
    Nullcell the button did nothing.

    §7 genuinely cannot build a circuit from one node, and pretending
    otherwise would be the one unforgivable lie here. So the portal is served
    on loopback and **labelled as local** — the same aiohttp application the
    real portal serves, so the work is useful before there is anybody to show
    it to, and it goes live by itself later.
    """
    (tmp_path / "index.html").write_text("<h1>hola</h1>")
    p = panel(ready=False)
    result = await p.publish_folder(tmp_path)

    assert result["state"] == "local"
    assert result["preview"].startswith("http://127.0.0.1:")
    assert result["address"].endswith(".null")
    await p.unpublish()


async def test_a_local_portal_really_serves_the_folder(tmp_path):
    """Not a mock-up: the preview must actually answer, or it is a label."""
    import aiohttp

    (tmp_path / "index.html").write_text("<h1>pan</h1>")
    p = panel(ready=False)
    result = await p.publish_folder(tmp_path)
    try:
        async with aiohttp.ClientSession() as http, http.get(result["preview"]) as response:
            assert response.status == 200
            assert "pan" in await response.text()
    finally:
        await p.unpublish()


async def test_the_intent_survives_a_restart(tmp_path):
    """A desktop that forgets what you asked for is the command line with
    extra steps."""
    folder = tmp_path / "sitio"
    folder.mkdir()
    (folder / "index.html").write_text("<h1>hola</h1>")
    home = tmp_path / "home"

    first = ControlPanel(FakeRuntime(endpoints=["203.0.113.9:4433"], ready=False), home=home)
    await first.publish_folder(folder)
    await first.set_sharing(True)
    await first.unpublish(save=False)  # cierra el servidor, no olvida la intención

    second = ControlPanel(FakeRuntime(endpoints=["203.0.113.9:4433"], ready=False), home=home)
    await second.restore()
    try:
        assert second.wants_sharing is True
        assert second.portal is not None
        assert second.portal["folder"] == str(folder)
    finally:
        await second.unpublish()


async def test_a_corrupt_state_file_starts_empty_instead_of_failing(tmp_path):
    """Refusing to start because a preferences file is broken punishes the
    operator for something the program did."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "desktop.json").write_text("{esto no es json")
    p = ControlPanel(FakeRuntime(endpoints=["203.0.113.9:4433"]), home=home)
    await p.restore()
    assert p.portal is None and p.wants_sharing is False


# ------------------------------------------------- §9: quién puede accionar
@pytest.fixture
async def client():
    runtime = FakeRuntime(endpoints=["203.0.113.9:4433"])
    import pathlib

    ui = LocalUI(
        lambda: {"nid": "a" * 56, "phase": "listo", "peers": 1, "circuits": []},
        EventBus(),
        port=9180,
        control=ControlPanel(runtime, home=pathlib.Path("/tmp/nullcell-test")),
    )
    async with TestClient(TestServer(ui.build_app())) as test_client:
        test_client.ui = ui  # type: ignore[attr-defined]
        yield test_client


def loopback() -> dict[str, str]:
    return {"Host": "127.0.0.1:9180"}


async def test_a_switch_cannot_be_flipped_with_only_a_cookie(client):
    """The attack this exists for: a hostile page submits a cross-site form to
    127.0.0.1:9180 and the browser attaches our session cookie. It cannot set
    a header, so a mutation asks for the one thing it cannot forge."""
    response = await client.post(
        "/api/control/sharing",
        json={"on": True},
        headers=loopback(),
        cookies={"ncell_session": client.ui.token},
    )
    assert response.status == 403
    assert "header" in await response.text()


async def test_a_switch_cannot_be_flipped_with_a_token_in_the_url(client):
    """Same reasoning: a URL travels in links, history and Referer."""
    response = await client.post(
        "/api/control/sharing",
        json={"on": True},
        headers=loopback(),
        params={"token": client.ui.token},
    )
    assert response.status == 403


async def test_reading_the_panel_still_works_with_a_cookie(client):
    """Reads keep the cookie, or a normal page could not hold a session."""
    response = await client.get(
        "/api/control", headers=loopback(), cookies={"ncell_session": client.ui.token}
    )
    assert response.status == 200
    assert (await response.json())["sharing"] is False


async def test_the_header_token_can_flip_the_switch(client):
    response = await client.post(
        "/api/control/sharing",
        json={"on": True},
        headers={**loopback(), TOKEN_HEADER: client.ui.token},
    )
    assert response.status == 200
    body = await response.json()
    assert body["sharing"] and body["stored_on"] == 7

    off = await client.post(
        "/api/control/sharing",
        json={"on": False},
        headers={**loopback(), TOKEN_HEADER: client.ui.token},
    )
    assert (await off.json())["sharing"] is False


async def test_a_foreign_host_still_cannot_reach_a_control_route(client):
    """DNS rebinding has to be blocked before the header check, not after."""
    response = await client.post(
        "/api/control/sharing",
        json={"on": True},
        headers={"Host": "evil.example.com:9180", TOKEN_HEADER: client.ui.token},
    )
    assert response.status == 403


async def test_a_missing_body_is_a_400_not_a_crash(client):
    response = await client.post(
        "/api/control/sharing",
        data=b"no-es-json",
        headers={**loopback(), TOKEN_HEADER: client.ui.token},
    )
    assert response.status == 400


async def test_a_read_only_node_answers_503():
    ui = LocalUI(lambda: {}, EventBus(), port=9180)  # no control panel
    async with TestClient(TestServer(ui.build_app())) as plain:
        response = await plain.post(
            "/api/control/sharing",
            json={"on": True},
            headers={"Host": "127.0.0.1:9180", TOKEN_HEADER: ui.token},
        )
        assert response.status == 503


# ------------------------------------------------- M9: el panel del escritorio
PANEL_ROOT = __import__("pathlib").Path(
    __import__("nullcell.desktop.node_thread", fromlist=["PANEL_ROOT"]).PANEL_ROOT
)


@pytest.fixture
async def panel_client():
    import pathlib

    runtime = FakeRuntime(endpoints=["203.0.113.9:4433"])
    ui = LocalUI(
        lambda: {"nid": "a" * 56, "phase": "listo", "peers": 1, "circuits": []},
        EventBus(),
        port=9180,
        control=ControlPanel(runtime, home=pathlib.Path("/tmp/nullcell-test")),
        panel_root=PANEL_ROOT,
    )
    async with TestClient(TestServer(ui.build_app())) as test_client:
        test_client.ui = ui  # type: ignore[attr-defined]
        yield test_client


def test_the_panel_markup_has_no_inline_script():
    """§9's CSP is `script-src 'self'`, and it **refused** the first version of
    this panel, which injected the token in an inline `<script>`. Caught by
    running it, not by a test. Loosening the CSP to `unsafe-inline` would have
    traded a real defence for the convenience of one line."""
    markup = (PANEL_ROOT / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in markup.replace(" ", "")
    assert 'src="/panel/config.js"' in markup


def test_the_panel_assets_are_referenced_by_absolute_path():
    """Served at `/panel`, a relative `panel.js` resolves to `/panel.js` — so
    the stylesheet and the script silently 404 and the panel renders naked."""
    markup = (PANEL_ROOT / "index.html").read_text(encoding="utf-8")
    for reference in ('href="/panel/panel.css"', 'src="/panel/panel.js"'):
        assert reference in markup, f"falta {reference}"


async def test_the_token_arrives_as_a_served_script(panel_client):
    response = await panel_client.get(
        "/panel/config.js", headers=loopback(), params={"token": panel_client.ui.token}
    )
    assert response.status == 200
    assert "javascript" in response.headers["Content-Type"]
    assert panel_client.ui.token in await response.text()


async def test_the_panel_files_cannot_escape_their_directory(panel_client):
    """The same check as the HUD's static route, and it is the same code on
    purpose: two copies is how one of them stops refusing `..`."""
    for name in ("../pyproject.toml", "..%2Fpyproject.toml", ".hidden"):
        response = await panel_client.get(
            f"/panel/{name}", headers=loopback(), params={"token": panel_client.ui.token}
        )
        assert response.status == 404, f"sirvió {name!r}"


async def test_a_node_without_a_panel_root_serves_no_panel(client):
    response = await client.get("/panel", headers=loopback(), params={"token": client.ui.token})
    assert response.status == 404


# ------------------------------------- M9: alcanzable por otro, o no se ofrece
def test_a_loopback_only_cell_is_not_offered_the_relay_switch():
    """The bug: the desktop announced 127.0.0.1 and the panel said you could
    share. Flipping it would publish a relay pointing at your own loopback —
    an unreachable entry in everybody else's directory."""
    p = panel(endpoints=["127.0.0.1:4433"])
    state = p.describe()
    assert state["can_share"] is False
    assert state["reach"] == "loopback"
    assert state["seed"] is None, "una semilla a loopback no le sirve a nadie"


async def test_sharing_from_loopback_is_refused_with_the_reason():
    p = panel(endpoints=["127.0.0.1:4433"])
    with pytest.raises(ControlError, match="alcanzable"):
        await p.set_sharing(True)


def test_a_lan_cell_can_share_and_is_told_what_that_means():
    """A LAN is a real network — the machines in one building — so the switch
    works; it just is not the internet, and the panel says which."""
    p = panel(endpoints=["192.168.1.20:4433"])
    state = p.describe()
    assert state["can_share"] is True
    assert state["reach"] == "lan"
    assert "local" in state["reach_detail"]
    assert state["seed"].endswith("@192.168.1.20:4433")


def test_the_furthest_endpoint_decides_how_far_the_cell_reaches():
    p = panel(endpoints=["127.0.0.1:4433", "8.8.8.8:4433"])
    assert p.describe()["reach"] == "internet"


# ------------------------------- M9: visitar tu propio portal sin red (§8)
async def test_your_own_portal_is_reachable_without_a_circuit(tmp_path):
    """Visiting your own site needs no circuit: you are both ends, so there is
    no third party and nothing to anonymise.

    Without this, typing your own name on a one-node network answered «ese
    nombre no existe en la red» — about a portal running on that very machine.
    """
    (tmp_path / "index.html").write_text("<h1>hola</h1>")
    p = panel(ready=False)
    await p.publish_folder(tmp_path, name="nullcell")
    try:
        address = p.runtime.identity.hostname
        for asked in ("nullcell", "nullcell.null", "NULLCELL.null", address, address[:-5]):
            assert p.local_url_for(asked), f"no reconoció {asked!r} como propio"
    finally:
        await p.unpublish()


async def test_the_shortcut_does_not_apply_to_anybody_else(tmp_path):
    """The whole safety of it. Any other host must go through the circuits, or
    this becomes a way to serve something local under somebody else's name."""
    (tmp_path / "index.html").write_text("<h1>hola</h1>")
    p = panel(ready=False)
    await p.publish_folder(tmp_path, name="nullcell")
    try:
        for ajeno in ("panaderia.null", "b" * 56 + ".null", "nullcell.example.com", ""):
            assert p.local_url_for(ajeno) is None, f"atajo aplicado a {ajeno!r}"
    finally:
        await p.unpublish()


async def test_without_a_portal_there_is_nothing_local_to_serve():
    assert panel().local_url_for("nullcell") is None


# ----------------------------------- M10: el escritorio y la decisión 8 (§7)
async def test_the_desktop_switches_to_circuits_when_it_can(tmp_path):
    """La misma decisión 8 sin conectar, por otro camino.

    Los comandos pasan por `_runtime(anonymous=True)`; el escritorio construye
    su Runtime a mano, así que su DHT se quedaba en directo para siempre — y
    el propio tablero lo mostraba como «dht: directo» sin que eso llamara la
    atención de nadie.
    """
    from nullcell.cli.runtime import Runtime
    from nullcell.core.identity import Identity

    runtime = Runtime.assemble(Identity.generate(), tmp_path)
    # `PathBuilder.ready` es de sólo lectura, así que se sustituye el
    # constructor entero por el doble: lo que se prueba es el cableado, no la
    # selección de rutas.
    runtime.paths = FakePaths(ready=True)
    p = ControlPanel(runtime, home=tmp_path)

    assert not runtime.anonymous
    await p.reconcile()
    assert runtime.anonymous, "el escritorio siguió consultando el DHT en claro"
    await runtime.close()


async def test_going_anonymous_happens_once(tmp_path):
    from nullcell.cli.runtime import Runtime
    from nullcell.core.identity import Identity

    runtime = Runtime.assemble(Identity.generate(), tmp_path)
    runtime.paths = FakePaths(ready=True)
    p = ControlPanel(runtime, home=tmp_path)
    await p.reconcile()
    first = runtime.node.transport
    await p.reconcile()
    assert runtime.node.transport is first, "se reconstruyó el transporte cada ciclo"
    await runtime.close()


# -------------------------------------------------- M10: chat en el panel
async def test_the_chat_identity_is_not_the_node_identity(tmp_path):
    """La pertenencia a un canal es pública, así que si el chat hablara con la
    identidad del nodo, ver tu apodo en #general revelaría que eres quien
    publica tu portal."""
    p = panel()
    p.home = tmp_path
    assert p.chat_identity.public_key != p.runtime.identity.public_key
    assert (tmp_path / "chat.key").is_file()


async def test_you_can_join_and_talk_before_there_is_any_network(tmp_path):
    """Como el portal: se puede usar, y dice que todavía no llega a nadie."""
    p = panel(ready=False)
    p.home = tmp_path
    state = await p.join("#general", "Diego")
    try:
        assert state["channel"] == "general"
        await p.say("hola")
        full = p.chat_state()
        assert [m["text"] for m in full["messages"]] == ["hola"]
        assert full["ready"] is False
        assert full["why_not_ready"]
    finally:
        await p.chat.close()


async def test_speaking_without_a_channel_is_refused():
    with pytest.raises(ControlError, match="ningún canal"):
        await panel().say("hola")
