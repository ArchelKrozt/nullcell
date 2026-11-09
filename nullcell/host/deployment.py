"""A deployment you can carry — §8, §12.

Everything that makes *your* presence on the network lives in one folder, so
moving from a laptop to a Raspberry Pi is a copy and not a reconstruction:

    mi-despliegue/
      nullcell.toml     lo que TÚ declaras: portales, nombres (o .json)
      estado.json       lo que el programa cambia en marcha
      identity.key      (opcional) la identidad de esta celda
      sitios/           (opcional) el contenido, si lo quieres dentro

**La declaración es tuya y el programa no la reescribe nunca.** Esa separación
no es orden por gusto: cuando un programa reescribe el fichero que una persona
edita a mano, se lleva por delante sus comentarios y su formato, y lo hace en
silencio. Ya pasó una vez aquí —guardar convertía un `../mi-web` escrito a mano
en una ruta absoluta y borraba el título— y la respuesta correcta no era
guardar con más cuidado, era dejar de guardar ahí. Lo que el panel cambia en
marcha (si estás compartiendo ancho de banda) va a `estado.json`, que es del
programa y nadie edita.

TOML se prefiere a JSON por una razón concreta: **admite comentarios**, y un
fichero de configuración que no puede explicarse a sí mismo acaba explicado en
otro sitio que se desactualiza.

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
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

__all__ = ["CONFIG_NAME", "Deployment", "Portal"]

log = logging.getLogger(__name__)

#: Se busca en este orden. TOML primero: admite comentarios.
CONFIG_NAMES: Final[tuple[str, ...]] = ("nullcell.toml", "nullcell.json")
CONFIG_NAME: Final[str] = CONFIG_NAMES[0]
#: Lo que cambia en marcha, separado de lo que declara la persona.
STATE_NAME: Final[str] = "estado.json"
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
        """The declaration this deployment was read from, if any exists."""
        for name in CONFIG_NAMES:
            candidate = self.root / name
            if candidate.is_file():
                return candidate
        return self.root / CONFIG_NAME

    @property
    def state_path(self) -> Path:
        return self.root / STATE_NAME

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
        path = deployment.config_path
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return deployment
        except OSError as exc:
            raise ValueError(f"{path} no se puede leer: {exc}") from exc

        try:
            body = tomllib.loads(raw) if path.suffix == ".toml" else json.loads(raw)
        except (tomllib.TOMLDecodeError, ValueError) as exc:
            raise ValueError(f"{path} no se puede leer: {exc}") from exc

        if not isinstance(body, dict):
            raise ValueError(f"{path} debería contener una tabla")
        version = body.get("version", VERSION)
        if not isinstance(version, int) or version > VERSION:
            raise ValueError(
                f"{path} dice versión {version} y este programa entiende hasta la "
                f"{VERSION}: actualiza nullcell antes de usarlo"
            )

        entries = body.get("portals")
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and (portal := Portal.from_dict(entry)):
                    deployment.portals.append(portal)
        deployment.sharing = bool(body.get("sharing", False))
        deployment._load_state()
        return deployment

    def _load_state(self) -> None:
        """Whatever the panel toggled last time. Never fatal."""
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("%s ilegible, se ignora: %s", self.state_path, exc)
            return
        if isinstance(state, dict) and "sharing" in state:
            self.sharing = bool(state["sharing"])

    # ------------------------------------------------------------ encontrar
    @staticmethod
    def candidates(home: Path | None = None) -> list[Path]:
        """Where a deployment is looked for, in order.

        Having to pass `--config` every time is a configuration file you have
        to remember the location of, which is most of the problem a
        configuration file was meant to solve.
        """
        home = home or (Path.home() / ".nullcell")
        here = Path.cwd()
        found = [
            here,  # la carpeta actual, si ya es un despliegue
            here / "null-despliegue",
            here / "despliegue",
            home / "despliegue",
        ]
        remembered = Deployment.remembered(home)
        return ([remembered] if remembered else []) + found

    @staticmethod
    def remembered(home: Path | None = None) -> Path | None:
        """The last deployment used, so it is not asked for twice."""
        home = home or (Path.home() / ".nullcell")
        try:
            body = json.loads((home / "despliegue.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        path = body.get("root") if isinstance(body, dict) else None
        return Path(path) if isinstance(path, str) and path else None

    def remember_location(self, home: Path | None = None) -> None:
        """Note where this deployment lives, for the next start."""
        home = home or (Path.home() / ".nullcell")
        try:
            home.mkdir(parents=True, exist_ok=True)
            (home / "despliegue.json").write_text(
                json.dumps({"root": str(self.root)}, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            log.warning("no se pudo recordar la carpeta de despliegue: %s", exc)

    @classmethod
    def discover(cls, home: Path | None = None) -> Deployment | None:
        """The first candidate that actually holds a declaration, or None.

        Returning None is a real answer — "nobody has set one up yet" — and the
        caller is expected to ask rather than invent one. A broken declaration
        is *not* skipped: it is raised, because silently moving on to the next
        candidate would hide a typo in the file the operator is editing right
        now.
        """
        seen: set[Path] = set()
        for candidate in cls.candidates(home):
            root = Path(candidate).expanduser()
            if not root.is_dir():
                continue
            resolved = root.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if any((resolved / name).is_file() for name in CONFIG_NAMES):
                return cls.load(resolved)
        return None

    def save(self) -> None:
        """Write back **only what the program owns**.

        The declaration is never touched. A program that rewrites the file a
        person edits by hand destroys their comments and their formatting, and
        does it silently; here it also once turned a hand-written `../mi-web`
        into an absolute path, which is the exact thing the warnings tell
        people not to write.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps({"version": VERSION, "sharing": self.sharing}, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_declaration(self, *, portals: list[Portal] | None = None) -> Path:
        """Create a starter declaration. Only used when there is none.

        Writes JSON rather than TOML on purpose: a generated TOML would have no
        comments, which is the only reason to prefer TOML, and this project
        does not carry a TOML writer to gain nothing.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        self.portals = portals if portals is not None else self.portals
        target = self.root / "nullcell.json"
        target.write_text(
            json.dumps(
                {"version": VERSION, "portals": [p.as_dict() for p in self.portals]},
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return target

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
