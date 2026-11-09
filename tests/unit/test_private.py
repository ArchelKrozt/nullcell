"""Private service descriptors — §6's second class of service."""

from __future__ import annotations

import time

import pytest

from nullcell.core.identity import Identity
from nullcell.dht import pow as P
from nullcell.dht import private as PR
from nullcell.dht import records as R
from nullcell.dht.store import Store
from nullcell.onion import blinding

BODY = {"ip": ["uno", "dos", "tres"], "x": b"\x11" * 32}


@pytest.fixture(autouse=True)
def cheap_pow(monkeypatch):
    monkeypatch.setattr(P, "DIFFICULTY", dict.fromkeys(P.DIFFICULTY, 3))


@pytest.fixture
def service() -> Identity:
    return Identity.generate()


async def descriptor(identity: Identity, *, period: int | None = None) -> R.Record:
    if period is None:
        period = PR.publish_keys(identity.public_key)[0][0]
    return await PR.build_private(identity, BODY, period=period)


# ------------------------------------------------- what the storing node sees
async def test_the_storing_node_can_verify_without_knowing_whose_it_is(service):
    """§6: spam is refused without anyone being identified."""
    record = await descriptor(service)
    R.verify(record)  # plain Ed25519 against the blinded key the record carries
    assert record.pub != service.public_key


async def test_the_real_key_never_appears_in_the_record(service):
    record = await descriptor(service)
    assert service.public_key not in record.encode()


async def test_the_index_does_not_reveal_the_key(service):
    record = await descriptor(service)
    assert service.public_key not in record.key
    assert record.pub not in record.key  # it is a hash of it, not the thing


async def test_a_record_filed_under_the_wrong_index_is_refused(service):
    """Without this, a publisher could scatter records across the keyspace."""
    record = await descriptor(service)
    moved = R.Record(
        record.type,
        b"\x00" * 32,
        record.pub,
        record.ts,
        record.ttl,
        record.body,
        record.nonce,
        record.sig,
    )
    with pytest.raises(R.RecordKeyMismatch):
        R.verify(moved)


async def test_a_tampered_body_is_refused(service):
    record = await descriptor(service)
    tampered = R.Record(
        record.type,
        record.key,
        record.pub,
        record.ts,
        record.ttl,
        {**record.body, "enc": b"otra cosa"},
        record.nonce,
        record.sig,
    )
    with pytest.raises(R.RecordSignatureError):
        R.verify(tampered)


# ------------------------------------------------ what the address holder gets
async def test_whoever_knows_the_address_opens_it(service):
    record = await descriptor(service)
    assert PR.open_private(record, service.public_key) == BODY


async def test_another_address_does_not_open_it(service):
    record = await descriptor(service)
    with pytest.raises(PR.PrivateError, match="different service"):
        PR.open_private(record, Identity.generate().public_key)


async def test_a_public_record_is_not_a_private_one(service):
    public = await R.build(
        service, R.RecordType.RELAY, R.RelayBody(endpoints=["1.2.3.4:1"]).as_dict()
    )
    with pytest.raises(PR.PrivateError, match="not a private descriptor"):
        PR.open_private(public, service.public_key)


async def test_a_malformed_body_is_refused(service):
    record = await descriptor(service)
    for body in ({}, {"p": "no es un entero", "enc": b"x"}, {"p": 1}):
        broken = R.Record(
            record.type,
            record.key,
            record.pub,
            record.ts,
            record.ttl,
            body,
            record.nonce,
            record.sig,
        )
        with pytest.raises(R.RecordError):
            PR.open_private(broken, service.public_key)


# ----------------------------------------------------------------- rotation §6
async def test_the_index_rotates_between_periods(service):
    first = PR.publish_keys(service.public_key)[0][0]
    a = await descriptor(service, period=first)
    b = await descriptor(service, period=first + 1)
    assert a.key != b.key
    assert a.pub != b.pub
    assert PR.open_private(a, service.public_key) == BODY
    assert PR.open_private(b, service.public_key) == BODY


async def test_the_body_key_rotates_too(service):
    first = PR.publish_keys(service.public_key)[0][0]
    assert PR.descriptor_key(service.public_key, first) != PR.descriptor_key(
        service.public_key, first + 1
    )


def test_publish_keys_covers_the_overlap(service):
    """§6: a period boundary must not make a service briefly unreachable."""
    base = blinding.PERIOD_LENGTH * 500
    assert len(PR.publish_keys(service.public_key, base + 60)) == 2
    assert len(PR.publish_keys(service.public_key, base + blinding.PERIOD_LENGTH // 2)) == 1


def test_publish_keys_now_is_usable(service):
    keys = PR.publish_keys(service.public_key, time.time())
    assert keys
    assert all(len(index) == 32 for _, index in keys)


# ---------------------------------------------------------------- integration
async def test_a_private_descriptor_round_trips_through_the_store(service):
    """What a DHT node actually does with one."""
    store = Store()
    record = await descriptor(service)
    pow_bits = R.verify(record)
    assert store.put(record, pow_bits=pow_bits)

    [found] = store.get(record.key)
    assert PR.open_private(found, service.public_key) == BODY
    # And the store learned nothing it should not have.
    assert found.pub != service.public_key


async def test_privacy_is_not_a_discount_on_proof_of_work():
    """§10: a private descriptor costs the same as a public one."""
    assert P.DIFFICULTY["private"] == P.DIFFICULTY["service"]
