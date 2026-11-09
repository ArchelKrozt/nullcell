"""Gossip de observaciones — §8 mechanism 3, §9 liveness."""

from __future__ import annotations

import time

import pytest

from nullcell.core.identity import Identity
from nullcell.search.gossip import (
    MAX_DELTAS,
    OBSERVATION_TTL,
    BloomFilter,
    GossipService,
    Observation,
)


@pytest.fixture
def portal() -> Identity:
    return Identity.generate()


# ------------------------------------------------------------- observations
def test_an_observation_verifies_under_its_own_signature(portal):
    me = Identity.generate()
    observation = GossipService(me).observe(portal.public_key)
    assert observation.verify()
    assert observation.observer == me.public_key


def test_a_forged_signature_is_refused(portal):
    liar, victim = Identity.generate(), Identity.generate()
    forged = Observation(
        portal=portal.public_key,
        observer=victim.public_key,  # claiming to be someone else
        seen_at=int(time.time()),
        ok=True,
        sig=b"\x00" * 64,
    )
    assert not forged.verify()
    assert GossipService(liar).ingest([forged]) == 0


def test_an_observation_cannot_be_re_attributed(portal):
    """The observer is inside the signed binding."""
    a, b = Identity.generate(), Identity.generate()
    real = GossipService(a).observe(portal.public_key)
    stolen = Observation(
        portal=real.portal, observer=b.public_key, seen_at=real.seen_at, ok=real.ok, sig=real.sig
    )
    assert not stolen.verify()


def test_a_sighting_from_the_future_is_not_a_sighting(portal):
    me = Identity.generate()
    future = Observation(
        portal=portal.public_key, observer=me.public_key, seen_at=int(time.time()) + 99_999, ok=True
    ).signed_by(me)
    assert not future.verify()


def test_a_stale_observation_is_refused(portal):
    me = Identity.generate()
    old = Observation(
        portal=portal.public_key,
        observer=me.public_key,
        seen_at=int(time.time()) - OBSERVATION_TTL * 3,
        ok=True,
    ).signed_by(me)
    assert not old.verify()


def test_malformed_observations_are_skipped():
    assert GossipService.decode_observations(None) == []
    assert GossipService.decode_observations([{"p": b"corto"}, 42, "x"]) == []


# ------------------------------------------------------- confirmations §9
def test_the_same_cell_seeing_a_portal_twice_is_one_confirmation(portal):
    """Otherwise one cell could confirm a portal a thousand times by visiting
    it a thousand times."""
    gossip = GossipService(Identity.generate())
    for _ in range(5):
        gossip.observe(portal.public_key)
    assert gossip.confirmations(portal.public_key) == 1
    assert len(gossip) == 1


def test_distinct_cells_each_count(portal):
    mine = GossipService(Identity.generate())
    for _ in range(4):
        other = GossipService(Identity.generate())
        other.observe(portal.public_key)
        mine.ingest(other.deltas(None))
    assert mine.confirmations(portal.public_key) == 4


def test_a_failed_visit_does_not_confirm(portal):
    gossip = GossipService(Identity.generate())
    gossip.observe(portal.public_key, ok=False)
    assert gossip.confirmations(portal.public_key) == 0
    assert len(gossip) == 1  # the failure is still recorded


def test_the_description_is_what_we_know_not_a_percentage(portal):
    """§9: never a '99,8 % uptime' nobody can verify."""
    gossip = GossipService(Identity.generate())
    assert gossip.describe(portal.public_key) == "sin observaciones"
    gossip.observe(portal.public_key)
    described = gossip.describe(portal.public_key)
    assert "visto hace" in described
    assert "confirmado por 1 celdas" in described
    assert "%" not in described


def test_expiring_drops_old_observations(portal):
    gossip = GossipService(Identity.generate())
    gossip.observe(portal.public_key)
    assert gossip.expire() == 0
    assert gossip.expire(now=int(time.time()) + OBSERVATION_TTL * 2) == 1
    assert len(gossip) == 0


# -------------------------------------------------------------- exchange §8
def test_an_exchange_sends_only_what_the_peer_lacks(portal):
    a, b = GossipService(Identity.generate()), GossipService(Identity.generate())
    for _ in range(3):
        other = GossipService(Identity.generate())
        other.observe(portal.public_key)
        a.ingest(other.deltas(None))

    assert len(a.deltas(b.digest())) == len(a)  # b knows nothing
    b.ingest(a.deltas(b.digest()))
    assert len(b) == len(a)
    assert a.deltas(b.digest()) == [], "sent again what the peer already had"


def test_our_own_claim_coming_back_is_ignored(portal):
    me = GossipService(Identity.generate())
    mine = me.observe(portal.public_key)
    assert me.ingest([mine]) == 0


def test_a_newer_sighting_replaces_an_older_one(portal):
    watcher = Identity.generate()
    mine = GossipService(Identity.generate())

    older = Observation(
        portal=portal.public_key,
        observer=watcher.public_key,
        seen_at=int(time.time()) - 600,
        ok=True,
    ).signed_by(watcher)
    newer = Observation(
        portal=portal.public_key,
        observer=watcher.public_key,
        seen_at=int(time.time()),
        ok=True,
    ).signed_by(watcher)

    assert mine.ingest([older]) == 1
    assert mine.ingest([newer]) == 1
    assert len(mine) == 1
    assert mine.last_seen(portal.public_key) == newer.seen_at
    assert mine.ingest([older]) == 0, "an older claim overwrote a newer one"


def test_an_exchange_is_bounded(portal):
    a = GossipService(Identity.generate())
    for _ in range(MAX_DELTAS * 3):
        other = GossipService(Identity.generate())
        other.observe(Identity.generate().public_key)
        a.ingest(other.deltas(None))
    assert len(a.deltas(None)) <= MAX_DELTAS


def test_confirmations_feed_the_index(portal, tmp_path):
    """So the ranking of §8 sees them."""
    import asyncio

    from nullcell.search.indexer import Indexer

    async def run() -> None:
        index = Indexer(tmp_path / "i.db")
        index.start()
        try:
            mine = GossipService(Identity.generate(), indexer=index)
            for _ in range(3):
                other = GossipService(Identity.generate())
                other.observe(portal.public_key)
                mine.ingest(other.deltas(None))
            await index.flush()
            [hit] = await index.recent()
            assert hit.nid == portal.nid
            assert hit.confirmed == 3
        finally:
            index.close()

    asyncio.run(run())


# ----------------------------------------------------------- bloom filters
def test_a_filter_reports_what_was_added():
    bloom = BloomFilter.sized_for(100)
    for i in range(100):
        bloom.add(f"x{i}".encode())
    assert all(f"x{i}".encode() in bloom for i in range(100))


def test_the_false_positive_rate_is_about_one_percent():
    bloom = BloomFilter.sized_for(1000, rate=0.01)
    for i in range(1000):
        bloom.add(f"x{i}".encode())
    false = sum(1 for i in range(1000, 11_000) if f"x{i}".encode() in bloom)
    assert false / 10_000 < 0.03, f"{false / 100:.2f}% false positives"


def test_a_filter_round_trips():
    bloom = BloomFilter.sized_for(50)
    bloom.add(b"hola")
    again = BloomFilter.from_dict(bloom.as_dict())
    assert again is not None
    assert b"hola" in again


@pytest.mark.parametrize(
    "raw",
    [
        {"b": 1 << 30, "k": 4, "d": b""},  # absurd size
        {"b": 0, "k": 4, "d": b""},
        {"b": 63, "k": 4, "d": b"\x00" * 8},  # not a multiple of 8
        {"b": 64, "k": 99, "d": b"\x00" * 8},
        {"b": 64, "k": 4, "d": b"\x00"},  # data does not match
        {"b": "muchos", "k": 4, "d": b""},
        None,
        [],
    ],
)
def test_a_hostile_filter_is_refused(raw):
    """A peer must not be able to make us allocate a gigabyte."""
    assert BloomFilter.from_dict(raw) is None


def test_a_non_bytes_lookup_is_false():
    assert 42 not in BloomFilter.sized_for(10)
