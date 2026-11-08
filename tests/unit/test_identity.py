"""Identity — §3. The M0 success test is "reproducible persistent identity"."""

import stat
import sys

import pytest

from nullcell.core import ids
from nullcell.core.identity import Identity, IdentityError


def test_identity_is_reproducible_across_reloads(tmp_path):
    path = tmp_path / "identity.key"
    first, created = Identity.load_or_create(path)
    assert created
    second, created_again = Identity.load_or_create(path)
    assert not created_again
    assert first.nid == second.nid
    assert first.public_key == second.public_key
    assert first.x25519_public == second.x25519_public


def test_both_keys_derive_from_one_seed():
    seed = bytes(range(32))
    a, b = Identity(seed), Identity(seed)
    assert a.public_key == b.public_key
    assert a.x25519_public == b.x25519_public
    # ...and they are different keys.
    assert a.public_key != a.x25519_public


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_key_file_is_600(tmp_path):
    path = tmp_path / "identity.key"
    identity, _ = Identity.load_or_create(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert identity.check_permissions(path) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_loose_permissions_are_reported(tmp_path):
    path = tmp_path / "identity.key"
    identity, _ = Identity.load_or_create(path)
    path.chmod(0o644)
    assert "0644" in (identity.check_permissions(path) or "")


def test_address_matches_the_public_key(tmp_path):
    identity, _ = Identity.load_or_create(tmp_path / "identity.key")
    assert ids.decode_nid(identity.nid).pub == identity.public_key


def test_signatures_verify_and_forgeries_do_not():
    a, b = Identity.generate(), Identity.generate()
    sig = a.sign(b"mensaje")
    assert Identity.verify(a.public_key, b"mensaje", sig)
    assert not Identity.verify(b.public_key, b"mensaje", sig)
    assert not Identity.verify(a.public_key, b"otro", sig)


def test_passphrase_protection(tmp_path):
    path = tmp_path / "enc.key"
    original = Identity.generate()
    original.save(path, "frase correcta")

    assert Identity.load(path, "frase correcta").nid == original.nid
    with pytest.raises(IdentityError, match="wrong passphrase"):
        Identity.load(path, "frase incorrecta")
    with pytest.raises(IdentityError, match="passphrase-protected"):
        Identity.load(path)


def test_corrupt_key_file_rejected(tmp_path):
    path = tmp_path / "identity.key"
    path.write_bytes(b"no soy cbor")
    with pytest.raises(IdentityError):
        Identity.load(path)


def test_bad_seed_length_rejected():
    for n in (0, 16, 31, 33):
        with pytest.raises(IdentityError):
            Identity(bytes(n))


def test_save_is_atomic_leaving_no_temp_file(tmp_path):
    path = tmp_path / "identity.key"
    Identity.generate().save(path)
    assert [p.name for p in tmp_path.iterdir()] == ["identity.key"]
