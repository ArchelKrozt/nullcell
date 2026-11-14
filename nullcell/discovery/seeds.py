"""Gestor e intercambio de semillas — §7, entrada a la red.

Una semilla es una **presentación**: `<dirección>@host:puerto` de una celda que
contesta. No firma nada ni avala nada (§7), y todo lo aprendido por ella se
verifica aparte. Lo que este módulo añade es que las semillas **circulen**:

* cada celda guarda las suyas en ``semillas.json`` —las que trae el programa,
  las que añadió su dueño y las que le pasaron otras—;
* periódicamente le ofrece a un relevo las que ha **visto contestar** y recibe
  las que le faltan (operación ``seeds`` del DHT, también por circuito);
* así una semilla que alguien añade a mano acaba llegando a todo el que entra,
  y la red deja de depender de una sola puerta.

Contra el envenenamiento —que alguien reparta direcciones falsas para aislar a
los recién llegados (eclipse, §10)—:

* sólo se **comparte** lo que esta celda ha visto contestar con su identidad
  verificada (el certificado del enlace es la dirección, §4);
* lo recibido entra como «compartida» y **sin verificar**: no se reparte hasta
  que contesta, y si falla varias veces seguidas se olvida sola;
* topes: por intercambio, en total, y por red (/16) para lo compartido, de modo
  que nadie llene la lista con un solo proveedor;
* lo que añadió el dueño **nunca** se borra solo.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

from nullcell.core import ids
from nullcell.dht.routing_table import Node

__all__ = [
    "MAX_OFFER",
    "MAX_REPLY",
    "MAX_SEEDS",
    "Seed",
    "SeedBook",
    "parse_line",
]

log = logging.getLogger("nullcell.seeds")

SEEDS_FILE: Final[str] = "semillas.json"
#: Cuántas se ofrecen y cuántas se devuelven en un intercambio.
MAX_OFFER: Final[int] = 16
MAX_REPLY: Final[int] = 32
#: Tope del gestor. Las del dueño no cuentan para el recorte.
MAX_SEEDS: Final[int] = 128
#: Compartidas por red /16 (IPv4) o /48 (IPv6): diversidad, como en §7.
MAX_PER_PREFIX: Final[int] = 4
#: Una compartida que no contesta tantas veces seguidas se olvida.
MAX_FAILS: Final[int] = 5
#: Qué se considera «contestó hace poco» para compartirla.
FRESH: Final[float] = 7 * 24 * 3600.0

SOURCES: Final[tuple[str, ...]] = ("incluida", "tuya", "compartida")


def parse_line(line: str) -> tuple[str, str] | None:
    """``<dirección>[.null]@host:puerto`` → ``(nid, endpoint)``, o None."""
    if not isinstance(line, str) or len(line) > 200:
        return None
    nid, _, endpoint = line.strip().partition("@")
    nid = ids.parse_hostname(nid)
    if not nid or not endpoint or ":" not in endpoint:
        return None
    try:
        ids.decode_nid(nid)
        from nullcell.net.link_manager import parse_endpoint

        parse_endpoint(endpoint)
    except Exception:  # noqa: BLE001 - cualquier cosa rara es «no es una semilla»
        return None
    return nid, endpoint


def _prefix(endpoint: str) -> str:
    """La red de un endpoint, para la diversidad: /16 en IPv4, /48 en IPv6."""
    import ipaddress

    host = endpoint.rsplit(":", 1)[0].strip("[]")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host  # dominio padre
    if address.version == 4:
        return str(ipaddress.ip_network(f"{address}/16", strict=False))
    return str(ipaddress.ip_network(f"{address}/48", strict=False))


def _public(endpoint: str) -> bool:
    from nullcell.core import reach

    return reach.classify(endpoint) == "internet"


@dataclass(slots=True)
class Seed:
    nid: str
    endpoint: str
    source: str = "compartida"
    note: str = ""
    added: float = field(default_factory=time.time)
    last_ok: float | None = None
    last_try: float | None = None
    fails: int = 0

    @property
    def line(self) -> str:
        return f"{self.nid}@{self.endpoint}"

    @property
    def verified(self) -> bool:
        return self.last_ok is not None and time.time() - self.last_ok < FRESH

    @property
    def node(self) -> Node:
        return Node(pub=ids.decode_nid(self.nid).pub, endpoints=[self.endpoint])

    def as_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body["line"] = self.line
        body["verified"] = self.verified
        body["public"] = _public(self.endpoint)
        return body


class SeedBook:
    """Las semillas de esta celda, persistidas en ``semillas.json``."""

    def __init__(self, path: Path, *, own_nid: str | None = None) -> None:
        self.path = Path(path)
        self.own_nid = own_nid
        self._seeds: dict[str, Seed] = {}
        self.stats = {"recibidas": 0, "nuevas": 0, "compartidas": 0, "olvidadas": 0}

    # ---------------------------------------------------------- persistencia
    def load(self) -> SeedBook:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = []
        except (OSError, ValueError) as exc:
            log.warning("%s ilegible, se empieza en limpio: %s", self.path, exc)
            raw = []
        for entry in raw if isinstance(raw, list) else []:
            if not isinstance(entry, dict):
                continue
            parsed = parse_line(f"{entry.get('nid')}@{entry.get('endpoint')}")
            if parsed is None:
                continue
            nid, endpoint = parsed
            seed = Seed(
                nid=nid,
                endpoint=endpoint,
                source=entry.get("source") if entry.get("source") in SOURCES else "compartida",
                note=str(entry.get("note") or "")[:80],
                added=float(entry.get("added") or time.time()),
                last_ok=_num(entry.get("last_ok")),
                last_try=_num(entry.get("last_try")),
                fails=int(entry.get("fails") or 0),
            )
            self._seeds[seed.line] = seed
        return self

    def save(self) -> None:
        data = [
            {k: v for k, v in asdict(s).items()}
            for s in sorted(self._seeds.values(), key=lambda s: s.added)
        ]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.tmp")
            tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("no se pudo guardar %s: %s", self.path, exc)

    # ------------------------------------------------------------- consulta
    def __len__(self) -> int:
        return len(self._seeds)

    def __iter__(self):
        return iter(list(self._seeds.values()))

    def get(self, line: str) -> Seed | None:
        parsed = parse_line(line)
        return self._seeds.get(f"{parsed[0]}@{parsed[1]}") if parsed else None

    def describe(self) -> list[dict[str, Any]]:
        orden = {"tuya": 0, "incluida": 1, "compartida": 2}
        return [
            s.as_dict()
            for s in sorted(
                self._seeds.values(), key=lambda s: (orden.get(s.source, 3), -(s.last_ok or 0))
            )
        ]

    def nodes(self) -> list[Node]:
        """Para entrar a la red: primero las que contestaron, luego el resto."""
        seeds = sorted(self._seeds.values(), key=lambda s: (not s.verified, s.fails))
        out: list[Node] = []
        for seed in seeds:
            with contextlib.suppress(Exception):
                out.append(seed.node)
        return out

    def shareable(self, limit: int = MAX_REPLY, *, exclude: set[str] | None = None) -> list[str]:
        """Lo que se puede repartir: visto contestar hace poco, y público."""
        exclude = exclude or set()
        good = [
            s
            for s in self._seeds.values()
            if s.verified and _public(s.endpoint) and s.line not in exclude
        ]
        random.shuffle(good)
        return [s.line for s in good[:limit]]

    # ------------------------------------------------------------- cambios
    def add(self, line: str, *, source: str = "tuya", note: str = "") -> Seed:
        """Añadir una semilla. Si ya estaba, se respeta el origen más fuerte."""
        parsed = parse_line(line)
        if parsed is None:
            raise ValueError("no tiene la forma dirección@host:puerto")
        nid, endpoint = parsed
        if nid == self.own_nid:
            raise ValueError("es la dirección de esta misma celda")
        key = f"{nid}@{endpoint}"
        seed = self._seeds.get(key)
        if seed is None:
            seed = self._seeds[key] = Seed(nid=nid, endpoint=endpoint, source=source)
        elif SOURCES.index(source) < SOURCES.index(seed.source) or source == "tuya":
            seed.source = source  # «tuya» o «incluida» pesan más que «compartida»
        if note:
            seed.note = note[:80]
        return seed

    def remove(self, line: str) -> bool:
        parsed = parse_line(line)
        return bool(parsed) and self._seeds.pop(f"{parsed[0]}@{parsed[1]}", None) is not None

    def merge(self, lines: Any) -> list[Seed]:
        """Lo que pasó otra celda: entra **sin verificar**, con topes."""
        if not isinstance(lines, list):
            return []
        nuevas: list[Seed] = []
        por_red: dict[str, int] = {}
        for s in self._seeds.values():
            if s.source == "compartida":
                por_red[_prefix(s.endpoint)] = por_red.get(_prefix(s.endpoint), 0) + 1
        for line in lines[:MAX_REPLY]:
            self.stats["recibidas"] += 1
            parsed = parse_line(line) if isinstance(line, str) else None
            if parsed is None or parsed[0] == self.own_nid:
                continue
            key = f"{parsed[0]}@{parsed[1]}"
            if key in self._seeds or not _public(parsed[1]):
                continue
            red = _prefix(parsed[1])
            if por_red.get(red, 0) >= MAX_PER_PREFIX:
                continue
            if len(self._seeds) >= MAX_SEEDS and not self._make_room():
                break
            seed = self._seeds[key] = Seed(nid=parsed[0], endpoint=parsed[1], source="compartida")
            por_red[red] = por_red.get(red, 0) + 1
            nuevas.append(seed)
        self.stats["nuevas"] += len(nuevas)
        return nuevas

    def mark(self, line: str, ok: bool) -> None:
        seed = self._seeds.get(line)
        if seed is None:
            return
        seed.last_try = time.time()
        if ok:
            seed.last_ok, seed.fails = seed.last_try, 0
            return
        seed.fails += 1
        if seed.source == "compartida" and seed.fails >= MAX_FAILS and not seed.verified:
            del self._seeds[line]
            self.stats["olvidadas"] += 1

    def _make_room(self) -> bool:
        """Olvidar la compartida peor para dejar sitio. Nunca una tuya."""
        candidatas = [s for s in self._seeds.values() if s.source == "compartida"]
        if not candidatas:
            return False
        peor = max(candidatas, key=lambda s: (not s.verified, s.fails, -(s.last_ok or 0)))
        del self._seeds[peor.line]
        self.stats["olvidadas"] += 1
        return True

    # --------------------------------------------------------- bootstrap
    def seed_from_bootstrap(self, nodes: list[Node]) -> None:
        """Las que trae el programa (`bootstrap.json`), como «incluida»."""
        for node in nodes:
            for endpoint in node.endpoints[:1]:
                with contextlib.suppress(ValueError):
                    self.add(f"{ids.encode_nid(node.pub)}@{endpoint}", source="incluida")

    def note_bootstrap(self, table_pubs: set[bytes]) -> None:
        """Tras entrar: las que respondieron están en la tabla de rutas."""
        for seed in list(self._seeds.values()):
            try:
                pub = ids.decode_nid(seed.nid).pub
            except Exception:  # noqa: BLE001
                continue
            self.mark(seed.line, pub in table_pubs)


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- en marcha
#: Primer intercambio poco después de entrar; luego, cada tanto.
EXCHANGE_FIRST: Final[float] = 20.0
EXCHANGE_EVERY: Final[float] = 900.0
#: Con cuántas celdas se intercambia en cada ronda.
EXCHANGE_PEERS: Final[int] = 3


async def verify(
    node: Any, book: SeedBook, *, only_pending: bool = True, relays: Any = None
) -> int:
    """Comprobar que las semillas contestan **con su identidad**.

    Un ping necesita un enlace verificado contra la dirección (§4): una semilla
    falsa —otra máquina en esa IP, o una dirección inventada— no pasa. Con el
    DHT por circuitos (un navegador anónimo) sólo se comprueban las que son
    relevos conocidos, porque a las demás no hay circuito que llegue: fallar
    ahí no diría nada de ellas.
    """
    import asyncio

    anonymous = bool(getattr(node.transport, "anonymous", False))
    pendientes = [s for s in book if not (only_pending and s.verified)]
    if anonymous:
        conocidos = {c.pub for c in relays.candidates()} if relays is not None else set()
        pendientes = [s for s in pendientes if _pub(s) in conocidos]
    ok = 0
    sem = asyncio.Semaphore(4)

    async def una(seed: Seed) -> None:
        nonlocal ok
        async with sem:
            try:
                vivo = await asyncio.wait_for(node.ping(seed.node), 12)
            except Exception:  # noqa: BLE001
                vivo = False
        book.mark(seed.line, vivo)
        ok += int(vivo)

    await asyncio.gather(*(una(s) for s in pendientes))
    if pendientes:
        book.save()
    return ok


async def exchange_once(node: Any, book: SeedBook, *, peers: int = EXCHANGE_PEERS) -> int:
    """Una ronda: intercambiar con unas pocas celdas que contestan."""
    candidatas = [s.node for s in book if s.verified]
    vistos = {n.pub for n in candidatas}
    candidatas += [n for n in node.table.all_nodes() if n.endpoints and n.pub not in vistos]
    random.shuffle(candidatas)
    nuevas = 0
    for peer in candidatas[:peers]:
        with contextlib.suppress(Exception):
            nuevas += await node.exchange_seeds(peer)
    return nuevas


async def run_forever(
    node: Any,
    book: SeedBook,
    *,
    relays: Any = None,
    first: float = EXCHANGE_FIRST,
    every: float = EXCHANGE_EVERY,
) -> None:
    """Tarea de fondo: intercambiar y verificar, para siempre."""
    import asyncio

    await asyncio.sleep(first)
    while True:
        try:
            nuevas = await exchange_once(node, book)
            if nuevas:
                log.info("semillas: %d nuevas compartidas por la red", nuevas)
            await verify(node, book, relays=relays)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - una ronda fallida no para el bucle
            log.debug("ronda de semillas fallida: %s", exc)
        await asyncio.sleep(every)


def _pub(seed: Seed) -> bytes | None:
    try:
        return ids.decode_nid(seed.nid).pub
    except Exception:  # noqa: BLE001
        return None
