"""Ed25519 key blinding for private services — §6.

A private service must be findable by anyone who knows its address and
invisible to everyone else, including the DHT nodes that store its descriptor.
That needs a derivation which is **public** — computable from the public key
alone, so a client can do it — and which still has a matching private side, so
the service can sign under the derived key. Hashing gives the first property
and not the second. Blinding gives both.

Given a keypair ``(a, A = a·B)`` and a blinding scalar ``h``:

    A' = h·A        the blinded public key, from the public key alone
    a' = h·a mod L  the blinded private key, from the private key

A signature made with ``a'`` verifies against ``A'`` under plain Ed25519, so the
node storing the descriptor verifies it normally while learning only ``A'``. It
cannot recover ``A``, so it cannot name the service, and it cannot enumerate the
network's services by walking the keyspace.

.. warning::

   **This is the one place in the project where a signature scheme is assembled
   from primitives instead of calling a library's ``sign()``.** §11 says we do
   not invent cryptography, and this is the exception, for the same reason Tor
   made it: no library offers signing under a blinded scalar, because the
   blinded private key is a raw scalar and not a seed.

   Every arithmetic operation is libsodium's. The signatures produced here are
   checked by **PyNaCl's own verifier**, which is independent, audited code we
   did not write — so a mistake in this module shows up as a failed
   verification rather than as a silent weakness. The tests also pin the
   degenerate case ``h = 1``, where blinding must reduce exactly to ordinary
   Ed25519.

   If one module in this repository deserves outside review, it is this one.
"""

from __future__ import annotations

import hashlib
from typing import Final

import nacl.bindings as sodium
import nacl.signing

__all__ = [
    "PERIOD_LENGTH",
    "PERIOD_OVERLAP",
    "BlindingError",
    "blind_private",
    "blind_public",
    "blinding_scalar",
    "credential",
    "index_key",
    "periods_for",
    "sign_blinded",
]

_BLIND_CONTEXT: Final[bytes] = b"nullcell-blind"
_INDEX_CONTEXT: Final[bytes] = b"nullcell-index"
_CRED_CONTEXT: Final[bytes] = b"nullcell-cred"
_NONCE_CONTEXT: Final[bytes] = b"nullcell-blind-nonce"

#: §6 — 24 h periods with 2 h of overlap at the edges, so a period change does
#: not make a service unreachable while clocks disagree (§16).
PERIOD_LENGTH: Final[int] = 24 * 3600
PERIOD_OVERLAP: Final[int] = 2 * 3600


class BlindingError(Exception):
    """A key or scalar that is not a usable curve element."""


def _check_sodium() -> None:
    if not sodium.has_crypto_core_ed25519:
        raise BlindingError(
            "this libsodium build lacks the ed25519 curve arithmetic that "
            "private services need; public portals still work"
        )


# --------------------------------------------------------------------- periods
def periods_for(now: float) -> list[int]:
    """Time periods a descriptor may be filed under at *now*.

    Returns the current period, plus the neighbouring one while inside the
    overlap window. A publisher stores under all of them and a client tries
    them in order, which is what stops a period boundary from making a service
    briefly unreachable (§6).
    """
    current = int(now // PERIOD_LENGTH)
    offset = now - current * PERIOD_LENGTH
    if offset < PERIOD_OVERLAP:
        return [current, current - 1]
    if offset > PERIOD_LENGTH - PERIOD_OVERLAP:
        return [current, current + 1]
    return [current]


# -------------------------------------------------------------------- scalars
def blinding_scalar(pub: bytes, period: int) -> bytes:
    """The public blinding factor ``h`` for a key and a time period.

    Derived from the public key, so a client with only the address computes the
    same value the service does.
    """
    _check_sodium()
    if len(pub) != 32:
        raise BlindingError(f"public key must be 32 bytes, got {len(pub)}")
    wide = hashlib.sha512(_BLIND_CONTEXT + pub + period.to_bytes(8, "big", signed=True)).digest()
    scalar = sodium.crypto_core_ed25519_scalar_reduce(wide)
    if scalar == bytes(32):
        raise BlindingError("blinding scalar reduced to zero")  # pragma: no cover
    return scalar


def _expand_seed(seed: bytes) -> tuple[bytes, bytes]:
    """Ed25519 seed -> (clamped scalar a, nonce prefix)."""
    if len(seed) != 32:
        raise BlindingError(f"seed must be 32 bytes, got {len(seed)}")
    wide = bytearray(hashlib.sha512(seed).digest())
    wide[0] &= 248
    wide[31] &= 127
    wide[31] |= 64
    return bytes(wide[:32]), bytes(wide[32:])


# ------------------------------------------------------------------- blinding
def blind_public(pub: bytes, period: int) -> bytes:
    """``A' = h·A``. Computable by anyone who knows the address (§6)."""
    _check_sodium()
    scalar = blinding_scalar(pub, period)
    try:
        return sodium.crypto_scalarmult_ed25519_noclamp(scalar, pub)
    except Exception as exc:
        raise BlindingError(f"not a valid curve point: {exc}") from exc


def blind_private(seed: bytes, pub: bytes, period: int) -> tuple[bytes, bytes]:
    """``a' = h·a mod L``. Returns ``(blinded_scalar, nonce_prefix)``.

    The nonce prefix is re-derived rather than reused, so two different periods
    never produce the same per-message nonce from the same message.
    """
    _check_sodium()
    scalar, prefix = _expand_seed(seed)
    factor = blinding_scalar(pub, period)
    blinded = sodium.crypto_core_ed25519_scalar_mul(factor, scalar)
    blinded_prefix = hashlib.sha512(
        _NONCE_CONTEXT + prefix + period.to_bytes(8, "big", signed=True)
    ).digest()[:32]
    return blinded, blinded_prefix


def sign_blinded(
    message: bytes, blinded_scalar: bytes, nonce_prefix: bytes, blinded_pub: bytes
) -> bytes:
    """Ed25519 signature under a blinded scalar.

    Plain Ed25519, with the private scalar supplied directly instead of
    expanded from a seed::

        r = H(prefix ‖ M) mod L      R = r·B
        k = H(R ‖ A' ‖ M) mod L      S = r + k·a' mod L
        signature = R ‖ S

    Verified by :func:`verify_blinded`, which calls PyNaCl, not this module.

    One subtlety worth recording, because it caught the first test written
    against it: an Ed25519 scalar expanded from a seed is **clamped, not
    reduced** — clamping sets bit 254, which puts it above the group order L.
    Blinding multiplies mod L, so even with ``h = 1`` the blinded scalar is not
    byte-equal to the original. It is the same group element: the public key and
    every signature come out identical. Only the bytes differ.
    """
    _check_sodium()
    if len(blinded_scalar) != 32 or len(nonce_prefix) != 32 or len(blinded_pub) != 32:
        raise BlindingError("blinded scalar, prefix and public key must be 32 bytes each")

    r = sodium.crypto_core_ed25519_scalar_reduce(hashlib.sha512(nonce_prefix + message).digest())
    point_r = sodium.crypto_scalarmult_ed25519_base_noclamp(r)
    k = sodium.crypto_core_ed25519_scalar_reduce(
        hashlib.sha512(point_r + blinded_pub + message).digest()
    )
    s = sodium.crypto_core_ed25519_scalar_add(
        r, sodium.crypto_core_ed25519_scalar_mul(k, blinded_scalar)
    )
    return point_r + s


def verify_blinded(blinded_pub: bytes, message: bytes, signature: bytes) -> bool:
    """Verify with PyNaCl — deliberately not with code from this module."""
    try:
        nacl.signing.VerifyKey(blinded_pub).verify(message, signature)
    except Exception:
        return False
    return True


# -------------------------------------------------------------- derived values
def index_key(pub: bytes, period: int) -> bytes:
    """DHT key a private service's descriptor is filed under (§6).

    ``sha256("nullcell-index" ‖ A' ‖ period)``. The storing node sees only
    this, cannot invert it, and cannot walk the keyspace to enumerate services.
    """
    blinded = blind_public(pub, period)
    return hashlib.sha256(
        _INDEX_CONTEXT + blinded + period.to_bytes(8, "big", signed=True)
    ).digest()


def credential(pub: bytes) -> bytes:
    """Secret shared by everyone who knows the address, and nobody else (§6).

    The descriptor body is encrypted under a key derived from this, so the node
    that stores it cannot read what it holds.
    """
    if len(pub) != 32:
        raise BlindingError(f"public key must be 32 bytes, got {len(pub)}")
    return hashlib.sha256(_CRED_CONTEXT + pub).digest()
