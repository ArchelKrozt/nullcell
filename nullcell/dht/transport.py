"""How DHT messages travel — the seam for §7's circuit decision.

§7 decided that **all of a client's DHT traffic goes over circuits**, because a
direct lookup reveals both this node's address and what it is looking for.
Circuits arrive in M3, so M2 ships :class:`DirectTransport`, and the Kademlia
implementation never learns which one it is using.

That means the DHT in M2 is **not anonymous**: lookups go out over a direct
link. It is a temporary state of the implementation, flagged here and in the
README, not a change to the design.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from nullcell.dht.routing_table import Node
from nullcell.net.link_manager import LinkManager

__all__ = ["DirectTransport", "Transport", "TransportError"]


class TransportError(Exception):
    """The peer could not be reached, or did not answer in time."""


@runtime_checkable
class Transport(Protocol):
    """Send a request to a node and await its reply."""

    async def rpc(self, node: Node, request: bytes, *, timeout: float = 10.0) -> bytes: ...

    async def close(self) -> None: ...


class DirectTransport:
    """M2 transport: one verified QUIC link per peer, cached and reused.

    Reuse is not just an optimisation — §7 budgets the cost of a lookup on the
    assumption that link construction is not paid per request.
    """

    def __init__(self, links: LinkManager | None = None) -> None:
        self.links = links if links is not None else LinkManager()

    async def rpc(self, node: Node, request: bytes, *, timeout: float = 10.0) -> bytes:
        if not node.endpoints:
            raise TransportError(f"{node.short} has no known endpoint")
        try:
            mux = await self.links.connect(node.pub, node.endpoints, timeout=timeout)
            return await mux.request(request, timeout=timeout)
        except TransportError:
            raise
        except Exception as exc:
            raise TransportError(f"rpc to {node.short} failed: {exc}") from exc

    async def close(self) -> None:
        await self.links.close()
