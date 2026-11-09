"""Kademlia node — §7.

Serves the four RPCs and performs iterative lookups with ``alpha`` requests in
flight at once. §7 budgets a lookup at 1-3 s because each hop costs a circuit
round trip, and that budget only holds if the hops run **concurrently**; doing
them in series would multiply it by the number of hops.

Nothing here trusts anything. Records are verified before storage and before
being returned (§7), referrals are hints until the link to them is verified
against the address (§4), and a peer that answers nothing useful simply falls
out of the routing table.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Final

from nullcell.core import ids
from nullcell.core.frames import RelayCmd
from nullcell.core.identity import Identity
from nullcell.dht import protocol as proto
from nullcell.dht import records as rec
from nullcell.dht.records import Record, RecordError
from nullcell.dht.routing_table import K, Node, RoutingTable, distance
from nullcell.dht.store import Store, StoreFull
from nullcell.dht.transport import Transport, TransportError
from nullcell.net.link import FrameProtocol
from nullcell.net.stream import StreamMux

__all__ = ["ALPHA", "KademliaNode"]

#: Concurrent requests per lookup round. Kademlia's usual value.
ALPHA: Final[int] = 3

_RPC_TIMEOUT: Final[float] = 5.0
_MAX_ROUNDS: Final[int] = 20

log = logging.getLogger("nullcell.dht")


class KademliaNode:
    """This node's view of the DHT."""

    def __init__(
        self,
        identity: Identity,
        transport: Transport,
        *,
        endpoints: list[str] | None = None,
        store: Store | None = None,
        alpha: int = ALPHA,
        k: int = K,
    ) -> None:
        self.identity = identity
        self.transport = transport
        self.endpoints = endpoints or []
        self.store = store if store is not None else Store()
        self.table = RoutingTable(identity.routing_key, k=k)
        self.alpha = alpha
        self.k = k
        self._muxes: list[StreamMux] = []
        #: A :class:`~nullcell.search.gossip.GossipService`, if this cell keeps
        #: observations. Optional: a cell that only routes need not.
        self.gossip = None
        self.stats = {"rpc_in": 0, "rpc_out": 0, "stored": 0, "rejected": 0}

    # ------------------------------------------------------------------ serving
    def attach(self, link: FrameProtocol) -> StreamMux:
        """Serve DHT requests arriving on *link*. Pass to ``Listener.on_link``."""
        mux = StreamMux(link)
        mux.set_handler(self._handle)
        self._muxes.append(mux)
        return mux

    def circuit_handler(self, relay) -> object:
        """A handler that answers DHT requests arriving over a circuit (§7).

        Chained onto a relay's ``on_relay_message`` seam. A client using
        :class:`~nullcell.dht.transport.CircuitTransport` reaches this node as
        the **exit** of its circuit, so the request arrives as relay ``DATA``
        and the answer goes back down the same circuit. Nothing about the
        caller travels with it, which is the whole of decision 8.
        """

        def handle(leg, header) -> bool:
            if header.rcmd != RelayCmd.DATA:
                return False
            asyncio.ensure_future(self._answer_over_circuit(relay, leg, header.payload))
            return True

        return handle

    async def _answer_over_circuit(self, relay, leg, request: bytes) -> None:
        reply = await self._handle(request)
        if reply is not None:
            relay.send_back(leg, RelayCmd.DATA, reply, stream_id=1)

    async def _handle(self, blob: bytes) -> bytes | None:
        self.stats["rpc_in"] += 1
        try:
            message = proto.decode(blob)
            op = message.get("op")
            if op == proto.Op.PING:
                reply = self._on_ping(message)
            elif op == proto.Op.FIND_NODE:
                reply = self._on_find_node(message)
            elif op == proto.Op.FIND_VALUE:
                reply = self._on_find_value(message)
            elif op == proto.Op.STORE:
                reply = self._on_store(message)
            elif op == proto.Op.GOSSIP:
                reply = self._on_gossip(message)
            else:
                reply = {"err": f"unknown op: {op!r}"}
        except proto.ProtocolError as exc:
            reply = {"err": str(exc)}
        except Exception as exc:  # a bad request must not take the node down
            log.warning("dht handler failed: %s", exc)
            reply = {"err": "internal error"}
        return proto.encode(reply)

    def _me(self) -> dict[str, Any]:
        return {"pub": self.identity.public_key, "ep": list(self.endpoints)}

    def _learn(self, message: dict[str, Any]) -> None:
        """Note the sender, if it told us how to reach it."""
        pub, endpoints = message.get("pub"), message.get("ep")
        if isinstance(pub, bytes) and len(pub) == 32 and isinstance(endpoints, list):
            clean = [e for e in endpoints if isinstance(e, str)][:4]
            if clean:
                self.table.add(Node(pub=pub, endpoints=clean))

    def _on_ping(self, message: dict[str, Any]) -> dict[str, Any]:
        self._learn(message)
        return {"ok": 1, **self._me()}

    def _on_find_node(self, message: dict[str, Any]) -> dict[str, Any]:
        self._learn(message)
        target = message.get("target")
        if not (isinstance(target, bytes) and len(target) == 32):
            return {"err": "target must be 32 bytes"}
        return {"ok": 1, **self._me(), "nodes": proto.pack_nodes(self.table.closest(target))}

    def _on_find_value(self, message: dict[str, Any]) -> dict[str, Any]:
        self._learn(message)
        key = message.get("key")
        if not (isinstance(key, bytes) and len(key) == 32):
            return {"err": "key must be 32 bytes"}
        values = self.store.blobs(key)[: proto.MAX_VALUES_PER_REPLY]
        if values:
            return {"ok": 1, **self._me(), "values": values}
        # No value: answer with the nodes closer to it, which is what keeps the
        # lookup converging instead of dead-ending.
        return {"ok": 1, **self._me(), "nodes": proto.pack_nodes(self.table.closest(key))}

    def _on_store(self, message: dict[str, Any]) -> dict[str, Any]:
        self._learn(message)
        blob = message.get("rec")
        if not isinstance(blob, bytes):
            return {"err": "rec must be bytes"}
        try:
            record = Record.decode(blob)
            pow_bits = rec.verify(record)
        except RecordError as exc:
            self.stats["rejected"] += 1
            return {"err": f"rejected: {exc}"}

        try:
            stored = self.store.put(record, blob=blob, pow_bits=pow_bits)
        except StoreFull as exc:
            return {"err": str(exc)}
        if stored:
            self.stats["stored"] += 1
        return {"ok": 1, **self._me(), "stored": bool(stored)}

    def _on_gossip(self, message: dict[str, Any]) -> dict[str, Any]:
        """Exchange observations with a peer (§8, §9).

        Both directions in one round trip: we take what they sent and answer
        with what their filter says they lack. Over a circuit that round trip
        costs 300-800 ms, so making it symmetric halves the cost of staying
        current.
        """
        self._learn(message)
        if self.gossip is None:
            return {"err": "this cell does not gossip"}

        from nullcell.search.gossip import BloomFilter

        kept = self.gossip.ingest(self.gossip.decode_observations(message.get("obs")))
        peer_bloom = BloomFilter.from_dict(message.get("bloom"))
        return {
            "ok": 1,
            **self._me(),
            "accepted": kept,
            "obs": [o.as_dict() for o in self.gossip.deltas(peer_bloom)],
            "bloom": self.gossip.digest().as_dict(),
        }

    # ------------------------------------------------------------------ calling
    async def _rpc(self, node: Node, message: dict[str, Any]) -> dict[str, Any] | None:
        """One request. Returns None and penalises the node on failure."""
        self.stats["rpc_out"] += 1
        started = time.perf_counter()
        try:
            blob = await self.transport.rpc(
                node, proto.encode({**message, **self._me()}), timeout=_RPC_TIMEOUT
            )
            reply = proto.decode(blob)
        except (TimeoutError, TransportError, proto.ProtocolError, OSError):
            self.table.record_failure(node.node_id)
            return None

        if "err" in reply:
            return None
        node.touch((time.perf_counter() - started) * 1000)
        self.table.add(node)
        return reply

    async def ping(self, node: Node) -> bool:
        return await self._rpc(node, {"op": proto.Op.PING}) is not None

    async def exchange_gossip(self, node: Node) -> int:
        """One gossip round with *node*. Returns how many observations we kept."""
        if self.gossip is None:
            return 0
        reply = await self._rpc(
            node,
            {
                "op": proto.Op.GOSSIP,
                "bloom": self.gossip.digest().as_dict(),
                "obs": [o.as_dict() for o in self.gossip.deltas(None, limit=16)],
            },
        )
        if reply is None:
            return 0
        return self.gossip.ingest(self.gossip.decode_observations(reply.get("obs")))

    # --------------------------------------------------------- iterative lookup
    async def _lookup(self, target: bytes, *, want_value: bool) -> tuple[list[Node], list[bytes]]:
        """Kademlia's iterative lookup, ``alpha`` requests in flight.

        Returns ``(closest_nodes, value_blobs)``. For a value lookup it stops at
        the first node that has it; §7's latency budget depends on this
        finishing in a handful of concurrent rounds.
        """
        op = proto.Op.FIND_VALUE if want_value else proto.Op.FIND_NODE
        field = "key" if want_value else "target"

        shortlist: dict[bytes, Node] = {n.node_id: n for n in self.table.closest(target, self.k)}
        asked: set[bytes] = set()
        values: list[bytes] = []

        for _ in range(_MAX_ROUNDS):
            candidates = [
                node
                for node in sorted(shortlist.values(), key=lambda n: distance(n.node_id, target))[
                    : self.k
                ]
                if node.node_id not in asked
            ][: self.alpha]
            if not candidates:
                break

            for node in candidates:
                asked.add(node.node_id)

            replies = await asyncio.gather(
                *(self._rpc(node, {"op": op, field: target}) for node in candidates)
            )

            progressed = False
            for reply in replies:
                if reply is None:
                    continue
                raw_values = reply.get("values")
                if want_value and isinstance(raw_values, list):
                    values.extend(v for v in raw_values if isinstance(v, bytes))
                for found in proto.unpack_nodes(reply.get("nodes")):
                    if found.node_id == self.table.local_id:
                        continue
                    if found.node_id not in shortlist:
                        shortlist[found.node_id] = found
                        progressed = True

            if values:
                break
            if not progressed:
                break  # converged: nobody knows anyone closer

        closest = sorted(shortlist.values(), key=lambda n: distance(n.node_id, target))[: self.k]
        return closest, values

    async def find_node(self, target: bytes) -> list[Node]:
        """The ``k`` nodes closest to *target* that the network knows about."""
        nodes, _ = await self._lookup(target, want_value=False)
        return nodes

    async def find_value(self, key: bytes) -> list[Record]:
        """Fetch records under *key*, verifying every one before returning it."""
        local = self.store.get(key)
        if local:
            return local

        _, blobs = await self._lookup(key, want_value=True)
        found: list[Record] = []
        seen: set[bytes] = set()
        for blob in blobs:
            try:
                record = Record.decode(blob)
                pow_bits = rec.verify(record)
            except RecordError:
                self.stats["rejected"] += 1
                continue  # a lying peer costs us one verification, nothing more
            if record.key != key or record.pub in seen:
                continue
            seen.add(record.pub)
            found.append(record)
            with contextlib.suppress(StoreFull):
                self.store.put(record, blob=blob, pow_bits=pow_bits)  # cache it (§7)
        return found

    async def store_record(self, record: Record) -> int:
        """Publish *record* to the ``k`` closest nodes. Returns how many took it."""
        rec.verify(record)  # never publish something we would reject ourselves
        blob = record.encode()
        targets = await self.find_node(record.key)

        self.store.put(record, blob=blob)
        replies = await asyncio.gather(
            *(self._rpc(node, {"op": proto.Op.STORE, "rec": blob}) for node in targets)
        )
        return sum(1 for r in replies if r is not None and r.get("stored"))

    # --------------------------------------------------------------- maintenance
    async def bootstrap(self, seeds: list[Node]) -> int:
        """Join the network from *seeds*. Returns how many nodes we ended up with.

        Pings the seeds, then looks up our own id, which is what populates the
        buckets near us, and then random ids to fill the far ones.
        """
        for seed in seeds:
            if seed.pub != self.identity.public_key:
                self.table.add(seed)

        alive = await asyncio.gather(*(self.ping(seed) for seed in seeds))
        if not any(alive):
            return len(self.table)

        await self.find_node(self.identity.routing_key)
        for _ in range(3):
            await self.find_node(ids.routing_key(self.identity.public_key[:31] + bytes([_])))
        return len(self.table)

    async def refresh(self, max_age: float = 3600.0) -> int:
        """Re-look-up stale buckets and drop expired records."""
        dropped = self.store.expire()
        for _ in self.table.buckets_needing_refresh(max_age):
            await self.find_node(ids.routing_key(self.identity.public_key))
        return dropped

    async def close(self) -> None:
        for mux in self._muxes:
            mux.close()
        self._muxes.clear()
        await self.transport.close()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<KademliaNode {self.identity.short} {len(self.table)} peers {self.store!r}>"
