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
from nullcell.host.deployment import CONFIG_NAME, Deployment, Portal


def write(root, body):
    root.mkdir(parents=True, exist_ok=True)
    (root / CONFIG_NAME).write_text(json.dumps(body), encoding="utf-8")
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
    (tmp_path / CONFIG_NAME).write_text("{esto no es json")
    with pytest.raises(ValueError, match=CONFIG_NAME):
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
def test_it_round_trips(tmp_path):
    deployment = Deployment(root=tmp_path, sharing=True)
    deployment.portals = [Portal(folder="sitios/web", name="nullcell", title="T")]
    deployment.save()

    again = Deployment.load(tmp_path)
    assert again.sharing is True
    assert again.portals[0].name == "nullcell"
    assert again.portals[0].title == "T"


def test_what_is_written_stays_readable_by_a_human(tmp_path):
    """People edit this file by hand, so it is indented and not escaped."""
    deployment = Deployment(root=tmp_path)
    deployment.portals = [Portal(folder="web", title="La Panadería")]
    deployment.save()
    text = deployment.config_path.read_text(encoding="utf-8")
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
async def test_saving_does_not_turn_a_relative_path_into_an_absolute_one(tmp_path):
    """The program must not write the very thing `warnings()` tells people not
    to write. `Path.relative_to` refuses to walk upwards, so a hand-written
    `../mi-web` became absolute on the first save and the folder stopped being
    portable — silently, since everything kept working on *this* machine.
    """
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from test_control import FakeRuntime

    from nullcell.gateway.control import ControlPanel

    site = tmp_path / "mi-web"
    site.mkdir()
    (site / "index.html").write_text("<h1>hola</h1>")
    root = write(tmp_path / "despliegue", {"portals": [{"folder": "../mi-web", "name": "x"}]})

    deployment = Deployment.load(root)
    panel = ControlPanel(
        FakeRuntime(endpoints=["192.168.1.5:4433"], ready=False),
        home=tmp_path / "home",
        deployment=deployment,
    )
    await panel.restore()
    panel.remember()
    await panel.unpublish(save=False)

    assert Deployment.load(root).portals[0].folder == "../mi-web"


async def test_saving_keeps_the_fields_it_does_not_know_about(tmp_path):
    """An update that does not carry a field is not an instruction to erase it
    — the same mistake that once wiped descriptor titles in gossip."""
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from test_control import FakeRuntime

    from nullcell.gateway.control import ControlPanel

    site = tmp_path / "despliegue" / "web"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<h1>hola</h1>")
    root = write(
        tmp_path / "despliegue",
        {
            "portals": [
                {
                    "folder": "web",
                    "name": "x",
                    "title": "La Panadería",
                    "desc": "pan",
                    "tags": ["comida"],
                }
            ]
        },
    )

    deployment = Deployment.load(root)
    panel = ControlPanel(
        FakeRuntime(endpoints=["192.168.1.5:4433"], ready=False),
        home=tmp_path / "home",
        deployment=deployment,
    )
    await panel.restore()
    panel.remember()
    await panel.unpublish(save=False)

    saved = Deployment.load(root).portals[0]
    assert saved.title == "La Panadería"
    assert saved.desc == "pan"
    assert saved.tags == ["comida"]
