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
import pathlib
import sys
import tomllib
from typing import Final

ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[2]
PACKAGE: Final[pathlib.Path] = ROOT / "nullcell"

#: Import name -> distribution name, where they differ.
_DISTRIBUTIONS: Final[dict[str, str]] = {
    "nacl": "pynacl",
    "noise": "noiseprotocol",
    "cryptography": "cryptography",
}


def _declared() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    names = set()
    for requirement in project["dependencies"]:
        name = requirement.split(";")[0]
        for separator in (">=", "==", "<=", ">", "<", "~=", "!="):
            name = name.split(separator)[0]
        names.add(name.strip().lower())
    return names


def _imported() -> set[str]:
    """Top-level third-party modules imported anywhere in the package."""
    found: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
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
    )
    script = "import " + ", ".join(modules)
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr.strip(), f"importing produced output: {result.stderr[:400]}"
