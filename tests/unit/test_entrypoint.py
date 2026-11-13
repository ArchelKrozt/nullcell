"""Abrir Nullcell con doble clic — accesibilidad de escritorio.

La línea de comandos no cambia: esto es otra puerta a la misma habitación.
Lo que se prueba aquí es que la puerta no se cierre en silencio, que es el
modo de fallo propio del doble clic — nadie tiene dónde leer el error.
"""

from __future__ import annotations

import ast
import pathlib
import stat
import subprocess
import sys
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "nullcell-escritorio.py"
COMMAND = ROOT / "Nullcell.command"


def code_of(obj) -> str:
    """El código de *obj*, **sin su documentación ni sus comentarios**.

    Un test que busca `node.stop()` en el fuente lo encuentra en la frase que
    explica por qué ya no se llama, y convierte la explicación en un fallo. Ha
    pasado dos veces, así que mejor una función que acordarse.

    `ast.unparse` basta: no conserva comentarios y devuelve código con sus
    espacios. Pasarlo además por `tokenize` juntando las piezas los borraba, y
    entonces «except Exception» dejaba de encontrarse por motivos de formato
    —otro test que fallaba por cómo mira, no por lo que mira.
    """
    import ast
    import inspect

    tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_both_entry_points_exist():
    assert SCRIPT.is_file()
    assert COMMAND.is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="permisos POSIX")
def test_they_are_executable():
    """Un .command sin permiso de ejecución no hace nada al doble clic."""
    for path in (SCRIPT, COMMAND):
        assert stat.S_IMODE(path.stat().st_mode) & stat.S_IXUSR, f"{path.name} no es ejecutable"


def test_the_script_is_valid_python():
    ast.parse(SCRIPT.read_text(encoding="utf-8"))


def test_the_command_changes_into_its_own_directory():
    """El doble clic arranca en la carpeta personal, así que sin `cd` el
    script de al lado no existiría."""
    text = COMMAND.read_text(encoding="utf-8")
    assert 'cd "$(dirname "$0")"' in text
    assert "nullcell-escritorio.py" in text


def test_it_checks_for_the_dependencies_and_not_just_the_package():
    """`import nullcell` funciona desde la carpeta del repositorio aunque no
    haya ninguna dependencia instalada, porque encuentra el directorio del
    paquete. Comprobarlo así daba por bueno un entorno inservible."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "import nullcell.cli.main" in text
    assert '"-c", "import nullcell"' not in text


def test_a_missing_installation_is_explained_not_silent():
    """Con doble clic no hay terminal donde mirar: si no se dice, no se sabe."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "osascript" in text, "en macOS el error tiene que verse en una ventana"
    assert "pip install" in text, "y decir cómo arreglarlo"


def test_it_relaunches_with_an_interpreter_that_works():
    """Abrir un .py con la app de Python lo ejecuta con el Python del sistema,
    no con el entorno virtual, y el resultado es un ModuleNotFoundError en una
    ventana que se cierra sola."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "os.execv" in text
    assert ".venvs" in text


def test_an_environment_somewhere_else_can_be_declared():
    """La lista de rutas habituales es una adivinanza, y falló en el CI: una
    instalación perfectamente válida en otro sitio recibía «no encuentro una
    instalación de Nullcell», que es sencillamente falso."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'ENV_VAR = "NULLCELL_PYTHON"' in text
    assert "os.environ.get(ENV_VAR)" in text


@pytest.mark.skipif(sys.platform == "win32", reason="ruta POSIX")
def test_an_interpreter_without_the_package_hands_over_to_one_that_has_it():
    """La prueba de verdad del re-lanzado: se arranca con un intérprete que no
    puede importar nullcell y tiene que acabar ejecutándolo igualmente.

    El intérprete bueno se declara en vez de buscarse, para que esto valga
    tanto aquí como en un runner donde `~/.venvs/nullcell` no existe — que es
    exactamente lo que rompió este test la primera vez."""
    system = pathlib.Path("/usr/bin/python3")
    if not system.is_file():
        pytest.skip("no hay un python de sistema con el que probar")
    if system.resolve() == pathlib.Path(sys.executable).resolve():
        pytest.skip("el python de sistema ya es el que tiene el paquete")

    import os

    done = subprocess.run(
        [str(system), str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=ROOT,
        env={**os.environ, "NULLCELL_PYTHON": sys.executable},
    )
    assert done.returncode == 0, done.stderr[-600:]
    assert "barra de menús" in done.stdout or "desktop" in done.stdout


# --------------------------------- M10: el icono de la bandeja en macOS
def test_the_tray_icon_has_no_context_menu_attached():
    """En macOS, un icono de bandeja con menú abre el menú al hacer clic
    izquierdo y **la señal `activated` no se emite nunca**: pulsar NULL daba
    un menú diminuto en lugar del panel, y el panel —que es toda la interfaz—
    parecía no existir. El menú se muestra a mano, sólo con el clic derecho.
    """
    source = (ROOT / "nullcell" / "desktop" / "app.py").read_text(encoding="utf-8")
    # La llamada, no la palabra: el comentario que explica por qué no se usa
    # contiene el nombre, y buscarlo a secas convertía la explicación en un
    # fallo.
    assert "tray.setContextMenu(" not in source
    assert "ActivationReason.Context" in source


def test_the_panel_is_not_a_popup():
    """Un Popup se cierra en cuanto pierde el foco, así que mostrarlo desde un
    menú que se estaba cerrando lo cerraba en el mismo gesto."""
    source = (ROOT / "nullcell" / "desktop" / "app.py").read_text(encoding="utf-8")
    assert "Qt.WindowType.Tool" in source
    assert "Qt.WindowType.Popup" not in source


def test_declining_a_deployment_is_remembered():
    """Preguntar en cada arranque a quien ya dijo que no es insistir, no
    ayudar: publicar un portal es opcional y mucha gente sólo navega."""
    source = (ROOT / "nullcell" / "desktop" / "app.py").read_text(encoding="utf-8")
    assert "sin-despliegue" in source
    assert "Borra este fichero" in source, "y hay que decir cómo deshacerlo"


# ----------------------------------- M10: «Salir» no debe congelar la ventana
def test_quit_never_joins_the_node_thread_from_the_ui():
    """`join` en el hilo de Qt congela la ventana el plazo entero: medido, con
    el navegador abierto eran **10,1 segundos** de aplicación muerta tras
    pulsar «Salir». Pulsar y que no pase nada es indistinguible de colgarse."""
    from nullcell.desktop.app import Shell

    source = code_of(Shell.quit)
    assert "request_stop" in source
    assert "node.stop()" not in source, "stop() hace join y bloquea la interfaz"


def test_quit_hides_everything_before_waiting():
    """Desaparecer es lo que la persona pidió; apagar el nodo es cosa nuestra
    y puede tardar."""
    from nullcell.desktop.app import Shell

    source = code_of(Shell.quit)
    for piece in ("self.panel.hide()", "self.tray.hide()", "self.browser.shutdown()"):
        assert piece in source, f"falta {piece}"
    assert source.index("hide()") < source.index("request_stop")


def test_requesting_a_stop_returns_without_waiting():
    """La separación que hace posible lo anterior."""
    from nullcell.desktop.node_thread import NodeThread

    assert "join" not in code_of(NodeThread.request_stop)
    assert "join" in code_of(NodeThread.stop), "la versión de terminal sí espera"


def test_the_nodes_own_shutdown_is_bounded():
    """Cerrar circuitos puede quedarse esperando algo que ya no llega, y
    entonces el hilo no termina nunca. Un apagado que tarda mucho es un
    apagado que no ocurre."""
    from nullcell.desktop.node_thread import NodeThread

    assert "wait_for(self._shutdown" in code_of(NodeThread._serve)


def test_the_browser_releases_its_page_before_the_profile():
    """Qt destruye padres e hijos en un orden que no promete nada, y si el
    perfil se va primero aparece «Release of profile requested but
    WebEnginePage still not deleted»: aviso en el mejor caso, cierre colgado
    en el peor."""
    source = (ROOT / "nullcell" / "desktop" / "browser.py").read_text(encoding="utf-8")
    assert "def shutdown(self)" in source
    assert "setPage(None)" in source
    assert "deleteLater()" in source


# ------------------- M10: la interfaz primero, la red después
def test_the_interface_starts_before_joining_the_network():
    """Entrar a la red puede tardar: una semilla muerta se reintenta durante
    segundos. Con el bootstrap por delante, la ventana no aparecía hasta
    terminarlo — y para quien mira, eso es un programa colgado que además «ha
    perdido» sus semillas, porque todavía no ha leído ninguna.

    Medido: con una semilla que no responde, el panel tardaba más de 14 s en
    existir. Ahora está listo en ~4.
    """
    from nullcell.desktop.node_thread import NodeThread

    source = code_of(NodeThread._serve)
    assert source.index("ui.start()") < source.index("_join_network")


def test_joining_the_network_never_takes_the_node_down():
    """La interfaz ya está viva cuando esto corre, así que un fallo aquí se
    registra y se sigue, en lugar de matar el arranque."""
    from nullcell.desktop.node_thread import NodeThread

    source = code_of(NodeThread._join_network)
    assert "except Exception" in source
    assert "_save_peers" in source


# ------------------- M10: el doble clic tiene que venir listo
def test_the_desktop_adapts_to_the_network_by_default():
    """Quien abre `Nullcell.command` no puede pasar flags, así que todo lo que
    haya que escribir en la terminal es algo que esa persona no tendrá.

    `auto` es un buen valor por defecto precisamente porque **nunca da menos
    anonimato del que la red permite**: usa el máximo disponible en cada
    momento y lo sube solo. Lo que no puede ser el valor por defecto es un
    número bajo fijo, que dejaría una red madura corriendo sin anonimato para
    siempre.
    """
    import inspect

    from nullcell.cli import main

    firma = inspect.signature(main.cmd_desktop)
    assert firma.parameters["hops"].default == "auto"


def test_every_entry_point_defaults_to_automatic_hops():
    """Red pequeña: navegador, nodo y `browse` arrancan en automático.

    Antes `ncell node` exigía tres saltos por defecto, con la idea de que un
    servidor que se deja corriendo meses debía ser estricto. En la práctica
    eso impedía justo lo que una red nueva necesita: un único servidor que
    aloja el portal y al que se entra con su sola semilla. En automático el
    anonimato se enciende solo al haber tres operadores —también en un
    servidor que lleva meses encendido—, y mientras no lo hay se dice.
    """
    import inspect

    from nullcell.cli import main

    for comando in (main.cmd_node, main.cmd_desktop, main.cmd_browse):
        assert inspect.signature(comando).parameters["hops"].default == "auto", comando.__name__


def test_the_warning_is_still_loud_while_it_lasts(capsys):
    """Un valor por defecto adaptativo sólo es honesto si dice cuándo no está
    protegiendo. Se comprueba llamando, no leyendo el fuente: la primera
    versión de este test miraba el código con un `or True` detrás, que es una
    forma elaborada de no comprobar nada."""
    from nullcell.cli.main import _warn_if_not_anonymous

    _warn_if_not_anonymous("auto")
    dicho = capsys.readouterr().err
    assert "menos de 3 operadores" in dicho
    assert "NO hay anonimato" in dicho

    _warn_if_not_anonymous(1)
    assert "SIN ANONIMATO" in capsys.readouterr().err

    _warn_if_not_anonymous(3)
    assert capsys.readouterr().err.strip() == "", "con tres saltos no hay nada que avisar"
