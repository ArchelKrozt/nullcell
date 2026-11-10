"""Saber si hay versión nueva, sin delatar que existes — §9, §12.

Un comprobador de actualizaciones es una petición periódica a un servidor
ajeno, y eso le dice a ese servidor que hay un nodo Nullcell en esa IP todos
los días. El test que más importa aquí no es de formato: es que **nada lo
llame solo**.
"""

from __future__ import annotations

import pytest

from nullcell.cli import updates


# ------------------------------------------------------------- comparación
@pytest.mark.parametrize(
    ("instalada", "ultima", "esperado"),
    [
        ("0.1.0", "0.2.0", -1),
        ("0.2.0", "0.1.0", 1),
        ("0.1.0", "0.1.0", 0),
        ("0.1.0", "v0.1.0", 0),  # la `v` de la etiqueta no cuenta
        ("1.0", "1.0.1", -1),  # longitudes distintas
        ("1.10.0", "1.9.0", 1),  # no es comparación de texto
    ],
)
def test_versions_compare_by_number(instalada, ultima, esperado):
    assert updates.compare(instalada, ultima) == esperado


def test_an_unreadable_version_does_not_crash():
    assert updates.compare("", "") == 0
    assert updates.compare("sin-numeros", "0.1.0") == -1


# --------------------------------------------------------------- la regla
def test_nothing_checks_for_updates_on_its_own():
    """La regla que hace esto aceptable en una herramienta de anonimato.

    Escribir `ncell update` es el consentimiento. Una comprobación al arrancar
    —o peor, una tarea de fondo— convertiría cada nodo en un latido diario
    hacia un servidor ajeno.
    """
    import pathlib

    root = pathlib.Path(updates.__file__).resolve().parents[1]
    llamadas = [
        path
        for path in root.rglob("*.py")
        if path.name not in ("updates.py", "main.py")
        and "fetch_latest" in path.read_text(encoding="utf-8")
    ]
    assert not llamadas, f"algo comprueba actualizaciones solo: {llamadas}"


def test_only_the_update_command_fetches():
    """Y dentro del CLI, sólo el comando que la persona escribe."""
    import inspect

    from nullcell.cli import main

    source = inspect.getsource(main)
    assert source.count("fetch_latest") == 1
    assert "fetch_latest" in inspect.getsource(main.cmd_update)


def test_the_request_does_not_identify_this_node():
    """Un agente de usuario con la versión y la plataforma convierte una
    consulta anónima en una huella."""
    assert updates.USER_AGENT == "nullcell"
    assert "@" not in updates.USER_AGENT


def test_the_host_contacted_is_named_so_it_can_be_shown():
    assert updates.HOST in updates.RELEASES_URL
    assert updates.RELEASES_URL.startswith("https://")


# ------------------------------------------------------------- actualizar
def test_an_editable_checkout_is_told_to_pull_not_to_pip_upgrade(tmp_path, monkeypatch):
    """`pip install --upgrade` sobre una instalación editable no hace nada
    visible y deja a la persona convencida de que ya actualizó."""
    import nullcell

    fake = tmp_path / "repo" / "nullcell"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text("")
    (tmp_path / "repo" / ".git").mkdir()
    monkeypatch.setattr(nullcell, "__file__", str(fake / "__init__.py"))

    command = updates.upgrade_command("/usr/bin/python3")
    assert "git pull" in command
    assert "--upgrade nullcell" not in command


def test_a_normal_install_is_told_to_pip_upgrade(tmp_path, monkeypatch):
    import nullcell

    fake = tmp_path / "site-packages" / "nullcell"
    fake.mkdir(parents=True)
    monkeypatch.setattr(nullcell, "__file__", str(fake / "__init__.py"))

    command = updates.upgrade_command("/usr/bin/python3")
    assert "pip install --upgrade nullcell" in command
    assert "git pull" not in command
