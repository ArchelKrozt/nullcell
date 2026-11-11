"""Entering the network — §1, §7.

Order: ``bootstrap.json`` shipped with the build, then peers saved from the last
session, then mDNS on the LAN, then PEX with whoever answers.

§1 names this the one semi-centralised point in the design, the same as in Tor
or Bitcoin: a node that knows nobody has to learn about someone from somewhere.
Saying so is the honest part. What keeps it from being an authority is that
seeds are only *introductions* — they sign nothing, vouch for nothing, and every
record learned through them is verified independently (§7).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Final

from nullcell.core import ids
from nullcell.dht.routing_table import Node

__all__ = ["BOOTSTRAP_FILE", "load_bootstrap", "load_peers", "save_peers"]

BOOTSTRAP_FILE: Final[str] = "bootstrap.json"
MAX_SAVED_PEERS: Final[int] = 256

log = logging.getLogger("nullcell.discovery")


def _parse_entries(entries: Any, source: str) -> list[Node]:
    """Turn a list of ``{"nid": ..., "endpoints": [...]}`` into nodes.

    Everything here is untrusted input — a bundled file can be stale and a saved
    file can be corrupt — so one bad entry is skipped rather than fatal.
    """
    nodes: list[Node] = []
    if not isinstance(entries, list):
        log.warning("%s: expected a list of peers", source)
        return nodes

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        nid, endpoints = entry.get("nid"), entry.get("endpoints")
        if not isinstance(nid, str) or not isinstance(endpoints, list):
            continue
        try:
            address = ids.decode_nid(nid)
        except ids.AddressError as exc:
            log.warning("%s: skipping %s: %s", source, nid[:12], exc)
            continue
        clean = [e for e in endpoints if isinstance(e, str)][:4]
        if clean:
            nodes.append(Node(pub=address.pub, endpoints=clean))
    return nodes


def load_bootstrap(path: Path | None = None) -> list[Node]:
    """Read the seed list shipped with the build."""
    path = Path(__file__).resolve().parents[2] / BOOTSTRAP_FILE if path is None else path
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("%s is unreadable: %s", path, exc)
        return []
    return _parse_entries(data.get("peers") if isinstance(data, dict) else data, str(path))


def load_peers(path: Path) -> list[Node]:
    """Read peers remembered from previous sessions (``~/.nullcell/peers.db``)."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("%s is unreadable, starting fresh: %s", path, exc)
        return []
    return _parse_entries(data, str(path))


def save_peers(path: Path, nodes: list[Node]) -> None:
    """Remember peers for the next start, **añadiendo** a lo que ya había.

    Sobrescribir era una pérdida silenciosa. La tabla de rutas expulsa a quien
    falla tres veces seguidas —correcto en un DHT, porque una tabla llena de
    nodos muertos no encuentra nada— pero guardar *sólo lo que hay en la
    tabla* convertía un apagón de un minuto en un olvido permanente: la celda
    por la que entraste a la red desaparecía del disco porque justo entonces
    no contestaba, y al reiniciar ya no conocías a nadie.

    Esto no resucita a los muertos. Una entrada vieja se reintenta y, si sigue
    sin responder, se vuelve a expulsar de la tabla; lo único que cambia es
    que la dirección sobrevive para poder reintentarla.
    """
    fresh = {
        node.nid: {"nid": node.nid, "endpoints": list(node.endpoints)}
        for node in nodes
        if node.endpoints
    }
    # Lo de ahora primero: si una celda cambió de dirección, manda la nueva.
    merged = dict(fresh)
    for old_node in load_peers(path):
        if old_node.nid not in merged and old_node.endpoints:
            merged[old_node.nid] = {
                "nid": old_node.nid,
                "endpoints": list(old_node.endpoints),
            }

    entries = list(merged.values())[:MAX_SAVED_PEERS]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    tmp.replace(path)
