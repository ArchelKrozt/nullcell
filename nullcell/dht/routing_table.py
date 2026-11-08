"""Kademlia routing table — §7.

``k = 20`` buckets over XOR distance on the **routing key**, ``sha256(pub)``,
not on the address (§3). The address carries the public key, which is not
uniformly distributed over the keyspace in any useful way; the hash is.

Eviction follows Kademlia's rule of **preferring nodes that have already proved
they stay up**: when a bucket is full the newcomer waits in a replacement cache
and only takes the slot if the least-recently-seen occupant stops answering.
That is not politeness, it is the main structural defence against an attacker
who floods the table with fresh identities to push honest nodes out — which is
half of how an eclipse attack works (§7, §10).
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Final

from nullcell.core import ids

__all__ = ["ID_BITS", "K", "AddResult", "Node", "RoutingTable", "distance"]

K: Final[int] = 20
ID_BITS: Final[int] = 256
_REPLACEMENTS: Final[int] = K  # per bucket


@dataclass(slots=True)
class Node:
    """A peer we know how to reach."""

    pub: bytes
    endpoints: list[str] = field(default_factory=list)
    last_seen: float = field(default_factory=time.monotonic)
    rtt_ms: float | None = None
    failures: int = 0

    @property
    def node_id(self) -> bytes:
        return ids.routing_key(self.pub)

    @property
    def nid(self) -> str:
        return ids.encode_nid(self.pub)

    @property
    def short(self) -> str:
        return ids.short(self.nid)

    def touch(self, rtt_ms: float | None = None) -> None:
        self.last_seen = time.monotonic()
        self.failures = 0
        if rtt_ms is not None:
            self.rtt_ms = rtt_ms

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Node) and other.pub == self.pub

    def __hash__(self) -> int:
        return hash(self.pub)


class AddResult(Enum):
    ADDED = auto()
    UPDATED = auto()
    CACHED = auto()  # bucket full; waiting for an occupant to fail
    SELF = auto()  # our own id, never stored


def distance(a: bytes, b: bytes) -> int:
    """XOR metric over routing keys."""
    return int.from_bytes(a, "big") ^ int.from_bytes(b, "big")


class RoutingTable:
    """``ID_BITS`` buckets of at most ``K`` nodes, indexed by XOR distance."""

    __slots__ = ("local_id", "k", "_buckets", "_cache")

    def __init__(self, local_id: bytes, k: int = K) -> None:
        if len(local_id) != ID_BITS // 8:
            raise ValueError(f"local id must be {ID_BITS // 8} bytes")
        self.local_id = local_id
        self.k = k
        self._buckets: list[OrderedDict[bytes, Node]] = [OrderedDict() for _ in range(ID_BITS)]
        self._cache: list[OrderedDict[bytes, Node]] = [OrderedDict() for _ in range(ID_BITS)]

    # ----------------------------------------------------------------- shape
    def bucket_index(self, node_id: bytes) -> int:
        """Bucket for *node_id*: 0 is farthest, ``ID_BITS - 1`` is closest."""
        d = distance(self.local_id, node_id)
        if d == 0:
            raise ValueError("own id has no bucket")
        return ID_BITS - d.bit_length()

    def __len__(self) -> int:
        return sum(len(b) for b in self._buckets)

    @property
    def buckets_in_use(self) -> int:
        return sum(1 for b in self._buckets if b)

    def all_nodes(self) -> list[Node]:
        return [n for bucket in self._buckets for n in bucket.values()]

    # ------------------------------------------------------------ membership
    def add(self, node: Node) -> AddResult:
        """Insert or refresh *node*, honouring the prefer-old-nodes rule."""
        node_id = node.node_id
        if node_id == self.local_id:
            return AddResult.SELF

        index = self.bucket_index(node_id)
        bucket = self._buckets[index]

        if node_id in bucket:
            existing = bucket[node_id]
            existing.endpoints = node.endpoints or existing.endpoints
            existing.touch(node.rtt_ms)
            bucket.move_to_end(node_id)  # most recently seen at the end
            return AddResult.UPDATED

        if len(bucket) < self.k:
            bucket[node_id] = node
            self._cache[index].pop(node_id, None)
            return AddResult.ADDED

        # Bucket full: the newcomer waits. An attacker cannot evict a live node
        # merely by showing up with a new identity.
        cache = self._cache[index]
        cache[node_id] = node
        cache.move_to_end(node_id)
        while len(cache) > _REPLACEMENTS:
            cache.popitem(last=False)
        return AddResult.CACHED

    def remove(self, node_id: bytes) -> Node | None:
        """Drop a node and promote a replacement if one is waiting."""
        if node_id == self.local_id:
            return None
        index = self.bucket_index(node_id)
        removed = self._buckets[index].pop(node_id, None)
        if removed is not None and self._cache[index]:
            promoted_id, promoted = self._cache[index].popitem(last=True)
            self._buckets[index][promoted_id] = promoted
        return removed

    def record_failure(self, node_id: bytes, max_failures: int = 3) -> bool:
        """Count an unanswered request. Returns True if the node was dropped."""
        if node_id == self.local_id:
            return False
        node = self._buckets[self.bucket_index(node_id)].get(node_id)
        if node is None:
            return False
        node.failures += 1
        if node.failures >= max_failures:
            self.remove(node_id)
            return True
        return False

    # --------------------------------------------------------------- queries
    def closest(self, target: bytes, n: int | None = None) -> list[Node]:
        """The *n* known nodes closest to *target*."""
        n = self.k if n is None else n
        return sorted(self.all_nodes(), key=lambda node: distance(node.node_id, target))[:n]

    def staleest_in_bucket(self, node_id: bytes) -> Node | None:
        """Least recently seen node in that bucket — the one to ping first."""
        bucket = self._buckets[self.bucket_index(node_id)]
        return next(iter(bucket.values()), None)

    def buckets_needing_refresh(self, max_age: float = 3600.0) -> list[int]:
        """Indices of non-empty buckets whose freshest node is older than *max_age*."""
        now = time.monotonic()
        stale = []
        for index, bucket in enumerate(self._buckets):
            if bucket and now - max(n.last_seen for n in bucket.values()) > max_age:
                stale.append(index)
        return stale

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RoutingTable {len(self)} nodes in {self.buckets_in_use} buckets>"
