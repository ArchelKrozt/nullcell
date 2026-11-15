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
from typing import Final, Protocol, runtime_checkable

from nullcell.core.frames import RELAY_DATA_SIZE, RelayCmd
from nullcell.dht.routing_table import Node
from nullcell.net.link_manager import LinkManager
from nullcell.onion.circuit import Circuit, Hop
from nullcell.onion.service import CircuitManager

__all__ = [
    "FRAMED",
    "CircuitTransport",
    "DirectTransport",
    "Reassembler",
    "Transport",
    "TransportError",
    "frame",
]

# ------------------------------------------------------------- troceado
#: Un mensaje DHT por circuito cabía en **una** celda (496 bytes) y no había
#: nada para los más largos: publicar una presencia de chat o pedir una clave
#: con varios registros fallaba en silencio («publicada en 0 celdas»). Ahora
#: viaja troceado: `FRAMED` + longitud (4 bytes) + mensaje, en tantas celdas
#: como haga falta. Ningún mensaje CBOR del protocolo empieza por 0x00, así
#: que un relevo distingue el formato nuevo del antiguo (una celda, sin
#: prefijo), que se sigue entendiendo en los dos sentidos.
FRAMED: Final[bytes] = b"\x00NCF"
#: Más que esto no es una respuesta del DHT, es un intento de llenar memoria.
MAX_FRAMED: Final[int] = 512 * 1024


def frame(blob: bytes) -> list[bytes]:
    """El mensaje con su cabecera, en trozos que caben en una celda."""
    if len(blob) > MAX_FRAMED:
        raise TransportError(f"mensaje DHT de {len(blob)} bytes: máximo {MAX_FRAMED}")
    data = FRAMED + len(blob).to_bytes(4, "big") + blob
    return [data[i : i + RELAY_DATA_SIZE] for i in range(0, len(data), RELAY_DATA_SIZE)]


class Reassembler:
    """Junta los trozos de un mensaje. `feed` devuelve el mensaje al completarse.

    Si el primer trozo no lleva la cabecera, es el formato antiguo: una celda
    es el mensaje entero, y `legacy` lo dice.
    """

    __slots__ = ("_buf", "_want", "legacy")

    def __init__(self) -> None:
        self._buf = bytearray()
        self._want: int | None = None
        self.legacy = False

    @property
    def idle(self) -> bool:
        return self._want is None and not self._buf

    def feed(self, chunk: bytes) -> bytes | None:
        if self._want is None and not self._buf:
            if not chunk.startswith(FRAMED):
                self.legacy = True
                return bytes(chunk)
            self.legacy = False
        self._buf += chunk
        head = len(FRAMED) + 4
        if self._want is None:
            if len(self._buf) < head:
                return None
            self._want = int.from_bytes(self._buf[len(FRAMED) : head], "big")
            if self._want > MAX_FRAMED:
                self.reset()
                raise TransportError("mensaje DHT troceado demasiado grande")
        if len(self._buf) < head + self._want:
            return None
        message = bytes(self._buf[head : head + self._want])
        self.reset()
        return message

    def reset(self) -> None:
        self._buf.clear()
        self._want = None


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
        #: Salidas que sólo entienden el formato antiguo (una celda, sin trocear).
        self._legacy: set[bytes] = set()

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

        antiguo = node.pub in self._legacy
        if antiguo and len(request) > RELAY_DATA_SIZE:
            raise TransportError(f"{node.short} es de una versión que no acepta mensajes largos")
        reply, legacy = await self._exchange(circuit, node, request, timeout, framed=not antiguo)
        if legacy and not antiguo:
            # Una salida antigua no entendió el formato troceado y contestó con
            # un error en una sola celda. Se recuerda y, si cabe, se repite.
            self._legacy.add(node.pub)
            if len(request) > RELAY_DATA_SIZE:
                raise TransportError(
                    f"{node.short} es de una versión que no acepta mensajes largos"
                )
            reply, _ = await self._exchange(circuit, node, request, timeout, framed=False)
        return reply

    async def _exchange(
        self, circuit: Circuit, node: Node, request: bytes, timeout: float, *, framed: bool
    ) -> tuple[bytes, bool]:
        future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        junta = Reassembler()

        def on_data(stream_id: int, rcmd: int, payload: bytes) -> None:
            if rcmd != RelayCmd.DATA or future.done():
                return
            try:
                message = junta.feed(bytes(payload))
            except TransportError as exc:
                future.set_exception(exc)
                return
            if message is not None:
                future.set_result(message)

        circuit.on_data = on_data
        try:
            for chunk in frame(request) if framed else [request]:
                await circuit.send_data(chunk, stream_id=self.stream_id)
            return await asyncio.wait_for(future, timeout), junta.legacy
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
