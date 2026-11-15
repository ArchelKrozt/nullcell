"""Un canal de chat — M10.

Tipo IRC: te unes a un nombre, ves quién está y lo que se dice mientras estás.
Sin historial que alguien tenga que guardar, porque guardarlo significa que
*alguien* lo guarda, y esa es una propiedad que una red así no debería tener
por defecto.

Cómo funciona, en una frase: cada miembro publica su presencia en el DHT, abre
una sesión de rendezvous con los demás y **reparte cada mensaje a todos**. Sin
servidor, sin un nodo que «tenga» el canal y sin nadie por quien pase todo.

Lo que eso implica, y conviene que esté escrito aquí y no sólo en la cabeza:

* **No llegan los mensajes de antes de entrar.** Como en IRC. Lo contrario
  exige que alguien almacene y reenvíe, y eso es un servidor con otro nombre.
* **Escala a un grupo, no a una multitud.** Reparto completo: N miembros son
  N-1 envíos por mensaje. A partir de unas decenas haría falta otra cosa, y
  fingir que no sería mentir sobre la forma del problema.
* **Estar en el canal es público** (ver `presence`). El contenido no.
* **Apodos como en IRC.** Si al entrar alguien ya usa tu apodo, el tuyo pasa
  a `apodo2`, `apodo3`… Lo conserva quien llegó antes; un empate se decide por
  la clave, igual en las dos celdas, para que no cambien de nombre las dos.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Final

from nullcell.chat import presence
from nullcell.chat.protocol import (
    MAX_NICK,
    MAX_TEXT,
    Message,
    MessageError,
    channel_name,
    clean_nick,
)
from nullcell.core.identity import Identity

__all__ = ["HISTORY", "Channel"]

log = logging.getLogger(__name__)

#: Lo que se guarda en memoria para pintar la ventana. Nada en disco.
HISTORY: Final[int] = 300
#: Cada cuánto se vuelve a mirar quién está y se renueva la propia presencia.
REFRESH: Final[float] = 60.0
#: Un miembro que no acepta una sesión no se reintenta más seguido que esto.
RETRY_AFTER: Final[float] = 120.0


class Enlace:
    """Una sesión con otro miembro, con mensajes enteros en vez de trozos.

    El chat va sobre sesiones de rendezvous **multiplexadas**, que hablan en
    *streams* y no tienen `send` ni `on_data`: el canal llamaba a
    `session.send`, fallaba en silencio y descartaba la sesión, así que ningún
    mensaje cruzaba. Y aun con una sesión simple, un mensaje largo llega en
    varios trozos: cada uno se tomaba por un mensaje entero y se rechazaba.

    Aquí cada mensaje va con su longitud delante (4 bytes) y se rearma al
    llegar, sobre un stream propio en las multiplexadas.
    """

    MAX: Final[int] = MAX_TEXT * 8

    def __init__(self, session: Any, entregar: Callable[[bytes], Any]) -> None:
        self.session = session
        self.entregar = entregar
        self._stream: Any = None
        self._buf = bytearray()
        if hasattr(session, "open"):  # MultiplexedSession
            session.on_stream = self._escuchar
        else:
            session.on_data = self._recibir

    def _escuchar(self, stream: Any) -> None:
        stream.on_data = self._recibir

    def _recibir(self, data: bytes) -> None:
        self._buf += bytes(data)
        while len(self._buf) >= 4:
            largo = int.from_bytes(self._buf[:4], "big")
            if largo > self.MAX:
                log.debug("mensaje de chat de %d bytes: se corta la sesión", largo)
                self._buf.clear()
                self.close()
                return
            if len(self._buf) < 4 + largo:
                return
            mensaje = bytes(self._buf[4 : 4 + largo])
            del self._buf[: 4 + largo]
            self.entregar(mensaje)

    async def send(self, raw: bytes) -> None:
        datos = len(raw).to_bytes(4, "big") + raw
        if hasattr(self.session, "open"):
            if self._stream is None or self._stream.closed:
                self._stream = await self.session.open(b"chat")
                self._stream.on_data = self._recibir
            await self._stream.write(datos)
        else:
            await self.session.send(datos)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.session.close()


class Channel:
    """Un canal al que esta celda está unida."""

    def __init__(
        self,
        name: str,
        identity: Identity,
        *,
        nick: str = "",
        dht: Any = None,
        service: Any = None,
        open_session: Any = None,
    ) -> None:
        self.name = channel_name(name)
        self.identity = identity
        self.nick = clean_nick(nick, fallback=identity.short)
        #: El apodo que se pidió; `nick` es el que se usa (puede llevar número).
        self.wanted = self.nick
        # Redondeada como se publica: compararla sin redondear rompería el desempate.
        self.since = round(time.time(), 3)
        self.dht = dht
        self.service = service
        self.open_session = open_session

        self.history: deque[Message] = deque(maxlen=HISTORY)
        self.members: list[presence.Member] = []
        self.on_message: Callable[[Message], None] | None = None
        self.stats = {"sent": 0, "received": 0, "rejected": 0, "delivered": 0, "failed": 0}

        self._seen: deque[str] = deque(maxlen=HISTORY * 4)
        self._seen_set: set[str] = set()
        self._sessions: dict[bytes, Any] = {}
        self._last_try: dict[bytes, float] = {}
        self._tasks: list[asyncio.Task] = []
        #: Sesiones que otros abrieron hacia esta celda: sólo se escucha por ellas.
        self._entrantes: list[Enlace] = []

    # ---------------------------------------------------------- recepción
    def accept(self, raw: bytes) -> Message | None:
        """Un mensaje que llega por una sesión. Verifica, deduplica, guarda."""
        try:
            message = Message.decode(raw)
        except MessageError as bad:
            self.stats["rejected"] += 1
            log.debug("mensaje rechazado: %s", bad)
            return None

        if message.channel != self.name:
            self.stats["rejected"] += 1
            return None
        if message.mid in self._seen_set:
            return None  # ya estaba: repartir a todos hace que vuelvan copias

        self._remember(message.mid)
        self.history.append(message)
        self.stats["received"] += 1
        if self.on_message is not None:
            self.on_message(message)
        return message

    def _remember(self, mid: str) -> None:
        if len(self._seen) == self._seen.maxlen:
            self._seen_set.discard(self._seen[0])
        self._seen.append(mid)
        self._seen_set.add(mid)

    def attach(self, session: Any) -> None:
        """Engancha una sesión entrante: lo que llegue por ella es del canal."""
        self._entrantes.append(Enlace(session, self.accept))

    # ------------------------------------------------------------- envío
    async def say(self, text: str) -> Message:
        """Escribe en el canal. Se guarda local aunque no llegue a nadie."""
        message = Message.create(self.identity, self.name, self.nick, text)
        self._remember(message.mid)
        self.history.append(message)
        self.stats["sent"] += 1
        if self.on_message is not None:
            self.on_message(message)
        await self.broadcast(message)
        return message

    async def broadcast(self, message: Message) -> int:
        """Reparte a todos los miembros alcanzables. Devuelve cuántos la recibieron."""
        raw = message.encode()
        targets = list(self._sessions.items())
        if not targets:
            return 0
        results = await asyncio.gather(
            *(self._send_to(pub, session, raw) for pub, session in targets),
            return_exceptions=True,
        )
        delivered = sum(1 for r in results if r is True)
        self.stats["delivered"] += delivered
        return delivered

    async def _send_to(self, pub: bytes, session: Any, raw: bytes) -> bool:
        try:
            await session.send(raw)
        except Exception as exc:  # noqa: BLE001 - un miembro caído no corta el canal
            log.debug("no se pudo entregar a %s: %s", pub.hex()[:8], exc)
            self.stats["failed"] += 1
            self._sessions.pop(pub, None)
            return False
        return True

    # -------------------------------------------------------- mantenimiento
    async def refresh(self) -> int:
        """Vuelve a mirar quién está, se anuncia y conecta con los nuevos."""
        if self.dht is None:
            return 0
        # Primero quién está, para no anunciarse con un apodo que ya tiene otro.
        self.members = await presence.members(self.dht, self.name, exclude=self.identity.public_key)
        self.resolve_nick()
        if self.service is not None:
            with contextlib.suppress(Exception):
                await presence.announce(
                    self.dht,
                    self.identity,
                    self.name,
                    nick=self.nick,
                    service=self.service,
                    since=self.since,
                )
            with contextlib.suppress(Exception):
                await presence.announce_directory(
                    self.dht, self.identity, self.name, nick=self.nick
                )
        await self._connect_to_new()
        return len(self.members)

    def resolve_nick(self) -> str:
        """Si alguien que llegó antes ya usa este apodo, toma el siguiente número.

        Como en IRC: `ana` → `ana2` → `ana3`. Sólo cede quien llegó después
        (o, a igual hora, la clave mayor), así que dos celdas que entran a la
        vez con el mismo apodo no se renombran las dos.
        """
        yo = (self.since, self.identity.public_key)
        antes = {m.nick.casefold() for m in self.members if (m.since, m.pub) < yo}
        if self.nick.casefold() not in antes:
            return self.nick
        usados = {m.nick.casefold() for m in self.members}
        n = 2
        while True:
            sufijo = str(n)
            candidato = self.wanted[: MAX_NICK - len(sufijo)] + sufijo
            if candidato.casefold() not in usados:
                break
            n += 1
        log.info("el apodo %s ya está en #%s: pasas a %s", self.nick, self.name, candidato)
        self.nick = candidato
        return self.nick

    async def _connect_to_new(self) -> None:
        if self.open_session is None:
            return
        now = time.time()
        for member in self.members:
            if member.pub in self._sessions:
                continue
            if now - self._last_try.get(member.pub, 0.0) < RETRY_AFTER:
                continue
            self._last_try[member.pub] = now
            try:
                session = await self.open_session(member)
            except Exception as exc:  # noqa: BLE001
                log.debug("no se pudo abrir sesión con %s: %s", member.nick, exc)
                continue
            self._sessions[member.pub] = Enlace(session, self.accept)

    async def run_forever(self, interval: float = REFRESH) -> None:
        # La primera vuelta ya la hizo quien entró al canal.
        while True:
            await asyncio.sleep(interval)
            with contextlib.suppress(Exception):
                await self.refresh()

    def spawn(self, runtime: Any) -> None:
        self._tasks.append(runtime.spawn(self.run_forever()))

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        for session in [*self._sessions.values(), *self._entrantes]:
            with contextlib.suppress(Exception):
                session.close()
        self._sessions.clear()
        self._entrantes.clear()

    # --------------------------------------------------------------- vista
    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return [
            {
                "mid": m.mid,
                "nick": m.nick,
                "text": m.text,
                "ts": m.ts,
                "author": m.author,
                "mine": m.pub == self.identity.public_key,
            }
            for m in list(self.history)[-limit:]
        ]

    def describe(self) -> dict[str, Any]:
        return {
            "channel": self.name,
            "nick": self.nick,
            "wanted": self.wanted,
            "address": self.identity.hostname,
            "members": [
                {"nick": m.nick, "address": m.address, "connected": m.pub in self._sessions}
                for m in self.members
            ],
            "connected": len(self._sessions),
            "stats": dict(self.stats),
        }
