"""Local DHT storage and its quotas — §7, §8."""

import time

import pytest

from nullcell.core.identity import Identity
from nullcell.dht import pow as P
from nullcell.dht import records as R
from nullcell.dht.store import Store, StoreFull


@pytest.fixture(autouse=True)
def cheap_pow(monkeypatch):
    monkeypatch.setattr(P, "DIFFICULTY", dict.fromkeys(P.DIFFICULTY, 3))


async def keyword(identity, word="pan", shard=0, title="x", ts=None):
    return await R.build(
        identity,
        R.RecordType.KEYWORD,
        {"id": identity.nid, "title": title},
        key=R.keyword_key(word, shard),
        ttl=3600,
        ts=ts,
    )


async def test_put_and_get():
    store, me = Store(), Identity.generate()
    record = await keyword(me)
    assert store.put(record)
    assert store.get(record.key) == [record]
    assert record.key in store


async def test_republication_refreshes_in_place():
    """One entry per publisher per key: nobody fills a key alone."""
    store, me = Store(), Identity.generate()
    now = int(time.time())
    first = await keyword(me, ts=now - 100)
    second = await keyword(me, ts=now, title="actualizado")
    store.put(first)
    assert store.put(second)
    assert len(store) == 1
    assert store.get(first.key)[0].body["title"] == "actualizado"


async def test_replay_of_an_older_record_is_refused():
    store, me = Store(), Identity.generate()
    now = int(time.time())
    newer = await keyword(me, ts=now)
    older = await keyword(me, ts=now - 600)
    store.put(newer)
    assert not store.put(older)
    assert store.get(newer.key)[0].ts == now


async def test_many_publishers_share_a_keyword():
    store = Store()
    records = [await keyword(Identity.generate()) for _ in range(5)]
    for record in records:
        assert store.put(record)
    assert len(store.get(records[0].key)) == 5


async def test_entries_per_key_is_capped():
    """§8: a popular word must not be able to exhaust the nodes holding it."""
    store = Store(max_entries_per_key=4)
    key = None
    for _ in range(4):
        record = await keyword(Identity.generate())
        key = record.key
        assert store.put(record)
    assert len(store.get(key)) == 4

    # Same difficulty, newer: must not displace an established entry.
    newcomer = await keyword(Identity.generate())
    assert not store.put(newcomer)
    assert len(store.get(key)) == 4


async def test_higher_proof_of_work_can_displace(monkeypatch):
    store = Store(max_entries_per_key=2)
    for _ in range(2):
        assert store.put(await keyword(Identity.generate()))

    monkeypatch.setattr(P, "DIFFICULTY", dict.fromkeys(P.DIFFICULTY, 8))
    stronger = await keyword(Identity.generate())
    assert store.put(stronger)
    assert len(store.get(stronger.key)) == 2
    assert stronger in store.get(stronger.key)


async def test_single_value_keys_are_first_come():
    """A name belongs to whoever registered it until it expires (§8)."""
    store = Store()
    first, second = Identity.generate(), Identity.generate()
    a = await R.build(first, R.RecordType.NAME, {"name": "panaderia"})
    b = await R.build(second, R.RecordType.NAME, {"name": "panaderia"})
    assert store.put(a)
    assert not store.put(b)
    assert store.get(a.key)[0].pub == first.public_key


async def test_expiry_removes_records_and_empty_keys():
    store, me = Store(), Identity.generate()
    now = int(time.time())
    record = await keyword(me, ts=now - 7200)  # ttl 3600 -> long gone
    store.put(record)
    assert store.get(record.key, now=now) == []  # hidden from reads already
    assert store.expire(now=now) == 1
    assert store.stats == {"keys": 0, "records": 0, "bytes": 0}


async def test_key_quota():
    store = Store(max_keys=2)
    for shard in range(2):
        assert store.put(await keyword(Identity.generate(), shard=shard))
    with pytest.raises(StoreFull, match="key quota"):
        store.put(await keyword(Identity.generate(), shard=2))


async def test_byte_quota():
    store = Store(max_bytes=100)
    with pytest.raises(StoreFull, match="byte quota"):
        store.put(await keyword(Identity.generate()))


async def test_byte_accounting_stays_consistent():
    store = Store()
    records = [await keyword(Identity.generate()) for _ in range(5)]
    for record in records:
        store.put(record)
    assert store.stats["bytes"] == sum(len(r.encode()) for r in records)
    store.expire(now=int(time.time()) + 10_000)
    assert store.stats["bytes"] == 0


async def test_blobs_match_get():
    store, me = Store(), Identity.generate()
    record = await keyword(me)
    store.put(record)
    assert store.blobs(record.key) == [record.encode()]


def test_get_unknown_key():
    assert Store().get(b"\x00" * 32) == []
