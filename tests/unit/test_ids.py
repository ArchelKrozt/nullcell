"""Addresses — §3."""

import os

import pytest

from nullcell.core import ids


def test_address_is_exactly_56_chars():
    for _ in range(50):
        assert len(ids.encode_nid(os.urandom(32))) == ids.NID_LEN


def test_no_base32_padding():
    assert "=" not in ids.encode_nid(os.urandom(32))


def test_roundtrip_preserves_the_key():
    pub = os.urandom(32)
    assert ids.decode_nid(ids.encode_nid(pub)).pub == pub


def test_address_carries_the_key_not_a_hash():
    """The whole point of decision 10: the key is recoverable from the address."""
    pub = os.urandom(32)
    assert ids.decode_nid(ids.encode_nid(pub)).pub == pub
    # ...and the routing key is a different value, on purpose.
    assert ids.routing_key(pub) != pub


def test_private_flag_roundtrips_and_changes_the_address():
    pub = os.urandom(32)
    public, private = ids.encode_nid(pub), ids.encode_nid(pub, private=True)
    assert public != private
    assert ids.decode_nid(public).private is False
    assert ids.decode_nid(private).private is True
    assert ids.decode_nid(private).pub == pub


@pytest.mark.parametrize("suffix", ["", ".null", ".null.alt", ".NULL", ".null."])
def test_accepts_bare_nid_and_both_suffixes(suffix):
    pub = os.urandom(32)
    nid = ids.encode_nid(pub)
    assert ids.decode_nid(nid + suffix).pub == pub


def test_single_character_typo_is_caught_locally():
    """A mistyped address must fail before any network traffic (§3)."""
    nid = ids.encode_nid(os.urandom(32))
    caught = 0
    for i in range(len(nid)):
        for replacement in "abcdefghijklmnopqrstuvwxyz234567":
            if replacement == nid[i]:
                continue
            typo = nid[:i] + replacement + nid[i + 1 :]
            with pytest.raises(ids.AddressError):
                ids.decode_nid(typo)
            caught += 1
            break
    assert caught == len(nid)


def test_wrong_length_rejected():
    nid = ids.encode_nid(os.urandom(32))
    for bad in (nid[:-1], nid + "a", "", "abc"):
        with pytest.raises(ids.AddressFormatError):
            ids.decode_nid(bad)


def test_characters_outside_base32_rejected():
    nid = ids.encode_nid(os.urandom(32))
    with pytest.raises(ids.AddressFormatError):
        ids.decode_nid("1" + nid[1:])  # '1' and '0' are not in the alphabet
    with pytest.raises(ids.AddressFormatError):
        ids.decode_nid("!" + nid[1:])


def test_unknown_version_rejected_with_a_valid_checksum():
    pub = os.urandom(32)
    future = ids.encode_nid(pub, version=2)  # checksum is correct for version 2
    with pytest.raises(ids.AddressVersionError):
        ids.decode_nid(future)


def test_bad_key_length_rejected():
    for n in (0, 31, 33, 64):
        with pytest.raises(ids.AddressFormatError):
            ids.encode_nid(os.urandom(n))


def test_routing_key_is_sha256_of_the_key():
    import hashlib

    pub = os.urandom(32)
    assert ids.routing_key(pub) == hashlib.sha256(pub).digest()
    assert len(ids.routing_key(pub)) == 32


def test_short_form_is_stable_and_shorter():
    nid = ids.encode_nid(os.urandom(32))
    s = ids.short(nid)
    assert s == ids.short(nid + ".null")
    assert len(s) < len(nid)
    assert s.startswith(nid[:6]) and s.endswith(nid[-2:])
