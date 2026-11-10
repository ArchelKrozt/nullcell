"""Signed DHT records — §7, §8.

Every value in the DHT is a signed envelope. Nothing is stored on trust: a node
that receives a record can check, locally and without asking anyone, that

* the signature matches the public key inside it,
* the key it is filed under is the one the signer is entitled to use,
* it is neither stale nor implausibly far in the future,
* and the publisher paid the proof of work for that record type.

There is no directory authority anywhere in this (§1), so these four checks are
the only thing standing between the DHT and anyone who wants to fill it.

**Verification order is a DoS decision, not style.** Checks run cheapest first:
shape, then key binding, then clock, then the Ed25519 signature (~50 us), and
only last the proof of work (~0,9 ms). Garbage costs an attacker's victim
microseconds instead of milliseconds.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

import cbor2

from nullcell.core import ids
from nullcell.core.identity import Identity
from nullcell.dht import pow as powmod

__all__ = [
    "MAX_RECORD_BYTES",
    "MAX_SKEW",
    "Record",
    "RecordError",
    "RecordExpired",
    "RecordKeyMismatch",
    "RecordMalformed",
    "RecordPowError",
    "RecordSignatureError",
    "RecordType",
    "build",
    "keyword_key",
    "name_key",
    "verify",
]

RECORD_VERSION: Final[int] = 1

#: Accepted clock skew in either direction (§16, relojes). We cannot trust the
#: system clock and will not depend on NTP, which is both a fingerprint and a
#: vector, so records are accepted within a window instead.
MAX_SKEW: Final[int] = 2 * 3600

#: A record that does not fit here cannot be stored. Keeps one publisher from
#: spending another node's memory.
MAX_RECORD_BYTES: Final[int] = 8 * 1024

_MIN_TTL: Final[int] = 60
_MAX_TTL: Final[int] = 24 * 3600


class RecordType(StrEnum):
    RELAY = "relay"
    SERVICE = "service"  # public portal: in the clear, indexed, enumerable
    PRIVATE = "private"  # private service: blinded index, encrypted body (§6)
    KEYWORD = "keyword"
    NAME = "name"
    PRESENCE = "presence"  # «estoy en este canal», con TTL corto (M10)


class RecordError(Exception):
    """Base class for every rejected record."""


class RecordMalformed(RecordError):
    """Wrong shape, wrong types, or too large."""


class RecordKeyMismatch(RecordError):
    """Filed under a key the signer is not entitled to use."""


class RecordExpired(RecordError):
    """Past its TTL, or too far in the future to be plausible."""


class RecordSignatureError(RecordError):
    """Signature does not match the embedded public key."""


class RecordPowError(RecordError):
    """Proof of work missing or below the difficulty for this record type."""


@dataclass(frozen=True, slots=True)
class Record:
    """A signed DHT record."""

    type: RecordType
    key: bytes
    pub: bytes
    ts: int
    ttl: int
    body: dict[str, Any]
    nonce: bytes = b""
    sig: bytes = b""
    version: int = RECORD_VERSION

    # ------------------------------------------------------------- encoding
    def unsigned_bytes(self) -> bytes:
        """Canonical encoding of everything the signature and PoW cover."""
        return cbor2.dumps(
            {
                "v": self.version,
                "t": str(self.type),
                "k": self.key,
                "pub": self.pub,
                "ts": self.ts,
                "ttl": self.ttl,
                "body": self.body,
            },
            canonical=True,
        )

    def encode(self) -> bytes:
        return cbor2.dumps(
            {
                "v": self.version,
                "t": str(self.type),
                "k": self.key,
                "pub": self.pub,
                "ts": self.ts,
                "ttl": self.ttl,
                "body": self.body,
                "pow": self.nonce,
                "sig": self.sig,
            },
            canonical=True,
        )

    @classmethod
    def decode(cls, blob: bytes) -> Record:
        if len(blob) > MAX_RECORD_BYTES:
            raise RecordMalformed(f"record is {len(blob)} bytes, max {MAX_RECORD_BYTES}")
        try:
            data = cbor2.loads(blob)
        except Exception as exc:
            raise RecordMalformed(f"not valid CBOR: {exc}") from exc
        if not isinstance(data, dict):
            raise RecordMalformed(f"record must be a map, got {type(data).__name__}")

        try:
            record_type = RecordType(data["t"])
        except (KeyError, ValueError) as exc:
            raise RecordMalformed(f"unknown record type: {data.get('t')!r}") from exc

        try:
            return cls(
                type=record_type,
                key=data["k"],
                pub=data["pub"],
                ts=int(data["ts"]),
                ttl=int(data["ttl"]),
                body=data["body"],
                nonce=data.get("pow", b""),
                sig=data.get("sig", b""),
                version=int(data.get("v", 0)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RecordMalformed(f"missing or malformed field: {exc}") from exc

    # --------------------------------------------------------------- helpers
    @property
    def expires_at(self) -> int:
        return self.ts + self.ttl

    def is_fresh(self, now: int | None = None) -> bool:
        """§9: a descriptor renewed within its TTL is the liveness signal."""
        return (now if now is not None else int(time.time())) < self.expires_at

    @property
    def age(self) -> int:
        return max(0, int(time.time()) - self.ts)

    @property
    def required_difficulty(self) -> int:
        """Minimum bits this record *type* must pay. Read from the live table."""
        return powmod.DIFFICULTY[str(self.type)]

    def measure_pow_bits(self) -> int:
        """Bits of work actually done on this record. Costs one argon2id hash.

        Distinct from :attr:`required_difficulty` on purpose: the required value
        comes from a global table that a protocol update could change, which
        would retroactively rewrite the standing of records already stored. The
        measured value is a property of the record itself.
        """
        return powmod.bits_of(self.unsigned_bytes(), self.nonce)

    @property
    def nid(self) -> str:
        return ids.encode_nid(self.pub)


# ----------------------------------------------------------------- key derivation
def name_key(name: str) -> bytes:
    """DHT key for a human-readable name (§8): ``sha256("name:" + name)``."""
    return hashlib.sha256(b"name:" + name.strip().lower().encode()).digest()


def channel_key(channel: str) -> bytes:
    """Clave DHT de un canal de chat (M10): ``sha256("ch:" + canal)``.

    Quien consulte esta clave ve **quién dice estar en el canal**. No se puede
    evitar en un canal abierto —hay que poder encontrarse para hablar— y por
    eso la presencia se publica con una identidad distinta de la del portal.
    """
    return hashlib.sha256(b"ch:" + channel.strip().lower().encode()).digest()


def keyword_key(word: str, shard: int = 0) -> bytes:
    """DHT key for a keyword, sharded (§8, §16).

    Popular words concentrate all their traffic and storage on the same 20
    nodes. Sharding spreads one word over 16 disjoint groups instead of
    punishing whoever happens to be responsible for "pan".
    """
    if not 0 <= shard < KEYWORD_SHARDS:
        raise ValueError(f"shard must be in 0..{KEYWORD_SHARDS - 1}")
    return hashlib.sha256(f"kw:{word.strip().lower()}:{shard}".encode()).digest()


KEYWORD_SHARDS: Final[int] = 16


def _expected_key(record_type: RecordType, pub: bytes, body: dict[str, Any]) -> bytes | None:
    """The key a record of this type must be filed under, or None if free.

    This is what stops a publisher from filing their own signed record under
    someone else's key — the cheapest possible attack on a DHT without it.
    """
    if record_type in (RecordType.RELAY, RecordType.SERVICE):
        return ids.routing_key(pub)
    if record_type is RecordType.PRIVATE:
        # The `pub` field holds the *blinded* key, so the binding can be
        # checked without anyone learning the real one (§6). The storing node
        # therefore refuses a record filed under an index that does not match
        # the blinded key it carries.
        period = body.get("p")
        if not isinstance(period, int):
            return None
        import hashlib

        return hashlib.sha256(
            b"nullcell-index" + pub + period.to_bytes(8, "big", signed=True)
        ).digest()
    if record_type is RecordType.NAME:
        name = body.get("name")
        return name_key(name) if isinstance(name, str) else None
    if record_type is RecordType.PRESENCE:
        channel = body.get("ch")
        return channel_key(channel) if isinstance(channel, str) else None
    return None  # KEYWORD: the key is the word, not the publisher


# ------------------------------------------------------------------ construction
async def build(
    identity: Identity,
    record_type: RecordType,
    body: dict[str, Any],
    *,
    key: bytes | None = None,
    ttl: int = 3600,
    ts: int | None = None,
) -> Record:
    """Build, prove and sign a record.

    Solves the proof of work in a process pool, so this is safe to await from
    the event loop even though it takes seconds (§16).
    """
    if not _MIN_TTL <= ttl <= _MAX_TTL:
        raise RecordMalformed(f"ttl must be {_MIN_TTL}..{_MAX_TTL} seconds, got {ttl}")

    if key is None:
        key = _expected_key(record_type, identity.public_key, body)
        if key is None:
            raise RecordMalformed(f"{record_type} records need an explicit key")

    draft = Record(
        type=record_type,
        key=key,
        pub=identity.public_key,
        ts=int(time.time()) if ts is None else ts,
        ttl=ttl,
        body=body,
    )

    challenge = draft.unsigned_bytes()
    nonce = await powmod.solve_async(challenge, draft.required_difficulty)
    # The signature covers the same bytes as the PoW, so the nonce cannot be
    # lifted onto a different record.
    sig = identity.sign(challenge)
    signed = Record(
        type=draft.type,
        key=draft.key,
        pub=draft.pub,
        ts=draft.ts,
        ttl=draft.ttl,
        body=draft.body,
        nonce=nonce,
        sig=sig,
    )
    if len(signed.encode()) > MAX_RECORD_BYTES:
        raise RecordMalformed("record exceeds the size limit once signed")
    return signed


# ------------------------------------------------------------------ verification
def verify(record: Record, *, now: int | None = None, check_pow: bool = True) -> int:
    """Validate a record fully and return the proof-of-work bits measured.

    The return value is what :meth:`Store.put` scores evictions against, so a
    caller that has already verified never pays for a second argon2id hash.
    Raises a :class:`RecordError` subclass on any failure.

    ``check_pow=False`` returns 0 and exists for tests and for re-validating a
    record this node already accepted; never use it on anything arriving from
    the network.
    """
    now = int(time.time()) if now is None else now

    # 1. shape — free
    if record.version != RECORD_VERSION:
        raise RecordMalformed(f"record version {record.version} not supported")
    if len(record.pub) != 32:
        raise RecordMalformed(f"public key is {len(record.pub)} bytes, expected 32")
    if len(record.key) != 32:
        raise RecordMalformed(f"key is {len(record.key)} bytes, expected 32")
    if len(record.sig) != 64:
        raise RecordSignatureError(f"signature is {len(record.sig)} bytes, expected 64")
    if not isinstance(record.body, dict):
        raise RecordMalformed("body must be a map")
    if not _MIN_TTL <= record.ttl <= _MAX_TTL:
        raise RecordMalformed(f"ttl out of range: {record.ttl}")

    # 2. key binding — one hash
    expected = _expected_key(record.type, record.pub, record.body)
    if expected is not None and record.key != expected:
        raise RecordKeyMismatch(
            f"{record.type} record filed under the wrong key "
            f"({record.key.hex()[:16]}…, expected {expected.hex()[:16]}…)"
        )

    # 3. clock — free (§16)
    if record.ts > now + MAX_SKEW:
        raise RecordExpired(f"timestamp is {record.ts - now} s in the future")
    if record.expires_at < now - MAX_SKEW:
        raise RecordExpired(f"expired {now - record.expires_at} s ago")

    # 4. signature — ~50 us
    challenge = record.unsigned_bytes()
    if not Identity.verify(record.pub, challenge, record.sig):
        raise RecordSignatureError("signature does not match the embedded public key")

    # 5. proof of work — ~0,9 ms, so it goes last
    if not check_pow:
        return 0
    try:
        return powmod.verify(challenge, record.nonce, record.required_difficulty)
    except powmod.PowError as exc:
        raise RecordPowError(str(exc)) from exc


# -------------------------------------------------------------- typed bodies
@dataclass(slots=True)
class RelayBody:
    """Body of a relay descriptor (§7)."""

    endpoints: list[str] = field(default_factory=list)
    x25519: bytes = b""
    bandwidth: int = 0
    versions: list[int] = field(default_factory=lambda: [1])
    flags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ep": self.endpoints,
            "x": self.x25519,
            "bw": self.bandwidth,
            "pv": self.versions,
            "fl": self.flags,
        }
