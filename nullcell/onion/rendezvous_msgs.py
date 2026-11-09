"""Wire format for the rendezvous commands — §6.

§6 describes rendezvous in prose; this is the format that prose implies. CBOR,
like the DHT records, and every parse treats its input as hostile: an intro
point parses messages from strangers, and a service parses whatever an intro
point forwards.

Nothing here is encrypted by itself. ``INTRODUCE1`` travels inside a circuit to
the intro point, and the part meant only for the service is a Noise handshake
message the intro point cannot read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

import cbor2

from nullcell.core import ids
from nullcell.onion.circuit import Hop

__all__ = [
    "COOKIE_LEN",
    "INTRO_POINTS",
    "Cookie",
    "EstablishIntro",
    "Introduce",
    "IntroPointRef",
    "MessageError",
    "new_cookie",
]

#: §6 — three intro points, so losing one does not take the service down.
INTRO_POINTS: Final[int] = 3
COOKIE_LEN: Final[int] = 20
_MAX_ENDPOINT: Final[int] = 64

Cookie = bytes


class MessageError(Exception):
    """Malformed rendezvous message."""


def new_cookie() -> Cookie:
    import secrets

    return secrets.token_bytes(COOKIE_LEN)


def _decode(blob: bytes) -> dict[str, Any]:
    try:
        message = cbor2.loads(blob)
    except Exception as exc:
        raise MessageError(f"not valid CBOR: {exc}") from exc
    if not isinstance(message, dict):
        raise MessageError(f"message must be a map, got {type(message).__name__}")
    return message


def _key(message: dict[str, Any], name: str, length: int) -> bytes:
    value = message.get(name)
    if not (isinstance(value, bytes) and len(value) == length):
        raise MessageError(f"field {name!r} must be {length} bytes")
    return value


def _endpoints(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, str) and 0 < len(e) <= _MAX_ENDPOINT][:4]


@dataclass(frozen=True, slots=True)
class IntroPointRef:
    """One intro point, as a descriptor lists it (§6)."""

    pub: bytes
    x25519: bytes
    endpoints: list[str] = field(default_factory=list)

    @property
    def short(self) -> str:
        return ids.short(ids.encode_nid(self.pub))

    def as_hop(self) -> Hop:
        return Hop(pub=self.pub, x25519=self.x25519, endpoints=list(self.endpoints))

    def as_dict(self) -> dict[str, Any]:
        return {"pub": self.pub, "x": self.x25519, "ep": list(self.endpoints)}

    @classmethod
    def from_dict(cls, raw: Any) -> IntroPointRef | None:
        """Parse one entry, returning None rather than raising.

        A descriptor with one bad intro point still has two good ones, and §6
        gives a service three for exactly that reason.
        """
        if not isinstance(raw, dict):
            return None
        pub, x25519 = raw.get("pub"), raw.get("x")
        if not (isinstance(pub, bytes) and len(pub) == 32):
            return None
        if not (isinstance(x25519, bytes) and len(x25519) == 32):
            return None
        endpoints = _endpoints(raw.get("ep"))
        if not endpoints:
            return None
        return cls(pub=pub, x25519=x25519, endpoints=endpoints)


@dataclass(frozen=True, slots=True)
class EstablishIntro:
    """Service to intro point: *this circuit is mine* (§6).

    The signature binds the request to the service's long-term key, so an intro
    point will not hold a circuit open for someone claiming to be a service
    they are not.
    """

    service_pub: bytes
    signature: bytes

    def encode(self) -> bytes:
        return cbor2.dumps({"pub": self.service_pub, "sig": self.signature}, canonical=True)

    @classmethod
    def decode(cls, blob: bytes) -> EstablishIntro:
        message = _decode(blob)
        return cls(service_pub=_key(message, "pub", 32), signature=_key(message, "sig", 64))

    @staticmethod
    def binding(service_pub: bytes, intro_pub: bytes) -> bytes:
        """What the signature covers: the service and the intro point.

        Both ends, so a signature captured at one intro point cannot be
        replayed at another.
        """
        return b"nullcell-establish-intro-v1" + service_pub + intro_pub


@dataclass(frozen=True, slots=True)
class Introduce:
    """Client to intro point, forwarded to the service unchanged (§6).

    ``handshake`` is a Noise NK message for the *service*, not for the intro
    point: the intro point carries it and cannot read it, which is what keeps
    it from learning anything about the conversation it arranges.
    """

    service_pub: bytes
    rendezvous: IntroPointRef
    cookie: Cookie
    handshake: bytes

    def encode(self) -> bytes:
        return cbor2.dumps(
            {
                "pub": self.service_pub,
                "rp": self.rendezvous.as_dict(),
                "c": self.cookie,
                "hs": self.handshake,
            },
            canonical=True,
        )

    @classmethod
    def decode(cls, blob: bytes) -> Introduce:
        message = _decode(blob)
        rendezvous = IntroPointRef.from_dict(message.get("rp"))
        if rendezvous is None:
            raise MessageError("rendezvous point is missing or malformed")
        handshake = message.get("hs")
        if not isinstance(handshake, bytes) or not handshake:
            raise MessageError("handshake must be non-empty bytes")
        return cls(
            service_pub=_key(message, "pub", 32),
            rendezvous=rendezvous,
            cookie=_key(message, "c", COOKIE_LEN),
            handshake=handshake,
        )
