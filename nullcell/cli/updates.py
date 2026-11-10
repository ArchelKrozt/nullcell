"""Saber si hay una versión nueva — §9, §12.

Un comprobador de actualizaciones es una petición periódica a un servidor
ajeno. Eso, en este proyecto, es exactamente lo que §9 se niega a hacer por una
tipografía web: **le dice a ese servidor que hay un nodo Nullcell en esta IP**,
todos los días, con la regularidad de un reloj. Para un relevo que corre meses
en un servidor, es un registro de su existencia que nadie pidió.

De ahí las dos reglas de este módulo:

1. **Nunca comprueba solo.** No hay tarea de fondo, no hay comprobación al
   arrancar y no hay nada que activar por error. Sólo ocurre cuando alguien
   escribe `ncell update`, que es un consentimiento inequívoco.
2. **Dice lo que va a hacer antes de hacerlo**, con el dominio al que
   contactará. Una herramienta de anonimato que contacta servidores sin
   decirlo enseña exactamente la costumbre equivocada.

> **Lo que debería ser, y aún no es:** publicar las versiones como registros
> firmados en el propio DHT. La consulta iría por circuito (§7, decisión 8),
> así que nadie sabría quién pregunta, y la firma del mantenedor sustituiría a
> la confianza en GitHub. Hace falta una red con gente para que sirva de algo,
> así que de momento queda escrito aquí y no implementado.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Final

__all__ = [
    "RELEASES_URL",
    "NoReleases",
    "Release",
    "compare",
    "fetch_latest",
    "upgrade_command",
]


class NoReleases(ConnectionError):
    """El proyecto aún no ha publicado ninguna versión."""


log = logging.getLogger(__name__)

RELEASES_URL: Final[str] = "https://api.github.com/repos/ArchelKrozt/nullcell/releases/latest"
HOST: Final[str] = "api.github.com"
TIMEOUT: Final[float] = 10.0
#: Genérico a propósito: el agente no debería distinguir a este nodo de otro.
USER_AGENT: Final[str] = "nullcell"


@dataclass(slots=True)
class Release:
    version: str
    url: str = ""
    notes: str = ""


def _parts(version: str) -> tuple[int, ...]:
    """`v1.2.3` → (1, 2, 3). Lo que no sea número se ignora."""
    found = re.findall(r"\d+", version or "")
    return tuple(int(n) for n in found[:4]) or (0,)


def compare(installed: str, latest: str) -> int:
    """-1 si la instalada es más vieja, 0 si igual, 1 si más nueva.

    Una instalada **más nueva** que la publicada es normal cuando se corre
    desde el repositorio, y decir «estás al día» ahí sería engañoso.
    """
    a, b = _parts(installed), _parts(latest)
    pad = max(len(a), len(b))
    a += (0,) * (pad - len(a))
    b += (0,) * (pad - len(b))
    return (a > b) - (a < b)


def fetch_latest(url: str = RELEASES_URL) -> Release:
    """Pregunta por la última versión publicada. **Hace una petición de red.**

    Sólo se llama desde `ncell update`, nunca sola.
    """
    request = urllib.request.Request(  # noqa: S310 - url fija, https
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # Un proyecto sin versiones publicadas todavía. No es un fallo de
            # red y tratarlo como tal manda a revisar la conexión para nada.
            raise NoReleases(
                "el proyecto todavía no publica versiones: sigue el repositorio "
                "para enterarte de la primera"
            ) from None
        raise ConnectionError(f"no se pudo preguntar por la última versión: {exc}") from None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise ConnectionError(f"no se pudo preguntar por la última versión: {exc}") from None

    tag = body.get("tag_name") or body.get("name")
    if not isinstance(tag, str) or not tag.strip():
        raise ConnectionError("la respuesta no dice qué versión es la última")
    return Release(
        version=tag.strip().lstrip("vV"),
        url=str(body.get("html_url") or ""),
        notes=str(body.get("body") or "")[:600],
    )


def upgrade_command(executable: str) -> str:
    """Cómo actualizar **esta** instalación, que no es una sola forma.

    Un `pip install --upgrade` sobre una instalación editable no hace nada
    visible y deja a la persona convencida de que ya actualizó.
    """
    from pathlib import Path

    import nullcell

    source = Path(nullcell.__file__).resolve().parents[1]
    if (source / ".git").is_dir():
        return f"cd {source} && git pull && {executable} -m pip install -e ."
    return f"{executable} -m pip install --upgrade nullcell"
