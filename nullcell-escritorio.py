#!/usr/bin/env python3
"""Abrir Nullcell sin escribir nada — doble clic sobre este fichero.

Hace exactamente lo mismo que `ncell desktop`. La línea de comandos no
desaparece ni cambia: esto es sólo otra puerta a la misma habitación.

    python3 nullcell-escritorio.py            # desde la terminal
    (o ábrelo con la app de Python / doble clic en Nullcell.command)

-------------------------------------------------------------- el re-lanzado
Abrir un `.py` con la app de Python de macOS lo ejecuta con **el Python del
sistema**, no con el entorno virtual donde están instaladas las dependencias,
y el resultado es un `ModuleNotFoundError` en una ventana que se cierra sola.
Así que si el intérprete actual no encuentra `nullcell`, este script busca uno
que sí y se vuelve a lanzar con él. Si no encuentra ninguno, lo dice y explica
cómo instalarlo, en lugar de desaparecer.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

#: Quien tenga el entorno en otro sitio lo dice aquí, y deja de ser adivinanza.
#: Sin esto, una instalación perfectamente válida en una ruta poco común
#: recibía «no encuentro una instalación de Nullcell», que es falso.
ENV_VAR = "NULLCELL_PYTHON"

#: Dónde suele vivir el entorno, por orden.
CANDIDATES = (
    Path.home() / ".venvs" / "nullcell" / "bin" / "python",
    Path(__file__).resolve().parent / ".venv" / "bin" / "python",
    Path.home() / ".venvs" / "nullcell" / "Scripts" / "python.exe",
    Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe",
)


def _usable(interpreter: Path) -> bool:
    """Un intérprete que puede importar nullcell de verdad.

    Se comprueba ejecutándolo, no mirando si el fichero existe: un entorno a
    medio instalar tiene el binario y no el paquete.
    """
    if not interpreter.is_file():
        return False
    try:
        done = subprocess.run(
            # `import nullcell.cli.main` y no `import nullcell`: lo segundo
            # funciona desde la carpeta del repositorio aunque no haya ninguna
            # dependencia instalada, porque encuentra el directorio del
            # paquete. Lo primero arrastra typer, cbor2 y el resto, que es lo
            # que realmente hace falta.
            [str(interpreter), "-c", "import nullcell.cli.main"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _complain(message: str) -> None:
    """Decirlo donde se pueda leer, que con doble clic no es la terminal."""
    print(message, file=sys.stderr)
    if sys.platform == "darwin" and shutil.which("osascript"):
        subprocess.run(
            [
                "osascript",
                "-e",
                'display dialog "{}" buttons {{"Cerrar"}} with icon stop '
                'with title "Nullcell"'.format(message.replace('"', "'")),
            ],
            check=False,
        )


def main() -> int:
    try:
        from nullcell.cli.main import app
    except ImportError:  # falta el paquete o alguna dependencia
        declared = os.environ.get(ENV_VAR)
        candidates = ([Path(declared)] if declared else []) + list(CANDIDATES)
        for candidate in candidates:
            if _usable(candidate):
                # Re-lanzado, no importado: el entorno correcto tiene que ser
                # el del proceso entero, no sólo el de este módulo.
                os.execv(
                    str(candidate), [str(candidate), str(Path(__file__).resolve()), *sys.argv[1:]]
                )
        _complain(
            "No encuentro una instalación de Nullcell.\n\n"
            "Instálalo con:\n"
            "    python3 -m venv ~/.venvs/nullcell\n"
            "    ~/.venvs/nullcell/bin/pip install 'nullcell[desktop]'"
        )
        return 2

    try:
        app(["desktop", *sys.argv[1:]], standalone_mode=False)
    except SystemExit as exit_code:  # typer sale así; no es un error
        return int(exit_code.code or 0)
    except Exception as exc:  # noqa: BLE001 - con doble clic no hay dónde mirar
        _complain(f"Nullcell no pudo arrancar:\n\n{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
