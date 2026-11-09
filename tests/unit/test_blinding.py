"""Ed25519 key blinding — §6.

This is the one module in the project that assembles a signature scheme from
primitives rather than calling a library's ``sign()`` (see its docstring for
why). So the tests here are the safety net, and every one of them checks the
result with **PyNaCl's verifier**, which is independent code we did not write.
"""

from __future__ import annotations

import time

import nacl.bindings as sodium
import nacl.signing
import pytest

from nullcell.onion import blinding as B

MESSAGE = b"descriptor del servicio privado"


@pytest.fixture
def keypair() -> tuple[bytes, bytes]:
    key = nacl.signing.SigningKey.generate()
    return bytes(key._seed), bytes(key.verify_key)


# -------------------------------------------------------- the two halves agree
@pytest.mark.parametrize("period", [0, 1, 20_000, 123_456, -1])
def test_blinded_public_matches_blinded_private(keypair, period):
    """``A' = h·A`` from the public side must equal ``a'·B`` from the private."""
    seed, pub = keypair
    scalar, _ = B.blind_private(seed, pub, period)
    assert sodium.crypto_scalarmult_ed25519_base_noclamp(scalar) == B.blind_public(pub, period)


def test_blinding_is_deterministic(keypair):
    seed, pub = keypair
    assert B.blind_public(pub, 7) == B.blind_public(pub, 7)
    assert B.blind_private(seed, pub, 7) == B.blind_private(seed, pub, 7)


def test_each_period_gives_a_different_key(keypair):
    _, pub = keypair
    keys = {B.blind_public(pub, p) for p in range(20)}
    assert len(keys) == 20


def test_blinded_key_is_not_the_real_one(keypair):
    _, pub = keypair
    assert B.blind_public(pub, 1) != pub


# ------------------------------------------------------------------- signing
def test_pynacl_verifies_our_blinded_signature(keypair):
    seed, pub = keypair
    period = 20_000
    scalar, prefix = B.blind_private(seed, pub, period)
    blinded = B.blind_public(pub, period)
    signature = B.sign_blinded(MESSAGE, scalar, prefix, blinded)

    nacl.signing.VerifyKey(blinded).verify(MESSAGE, signature)  # raises on failure
    assert B.verify_blinded(blinded, MESSAGE, signature)


def test_a_tampered_message_is_rejected(keypair):
    seed, pub = keypair
    scalar, prefix = B.blind_private(seed, pub, 5)
    blinded = B.blind_public(pub, 5)
    signature = B.sign_blinded(MESSAGE, scalar, prefix, blinded)
    assert not B.verify_blinded(blinded, MESSAGE + b"x", signature)


def test_the_real_key_does_not_verify_a_blinded_signature(keypair):
    seed, pub = keypair
    scalar, prefix = B.blind_private(seed, pub, 5)
    blinded = B.blind_public(pub, 5)
    signature = B.sign_blinded(MESSAGE, scalar, prefix, blinded)
    assert not B.verify_blinded(pub, MESSAGE, signature)


def test_another_period_does_not_verify(keypair):
    seed, pub = keypair
    scalar, prefix = B.blind_private(seed, pub, 5)
    signature = B.sign_blinded(MESSAGE, scalar, prefix, B.blind_public(pub, 5))
    assert not B.verify_blinded(B.blind_public(pub, 6), MESSAGE, signature)


def test_another_keypair_does_not_verify(keypair):
    seed, pub = keypair
    other = bytes(nacl.signing.SigningKey.generate().verify_key)
    scalar, prefix = B.blind_private(seed, pub, 5)
    signature = B.sign_blinded(MESSAGE, scalar, prefix, B.blind_public(pub, 5))
    assert not B.verify_blinded(B.blind_public(other, 5), MESSAGE, signature)


def test_signatures_differ_per_period(keypair):
    """The nonce prefix is re-derived per period, so the same message signed in
    two periods does not produce the same R."""
    seed, pub = keypair
    signatures = set()
    for period in range(5):
        scalar, prefix = B.blind_private(seed, pub, period)
        signatures.add(B.sign_blinded(MESSAGE, scalar, prefix, B.blind_public(pub, period)))
    assert len(signatures) == 5


def test_h_equals_one_reduces_to_ordinary_ed25519(keypair, monkeypatch):
    """The degenerate case, pinned on purpose.

    With ``h = 1`` the blinded key must *be* the original key and the signature
    must verify under it. The blinded **scalar** is not byte-equal, though: an
    Ed25519 scalar from a seed is clamped rather than reduced (clamping sets
    bit 254, above the group order L), and blinding multiplies mod L. Same
    group element, same key, same signatures — different bytes.
    """
    seed, pub = keypair
    monkeypatch.setattr(B, "blinding_scalar", lambda p, period: (1).to_bytes(32, "little"))

    scalar, prefix = B.blind_private(seed, pub, 0)
    assert B.blind_public(pub, 0) == pub

    signature = B.sign_blinded(MESSAGE, scalar, prefix, pub)
    assert B.verify_blinded(pub, MESSAGE, signature)

    expected, _ = B._expand_seed(seed)
    assert scalar != expected  # clamped vs reduced
    assert sodium.crypto_core_ed25519_scalar_reduce(expected + bytes(32)) == scalar


# -------------------------------------------------------- index and credential
def test_index_rotates_and_hides_the_key(keypair):
    _, pub = keypair
    keys = {B.index_key(pub, p) for p in range(20)}
    assert len(keys) == 20
    assert all(pub not in k for k in keys)
    assert all(len(k) == 32 for k in keys)


def test_index_is_computable_from_the_address_alone(keypair):
    """What makes a private service findable by whoever knows its address."""
    _, pub = keypair
    assert B.index_key(pub, 42) == B.index_key(pub, 42)


def test_credential_is_derived_from_the_public_key(keypair):
    _, pub = keypair
    other = bytes(nacl.signing.SigningKey.generate().verify_key)
    assert len(B.credential(pub)) == 32
    assert B.credential(pub) != B.credential(other)


# ----------------------------------------------------------------- periods §6
def test_periods_overlap_at_the_edges():
    """§6: a period change must not make a service briefly unreachable."""
    base = B.PERIOD_LENGTH * 1000

    assert B.periods_for(base + B.PERIOD_LENGTH // 2) == [1000]
    assert B.periods_for(base + 60) == [1000, 999]  # just after a boundary
    assert B.periods_for(base + B.PERIOD_LENGTH - 60) == [1000, 1001]  # just before


def test_period_length_and_overlap_match_the_architecture():
    assert B.PERIOD_LENGTH == 24 * 3600
    assert B.PERIOD_OVERLAP == 2 * 3600


def test_periods_for_now_is_usable():
    periods = B.periods_for(time.time())
    assert 1 <= len(periods) <= 2


# ---------------------------------------------------------------- bad input
def test_malformed_keys_are_refused():
    for bad in (b"", b"\x00" * 31, b"\x00" * 33):
        with pytest.raises(B.BlindingError):
            B.blind_public(bad, 0)
        with pytest.raises(B.BlindingError):
            B.credential(bad)


def test_a_non_curve_point_is_refused():
    with pytest.raises(B.BlindingError, match="valid curve point"):
        B.blind_public(b"\xff" * 32, 0)
