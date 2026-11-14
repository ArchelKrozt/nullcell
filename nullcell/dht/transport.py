"""How DHT messages travel — the seam for §7's circuit decision.

§7 decided that **all of a client's DHT traffic goes over circuits**, because a
direct lookup reveals both this node's address and what it is looking for.
Kademlia talks to a :class:`Transport` and never learns which one it has:

* :class:`DirectTransport` — one verified QUIC link per peer. Fast, and **not
  anonymous**: the peer sees this node's address and what it asked for. Correct
  for a public relay, which is reachable anyway and has no address to hide, and
  wrong for a client.
* :class:`CircuitTransport` — every request over a three-hop circuit, which is
  what §7 actually decided. §7 also says this circuit must be **kept warm and
  reused**: the latency budget of 1-3 s per lookup assumes circuit construction
  is not paid per request.

A client that uses :class:`DirectTransport` has the DHT of M2, not the design.
Which one is in use is therefore reported in the HUD rather than left implicit.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from nullcell.core.frames import RelayCmd
from nullcell.dht.routing_table import Node
from nullcell.net.link_manager import LinkManager
from nullcell.onion.circuit import Circuit, Hop
from nullcell.onion.service import CircuitManager

__all__ = ["CircuitTransport", "DirectTransport", "Transport", "TransportError"]


class TransportError(Exception):
    """The peer could not be reached, or did not answer in time."""


@runtime_checkable
class Transport(Protocol):
    """Send a request to a node and await its reply."""

    #: Whether this transport hides the caller's address from the peer. The
    #: HUD shows it, because the difference is the whole of §7's decision 8.
    anonymous: bool

    async def rpc(self, node: Node, request: bytes, *, timeout: float = 10.0) -> bytes: ...

    async def close(self) -> None: ...


class DirectTransport:
    """One verified QUIC link per peer, cached and reused.

    Reuse is not just an optimisation — §7 budgets the cost of a lookup on the
    assumption that link construction is not paid per request.

    **Not anonymous.** Appropriate for a public relay doing DHT maintenance,
    which is reachable anyway; wrong for a client, which should use
    :class:`CircuitTransport`.
    """

    anonymous = False

    def __init__(self, links: LinkManager | None = None) -> None:
        self.links = links if links is not None else LinkManager()

    async def rpc(self, node: Node, request: bytes, *, timeout: float = 10.0) -> bytes:
        if not node.endpoints:
            raise TransportError(f"{node.short} has no known endpoint")
        try:
            mux = await self.links.connect(node.pub, node.endpoints, timeout=timeout)
        except TransportError:
            raise
        except Exception as exc:
            raise TransportError(f"rpc to {node.short} failed: {exc}") from exc
        try:
            return await mux.request(request, timeout=timeout)
        except Exception as exc:
            # A link can die without saying so (a NAT mapping expires, the
            # peer restarts). Keeping it would fail every retry the same way
            # and get a live peer dropped from the table; the next attempt
            # opens a fresh one instead.
            if self.links.get(node.pub) is mux:
                await self.links.disconnect(node.pub)
            if isinstance(exc, TransportError):
                raise
            raise TransportError(f"rpc to {node.short} failed: {exc}") from exc

    async def close(self) -> None:
        await self.links.close()


class CircuitTransport:
    """§7's decision 8: every DHT request over a three-hop circuit.

    One circuit, built once and reused for every lookup — §7 calls it the
    *directory circuit*. Rebuilding it per request would multiply the latency
    budget by the cost of a circuit build, and rotating it constantly is the
    behaviour §7 warns against for guards.

    The peer is reached as the circuit's **last hop**, so the request arrives
    from the exit and nothing about the caller travels with it.
    """

    anonymous = True

    def __init__(
        self,
        build_path: Callable[[Node], Awaitable[list[Hop]]],
        manager: CircuitManager,
        *,
        stream_id: int = 1,
    ) -> None:
        self.build_path = build_path
        self.manager = manager
        self.stream_id = stream_id
        self._circuits: dict[bytes, Circuit] = {}
        self._lock = asyncio.Lock()
        #: Una petición a la vez por circuito. La respuesta llega por el
        #: `on_data` del circuito, que es **uno solo**: con dos peticiones en
        #: vuelo, la segunda pisaba el receptor de la primera, que se quedaba
        #: sin respuesta, y a veces recibía la de la otra. El buscador lanza
        #: 16 consultas a la vez (una por fragmento del índice): casi todas
        #: fallaban, y el nombre de un portal salía «no registrado».
        self._in_flight: dict[bytes, asyncio.Lock] = {}

    async def _circuit_for(self, node: Node, timeout: float) -> Circuit:
        """A warm circuit whose exit is *node*, built once (§7)."""
        circuit = self._circuits.get(node.pub)
        if circuit is not None and circuit.ready:
            retire, _ = circuit.should_retire
            if not retire:
                return circuit
            circuit.destroy()
            self._circuits.pop(node.pub, None)

        async with self._lock:
            circuit = self._circuits.get(node.pub)
            if circuit is not None and circuit.ready:
                return circuit
            path = await self.build_path(node)
            circuit = await asyncio.wait_for(self.manager.build(path), timeout)
            self._circuits[node.pub] = circuit
            return circuit

    async def rpc(self, node: Node, request: bytes, *, timeout: float = 10.0) -> bytes:
        if not node.endpoints:
            raise TransportError(f"{node.short} has no known endpoint")
        lock = self._in_flight.setdefault(node.pub, asyncio.Lock())
        async with lock:
            return await self._rpc_alone(node, request, timeout=timeout)

    async def _rpc_alone(self, node: Node, request: bytes, *, timeout: float) -> bytes:
        """Una petición con el circuito para ella sola (ver `_in_flight`)."""
        try:
            circuit = await self._circuit_for(node, timeout)
        except Exception as exc:
            raise TransportError(f"no circuit to {node.short}: {exc}") from exc

        future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

        def on_data(stream_id: int, rcmd: int, payload: bytes) -> None:
            if rcmd == RelayCmd.DATA and not future.done():
                future.set_result(bytes(payload))

        circuit.on_data = on_data
        try:
            await circuit.send_data(request, stream_id=self.stream_id)
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as exc:
            raise TransportError(f"{node.short} did not answer within {timeout} s") from exc
        except Exception as exc:
            raise TransportError(f"rpc to {node.short} failed: {exc}") from exc
        finally:
            circuit.on_data = None

    async def close(self) -> None:
        for circuit in self._circuits.values():
            circuit.destroy()
        self._circuits.clear()
        await self.manager.close()
