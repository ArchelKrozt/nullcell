"""Node paths and configuration — see NULLCELL-ARQUITECTURA.md §12.

``NULLCELL_HOME`` overrides the state directory, which is what the test suite and
``nullcell-lab`` use to run many nodes on one machine.
"""

from __future__ import annotations

import os
import stat
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

__all__ = ["Config", "cloud_synced_warning", "home", "load"]

DEFAULT_HOME: Final[str] = "~/.nullcell"
DIR_MODE: Final[int] = 0o700

# Paths whose contents a sync client will happily upload and corrupt.
_CLOUD_MARKERS: Final[tuple[str, ...]] = (
    "Mobile Documents/com~apple~CloudDocs",
    "Library/CloudStorage",
    "Dropbox",
    "Google Drive",
    "OneDrive",
)


def home() -> Path:
    """The node state directory, created with mode 700 if missing."""
    path = Path(os.environ.get("NULLCELL_HOME", DEFAULT_HOME)).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        current = stat.S_IMODE(path.stat().st_mode)
        if current != DIR_MODE:
            path.chmod(DIR_MODE)
    return path


def cloud_synced_warning(path: Path | None = None) -> str | None:
    """Return a warning if the state directory sits inside a sync folder (§12).

    Syncing ``identity.key`` copies a private key off the machine, and syncing the
    SQLite databases corrupts them.
    """
    target = str((path or home()).resolve())
    for marker in _CLOUD_MARKERS:
        if marker in target:
            return (
                f"~/.nullcell está dentro de una carpeta sincronizada ({marker}). "
                "Mueve el directorio: sincronizar identity.key saca tu clave privada "
                "de la máquina y sincronizar las bases SQLite las corrompe."
            )
    return None


@dataclass(slots=True)
class Config:
    """Node configuration. Only the M0/M1 keys exist so far."""

    listen_host: str = "0.0.0.0"
    listen_port: int = 4433
    relay_enabled: bool = False  # opt-in, always (§10)
    relay_max_mbps: int = 0  # 0 = sin límite declarado
    log_level: str = "info"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def path_identity(self) -> Path:
        return home() / "identity.key"

    @property
    def path_peers(self) -> Path:
        return home() / "peers.db"

    @property
    def path_config(self) -> Path:
        return home() / "config.toml"


def load() -> Config:
    """Load ``~/.nullcell/config.toml``, falling back to defaults."""
    cfg = Config()
    path = cfg.path_config
    if not path.exists():
        return cfg

    with path.open("rb") as fh:
        data = tomllib.load(fh)

    node = data.get("node", {})
    relay = data.get("relay", {})
    cfg.listen_host = node.get("listen_host", cfg.listen_host)
    cfg.listen_port = int(node.get("listen_port", cfg.listen_port))
    cfg.log_level = node.get("log_level", cfg.log_level)
    cfg.relay_enabled = bool(relay.get("enabled", cfg.relay_enabled))
    cfg.relay_max_mbps = int(relay.get("max_mbps", cfg.relay_max_mbps))
    cfg.extra = {k: v for k, v in data.items() if k not in ("node", "relay")}
    return cfg
