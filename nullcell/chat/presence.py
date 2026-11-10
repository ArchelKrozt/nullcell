"""Quién está en un canal — M10, sobre §7.

El «ping» que busca a otros programas activos no es un ping: es un registro de
TTL corto en el DHT bajo la clave del canal, que dice *«estoy aquí, y se me
alcanza en esta dirección»*. Quien quiera hablar consulta esa clave y encuentra
a los demás.

Por qué así y no un multicast o un ping directo:

* Un multicast sólo cruza la red local, así que serviría para la casa y para
  nada más.
* Un ping directo exige saber a quién, que es el problema que esto resuelve.
* El DHT ya está, ya se consulta **por circuito** (§7, decisión 8) y ya sabe
  caducar lo que nadie renueva.

Lo que cuesta, dicho claro: **la pertenencia a un canal es pública**. Cualquiera
puede consultar `ch:general` y ver qué direcciones dicen estar ahí. En un canal
abierto eso es inevitable —hay que poder encontrarse para hablar— y por eso la
dirección que se publica aquí es la del **chat**, no la de tu portal.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Final

from nullcell.chat.protocol import channel_name
from nullcell.core import ids
from nullcell.core.identity import Identity
from nullcell.dht import records as rec

__all__ = ["PRESENCE_TTL", "Member", "announce", "members"]

log = logging.getLogger(__name__)

#: Suficiente para no republicar sin parar, corto para que colgarse se note.
PRESENCE_TTL: Final[int] = 300
#: Se renueva antes de caducar, o hay un hueco en el que pareces desconectado.
RENEW_MARGIN: Final[int] = 60
#: Más miembros que esto no caben en una conversación que se pueda seguir.
MAX_MEMBERS: Final[int] = 64


@dataclass(slots=True)
class Member:
    """Alguien que dice estar en el canal."""

    pub: bytes
    x25519: bytes
    intro: list[str]
    nick: str
    ts: float

    @property
    def address(self) -> str:
        return ids.encode_nid(self.pub)

    def fresh(self, *, now: float | None = None) -> bool:
        return (time.time() if now is None else now) - self.ts <= PRESENCE_TTL


async def announce(dht: Any, identity: Identity, channel: str, *, nick: str, service: Any) -> int:
    """Publica «estoy en este canal». Devuelve en cuántas celdas quedó.

    Lleva los puntos de enlace del servicio de chat, que es lo que hace falta
    para alcanzarte: sin ellos la presencia diría quién está pero no por dónde,
    y habría que resolver un descriptor aparte para cada miembro.
    """
    channel = channel_name(channel)
    body = {
        "ch": channel,
        "n": nick,
        "x": service.x25519_public,
        "ip": [ref.as_dict() if hasattr(ref, "as_dict") else ref for ref in service.intro_points],
    }
    record = await rec.build(identity, rec.RecordType.PRESENCE, body, ttl=PRESENCE_TTL)
    stored = await dht.store_record(record)
    log.info("presencia en #%s publicada en %d celdas", channel, stored)
    return stored


async def members(dht: Any, channel: str, *, exclude: bytes | None = None) -> list[Member]:
    """Quién dice estar en el canal, ahora mismo.

    Se filtra por frescura aquí y no sólo por el TTL del DHT: un registro puede
    sobrevivir a su TTL en una celda que no ha hecho limpieza, y mostrar como
    presente a quien se fue hace horas es peor que no mostrarlo.
    """
    channel = channel_name(channel)
    found = await dht.find_value(rec.channel_key(channel))
    now = time.time()
    seen: dict[bytes, Member] = {}

    for record in sorted(found, key=lambda r: r.ts, reverse=True):
        if record.type is not rec.RecordType.PRESENCE:
            continue
        if exclude is not None and record.pub == exclude:
            continue
        body = record.body
        if body.get("ch") != channel:
            continue
        x25519, intro = body.get("x"), body.get("ip")
        if not (isinstance(x25519, bytes) and len(x25519) == 32):
            continue
        if not isinstance(intro, list) or not intro:
            continue
        member = Member(
            pub=record.pub,
            x25519=x25519,
            intro=intro,
            nick=str(body.get("n") or ids.short(ids.encode_nid(record.pub))),
            ts=float(record.ts),
        )
        if not member.fresh(now=now):
            continue
        # El más reciente de cada quien, no uno por registro.
        seen.setdefault(record.pub, member)

    return sorted(seen.values(), key=lambda m: m.ts, reverse=True)[:MAX_MEMBERS]
