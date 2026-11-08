"""Nullcell addresses (NIDs) — see NULLCELL-ARQUITECTURA.md §3.

An address carries the Ed25519 public key *itself*, not a hash of it::

    NID = base32_nopad( pub(32) || checksum(2) || version(1) )   -> 56 chars

    checksum = sha256(b"nullcell-addr" || pub || version)[:2]
    version  = bits 0..6  address format version (currently 1)
               bit 7      service class: 0 = public portal, 1 = private service

35 bytes is exactly 56 base32 characters, so there is never any padding to strip.

Carrying the key (rather than its hash) is what makes blinding of private
services possible (§6), lets a peer be verified without first fetching its
descriptor (§4), and gives us a checksum that catches typos before any network
traffic happens.

The DHT routing key is deliberately a *different* value, ``sha256(pub)``, so the
Kademlia keyspace stays uniformly distributed and blinded indices (§6) can be
computed over the blinded key instead.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Final

__all__ = [
    "ADDR_VERSION",
    "NID_LEN",
    "PRIVATE_FLAG",
    "SUFFIX",
    "SUFFIX_ALT",
    "Address",
    "AddressChecksumError",
    "AddressError",
    "AddressFormatError",
    "AddressVersionError",
    "decode_nid",
    "encode_nid",
    "parse_hostname",
    "routing_key",
    "short",
]

ADDR_VERSION: Final[int] = 1
PRIVATE_FLAG: Final[int] = 0x80
VERSION_MASK: Final[int] = 0x7F

PUB_LEN: Final[int] = 32
CHECKSUM_LEN: Final[int] = 2
RAW_LEN: Final[int] = PUB_LEN + CHECKSUM_LEN + 1  # 35
NID_LEN: Final[int] = 56  # ceil(35 * 8 / 5), a multiple of 8 -> no padding

SUFFIX: Final[str] = ".null"
SUFFIX_ALT: Final[str] = ".null.alt"  # RFC 9476 form, §15

_CHECKSUM_CONTEXT: Final[bytes] = b"nullcell-addr"
_B32_ALPHABET: Final[frozenset[str]] = frozenset("abcdefghijklmnopqrstuvwxyz234567")


class AddressError(ValueError):
    """Base class for every malformed-address condition."""


class AddressFormatError(AddressError):
    """Wrong length, or a character outside the base32 alphabet."""


class AddressChecksumError(AddressError):
    """Checksum mismatch — almost always a typo, possibly a forgery attempt."""


class AddressVersionError(AddressError):
    """Address format version this build does not understand."""


@dataclass(frozen=True, slots=True)
class Address:
    """A decoded Nullcell address."""

    pub: bytes
    version: int
    private: bool

    @property
    def nid(self) -> str:
        return encode_nid(self.pub, private=self.private, version=self.version)

    @property
    def hostname(self) -> str:
        return self.nid + SUFFIX

    @property
    def routing_key(self) -> bytes:
        return routing_key(self.pub)


def _checksum(pub: bytes, version_byte: int) -> bytes:
    digest = hashlib.sha256(_CHECKSUM_CONTEXT + pub + bytes([version_byte])).digest()
    return digest[:CHECKSUM_LEN]


def encode_nid(pub: bytes, *, private: bool = False, version: int = ADDR_VERSION) -> str:
    """Encode an Ed25519 public key as a 56-character Nullcell address."""
    if len(pub) != PUB_LEN:
        raise AddressFormatError(f"public key must be {PUB_LEN} bytes, got {len(pub)}")
    if not 0 <= version <= VERSION_MASK:
        raise AddressVersionError(f"version must fit in 7 bits, got {version}")

    version_byte = version | (PRIVATE_FLAG if private else 0)
    raw = pub + _checksum(pub, version_byte) + bytes([version_byte])
    nid = base64.b32encode(raw).decode("ascii").lower()
    assert len(nid) == NID_LEN, "35 bytes must encode to exactly 56 base32 chars"
    return nid


def decode_nid(nid: str) -> Address:
    """Decode and fully validate a Nullcell address.

    Accepts a bare NID or a ``.null`` / ``.null.alt`` hostname, in any case.
    """
    nid = parse_hostname(nid)

    if len(nid) != NID_LEN:
        raise AddressFormatError(f"address must be {NID_LEN} characters, got {len(nid)}")
    if not _B32_ALPHABET.issuperset(nid):
        bad = sorted(set(nid) - _B32_ALPHABET)
        raise AddressFormatError(f"characters outside the base32 alphabet: {''.join(bad)!r}")

    try:
        raw = base64.b32decode(nid.upper())
    except Exception as exc:  # pragma: no cover - guarded by the checks above
        raise AddressFormatError(f"base32 decode failed: {exc}") from exc

    pub, checksum, version_byte = raw[:PUB_LEN], raw[PUB_LEN:-1], raw[-1]

    # Compare before interpreting the version: a corrupt version byte would
    # otherwise produce a confusing "unsupported version" error for a typo.
    if checksum != _checksum(pub, version_byte):
        raise AddressChecksumError("checksum mismatch — mistyped or tampered address")

    version = version_byte & VERSION_MASK
    if version != ADDR_VERSION:
        raise AddressVersionError(
            f"address format version {version} not supported (this build speaks {ADDR_VERSION})"
        )

    return Address(pub=pub, version=version, private=bool(version_byte & PRIVATE_FLAG))


def parse_hostname(host: str) -> str:
    """Strip a ``.null`` or ``.null.alt`` suffix and normalise to lowercase."""
    host = host.strip().strip(".").lower()
    for suffix in (SUFFIX_ALT, SUFFIX):  # longest first
        if host.endswith(suffix):
            return host[: -len(suffix)]
    return host


def routing_key(pub: bytes) -> bytes:
    """Kademlia routing key for a node — ``sha256(pub)``, *not* the address (§3)."""
    if len(pub) != PUB_LEN:
        raise AddressFormatError(f"public key must be {PUB_LEN} bytes, got {len(pub)}")
    return hashlib.sha256(pub).digest()


def short(nid: str, head: int = 6, tail: int = 2) -> str:
    """Abbreviate an address for the HUD and logs: ``k7f2qz…xq``."""
    nid = parse_hostname(nid)
    if len(nid) <= head + tail + 1:
        return nid
    return f"{nid[:head]}…{nid[-tail:]}"
