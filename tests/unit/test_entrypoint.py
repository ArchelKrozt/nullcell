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

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "nullcell-escritorio.py"
COMMAND = ROOT / "Nullcell.command"


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
