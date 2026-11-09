"""Circuit handshake — Noise_NK_25519_ChaChaPoly_SHA256, §5.

``NK`` is the pattern the design calls for:

* ``N`` — the initiator has **no static key**. A client building a circuit
  presents no identity at all, which is what keeps it anonymous at this layer
  just as it is at the link layer (§4).
* ``K`` — the responder's static key is **known in advance**, from its relay
  descriptor in the DHT (§7). Nothing is taken on trust at handshake time: a
  relay that cannot complete the handshake is not the relay whose key we read.

Two messages, 48 bytes each with no payload:

    -> e, es
    <- e, ee

§11 chose a library for this rather than hand-rolling it, so the key agreement
is `noiseprotocol`'s. What this module adds is the step Noise leaves to the
application: expanding the two directional keys from ``split()`` into the
keystream and digest material the onion layers need (:mod:`nullcell.onion.layers`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from noise.connection import Keypair, NoiseConnection

__all__ = [
    "HANDSHAKE_LEN",
    "NOISE_PATTERN",
    "TRANSPORT_OVERHEAD",
    "HandshakeError",
    "HandshakeKeys",
    "Initiator",
    "Responder",
    "x25519_public_bytes",
]

NOISE_PATTERN: Final[bytes] = b"Noise_NK_25519_ChaChaPoly_SHA256"

#: Both handshake messages are 32 bytes of ephemeral key plus a 16-byte tag.
HANDSHAKE_LEN: Final[int] = 48

#: Overhead Noise transport mode adds per message (the AEAD tag).
TRANSPORT_OVERHEAD: Final[int] = 16


class HandshakeError(Exception):
    """Malformed handshake message, or one that failed to authenticate."""


@dataclass(frozen=True, slots=True)
class HandshakeKeys:
    """The two directional keys a completed handshake yields.

    ``forward`` is client to relay, ``backward`` is relay to client. Each side
    sees them from its own end: an initiator's ``forward`` is what it encrypts
    with, a responder's ``forward`` is what it decrypts with.
    """

    forward: bytes
    backward: bytes

    def __post_init__(self) -> None:
        if len(self.forward) != 32 or len(self.backward) != 32:
            raise HandshakeError("directional keys must be 32 bytes")


def x25519_public_bytes(private: X25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _private_bytes(private: X25519PrivateKey) -> bytes:
    return private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


def _split_keys(connection: NoiseConnection) -> tuple[bytes, bytes]:
    """Read the two keys ``split()`` produced.

    `noiseprotocol` keeps them on the cipher states rather than exposing them,
    so this reaches into one documented-by-source attribute. It is read in
    exactly one place, so an upstream rename is a one-line fix rather than a
    hunt — the same treatment as aioquic's peer certificate in
    :mod:`nullcell.net.link`.
    """
    protocol = connection.noise_protocol
    encrypt = getattr(protocol.cipher_state_encrypt, "k", None)
    decrypt = getattr(protocol.cipher_state_decrypt, "k", None)
    if not (isinstance(encrypt, bytes) and isinstance(decrypt, bytes)):
        raise HandshakeError(
            "could not read the Noise split keys; the noiseprotocol version "
            "may have moved CipherState.k"
        )
    return encrypt, decrypt


class Initiator:
    """Client side of a circuit handshake. Presents no identity."""

    __slots__ = ("_conn", "_relay_pub", "keys")

    def __init__(self, relay_x25519_pub: bytes) -> None:
        if len(relay_x25519_pub) != 32:
            raise HandshakeError(f"relay X25519 key must be 32 bytes, got {len(relay_x25519_pub)}")
        self._relay_pub = relay_x25519_pub
        self.keys: HandshakeKeys | None = None

        self._conn = NoiseConnection.from_name(NOISE_PATTERN)
        self._conn.set_as_initiator()
        self._conn.set_keypair_from_public_bytes(Keypair.REMOTE_STATIC, relay_x25519_pub)
        self._conn.start_handshake()

    def create(self) -> bytes:
        """First message: ``-> e, es``. Goes in a ``CREATE`` frame."""
        return bytes(self._conn.write_message())

    def created(self, message: bytes) -> HandshakeKeys:
        """Consume ``CREATED`` and derive the directional keys."""
        if len(message) != HANDSHAKE_LEN:
            raise HandshakeError(f"CREATED is {len(message)} bytes, expected {HANDSHAKE_LEN}")
        try:
            self._conn.read_message(message)
        except Exception as exc:
            raise HandshakeError(f"CREATED failed to authenticate: {exc}") from exc
        if not self._conn.handshake_finished:
            raise HandshakeError("handshake did not complete")

        encrypt, decrypt = _split_keys(self._conn)
        self.keys = HandshakeKeys(forward=encrypt, backward=decrypt)
        return self.keys

    # ----------------------------------------------------------- transport
    def encrypt(self, plaintext: bytes) -> bytes:
        """Noise transport mode, for end-to-end data over a splice (§6).

        Used instead of another layer of our own: the client and the service
        are the two ends, nobody in between peels anything, and Noise's own
        AEAD gives confidentiality and integrity with no construction of ours
        in the middle. Costs 16 bytes of tag.
        """
        return bytes(self._conn.encrypt(plaintext))

    def decrypt(self, ciphertext: bytes) -> bytes:
        return bytes(self._conn.decrypt(ciphertext))


class Responder:
    """Relay side of a circuit handshake."""

    __slots__ = ("_conn", "keys")

    def __init__(self, static_private: X25519PrivateKey) -> None:
        self.keys: HandshakeKeys | None = None
        self._conn = NoiseConnection.from_name(NOISE_PATTERN)
        self._conn.set_as_responder()
        self._conn.set_keypair_from_private_bytes(Keypair.STATIC, _private_bytes(static_private))
        self._conn.start_handshake()

    def respond(self, create: bytes) -> tuple[bytes, HandshakeKeys]:
        """Consume ``CREATE`` and return ``(created_message, keys)``."""
        if len(create) != HANDSHAKE_LEN:
            raise HandshakeError(f"CREATE is {len(create)} bytes, expected {HANDSHAKE_LEN}")
        try:
            self._conn.read_message(create)
            created = bytes(self._conn.write_message())
        except Exception as exc:
            raise HandshakeError(f"CREATE failed to authenticate: {exc}") from exc
        if not self._conn.handshake_finished:
            raise HandshakeError("handshake did not complete")

        encrypt, decrypt = _split_keys(self._conn)
        # Mirrored: what the initiator encrypts with is what we decrypt with.
        self.keys = HandshakeKeys(forward=decrypt, backward=encrypt)
        return created, self.keys

    # ----------------------------------------------------------- transport
    def encrypt(self, plaintext: bytes) -> bytes:
        """Noise transport mode — see :meth:`Initiator.encrypt`."""
        return bytes(self._conn.encrypt(plaintext))

    def decrypt(self, ciphertext: bytes) -> bytes:
        return bytes(self._conn.decrypt(ciphertext))


def load_x25519_public(raw: bytes) -> X25519PublicKey:
    if len(raw) != 32:
        raise HandshakeError(f"X25519 key must be 32 bytes, got {len(raw)}")
    return X25519PublicKey.from_public_bytes(raw)
