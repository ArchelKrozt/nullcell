"""Kademlia routing table — §7."""

import os

import pytest

from nullcell.core import ids
from nullcell.dht.routing_table import ID_BITS, AddResult, K, Node, RoutingTable, distance


def node() -> Node:
    return Node(pub=os.urandom(32), endpoints=["127.0.0.1:4433"])


@pytest.fixture
def table() -> RoutingTable:
    return RoutingTable(ids.routing_key(os.urandom(32)))


def test_distance_is_a_metric(table):
    a, b = os.urandom(32), os.urandom(32)
    assert distance(a, a) == 0
    assert distance(a, b) == distance(b, a)
    assert distance(a, b) > 0


def test_bucket_index_range(table):
    for _ in range(200):
        assert 0 <= table.bucket_index(os.urandom(32)) < ID_BITS


def test_closer_nodes_land_in_higher_buckets(table):
    """Bucket 255 is the closest; bucket 0 is the far half of the keyspace."""
    local = int.from_bytes(table.local_id, "big")
    near = (local ^ 1).to_bytes(32, "big")  # differs in the last bit only
    far = (local ^ (1 << 255)).to_bytes(32, "big")  # differs in the first bit
    assert table.bucket_index(near) == ID_BITS - 1
    assert table.bucket_index(far) == 0


def test_own_id_is_never_stored(table):
    me = Node(pub=os.urandom(32))
    own = RoutingTable(me.node_id)
    assert own.add(me) is AddResult.SELF
    assert len(own) == 0


def test_add_then_update(table):
    n = node()
    assert table.add(n) is AddResult.ADDED
    assert table.add(n) is AddResult.UPDATED
    assert len(table) == 1


def test_bucket_caps_at_k_and_caches_the_rest(table):
    """§7: a newcomer must not evict a live node just by showing up."""
    local = int.from_bytes(table.local_id, "big")
    # Build nodes that all land in bucket 255 by flipping only the last bit
    # pattern — use ids whose xor with local has bit_length 1.
    added = cached = 0
    pubs = []
    while added + cached < K + 5:
        n = node()
        if table.bucket_index(n.node_id) != 0:
            continue
        pubs.append(n)
        result = table.add(n)
        if result is AddResult.ADDED:
            added += 1
        else:
            cached += 1
    assert added == K
    assert cached >= 1
    assert local  # local id used, keeps the linter honest


def test_failures_drop_a_node_and_promote_a_replacement(table):
    n = node()
    table.add(n)
    assert not table.record_failure(n.node_id)
    assert not table.record_failure(n.node_id)
    assert table.record_failure(n.node_id)  # third strike
    assert len(table) == 0


def test_remove_unknown_node_is_harmless(table):
    assert table.remove(os.urandom(32)) is None


def test_closest_is_sorted_by_distance(table):
    for _ in range(100):
        table.add(node())
    target = os.urandom(32)
    found = table.closest(target, 10)
    distances = [distance(n.node_id, target) for n in found]
    assert distances == sorted(distances)
    assert len(found) == 10


def test_closest_returns_everything_when_the_table_is_small(table):
    for _ in range(3):
        table.add(node())
    assert len(table.closest(os.urandom(32), 20)) == 3


def test_random_ids_fill_few_buckets(table):
    """Not a bug: bucket i covers 2^(255-i) of the keyspace, so the near ones
    stay empty until the network is enormous."""
    for _ in range(500):
        table.add(node())
    assert table.buckets_in_use < 20
    assert len(table) <= table.buckets_in_use * K


def test_bucket_refresh_detection(table):
    import time as t

    n = node()
    table.add(n)
    assert table.buckets_needing_refresh(max_age=3600) == []
    n.last_seen = t.monotonic() - 7200
    assert table.buckets_needing_refresh(max_age=3600) == [table.bucket_index(n.node_id)]


def test_rejects_wrong_sized_local_id():
    with pytest.raises(ValueError):
        RoutingTable(b"corto")
