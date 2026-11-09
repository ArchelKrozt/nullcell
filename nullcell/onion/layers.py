"""Onion layers — the crypto core of §5.

Each hop of a circuit holds two independent keystreams and two running digests,
one per direction. The client holds all three hops' state; a relay holds only
its own, and therefore cannot read what it forwards.

**Which layers apply to which frame.** A frame addressed to hop *k* is wrapped
with layers *k, k-1, ..., 0* — innermost is hop *k*'s. It then travels outward,
and each hop from 0 to *k* peels exactly one layer. Hops beyond *k* never see
it. That is why the keystreams stay in step: a hop's stream advances once per
frame that passes through it, and the client advances exactly the same set.
Wrapping with all three layers unconditionally would desynchronise every hop
past the addressee.

**The running digest, and why it is not optional.** Without it a malicious guard
can flip bits in a passing frame; the corruption reaches the far end, and if the
adversary also controls that end the pattern *confirms* both hops are on the
same circuit — anonymity broken with no cryptography broken. So each direction
carries a 4-byte prefix of a SHA-256 running digest over everything that hop has
seen. A tampered frame fails to verify, and the circuit is torn down before the
adversary learns anything.

A digest is checked *tentatively*: an intermediate hop cannot know in advance
whether a frame is addressed to it, so it checks against a copy of its digest
state and only commits when the frame turns out to be its own.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Final

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from nullcell.core.frames import DIGEST_OFFSET, DIGEST_SIZE
from nullcell.onion.handshake_nk import HandshakeKeys

__all__ = ["HopCrypto", "LayerError", "OnionPath"]

_HKDF_INFO: Final[bytes] = b"nullcell-layer-v1"
_ZEROED_DIGEST: Final[bytes] = bytes(DIGEST_SIZE)
_DIGEST_END: Final[int] = DIGEST_OFFSET + DIGEST_SIZE
_CIPHER_KEY_LEN: Final[int] = 32
_NONCE_LEN: Final[int] = 16  # ChaCha20 in `cryptography`: 4-byte counter + 12
_SEED_LEN: Final[int] = 32
_OKM_LEN: Final[int] = _CIPHER_KEY_LEN + _NONCE_LEN + _SEED_LEN


class LayerError(Exception):
    """A frame that failed its digest, or a malformed layer operation."""


@dataclass(frozen=True, slots=True)
class _Material:
    cipher_key: bytes
    nonce: bytes
    digest_seed: bytes


def _derive(key: bytes, label: bytes) -> _Material:
    """Expand one 32-byte directional key into keystream and digest material.

    The key comes from Noise's ``split()`` (§5), so it is already a strong
    uniform secret; HKDF-Expand is used to get independent values out of it
    rather than to extract entropy.
    """
    okm = HKDF(
        algorithm=hashes.SHA256(), length=_OKM_LEN, salt=None, info=_HKDF_INFO + label
    ).derive(key)
    return _Material(
        cipher_key=okm[:_CIPHER_KEY_LEN],
        nonce=okm[_CIPHER_KEY_LEN : _CIPHER_KEY_LEN + _NONCE_LEN],
        digest_seed=okm[_CIPHER_KEY_LEN + _NONCE_LEN :],
    )


def _update_zeroed(hasher, payload: bytes) -> None:
    """Feed *payload* to *hasher* as if its digest field were zero.

    Done in three slices over a ``memoryview`` rather than by copying the
    payload with the field cleared: the copy was 507 bytes allocated on every
    digest operation, which is exactly the per-frame allocation §16 rules out
    of the hot path.
    """
    view = memoryview(payload)
    hasher.update(view[:DIGEST_OFFSET])
    hasher.update(_ZEROED_DIGEST)
    hasher.update(view[_DIGEST_END:])


def _keystream(material: _Material):
    """A ChaCha20 keystream that continues across frames.

    One object per hop per direction, fed every frame in order. Both ends build
    it from the same material, and XOR is its own inverse, so the same call
    both adds and removes a layer.
    """
    return Cipher(algorithms.ChaCha20(material.cipher_key, material.nonce), mode=None).encryptor()


class HopCrypto:
    """One hop's layer state, held identically at both ends of that hop."""

    __slots__ = ("_fwd", "_bwd", "_fwd_digest", "_bwd_digest")

    def __init__(self, keys: HandshakeKeys) -> None:
        forward = _derive(keys.forward, b"/forward")
        backward = _derive(keys.backward, b"/backward")
        self._fwd = _keystream(forward)
        self._bwd = _keystream(backward)
        self._fwd_digest = hashlib.sha256(forward.digest_seed)
        self._bwd_digest = hashlib.sha256(backward.digest_seed)

    # ------------------------------------------------------------- keystreams
    def crypt_forward(self, payload: bytes) -> bytes:
        """Add or remove this hop's forward layer. Advances the keystream."""
        return self._fwd.update(payload)

    def crypt_backward(self, payload: bytes) -> bytes:
        """Add or remove this hop's backward layer. Advances the keystream."""
        return self._bwd.update(payload)

    # ---------------------------------------------------------------- digests
    def tag_forward(self, payload: bytes) -> bytes:
        """Digest a payload we are sending forward. Advances the digest."""
        _update_zeroed(self._fwd_digest, payload)
        return self._fwd_digest.digest()[:DIGEST_SIZE]

    def tag_backward(self, payload: bytes) -> bytes:
        """Digest a payload we are sending backward. Advances the digest."""
        _update_zeroed(self._bwd_digest, payload)
        return self._bwd_digest.digest()[:DIGEST_SIZE]

    def check_forward(self, payload: bytes) -> bool:
        """Is this payload ours, arriving forward? Advances only on a match."""
        return self._check(payload, self._fwd_digest, forward=True)

    def check_backward(self, payload: bytes) -> bool:
        """Is this payload ours, arriving backward? Advances only on a match."""
        return self._check(payload, self._bwd_digest, forward=False)

    def _check(self, payload: bytes, digest, *, forward: bool) -> bool:
        tentative = digest.copy()
        _update_zeroed(tentative, payload)
        expected = memoryview(payload)[DIGEST_OFFSET:_DIGEST_END]
        if not hmac.compare_digest(tentative.digest()[:DIGEST_SIZE], expected):
            return False
        # It is ours: commit the advance.
        if forward:
            self._fwd_digest = tentative
        else:
            self._bwd_digest = tentative
        return True


class OnionPath:
    """The client's view of a circuit: its hops, in order from the guard."""

    __slots__ = ("hops",)

    def __init__(self) -> None:
        self.hops: list[HopCrypto] = []

    def __len__(self) -> int:
        return len(self.hops)

    def add(self, keys: HandshakeKeys) -> HopCrypto:
        hop = HopCrypto(keys)
        self.hops.append(hop)
        return hop

    # ---------------------------------------------------------------- sending
    def wrap(self, payload: bytes, hop_index: int | None = None) -> bytes:
        """Address *payload* to a hop and wrap it for the whole path.

        ``hop_index`` defaults to the last hop. The digest is written in before
        any layer is applied, so every hop in between sees only ciphertext.
        """
        if not self.hops:
            raise LayerError("circuit has no hops")
        index = len(self.hops) - 1 if hop_index is None else hop_index
        if not 0 <= index < len(self.hops):
            raise LayerError(f"no hop {index} in a {len(self.hops)}-hop circuit")

        # The tag is written in place on one mutable copy instead of calling
        # set_digest(), which would allocate a second 507-byte payload per
        # frame (§16, no per-frame allocations in the hot path).
        buf = bytearray(payload)
        buf[DIGEST_OFFSET:_DIGEST_END] = self.hops[index].tag_forward(buf)
        addressed = bytes(buf)
        for hop in reversed(self.hops[: index + 1]):
            addressed = hop.crypt_forward(addressed)
        return addressed

    # -------------------------------------------------------------- receiving
    def unwrap(self, payload: bytes) -> tuple[int, bytes]:
        """Peel inbound layers until one hop recognises the frame.

        Returns ``(hop_index, payload)``. Raises :class:`LayerError` if no hop
        claims it, which means either corruption or tampering — and §5 says the
        circuit goes down rather than carry on.
        """
        for index, hop in enumerate(self.hops):
            payload = hop.crypt_backward(payload)
            if hop.check_backward(payload):
                return index, payload
        raise LayerError("no hop recognised the frame: corrupted, or tampered with in transit")
