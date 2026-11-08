"""Peer exchange — §7.

Asks the peers we already have for peers near random points of the keyspace.
Random targets are the point: looking up only our own id fills the buckets close
to us and leaves the far ones empty, which is exactly the shape an eclipse
attack wants (§7, §10).

PEX never adds a peer on a peer's word. A referral is a hint; the node enters
the routing table only once a link to it has been verified against its address
(§4), which happens on first contact.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Final

from nullcell.core import ids

if TYPE_CHECKING:
    from nullcell.dht.kademlia import KademliaNode

__all__ = ["PEX_ROUNDS", "exchange", "run_periodically"]

PEX_ROUNDS: Final[int] = 4
PEX_INTERVAL: Final[float] = 300.0


async def exchange(node: KademliaNode, rounds: int = PEX_ROUNDS) -> int:
    """Look up random targets to widen the routing table. Returns the new size."""
    for _ in range(rounds):
        await node.find_node(ids.routing_key(os.urandom(32)))
    return len(node.table)


async def run_periodically(
    node: KademliaNode, *, interval: float = PEX_INTERVAL, rounds: int = PEX_ROUNDS
) -> None:
    """Background task: PEX and bucket refresh forever."""
    while True:
        await asyncio.sleep(interval)
        try:
            await exchange(node, rounds)
            await node.refresh()
        except asyncio.CancelledError:
            raise
        except Exception:  # a failed round must not kill the loop
            continue
