"""Service descriptors — §8.

The record a portal publishes so it can be reached and, if it is public, found:

.. code-block:: json

    {
      "id": "k7f2qz...xq", "kind": "web", "name": "panaderia",
      "title": "La Panadería", "desc": "Pan, pedidos y recetas",
      "tags": ["comida", "tienda"], "lang": "es",
      "intro_points": ["m4x...", "p9z...", "q2w..."],
      "ts": 1762560000, "ttl": 3600, "pow": "00000a3f...", "sig": "ed25519(...)"
    }

The signature, timestamp, TTL and proof of work come from the record envelope
(§7), so they are not repeated in the body. What is here is the part a client
needs to *reach* the service — the intro points and the key for the end-to-end
handshake — plus the metadata the search index of §8 reads.

Metadata is bounded on parse. It is attacker-controlled text that ends up in a
search index and, eventually, on a screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from nullcell.core import ids
from nullcell.dht.records import Record, RecordType
from nullcell.onion.rendezvous_msgs import INTRO_POINTS, IntroPointRef

__all__ = ["MAX_TAGS", "MAX_TEXT", "ServiceDescriptor"]

MAX_TEXT: Final[int] = 120
MAX_DESC: Final[int] = 400
MAX_TAGS: Final[int] = 8
_KINDS: Final[frozenset[str]] = frozenset({"web", "chat", "files", "other"})


def _text(value: Any, limit: int) -> str:
    """Bounded text from an untrusted descriptor."""
    return value[:limit] if isinstance(value, str) else ""


@dataclass(slots=True)
class ServiceDescriptor:
    """What a portal publishes about itself."""

    pub: bytes
    x25519: bytes
    intro_points: list[IntroPointRef] = field(default_factory=list)
    kind: str = "web"
    name: str = ""
    title: str = ""
    desc: str = ""
    tags: list[str] = field(default_factory=list)
    lang: str = ""

    @property
    def nid(self) -> str:
        return ids.encode_nid(self.pub)

    @property
    def short(self) -> str:
        return ids.short(self.nid)

    @property
    def hostname(self) -> str:
        return self.nid + ids.SUFFIX

    # ------------------------------------------------------------------ wire
    def body(self) -> dict[str, Any]:
        return {
            "x": self.x25519,
            "ip": [point.as_dict() for point in self.intro_points[:INTRO_POINTS]],
            "kind": self.kind,
            "name": self.name[:MAX_TEXT],
            "title": self.title[:MAX_TEXT],
            "desc": self.desc[:MAX_DESC],
            "tags": [t[:MAX_TEXT] for t in self.tags[:MAX_TAGS]],
            "lang": self.lang[:8],
        }

    @classmethod
    def from_body(cls, pub: bytes, body: dict[str, Any]) -> ServiceDescriptor | None:
        """Parse a descriptor body. None if it cannot be reached with it."""
        if not isinstance(body, dict):
            return None
        x25519 = body.get("x")
        if not (isinstance(x25519, bytes) and len(x25519) == 32):
            return None

        raw_points = body.get("ip")
        points: list[IntroPointRef] = []
        if isinstance(raw_points, list):
            for entry in raw_points[:INTRO_POINTS]:
                point = IntroPointRef.from_dict(entry)
                if point is not None:
                    points.append(point)
        if not points:
            return None  # unreachable without at least one intro point

        kind = body.get("kind")
        tags = body.get("tags")
        return cls(
            pub=pub,
            x25519=x25519,
            intro_points=points,
            kind=kind if kind in _KINDS else "other",
            name=_text(body.get("name"), MAX_TEXT),
            title=_text(body.get("title"), MAX_TEXT),
            desc=_text(body.get("desc"), MAX_DESC),
            tags=[t[:MAX_TEXT] for t in tags[:MAX_TAGS] if isinstance(t, str)]
            if isinstance(tags, list)
            else [],
            lang=_text(body.get("lang"), 8),
        )

    @classmethod
    def from_record(cls, record: Record, *, pub: bytes | None = None) -> ServiceDescriptor | None:
        """Parse a verified descriptor record.

        For a public portal the record carries the real key. For a private
        service the record's ``pub`` is the *blinded* key (§6), so the caller
        passes the real one — which it has, because it knew the address.
        """
        if record.type is RecordType.SERVICE:
            return cls.from_body(record.pub, record.body)
        if record.type is RecordType.PRIVATE and pub is not None:
            return cls.from_body(pub, record.body)
        return None

    # -------------------------------------------------------------- searching
    def keywords(self) -> list[str]:
        """Words this portal should be indexed under (§8).

        Title, description and tags, lowercased and deduplicated. The index is
        built from these at publication time, so searching works from day one
        with no crawler.
        """
        words: set[str] = set()
        for source in (self.name, self.title, self.desc, " ".join(self.tags)):
            for raw in source.lower().split():
                word = "".join(ch for ch in raw if ch.isalnum())
                if len(word) >= 3:
                    words.add(word)
        return sorted(words)
