"""A deployment you can carry — §8, §12.

Everything that makes *your* presence on the network lives in one folder, so
moving from a laptop to a Raspberry Pi is a copy and not a reconstruction:

    mi-despliegue/
      nullcell.json     qué portales publicar y con qué nombre
      identity.key      (opcional) la identidad de esta celda
      sitios/           (opcional) el contenido, si lo quieres dentro

**The address is the key.** `nullcell.null` is not a registration you own at a
registrar; it is a name in the DHT bound to an Ed25519 key, and the portal's
own address is that key encoded (§3). Move the folder without
`identity.key` and you get a different cell with a different address, and the
name points at the old one until its record expires. So the single most
important thing this module does is keep the key and the configuration
together, and say so loudly when they are apart.

That is also why the key is a **risk you are carrying**: a deployment folder on
a USB stick is a private key on a USB stick. The file is written 0600 and
`warnings()` says what it finds, because a quiet permission is how a key ends
up world-readable in a backup.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

__all__ = ["CONFIG_NAME", "Deployment", "Portal"]

log = logging.getLogger(__name__)

CONFIG_NAME: Final[str] = "nullcell.json"
VERSION: Final[int] = 1


@dataclass(slots=True)
class Portal:
    """One folder published under one name."""

    folder: str
    name: str | None = None
    title: str = ""
    desc: str = ""
    tags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"folder": self.folder}
        for key in ("name", "title", "desc"):
            if value := getattr(self, key):
                body[key] = value
        if self.tags:
            body["tags"] = list(self.tags)
        return body

    @classmethod
    def from_dict(cls, body: dict[str, Any]) -> Portal | None:
        folder = body.get("folder")
        if not isinstance(folder, str) or not folder.strip():
            return None
        tags = body.get("tags")
        return cls(
            folder=folder,
            name=body.get("name") or None,
            title=str(body.get("title") or ""),
            desc=str(body.get("desc") or ""),
            tags=[str(t) for t in tags] if isinstance(tags, list) else [],
        )


@dataclass(slots=True)
class Deployment:
    """The contents of a deployment folder."""

    root: Path
    portals: list[Portal] = field(default_factory=list)
    sharing: bool = False

    # --------------------------------------------------------------- rutas
    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_NAME

    @property
    def identity_path(self) -> Path | None:
        """The key carried *with* this deployment, if it is carried at all."""
        candidate = self.root / "identity.key"
        return candidate if candidate.is_file() else None

    def folder_for(self, portal: Portal) -> Path:
        """A portal's folder, resolved **relative to the deployment**.

        Relative paths are what make the folder portable: an absolute
        `/Users/alguien/sitio` stops existing the moment it reaches the
        Raspberry Pi, and the failure arrives as an empty portal rather than as
        an error about a path.
        """
        folder = Path(portal.folder).expanduser()
        return folder if folder.is_absolute() else (self.root / folder)

    # -------------------------------------------------------------- cargar
    @classmethod
    def load(cls, root: str | Path) -> Deployment:
        """Read a deployment folder. A missing config is an empty deployment."""
        root = Path(root).expanduser().resolve()
        deployment = cls(root=root)
        try:
            body = json.loads(deployment.config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return deployment
        except (OSError, ValueError) as exc:
            raise ValueError(f"{deployment.config_path} no se puede leer: {exc}") from exc

        if not isinstance(body, dict):
            raise ValueError(f"{deployment.config_path} debería contener un objeto JSON")
        version = body.get("version", VERSION)
        if not isinstance(version, int) or version > VERSION:
            raise ValueError(
                f"{deployment.config_path} dice versión {version} y este programa "
                f"entiende hasta la {VERSION}: actualiza nullcell antes de usarlo"
            )

        entries = body.get("portals")
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and (portal := Portal.from_dict(entry)):
                    deployment.portals.append(portal)
        deployment.sharing = bool(body.get("sharing", False))
        return deployment

    def save(self) -> None:
        """Write it back, keeping it readable: people edit this by hand."""
        self.root.mkdir(parents=True, exist_ok=True)
        body = {
            "version": VERSION,
            "sharing": self.sharing,
            "portals": [p.as_dict() for p in self.portals],
        }
        self.config_path.write_text(
            json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    # ------------------------------------------------------------- revisar
    def warnings(self) -> list[str]:
        """Everything wrong that can be said before anything is published."""
        found: list[str] = []
        for portal in self.portals:
            folder = self.folder_for(portal)
            if not folder.is_dir():
                found.append(f"{portal.name or portal.folder}: no existe la carpeta {folder}")
                continue
            if not (folder / "index.html").is_file():
                found.append(f"{portal.name or portal.folder}: {folder} no tiene index.html")
            if Path(portal.folder).is_absolute():
                found.append(
                    f"{portal.name or portal.folder}: la ruta {portal.folder} es absoluta, "
                    "así que no sobrevivirá a mover esta carpeta a otra máquina"
                )

        key = self.identity_path
        if key is None:
            found.append(
                "esta carpeta no lleva identity.key, así que al moverla a otra máquina la "
                "celda tendrá OTRA dirección .null y el nombre seguirá apuntando a la vieja "
                "hasta que su registro caduque"
            )
        elif sys.platform != "win32":
            mode = stat.S_IMODE(key.stat().st_mode)
            if mode & 0o077:
                found.append(f"{key} tiene modo {mode:04o}; debería ser 0600")
        return found

    def adopt_identity(self, source: Path) -> Path:
        """Copy an identity **into** the deployment, so it travels with it."""
        target = self.root / "identity.key"
        self.root.mkdir(parents=True, exist_ok=True)
        data = Path(source).read_bytes()
        handle = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        log.info("identidad copiada a %s", target)
        return target
