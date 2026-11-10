"""Mensajes de chat: formato, firma y límites — M10.

Un mensaje es un objeto pequeño, firmado por quien lo escribe, que viaja por
una sesión de rendezvous ya cifrada. La firma no está por confidencialidad
—eso ya lo da la sesión— sino porque en un canal **cualquiera puede hablarte**:
sin firma, quien te entrega un mensaje puede decir que lo escribió otro.

Tres límites, cada uno contra algo concreto:

* **Tamaño.** Un mensaje de un mega llena la pantalla de todo el canal y la
  memoria de quien lo recibe.
* **Antigüedad.** Un mensaje con fecha de hace un año, o de dentro de un año,
  se rechaza: sin eso se puede reordenar una conversación a voluntad.
* **Identificador.** Cada mensaje lleva uno derivado de su contenido, así que
  reenviarlo mil veces sigue siendo un solo mensaje en pantalla.
"""

from __future__ import annotations

import hashlib
import json
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Final

from nullcell.core import ids
from nullcell.core.identity import Identity

__all__ = [
    "MAX_NICK",
    "MAX_TEXT",
    "Message",
    "MessageError",
    "channel_name",
    "clean_nick",
]

#: Un mensaje más largo que esto se rechaza al recibirlo.
MAX_TEXT: Final[int] = 2000
MAX_NICK: Final[int] = 24
MAX_CHANNEL: Final[int] = 32
#: Fuera de esta ventana, el reloj de quien lo envía no es creíble.
MAX_SKEW: Final[float] = 300.0
MAX_AGE: Final[float] = 86_400.0


class MessageError(ValueError):
    """El mensaje no se puede aceptar. El motivo se enseña."""


def channel_name(raw: str) -> str:
    """Normaliza `#General` → `general`.

    Sin normalizar, `#General` y `#general` son canales distintos con la misma
    pinta, que es la forma más barata de partir una conversación en dos sin
    que nadie entienda por qué.
    """
    text = unicodedata.normalize("NFKC", (raw or "").strip()).lstrip("#").lower()
    text = "".join(c for c in text if c.isalnum() or c in "-_")[:MAX_CHANNEL]
    if not text:
        raise MessageError("un canal necesita un nombre")
    return text


def clean_nick(raw: str, fallback: str = "anon") -> str:
    """Un apodo imprimible y acotado.

    Los caracteres de control se quitan porque un apodo con un `\\r` reescribe
    la línea de al lado en cualquier vista de texto, y las marcas de dirección
    bidireccional porque reordenan lo que se lee sin cambiar lo que dice.
    """
    text = unicodedata.normalize("NFKC", (raw or "").strip())
    text = "".join(c for c in text if unicodedata.category(c)[0] != "C")
    text = "".join(c for c in text if c not in "‪‫‬‭‮⁦⁧⁨⁩")
    text = " ".join(text.split())[:MAX_NICK]
    return text or fallback


@dataclass(slots=True)
class Message:
    """Una línea de chat, firmada por quien la escribió."""

    channel: str
    nick: str
    text: str
    ts: float
    pub: bytes
    sig: bytes = b""

    # ------------------------------------------------------------ identidad
    @property
    def mid(self) -> str:
        """Identificador derivado del contenido: reenviarlo no lo duplica."""
        return hashlib.sha256(self._signed_bytes() + self.pub).hexdigest()[:16]

    @property
    def author(self) -> str:
        """La dirección de quien lo escribió, que es su identidad real."""
        return ids.encode_nid(self.pub)

    def _signed_bytes(self) -> bytes:
        """Exactamente lo que se firma. Canónico, o la firma no verifica."""
        return json.dumps(
            {
                "c": self.channel,
                "n": self.nick,
                "t": self.text,
                "ts": round(self.ts, 3),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    # --------------------------------------------------------------- crear
    @classmethod
    def create(cls, identity: Identity, channel: str, nick: str, text: str) -> Message:
        body = (text or "").strip()
        if not body:
            raise MessageError("un mensaje vacío no se envía")
        if len(body) > MAX_TEXT:
            raise MessageError(f"el mensaje pasa de {MAX_TEXT} caracteres")
        message = cls(
            channel=channel_name(channel),
            nick=clean_nick(nick, fallback=identity.short),
            text=body,
            ts=time.time(),
            pub=identity.public_key,
        )
        message.sig = identity.sign(message._signed_bytes())
        return message

    # ---------------------------------------------------------- transporte
    def as_dict(self) -> dict[str, Any]:
        return {
            "c": self.channel,
            "n": self.nick,
            "t": self.text,
            "ts": round(self.ts, 3),
            "pub": self.pub.hex(),
            "sig": self.sig.hex(),
        }

    def encode(self) -> bytes:
        return json.dumps(self.as_dict(), separators=(",", ":"), ensure_ascii=False).encode()

    @classmethod
    def decode(cls, raw: bytes, *, now: float | None = None) -> Message:
        """Lee y **verifica**. Un mensaje que no pasa aquí no entra en memoria."""
        if len(raw) > MAX_TEXT * 4:
            raise MessageError("el mensaje es demasiado grande")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise MessageError(f"no es un mensaje legible: {exc}") from None
        if not isinstance(body, dict):
            raise MessageError("no es un mensaje")

        try:
            pub = bytes.fromhex(str(body["pub"]))
            sig = bytes.fromhex(str(body["sig"]))
            message = cls(
                channel=channel_name(str(body["c"])),
                nick=clean_nick(str(body["n"])),
                text=str(body["t"]),
                ts=float(body["ts"]),
                pub=pub,
                sig=sig,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MessageError(f"le falta algo o está mal formado: {exc}") from None

        if len(pub) != 32:
            raise MessageError("la clave del autor no mide 32 bytes")
        if len(message.text) > MAX_TEXT:
            raise MessageError(f"el mensaje pasa de {MAX_TEXT} caracteres")

        now = time.time() if now is None else now
        if message.ts > now + MAX_SKEW:
            raise MessageError("viene del futuro")
        if message.ts < now - MAX_AGE:
            raise MessageError("es demasiado viejo")

        # Lo último, porque es lo más caro: no se gasta una verificación de
        # firma en algo que ya se iba a rechazar por la forma.
        if not Identity.verify(pub, message._signed_bytes(), sig):
            raise MessageError("la firma no corresponde a su autor")
        return message
