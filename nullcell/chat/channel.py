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
from nullcell.chat.protocol import Message, MessageError, channel_name, clean_nick
from nullcell.core.identity import Identity

__all__ = ["HISTORY", "Channel"]

log = logging.getLogger(__name__)

#: Lo que se guarda en memoria para pintar la ventana. Nada en disco.
HISTORY: Final[int] = 300
#: Cada cuánto se vuelve a mirar quién está y se renueva la propia presencia.
REFRESH: Final[float] = 60.0
#: Un miembro que no acepta una sesión no se reintenta más seguido que esto.
RETRY_AFTER: Final[float] = 120.0


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

        def deliver(data: bytes) -> None:
            self.accept(bytes(data))

        session.on_data = deliver

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
        if self.service is not None:
            with contextlib.suppress(Exception):
                await presence.announce(
                    self.dht, self.identity, self.name, nick=self.nick, service=self.service
                )
        self.members = await presence.members(self.dht, self.name, exclude=self.identity.public_key)
        await self._connect_to_new()
        return len(self.members)

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
            session.on_data = lambda data: self.accept(bytes(data))
            self._sessions[member.pub] = session

    async def run_forever(self, interval: float = REFRESH) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self.refresh()
            await asyncio.sleep(interval)

    def spawn(self, runtime: Any) -> None:
        self._tasks.append(runtime.spawn(self.run_forever()))

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        for session in self._sessions.values():
            with contextlib.suppress(Exception):
                session.close()
        self._sessions.clear()

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
            "address": self.identity.hostname,
            "members": [
                {"nick": m.nick, "address": m.address, "connected": m.pub in self._sessions}
                for m in self.members
            ],
            "connected": len(self._sessions),
            "stats": dict(self.stats),
        }
