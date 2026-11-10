"""Un despliegue que se puede mudar — §8, §12.

The point of the folder is that moving from a laptop to a Raspberry Pi is a
copy. Every test here is a way that silently stops being true.
"""

from __future__ import annotations

import json
import stat
import sys

import pytest

from nullcell.desktop.failures import explain
from nullcell.host.deployment import Deployment, Portal


def write(root, body):
    """A JSON declaration. Named explicitly: the preferred name is TOML now,
    and writing JSON into a `.toml` file is a test that lies about itself."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "nullcell.json").write_text(json.dumps(body), encoding="utf-8")
    return root


def write_toml(root, text):
    root.mkdir(parents=True, exist_ok=True)
    (root / "nullcell.toml").write_text(text, encoding="utf-8")
    return root


# --------------------------------------------------------------------- cargar
def test_a_folder_without_a_config_is_an_empty_deployment(tmp_path):
    """Not an error: it is how a deployment starts."""
    deployment = Deployment.load(tmp_path)
    assert deployment.portals == []
    assert deployment.sharing is False


def test_a_portal_is_read_with_its_name(tmp_path):
    write(tmp_path, {"portals": [{"folder": "sitios/web", "name": "nullcell"}]})
    deployment = Deployment.load(tmp_path)
    assert [(p.name, p.folder) for p in deployment.portals] == [("nullcell", "sitios/web")]


def test_a_relative_folder_resolves_against_the_deployment(tmp_path):
    """What makes the folder portable: the path travels with it."""
    write(tmp_path, {"portals": [{"folder": "sitios/web"}]})
    deployment = Deployment.load(tmp_path)
    assert deployment.folder_for(deployment.portals[0]) == tmp_path / "sitios" / "web"


def test_an_absolute_folder_is_kept_but_reported(tmp_path):
    """`/Users/alguien/sitio` stops existing the moment the folder reaches the
    Raspberry Pi, and the failure arrives as an empty portal rather than as an
    error about a path."""
    site = tmp_path / "fuera"
    site.mkdir()
    (site / "index.html").write_text("<h1>hola</h1>")
    write(tmp_path, {"portals": [{"folder": str(site)}]})
    deployment = Deployment.load(tmp_path)

    assert deployment.folder_for(deployment.portals[0]) == site
    assert any("absoluta" in w for w in deployment.warnings())


def test_a_broken_config_says_which_file_and_why(tmp_path):
    (tmp_path / "nullcell.json").write_text("{esto no es json")
    with pytest.raises(ValueError, match="nullcell.json"):
        Deployment.load(tmp_path)


def test_a_broken_toml_also_says_which_file(tmp_path):
    write_toml(tmp_path, "esto [no es toml")
    with pytest.raises(ValueError, match="nullcell.toml"):
        Deployment.load(tmp_path)


def test_a_config_from_the_future_refuses_rather_than_guesses(tmp_path):
    write(tmp_path, {"version": 99, "portals": []})
    with pytest.raises(ValueError, match="actualiza nullcell"):
        Deployment.load(tmp_path)


def test_entries_that_make_no_sense_are_skipped_not_fatal(tmp_path):
    write(tmp_path, {"portals": [{"name": "sin-carpeta"}, {"folder": "web"}, "basura"]})
    deployment = Deployment.load(tmp_path)
    assert [p.folder for p in deployment.portals] == ["web"]


# -------------------------------------------------------------------- guardar
def test_a_declaration_round_trips(tmp_path):
    deployment = Deployment(root=tmp_path)
    deployment.write_declaration(portals=[Portal(folder="sitios/web", name="nullcell", title="T")])
    again = Deployment.load(tmp_path)
    assert again.portals[0].name == "nullcell"
    assert again.portals[0].title == "T"


def test_save_writes_the_state_and_only_the_state(tmp_path):
    """`save()` is for what the program owns. The declaration is the
    operator's and is never touched by it."""
    write(tmp_path, {"portals": [{"folder": "web"}]})
    before = (tmp_path / "nullcell.json").read_text(encoding="utf-8")

    deployment = Deployment.load(tmp_path)
    deployment.sharing = True
    deployment.save()

    assert (tmp_path / "nullcell.json").read_text(encoding="utf-8") == before
    assert Deployment.load(tmp_path).sharing is True


def test_what_is_written_stays_readable_by_a_human(tmp_path):
    """People edit this file by hand, so it is indented and not escaped."""
    deployment = Deployment(root=tmp_path)
    written = deployment.write_declaration(portals=[Portal(folder="web", title="La Panadería")])
    text = written.read_text(encoding="utf-8")
    assert "\n  " in text
    assert "Panadería" in text, "ensure_ascii destrozaría los acentos"


# ------------------------------------------------------ la dirección ES la clave
def test_a_deployment_without_an_identity_warns_that_the_address_will_change(tmp_path):
    """The one misunderstanding that costs a name: `.null` is not a domain
    somebody rents you, it is a key. Copy the folder without the key and the
    new machine is a different cell."""
    write(tmp_path, {"portals": []})
    assert any("OTRA dirección" in w for w in Deployment.load(tmp_path).warnings())


def test_adopting_an_identity_writes_it_private(tmp_path):
    source = tmp_path / "identity.key"
    source.write_bytes(b"x" * 48)
    deployment = Deployment(root=tmp_path / "despliegue")
    target = deployment.adopt_identity(source)

    assert target.read_bytes() == b"x" * 48
    assert Deployment.load(deployment.root).identity_path == target
    if sys.platform != "win32":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="permisos POSIX")
def test_a_loose_key_is_reported(tmp_path):
    key = tmp_path / "identity.key"
    key.write_bytes(b"x" * 48)
    key.chmod(0o644)
    write(tmp_path, {"portals": []})
    assert any("0600" in w for w in Deployment.load(tmp_path).warnings())


def test_a_missing_folder_is_reported_before_anything_is_published(tmp_path):
    write(tmp_path, {"portals": [{"folder": "no-existe", "name": "x"}]})
    assert any("no existe la carpeta" in w for w in Deployment.load(tmp_path).warnings())


# ------------------------------------------------- qué ve quien visita (§9)
def test_an_unregistered_name_and_an_offline_portal_are_not_the_same_answer():
    """They look alike from outside and mean opposite things: one is nothing to
    wait for, the other comes back by itself. A generic error says neither."""
    missing, _ = explain(Exception("panaderia.null is not registered in the directory"), "pan")
    offline, detail = explain(
        Exception("k7f2 has no usable service descriptor: it may be offline"), "pan"
    )
    assert missing != offline
    assert "no existe" in missing
    assert "apagado" in offline
    assert "vuelve solo" in detail


def test_our_own_lack_of_network_is_not_blamed_on_the_portal():
    title, detail = explain(Exception("2 of 2 relays are usable, 3 needed"), "pan")
    assert "red suficiente" in title
    assert "de este lado" in detail


def test_an_unknown_failure_still_says_what_happened():
    title, detail = explain(Exception("algo muy raro"), "pan")
    assert detail == "algo muy raro"
    assert title


# ------------------------------- lo que pasa al guardar lo que se restauró
async def test_the_declaration_is_never_rewritten(tmp_path):
    """The design the earlier bug argued for.

    Saving used to rewrite the operator's file: `Path.relative_to` refuses to
    walk upwards, so a hand-written `../mi-web` became an absolute path — the
    program writing exactly what `warnings()` tells people not to write — and
    the title, description and tags were dropped on the way.

    Guarding each field was the small fix. The real one is that a file a person
    edits by hand, with comments in it, is not the program's to rewrite. What
    the panel toggles lives in `estado.json` instead.
    """
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from test_control import FakeRuntime

    from nullcell.gateway.control import ControlPanel

    site = tmp_path / "mi-web"
    site.mkdir()
    (site / "index.html").write_text("<h1>hola</h1>")

    declaration = """# un comentario que debe sobrevivir
version = 1
[[portals]]
folder = "../mi-web"
name = "x"
title = "La Panaderia"
tags = ["comida"]
"""
    root = write_toml(tmp_path / "despliegue", declaration)

    deployment = Deployment.load(root)
    panel = ControlPanel(
        FakeRuntime(endpoints=["192.168.1.5:4433"], ready=False),
        home=tmp_path / "home",
        deployment=deployment,
    )
    await panel.restore()
    await panel.set_sharing(True)  # algo que SÍ se guarda
    await panel.unpublish(save=False)

    assert (root / "nullcell.toml").read_text(encoding="utf-8") == declaration
    assert (root / "estado.json").is_file(), "el estado debe ir a su propio fichero"
    assert Deployment.load(root).sharing is True


# ------------------------------------------------------------------ encontrar
def test_a_deployment_is_found_without_being_told_where(tmp_path, monkeypatch):
    """Having to pass `--config` every time is a configuration file you have to
    remember the location of, which is most of the problem it was meant to
    solve."""
    write(tmp_path / "null-despliegue", {"portals": [{"folder": "web"}]})
    monkeypatch.chdir(tmp_path)
    found = Deployment.discover(home=tmp_path / "home")
    assert found is not None
    assert found.root == (tmp_path / "null-despliegue").resolve()


def test_nothing_found_is_a_real_answer(tmp_path, monkeypatch):
    """None means "nobody has set one up", and the caller is expected to ask
    rather than invent one."""
    monkeypatch.chdir(tmp_path)
    assert Deployment.discover(home=tmp_path / "home") is None


def test_a_remembered_location_is_looked_at_first(tmp_path, monkeypatch):
    far = write(tmp_path / "lejos", {"portals": [{"folder": "web", "name": "lejano"}]})
    write(tmp_path / "null-despliegue", {"portals": [{"folder": "web", "name": "cercano"}]})
    monkeypatch.chdir(tmp_path)

    home = tmp_path / "home"
    Deployment.load(far).remember_location(home)
    found = Deployment.discover(home=home)
    assert found.portals[0].name == "lejano"


def test_a_broken_declaration_is_raised_not_skipped(tmp_path, monkeypatch):
    """Quietly moving on to the next candidate would hide a typo in the file
    the operator is editing right now."""
    (tmp_path / "null-despliegue").mkdir()
    (tmp_path / "null-despliegue" / "nullcell.json").write_text("{roto")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        Deployment.discover(home=tmp_path / "home")


# ----------------------------------------------------------------------- TOML
def test_toml_is_read_with_its_comments_intact(tmp_path):
    """TOML is preferred over JSON for one concrete reason: it takes comments,
    and a configuration file that cannot explain itself ends up explained
    somewhere else that goes stale."""
    write_toml(
        tmp_path,
        '# el portal principal\nversion = 1\n[[portals]]\nfolder = "web"\nname = "pan"\n',
    )
    deployment = Deployment.load(tmp_path)
    assert deployment.portals[0].name == "pan"
    assert "# el portal principal" in deployment.config_path.read_text(encoding="utf-8")


def test_toml_wins_when_both_are_there(tmp_path):
    write(tmp_path, {"portals": [{"folder": "json", "name": "json"}]})
    write_toml(tmp_path, '[[portals]]\nfolder = "toml"\nname = "toml"\n')
    assert Deployment.load(tmp_path).portals[0].name == "toml"


def test_a_generated_declaration_can_be_read_back(tmp_path):
    deployment = Deployment(root=tmp_path)
    written = deployment.write_declaration(portals=[Portal(folder="web", name="pan")])
    assert written.name == "nullcell.json"
    assert Deployment.load(tmp_path).portals[0].name == "pan"


# ------------------------------- la barra de direcciones: portal o búsqueda
def test_only_an_explicit_name_or_a_valid_address_is_a_destination():
    """`ids.parse_hostname` only strips the suffix and lowercases — it does not
    validate — so deciding with it alone made the address bar treat «recetas de
    pan» as a portal and left the search box as dead code."""
    from nullcell.core.identity import Identity
    from nullcell.desktop.browser_chrome import is_destination

    address = Identity.generate().hostname.removesuffix(".null")

    assert is_destination("nullcell.null") == "nullcell"
    assert is_destination("HOLA.null") == "hola"
    assert is_destination(address) == address
    assert is_destination(f"{address}.null") == address

    for search in ("recetas de pan", "pan", "nullcell", "algo.com", "", "   "):
        assert is_destination(search) is None, f"{search!r} no debería ser un destino"


def test_a_corrupted_address_is_not_a_destination():
    """One wrong character fails the checksum, and a near-miss address is
    exactly what somebody would hand you on purpose."""
    from nullcell.core.identity import Identity
    from nullcell.desktop.browser_chrome import is_destination

    address = Identity.generate().hostname.removesuffix(".null")
    # Not a fixed letter: "z" + address[1:] is the *same* address once in 32,
    # and a test that passes 31 times out of 32 is worse than none.
    other = "z" if address[0] != "z" else "q"
    assert is_destination(other + address[1:]) is None
