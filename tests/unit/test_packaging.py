"""Does the project declare what it actually needs?

This file exists because of a bug it would have caught. ``noiseprotocol`` and
``aiohttp`` were imported by the circuit handshake, the local UI and the HTTP a
portal serves, and **neither was declared in ``pyproject.toml``**. Everything
worked on the machine where they had been installed by hand; a clean
``pip install`` produced ``ModuleNotFoundError`` on anything that touched the
network, and CI — which had never run — would have failed on its first attempt.

"Works on my machine" is the failure mode a dependency list exists to prevent,
so the list is checked by a test rather than by remembering.
"""

from __future__ import annotations

import ast
import fnmatch
import pathlib
import sys
import tomllib
from typing import Final

import pytest

ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[2]
PACKAGE: Final[pathlib.Path] = ROOT / "nullcell"

#: Import name -> distribution name, where they differ.
_DISTRIBUTIONS: Final[dict[str, str]] = {
    "nacl": "pynacl",
    "noise": "noiseprotocol",
    "cryptography": "cryptography",
}


#: Subpackages that are an optional extra, mapped to the extra that carries
#: them. Their imports are checked against that extra instead of against the
#: required dependencies — a relay on a server must not download a browser
#: engine to forward frames.
_EXTRAS: Final[dict[str, str]] = {"desktop": "desktop"}


def _requirement_names(requirements: list[str]) -> set[str]:
    names = set()
    for requirement in requirements:
        name = requirement.split(";")[0]
        for separator in (">=", "==", "<=", ">", "<", "~=", "!="):
            name = name.split(separator)[0]
        names.add(name.strip().lower())
    return names


def _declared(extra: str | None = None) -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    if extra is None:
        return _requirement_names(project["dependencies"])
    return _requirement_names(project["optional-dependencies"][extra])


def _imported(subpackage: str | None = None) -> set[str]:
    """Top-level third-party modules imported in the package, or in one part.

    ``subpackage=None`` skips the optional extras, so an engine imported only
    by the desktop never looks like a missing core dependency.
    """
    root = PACKAGE / subpackage if subpackage else PACKAGE
    found: set[str] = set()
    for path in root.rglob("*.py"):
        if subpackage is None and path.relative_to(PACKAGE).parts[0] in _EXTRAS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])

    standard = set(sys.stdlib_module_names)
    return {name for name in found if name not in standard and name != "nullcell"}


def test_every_imported_package_is_declared():
    declared = _declared()
    missing = sorted(
        name for name in _imported() if _DISTRIBUTIONS.get(name, name).lower() not in declared
    )
    assert not missing, (
        f"imported but not declared in pyproject.toml: {missing}. "
        "A clean install will fail on these."
    )


def test_each_optional_subpackage_declares_its_own_engine():
    """The extras are checked too, just against their own list.

    Without this the desktop could import anything and nobody would notice
    until `pip install 'nullcell[desktop]'` produced a shell that cannot
    start — the same class of failure as the undeclared `noiseprotocol`, moved
    one level down.
    """
    for subpackage, extra in _EXTRAS.items():
        allowed = _declared() | _declared(extra)
        missing = sorted(
            name
            for name in _imported(subpackage)
            if _DISTRIBUTIONS.get(name, name).lower() not in allowed
        )
        assert not missing, f"{subpackage}/ importa sin declarar en [{extra}]: {missing}"


def test_the_desktop_engine_is_pinned_to_a_version_that_has_a_browser():
    """Qt stopped shipping QtWebEngine for macOS in PySide6 6.12, so a floating
    `PySide6>=6.11` installs a desktop with no browser in it and the failure
    arrives at runtime as an ImportError. 6.11.3 is the last one that has it.
    """
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = data["project"]["optional-dependencies"]["desktop"]
    pyside = next(r for r in requirements if r.lower().startswith("pyside6"))
    assert "==" in pyside, f"PySide6 debe ir fijada, no {pyside!r}"
    assert pyside.split("==")[1].strip() == "6.11.3"


def test_nothing_is_declared_that_is_never_imported():
    """An unused dependency is an unnecessary attack surface for a privacy
    tool, and §11's stack table is meant to be the real list."""
    imported = {_DISTRIBUTIONS.get(name, name).lower() for name in _imported()}
    unused = sorted(_declared() - imported)
    assert not unused, f"declared but never imported: {unused}"


def test_the_package_has_no_import_time_side_effects():
    """Importing a module must not open a socket, read a key or start a thread.

    Every module here is imported by the test suite and by `ncell --help`; one
    that did real work on import would make the CLI slow and the tests
    order-dependent.
    """
    import subprocess

    modules = sorted(
        str(path.relative_to(ROOT)).removesuffix(".py").replace("/", ".")
        for path in PACKAGE.rglob("*.py")
        if path.name != "__init__.py"
        # The optional extras are not installed in a plain `.[dev]` checkout,
        # so importing them here would fail for the one reason that is not a
        # bug. They get their own test below, skipped when absent.
        and path.relative_to(PACKAGE).parts[0] not in _EXTRAS
    )
    script = "import " + ", ".join(modules)
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr.strip(), f"importing produced output: {result.stderr[:400]}"


# ------------------------------------------- M9: los ficheros que no son .py
def test_the_interface_files_are_declared_as_package_data():
    """A wheel without the HUD installs a panel that cannot open.

    This is not hypothetical and it was not caught by any test: the wheel
    carried **no** HUD file at all. `pip install -e` hid it, because an
    editable install points at the source tree, while `deploy/install.sh` does
    a normal install — so every operator who followed the official instructions
    had a HUD that died on `index.html`.

    Checked against the files on disk rather than a fixed list, so a new asset
    cannot be added without being declared.
    """
    import tomllib

    root = pathlib.Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["nullcell"]

    assets = [
        path.relative_to(root / "nullcell").as_posix()
        for folder in ("hud", "desktop/panel")
        for path in (root / "nullcell" / folder).iterdir()
        if path.suffix in {".html", ".css", ".js", ".svg"}
    ]
    assert assets, "no se encontró ningún fichero de interfaz"

    for asset in assets:
        covered = any(fnmatch.fnmatch(asset, pattern) for pattern in patterns)
        assert covered, f"{asset} no lo cubre ningún patrón de package-data: {patterns}"


def test_the_desktop_imports_cleanly_when_its_extra_is_installed():
    """Checked where the engine exists, skipped where it does not.

    The point is the same as the test above — a module that only fails on
    import in production is the worst kind — but it cannot run in a checkout
    that installed only `.[dev]`, and failing there would be reporting an
    absent optional dependency as a defect.
    """
    import subprocess

    pytest.importorskip("PySide6", reason="el extra [desktop] no está instalado")

    modules = sorted(
        str(path.relative_to(ROOT)).removesuffix(".py").replace("/", ".")
        for path in (PACKAGE / "desktop").rglob("*.py")
        if path.name != "__init__.py"
    )
    result = subprocess.run(
        [sys.executable, "-c", "import " + ", ".join(modules)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
