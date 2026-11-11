"""Lo que un operador toca — §1, §10, §12.

A relay is what makes a network exist, so the pieces an operator uses are
tested like anything else: the seed line, the log's refusal to record peer
addresses, the deployment files, and the padding §10 counts as a mitigation.
"""

from __future__ import annotations

import logging
import pathlib
import re
import subprocess
import sys

import pytest

from nullcell.cli.logging_setup import LEVELS, setup
from nullcell.cli.relay import DRAIN_TIMEOUT, seed_line
from nullcell.core import ids
from nullcell.core.identity import Identity

ROOT = pathlib.Path(__file__).resolve().parents[2]


# -------------------------------------------------------------- la semilla §7
def test_the_seed_line_is_an_address_and_an_endpoint():
    identity = Identity.generate()
    line = seed_line(identity, "203.0.113.9:4433")
    nid, _, endpoint = line.partition("@")
    assert ids.decode_nid(nid).pub == identity.public_key
    assert endpoint == "203.0.113.9:4433"


def test_the_seed_line_round_trips_through_the_parser():
    from nullcell.cli.relay import _parse_seed

    identity = Identity.generate()
    node = _parse_seed(seed_line(identity, "198.51.100.4:4433"))
    assert node is not None
    assert node.pub == identity.public_key
    assert node.endpoints == ["198.51.100.4:4433"]


@pytest.mark.parametrize(
    "bad", ["", "sin-arroba", "no-es-nid@1.2.3.4:443", "x" * 56 + "@1.2.3.4:443"]
)
def test_a_malformed_seed_is_skipped_not_fatal(bad):
    """An operator pasting a broken line should get a warning, not a crash."""
    from nullcell.cli.relay import _parse_seed

    assert _parse_seed(bad) is None


# ------------------------------------------------------------------- log §12
def test_the_log_does_not_record_peer_addresses(caplog):
    """§12: the difference between an operator who has nothing to hand over
    and one who does."""
    setup("info")
    log = logging.getLogger("nullcell.relay.test")
    with caplog.at_level(logging.INFO):
        log.info("circuito de 192.168.1.44:51234 hacia 10.0.0.9:4433 establecido")
    rendered = _render("info", 'log.info("circuito de 192.168.1.44:51234 hacia 10.0.0.9:4433")')
    assert "192.168.1.44" not in rendered


def test_our_own_address_is_not_filtered():
    """The seed line is this relay's own address — public by construction, and
    the one line a new operator needs. The filter swallowed it at first."""
    rendered = _render(
        "info",
        'log.info("seed line for others: %s", "x"*56 + "@203.0.113.9:4433",'
        ' extra={"public": True})',
    )
    assert "203.0.113.9:4433" in rendered


def test_debug_shows_everything_if_the_operator_asks():
    rendered = _render("debug", 'log.info("circuito de 192.168.1.44:51234 hacia 10.0.0.9:4433")')
    assert "192.168.1.44" in rendered


def _render(level: str, statement: str) -> str:
    """Run a log statement in a fresh process and capture stdout.

    In-process capture fights pytest's own handlers, and what matters here is
    what actually reaches stdout — which is what journald and Docker collect.
    """
    script = (
        "import logging\n"
        "from nullcell.cli.logging_setup import setup\n"
        f"setup({level!r}, journal=False)\n"
        "log = logging.getLogger('nullcell.relay')\n"
        f"{statement}\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_every_level_is_usable():
    for name in LEVELS:
        setup(name)


# ----------------------------------------------------------- relleno §5, §10
async def test_link_padding_sends_only_while_idle():
    """§5: an idle link disappearing from flow records draws a picture of when
    you are active."""
    import asyncio

    from nullcell.onion.padding import LinkPadding

    class FakeLink:
        def __init__(self) -> None:
            self.frames_in = 0
            self.frames_out = 0
            self.padding = 0

        def send_frame(self, circ_id, cmd, payload=b"") -> None:
            self.padding += 1
            self.frames_out += 1

    link = FakeLink()
    padding = LinkPadding(link, idle=0.05, jitter=0.0)
    padding.start()
    try:
        await asyncio.sleep(0.3)
        assert padding.sent >= 1, "an idle link sent no padding"

        # Now make it busy: real traffic should suppress padding.
        before = padding.sent
        for _ in range(10):
            link.frames_in += 1
            await asyncio.sleep(0.03)
        assert padding.sent - before <= 2, "padding kept firing on a busy link"
    finally:
        padding.stop()


async def test_padding_frames_are_indistinguishable_on_the_wire():
    """§5: 512 bytes, the same fixed size as everything else — which is the
    entire reason the frame size is fixed."""
    from nullcell.core.frames import FRAME_SIZE, Cmd, pack

    assert len(pack(0, Cmd.PADDING, b"")) == FRAME_SIZE
    assert len(pack(7, Cmd.RELAY, b"x" * 400)) == FRAME_SIZE


async def test_construction_padding_does_not_delay_the_build():
    import asyncio

    from nullcell.onion.padding import ConstructionPadding

    class FakeLink:
        def __init__(self) -> None:
            self.padding = 0

        def send_frame(self, circ_id, cmd, payload=b"") -> None:
            self.padding += 1

    link = FakeLink()

    async def build() -> str:
        await asyncio.sleep(0.15)
        return "listo"

    padding = ConstructionPadding(link, burst=3)
    assert await padding.around(build()) == "listo"
    assert padding.sent >= 1


def test_a_relay_drops_padding_instead_of_forwarding_it():
    """§5: padding is link-local and stops at the hop that receives it."""
    import inspect

    from nullcell.onion import relay_logic

    source = inspect.getsource(relay_logic.RelayService._on_frame)
    assert "Cmd.PADDING" in source


# ------------------------------------------------------------- despliegue §12
def test_the_deployment_files_exist():
    """§12 promises RELAY_OP.md, and a relay nobody can install is a relay
    nobody runs."""
    for relative in (
        "Dockerfile",
        "deploy/docker-compose.yml",
        "deploy/docker-compose.lab.yml",
        "deploy/nullcell-relay.service",
        "deploy/nullcell-portal.service",
        "deploy/install.sh",
        "docs/RELAY_OP.md",
        "docs/VPS.md",
    ):
        assert (ROOT / relative).is_file(), f"falta {relative}"


def test_the_installer_is_valid_shell():
    result = subprocess.run(
        ["bash", "-n", str(ROOT / "deploy" / "install.sh")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_the_compose_files_are_valid_yaml():
    import yaml

    for relative in ("deploy/docker-compose.yml", "deploy/docker-compose.lab.yml"):
        data = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
        assert data["services"], f"{relative} declares no services"


def test_the_systemd_unit_drains_before_exiting():
    """§10: dropping hundreds of circuits at once is both a bad experience and
    an observable event on the network."""
    unit = (ROOT / "deploy" / "nullcell-relay.service").read_text(encoding="utf-8")
    assert "KillSignal=SIGTERM" in unit
    timeout = int(re.search(r"TimeoutStopSec=(\d+)", unit).group(1))
    assert timeout > DRAIN_TIMEOUT, "systemd would kill the relay mid-drain"


def test_the_systemd_unit_runs_unprivileged():
    """§0, decision 3: a relay needs no root for anything."""
    unit = (ROOT / "deploy" / "nullcell-relay.service").read_text(encoding="utf-8")
    for hardening in (
        "User=nullcell",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "CapabilityBoundingSet=",
    ):
        assert hardening in unit, f"falta {hardening}"


def test_docker_gives_the_relay_time_to_drain():
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))
    grace = compose["services"]["relay"]["stop_grace_period"]
    assert int(str(grace).rstrip("s")) > DRAIN_TIMEOUT


def test_the_image_does_not_run_as_root():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER nullcell" in dockerfile
    # And the node's state, which holds the private key, is a volume rather
    # than part of the image.
    assert "VOLUME" in dockerfile
    assert "NULLCELL_HOME" in dockerfile


# ------------------------------------- M8: la pasarela fuera de loopback (§8)
def test_the_vps_guide_refuses_the_web_gateway_in_writing():
    """It is the request this project has to keep saying no to, so the reason
    lives in the docs rather than only in a conversation."""
    guide = (ROOT / "docs" / "VPS.md").read_text(encoding="utf-8")
    assert "tor2web" in guide
    assert "mismo sitio" in guide  # the same-origin collapse, named


def test_the_vps_guide_states_the_three_risks_it_adds():
    guide = (ROOT / "docs" / "VPS.md").read_text(encoding="utf-8")
    for claim in (
        "ve texto claro",  # the VPS is a trusted hop
        "system-wide",  # the CA on a phone
        "NameConstraints",  # and that its enforcement is unverified
        "no lo he verificado",
    ):
        assert claim in guide, f"la guía no dice: {claim}"


def test_browse_refuses_a_public_bind():
    """`--bind 0.0.0.0` on a VPS is an open proxy that reads plaintext. The CLI
    must refuse it, not warn: a warning scrolls past."""
    from typer.testing import CliRunner

    from nullcell.cli.main import app

    runner = CliRunner()
    for address in ("0.0.0.0", "::", "8.8.8.8", "mivps.com"):
        result = runner.invoke(app, ["browse", "--bind", address, "--no-launch"])
        assert result.exit_code == 2, f"aceptó --bind {address}"


def test_browse_help_points_at_the_guide():
    from typer.testing import CliRunner

    from nullcell.cli.main import app

    result = CliRunner().invoke(app, ["browse", "--help"])
    assert result.exit_code == 0
    assert "docs/VPS.md" in result.stdout.replace("\n", " ")


def test_the_guide_names_the_ca_file_that_actually_exists():
    """I wrote `ca.crt` from memory; it is `ca.pem`, and an operator following
    that line gets an empty `cat` and no idea why the phone distrusts the
    certificate."""
    from nullcell.gateway.localca import LocalCA

    name = LocalCA(pathlib.Path("/tmp/x")).cert_path.name
    guide = (ROOT / "docs" / "VPS.md").read_text(encoding="utf-8")
    assert name in guide, f"la guía no nombra {name}"


def test_the_guide_only_uses_commands_that_exist():
    """Same class of rot: `ncell node --once` was in the first draft and the
    flag does not exist."""
    import re

    from nullcell.cli.main import app

    known = {command.name or command.callback.__name__ for command in app.registered_commands}
    guide = (ROOT / "docs" / "VPS.md").read_text(encoding="utf-8")
    used = set(re.findall(r"\bncell ([a-z]+)", guide))
    assert used <= known, f"la guía usa comandos inexistentes: {sorted(used - known)}"


def test_the_lab_network_can_actually_build_a_circuit():
    """The lab validated discovery and nothing else, and said nothing about it.

    Two independent reasons a circuit was impossible in it:
    bare hostnames (`seed:4433`) have no comparable prefix key, so §7 excluded
    every relay from path selection; and Docker's default bridge puts all
    containers in one /16, so even with IP announces only *one* hop would have
    been admissible. A /13 subnet with one fixed IP per relay in a distinct /16
    fixes both, and this test is what keeps it fixed.
    """
    import yaml

    from nullcell.onion.path_select import CIRCUIT_LENGTH, endpoint_prefix

    compose = yaml.safe_load((ROOT / "deploy" / "docker-compose.lab.yml").read_text())
    prefixes = []
    for name, service in compose["services"].items():
        command = service["command"]
        announced = command[command.index("--announce") + 1]
        prefix = endpoint_prefix(announced)
        assert prefix is not None, f"{name} anuncia {announced}, sin prefijo comparable (§7)"
        prefixes.append(prefix)

    assert len(set(prefixes)) == len(prefixes), f"prefijos repetidos: {prefixes}"
    assert len(set(prefixes)) >= CIRCUIT_LENGTH, (
        f"{len(set(prefixes))} operadores distintos, §7 exige {CIRCUIT_LENGTH}"
    )


def test_the_lab_relays_share_one_subnet_so_they_can_find_each_other():
    """The other half: distinct /16s are useless if they cannot reach each
    other, since the relays discover the seed by LAN beacon."""
    import ipaddress

    import yaml

    compose = yaml.safe_load((ROOT / "deploy" / "docker-compose.lab.yml").read_text())
    subnet = ipaddress.ip_network(compose["networks"]["lab"]["ipam"]["config"][0]["subnet"])
    for name, service in compose["services"].items():
        address = ipaddress.ip_address(service["networks"]["lab"]["ipv4_address"])
        assert address in subnet, f"{name} ({address}) fuera de {subnet}"


# ----------------------------- M9: entrar una vez, y ya (§1, §7)
def test_the_desktop_remembers_who_it_met():
    """It did not, and that is the difference between "you join once" and "you
    join every time".

    `ncell node`, `relay` and `browse` all went through `_seeds()`, which reads
    `bootstrap.json` and the peers saved last session. The desktop bootstrapped
    only from `--seed`: paste a seed, find cells, close the window, and it woke
    up knowing nobody.
    """
    import inspect

    from nullcell.desktop import node_thread

    source = inspect.getsource(node_thread)
    for piece in ("load_bootstrap", "load_peers", "save_peers"):
        assert piece in source, f"el escritorio no usa {piece}"


def test_peers_are_saved_as_soon_as_there_are_any(tmp_path):
    """Not only on the way out. A desktop app does not always die where you
    expect — a SIGTERM takes the node's thread without passing through the
    orderly shutdown — and measured that way everything learned was lost."""
    import inspect

    from nullcell.desktop import node_thread

    body = inspect.getsource(node_thread.NodeThread._serve)
    bootstrap = body.index("bootstrap(")
    saved = body.index("_save_peers()")
    assert saved > bootstrap, "hay que guardar después de conocer a alguien"
    assert saved < body.index("await self._stop.wait()"), (
        "guardar sólo al salir pierde todo si el proceso muere de otra forma"
    )


def test_saving_peers_without_a_node_is_quiet_and_harmless(tmp_path):
    """`_save_peers` reads `self.runtime`, which is set while the node starts.
    Calling it too early raised inside a broad `except` and the save silently
    did nothing — which is exactly how this bug hid."""
    from nullcell.desktop.node_thread import NodeThread

    thread = NodeThread()
    thread.peers_path = tmp_path / "peers.db"
    thread._save_peers()  # noqa: SLF001
    assert not (tmp_path / "peers.db").exists()


def test_a_saved_peer_comes_back(tmp_path):
    from nullcell.core.identity import Identity
    from nullcell.dht.routing_table import Node
    from nullcell.discovery.bootstrap import load_peers, save_peers

    node = Node(pub=Identity.generate().public_key, endpoints=["203.0.113.9:4433"])
    save_peers(tmp_path / "peers.db", [node])
    back = load_peers(tmp_path / "peers.db")
    assert [n.pub for n in back] == [node.pub]
    assert back[0].endpoints == ["203.0.113.9:4433"]


# ------------------------------------- M10: una Raspberry Pi, sin pantalla
def test_the_portal_unit_runs_headless_with_a_deployment():
    """`ncell desktop` necesita PySide6, que no publica ruedas para ARM, así
    que en una Pi la única vía es `ncell node --config`. Una unidad que
    invocara el escritorio fallaría al arrancar, en la máquina donde menos se
    mira."""
    unit = (ROOT / "deploy" / "nullcell-portal.service").read_text(encoding="utf-8")
    assert "ncell node" in unit
    assert "--config" in unit
    assert "desktop" not in unit.split("[Service]")[1]


def test_the_portal_unit_drains_before_exiting():
    unit = (ROOT / "deploy" / "nullcell-portal.service").read_text(encoding="utf-8")
    assert "KillSignal=SIGTERM" in unit
    assert "TimeoutStopSec" in unit


def test_the_portal_unit_runs_unprivileged_and_keeps_its_identity():
    """La dirección .null *es* la clave (§3): si el estado no sobrevive al
    reinicio, el portal cambia de nombre solo."""
    unit = (ROOT / "deploy" / "nullcell-portal.service").read_text(encoding="utf-8")
    assert "User=nullcell" in unit
    assert "NoNewPrivileges=true" in unit
    assert "StateDirectory=nullcell" in unit
    assert "ReadWritePaths=/var/lib/nullcell" in unit


def test_the_node_command_takes_a_deployment():
    """El hueco que impedía desplegar en una Pi: toda la carpeta de despliegue
    estaba cableada sólo al escritorio."""
    import inspect

    from nullcell.cli import main

    source = inspect.getsource(main.cmd_node)
    assert '"--config"' in source
    assert "Deployment.load" in source


# ------------------------------- M10: el nivel de registro que no existía
def test_both_spellings_of_every_level_work():
    """La tabla tenía `warn` y no `warning`, y el escritorio pedía `warning`:
    caía al valor por defecto —INFO— **en silencio**, así que una app abierta
    con doble clic escupía sus líneas internas en la terminal."""
    assert LEVELS["warning"] == LEVELS["warn"] == logging.WARNING
    assert LEVELS["info"] == logging.INFO


def test_the_desktop_default_is_actually_quiet():
    """Y que el valor por defecto del comando exista en la tabla, que es lo
    que falló: la ortografía natural no hacía nada."""
    import inspect

    from nullcell.cli import main

    source = inspect.getsource(main.cmd_desktop)
    default = source.split('typer.Option("--log-level")] = "')[1].split('"')[0]
    assert default in LEVELS, f"el nivel por defecto {default!r} no existe en LEVELS"
    assert LEVELS[default] >= logging.WARNING


def test_an_unknown_level_is_reported_not_swallowed(capsys):
    """Un nivel mal escrito que se sustituye sin decir nada deja a alguien
    creyendo que pidió silencio. Es exactamente cómo se escondió el fallo.

    Con `capsys` y no `caplog`: `setup()` vacía los manejadores de la raíz —es
    su trabajo— y entre ellos está el que `caplog` instala, así que el aviso
    se emite por el manejador nuevo, hacia la salida estándar."""
    try:
        setup("disparate")
        assert "desconocido" in capsys.readouterr().out
    finally:
        setup("info")  # dejarlo como estaba para el resto de la suite


# ------------------- M10: no obligar a escribir lo que se puede averiguar
def test_a_relay_can_find_its_own_address_when_it_has_one(monkeypatch):
    """En una máquina alojada la dirección pública está en su interfaz, así
    que exigir `--announce` era fricción, no una necesidad."""
    from nullcell.core import reach

    monkeypatch.setattr(reach, "lan_address", lambda: "62.171.160.130")
    endpoint, alcance = reach.best_endpoint(4433)
    assert endpoint == "62.171.160.130:4433"
    assert alcance == "internet"


def test_a_relay_behind_nat_is_refused_with_instructions():
    """Detrás de NAT no se puede averiguar, y entonces hay que decirlo: un
    relevo que se anuncia en 192.168.x es una entrada inalcanzable en el
    directorio de todos los demás."""
    import inspect

    from nullcell.cli import main

    source = inspect.getsource(main.cmd_relay)
    assert 'alcance != "internet"' in source
    assert "redirige el puerto UDP" in source
    assert "raise typer.Exit(2)" in source


def test_announce_is_optional_now():
    import inspect

    from nullcell.cli import main

    signature = inspect.signature(main.cmd_relay)
    assert signature.parameters["announce"].default is None


# ------------------- M10: no olvidar una celda porque hoy no contesta
def test_saving_peers_adds_instead_of_replacing(tmp_path):
    """La tabla de rutas expulsa a quien falla tres veces —correcto en un DHT,
    porque una tabla de nodos muertos no encuentra nada— pero guardar *sólo lo
    que hay en la tabla* convertía un apagón de un minuto en un olvido
    permanente: la celda por la que entraste desaparecía del disco."""
    from nullcell.core.identity import Identity
    from nullcell.dht.routing_table import Node
    from nullcell.discovery.bootstrap import load_peers, save_peers

    archivo = tmp_path / "peers.db"
    vps = Node(pub=Identity.generate().public_key, endpoints=["62.171.160.130:4433"])
    otra = Node(pub=Identity.generate().public_key, endpoints=["190.95.43.9:4433"])

    save_peers(archivo, [vps])
    save_peers(archivo, [otra])  # el VPS no contesta ahora mismo

    recordados = {n.nid for n in load_peers(archivo)}
    assert vps.nid in recordados, "se perdió la celda por la que entraste"
    assert otra.nid in recordados


def test_saving_nothing_does_not_wipe_what_was_known(tmp_path):
    """Un arranque que no llega a conocer a nadie no debe borrar la red."""
    from nullcell.core.identity import Identity
    from nullcell.dht.routing_table import Node
    from nullcell.discovery.bootstrap import load_peers, save_peers

    archivo = tmp_path / "peers.db"
    save_peers(archivo, [Node(pub=Identity.generate().public_key, endpoints=["1.2.3.4:4433"])])
    save_peers(archivo, [])
    assert len(load_peers(archivo)) == 1


def test_a_new_address_for_the_same_cell_wins(tmp_path):
    """Si una celda se mudó, manda lo de ahora, no lo de ayer."""
    from nullcell.core.identity import Identity
    from nullcell.dht.routing_table import Node
    from nullcell.discovery.bootstrap import load_peers, save_peers

    archivo = tmp_path / "peers.db"
    key = Identity.generate().public_key
    save_peers(archivo, [Node(pub=key, endpoints=["1.1.1.1:4433"])])
    save_peers(archivo, [Node(pub=key, endpoints=["2.2.2.2:4433"])])

    guardados = load_peers(archivo)
    assert len(guardados) == 1
    assert guardados[0].endpoints == ["2.2.2.2:4433"]


def test_the_list_stays_bounded(tmp_path):
    """Añadir no puede significar crecer sin límite."""
    from nullcell.core.identity import Identity
    from nullcell.dht.routing_table import Node
    from nullcell.discovery.bootstrap import MAX_SAVED_PEERS, load_peers, save_peers

    archivo = tmp_path / "peers.db"
    for i in range(MAX_SAVED_PEERS + 40):
        save_peers(
            archivo,
            [
                Node(
                    pub=Identity.generate().public_key,
                    endpoints=[f"10.0.{i // 256}.{i % 256}:4433"],
                )
            ],
        )
    assert len(load_peers(archivo)) <= MAX_SAVED_PEERS
