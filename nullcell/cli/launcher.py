"""Un lanzador de escritorio — abrir Nullcell sin escribir nada.

La línea de comandos no desaparece: sigue siendo la única vía en un servidor y
la mejor para automatizar. Pero pedirle a alguien que abra una terminal y
recuerde una ruta para usar un programa de escritorio es pedirle que haga de
instalador, y la mayoría no lo hará.

Esto crea el lanzador que corresponde al sistema:

* **macOS** — un `Nullcell.app` de verdad en `~/Applications`, con su icono.
* **Linux** — una entrada `.desktop`, que es lo que leen los menús.
* **Windows** — un acceso directo `.bat` sin ventana de consola.

Dos decisiones que no son obvias:

1. **El lanzador apunta al intérprete concreto que lo creó**, no a `ncell` del
   PATH. Un entorno virtual no está en el PATH de la sesión gráfica —el Finder
   no lee tu `.zshrc`— así que un lanzador que diga `ncell` funciona desde la
   terminal y falla al hacer doble clic, que es justo cuando nadie puede leer
   el error.
2. **Si algo falla, lo dice con una ventana.** Un programa gráfico que termina
   en silencio es indistinguible de uno que no arrancó, y el doble clic no
   deja dónde mirar.
"""

from __future__ import annotations

import os
import platform
import plistlib
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = ["Launcher", "create", "target_for"]

APP_NAME: Final[str] = "Nullcell"
BUNDLE_ID: Final[str] = "org.nullcell.desktop"


@dataclass(slots=True)
class Launcher:
    path: Path
    kind: str
    note: str = ""


def _executable() -> tuple[str, list[str]]:
    """Cómo arrancar el escritorio desde este mismo entorno.

    Prefiere el binario `ncell` que está junto al intérprete; si no existe
    —instalación rara, o ejecutado desde el repositorio— vale `python -m`.
    """
    here = Path(sys.executable).parent
    binary = here / ("ncell.exe" if os.name == "nt" else "ncell")
    if binary.is_file():
        return str(binary), ["desktop"]
    return sys.executable, ["-m", "nullcell.cli.main", "desktop"]


def target_for(system: str | None = None) -> Path:
    """Dónde va el lanzador en este sistema."""
    system = system or sys.platform
    if system == "darwin":
        return Path.home() / "Applications" / f"{APP_NAME}.app"
    if system.startswith("win"):
        return Path.home() / "Desktop" / f"{APP_NAME}.bat"
    return Path.home() / ".local" / "share" / "applications" / "nullcell.desktop"


# --------------------------------------------------------------------- macOS
def _icon(destination: Path) -> bool:
    """Dibuja el icono y lo convierte a `.icns`. Falla en silencio.

    Vive en `nullcell.desktop` porque usa Qt, que es un extra opcional: tenerlo
    aquí hacía que el paquete base pareciera depender de PySide6, y un test de
    empaquetado lo dijo antes de que llegara a romperle la instalación a nadie.
    """
    try:
        from nullcell.desktop.icon import write_icns
    except ImportError:
        return False
    return write_icns(destination)


def _macos(target: Path, extra: list[str]) -> Launcher:
    contents = target / "Contents"
    macos, resources = contents / "MacOS", contents / "Resources"
    for folder in (macos, resources):
        folder.mkdir(parents=True, exist_ok=True)

    binary, args = _executable()
    parts = [binary, *args, *extra]

    # `arch -arm64` (o -x86_64) y no a secas: LaunchServices arranca el bundle
    # en la arquitectura que le parece, y en un Mac Apple Silicon eligió
    # **x86_64**. Los módulos nativos del entorno son arm64, así que el
    # programa moría con «incompatible architecture» antes de imprimir nada —
    # y sólo al hacer doble clic, porque desde la terminal hereda la
    # arquitectura correcta. Se fija la que está corriendo *ahora*, que es por
    # definición aquella para la que se compiló este entorno.
    machine = platform.machine()
    if shutil.which("arch") and machine in ("arm64", "x86_64"):
        parts = ["/usr/bin/arch", f"-{machine}", *parts]
    command = " ".join(f'"{part}"' for part in parts)

    script = f"""#!/bin/sh
# Generado por `ncell launcher`. Apunta al intérprete que lo creó, porque el
# Finder no lee tu shell y un `ncell` a secas no existiría aquí.
salida=$({command} 2>&1) || osascript -e "display dialog \\"Nullcell no pudo \\
arrancar:

$salida\\" buttons {{\\"Cerrar\\"}} with icon stop with title \\"Nullcell\\""
"""
    launcher = macos / APP_NAME
    launcher.write_text(script, encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    info = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleVersion": "1",
        "CFBundleExecutable": APP_NAME,
        "CFBundlePackageType": "APPL",
        # Sin esto aparece un icono rebotando en el Dock para un programa que
        # vive en la barra de menús, que es exactamente lo que no queremos.
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        # Declarado también en el bundle, por si algún día el ejecutable deja
        # de ser un script de shell.
        "LSArchitecturePriority": [platform.machine()],
    }
    if _icon(resources / "nullcell.icns"):
        info["CFBundleIconFile"] = "nullcell.icns"
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))

    return Launcher(
        path=target,
        kind="macOS .app",
        note="Ábrelo desde el Launchpad o la carpeta Aplicaciones de tu usuario.",
    )


# --------------------------------------------------------------------- Linux
def _linux(target: Path, extra: list[str]) -> Launcher:
    binary, args = _executable()
    command = " ".join([binary, *args, *extra])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Red P2P cifrada y anónima\n"
        f"Exec={command}\n"
        "Terminal=false\n"
        "Categories=Network;\n"
        "StartupNotify=false\n",
        encoding="utf-8",
    )
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    return Launcher(path=target, kind=".desktop", note="Aparecerá en el menú de aplicaciones.")


# ------------------------------------------------------------------- Windows
def _windows(target: Path, extra: list[str]) -> Launcher:
    binary, args = _executable()
    command = " ".join(f'"{part}"' for part in [binary, *args, *extra])
    target.parent.mkdir(parents=True, exist_ok=True)
    # `start ""` evita que quede una ventana de consola abierta detrás.
    target.write_text(f'@echo off\r\nstart "" {command}\r\n', encoding="utf-8")
    return Launcher(path=target, kind=".bat", note="Haz doble clic en el acceso del Escritorio.")


def create(extra: list[str] | None = None, *, system: str | None = None) -> Launcher:
    """Crea el lanzador de este sistema. `extra` son argumentos para `desktop`."""
    system = system or sys.platform
    target = target_for(system)
    extra = list(extra or [])
    if system == "darwin":
        if target.exists():
            shutil.rmtree(target)  # un .app es una carpeta: reescribirlo no basta
        return _macos(target, extra)
    if system.startswith("win"):
        return _windows(target, extra)
    return _linux(target, extra)
