"""Signed DHT records — §7, §8."""

import time

import pytest

from nullcell.core.identity import Identity
from nullcell.dht import pow as P
from nullcell.dht import records as R


@pytest.fixture(autouse=True)
def cheap_pow(monkeypatch):
    """Real difficulties take seconds; the logic is identical at 3 bits."""
    monkeypatch.setattr(P, "DIFFICULTY", dict.fromkeys(P.DIFFICULTY, 3))


@pytest.fixture
def me():
    return Identity.generate()


def relay_body(identity):
    return R.RelayBody(
        endpoints=["127.0.0.1:4433"], x25519=identity.x25519_public, bandwidth=1_000_000
    ).as_dict()


async def test_build_and_verify(me):
    record = await R.build(me, R.RecordType.RELAY, relay_body(me))
    R.verify(record)
    assert record.key == me.routing_key
    assert record.pub == me.public_key
    assert record.is_fresh()
    assert record.nid == me.nid


async def test_cbor_roundtrip_is_exact(me):
    record = await R.build(me, R.RecordType.RELAY, relay_body(me))
    assert R.Record.decode(record.encode()) == record
    R.verify(R.Record.decode(record.encode()))


async def test_record_cannot_be_filed_under_someone_elses_key(me):
    """The cheapest attack on a DHT without this check."""
    other = Identity.generate()
    good = await R.build(me, R.RecordType.RELAY, relay_body(me))
    stolen = R.Record(
        good.type, other.routing_key, good.pub, good.ts, good.ttl, good.body, good.nonce, good.sig
    )
    with pytest.raises(R.RecordKeyMismatch):
        R.verify(stolen)


async def test_tampered_body_rejected(me):
    good = await R.build(me, R.RecordType.RELAY, relay_body(me))
    tampered = R.Record(
        good.type, good.key, good.pub, good.ts, good.ttl, {"ep": ["evil:1"]}, good.nonce, good.sig
    )
    with pytest.raises(R.RecordSignatureError):
        R.verify(tampered)


async def test_proof_of_work_cannot_be_lifted_onto_another_record(me, monkeypatch):
    """Signature and PoW cover the same bytes, so a nonce does not transfer.

    This one test raises the difficulty above the suite default: a borrowed
    nonce still passes by chance with probability ``2**-bits``, which at the
    3 bits the other tests use is one run in eight. At 10 bits across three
    independent forgeries the flake probability is about 1e-9.
    """
    monkeypatch.setattr(P, "DIFFICULTY", dict.fromkeys(P.DIFFICULTY, 10))

    for i in range(3):
        a = await R.build(me, R.RecordType.RELAY, {**relay_body(me), "bw": i})
        b = await R.build(me, R.RecordType.RELAY, {**relay_body(me), "bw": 1000 + i})
        assert a.unsigned_bytes() != b.unsigned_bytes()

        forged = R.Record(b.type, b.key, b.pub, b.ts, b.ttl, b.body, a.nonce, b.sig)
        with pytest.raises(R.RecordPowError):
            R.verify(forged)


async def test_insufficient_proof_of_work_rejected(me, monkeypatch):
    """Built at the suite's 3 bits, then judged against 24. Deterministic, and
    it doubles as the test for a protocol update raising the difficulty: old
    records stop being accepted."""
    record = await R.build(me, R.RecordType.RELAY, relay_body(me))
    R.verify(record)

    monkeypatch.setattr(P, "DIFFICULTY", dict.fromkeys(P.DIFFICULTY, 24))
    with pytest.raises(R.RecordPowError, match="needs 24"):
        R.verify(record)


async def test_zeroed_nonce_rejected(me, monkeypatch):
    """Build cheap, judge at 24 bits. Raising the difficulty *before* building
    would make build() search for a 2**24 solution — about an hour."""
    good = await R.build(me, R.RecordType.RELAY, relay_body(me), ttl=60)
    naked = R.Record(
        good.type, good.key, good.pub, good.ts, good.ttl, good.body, b"\x00" * 16, good.sig
    )
    monkeypatch.setattr(P, "DIFFICULTY", dict.fromkeys(P.DIFFICULTY, 24))
    with pytest.raises(R.RecordPowError):
        R.verify(naked)


async def test_clock_skew_window(me):
    """§16: ±2 h accepted, because we trust neither the clock nor NTP."""
    now = int(time.time())
    record = await R.build(me, R.RecordType.RELAY, relay_body(me), ttl=3600, ts=now)

    R.verify(record, now=now + R.MAX_SKEW - 10)  # expired, but inside skew
    R.verify(record, now=now - R.MAX_SKEW + 10)  # our clock is behind

    with pytest.raises(R.RecordExpired, match="in the future"):
        R.verify(record, now=now - R.MAX_SKEW - 60)
    with pytest.raises(R.RecordExpired, match="expired"):
        R.verify(record, now=now + 3600 + R.MAX_SKEW + 60)


async def test_ttl_bounds_enforced(me):
    for bad_ttl in (0, 30, 25 * 3600):
        with pytest.raises(R.RecordMalformed, match="ttl"):
            await R.build(me, R.RecordType.RELAY, relay_body(me), ttl=bad_ttl)


async def test_name_record_key_is_derived_from_the_name(me):
    record = await R.build(me, R.RecordType.NAME, {"name": "panaderia"})
    R.verify(record)
    assert record.key == R.name_key("panaderia")
    assert R.name_key("Panaderia") == R.name_key(" panaderia ")  # normalised


async def test_name_record_cannot_claim_a_different_name(me):
    record = await R.build(me, R.RecordType.NAME, {"name": "panaderia"})
    hijack = R.Record(
        record.type,
        R.name_key("banco"),
        record.pub,
        record.ts,
        record.ttl,
        record.body,
        record.nonce,
        record.sig,
    )
    with pytest.raises(R.RecordKeyMismatch):
        R.verify(hijack)


async def test_keyword_shards_are_distinct_and_bounded(me):
    keys = {R.keyword_key("pan", i) for i in range(R.KEYWORD_SHARDS)}
    assert len(keys) == R.KEYWORD_SHARDS
    with pytest.raises(ValueError):
        R.keyword_key("pan", R.KEYWORD_SHARDS)


async def test_keyword_record_accepts_any_key(me):
    """A keyword is not bound to its publisher: anyone may index any word."""
    record = await R.build(
        me,
        R.RecordType.KEYWORD,
        {"id": me.nid, "title": "La Panaderia"},
        key=R.keyword_key("pan", 3),
    )
    R.verify(record)
    assert record.key == R.keyword_key("pan", 3)


def test_malformed_input_rejected():
    with pytest.raises(R.RecordMalformed):
        R.Record.decode(b"no soy cbor")
    with pytest.raises(R.RecordMalformed):
        R.Record.decode(b"\x00" * (R.MAX_RECORD_BYTES + 1))
    import cbor2

    with pytest.raises(R.RecordMalformed, match="must be a map"):
        R.Record.decode(cbor2.dumps([1, 2, 3]))
    with pytest.raises(R.RecordMalformed, match="unknown record type"):
        R.Record.decode(cbor2.dumps({"t": "inventado"}))


async def test_shape_checks_before_crypto(me):
    good = await R.build(me, R.RecordType.RELAY, relay_body(me))
    short_sig = R.Record(
        good.type, good.key, good.pub, good.ts, good.ttl, good.body, good.nonce, b"\x00" * 10
    )
    with pytest.raises(R.RecordSignatureError, match="64"):
        R.verify(short_sig)
