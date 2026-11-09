"""DHT message format — §7.

CBOR request/response pairs carried by :class:`~nullcell.net.stream.StreamMux`.
The stream id correlates a reply with its request, so messages carry no
request id of their own.

Every reply includes the responder's public key. The caller already verified
the link against the address it dialled (§4), so this is not how trust is
established — it is how a caller learns the identity of a peer it reached
through a referral, to put in its routing table.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final

import cbor2

from nullcell.dht.routing_table import Node

__all__ = ["MAX_NODES_PER_REPLY", "MAX_VALUES_PER_REPLY", "Op", "ProtocolError", "decode", "encode"]

MAX_NODES_PER_REPLY: Final[int] = 20
MAX_VALUES_PER_REPLY: Final[int] = 8


class Op(StrEnum):
    PING = "ping"
    FIND_NODE = "find_node"
    FIND_VALUE = "find_value"
    STORE = "store"
    #: §8 mechanism 3 and §9's liveness: exchange Bloom filters of known
    #: observations and send back only the deltas, so a new cell inherits what
    #: the network knows instead of recrawling it.
    GOSSIP = "gossip"


class ProtocolError(Exception):
    """Unparseable or structurally invalid message."""


def encode(message: dict[str, Any]) -> bytes:
    return cbor2.dumps(message, canonical=True)


def decode(blob: bytes) -> dict[str, Any]:
    try:
        message = cbor2.loads(blob)
    except Exception as exc:
        raise ProtocolError(f"not valid CBOR: {exc}") from exc
    if not isinstance(message, dict):
        raise ProtocolError(f"message must be a map, got {type(message).__name__}")
    return message


def pack_nodes(nodes: list[Node]) -> list[list[Any]]:
    """Compact node list for the wire: ``[[pub, [endpoint, ...]], ...]``."""
    return [[n.pub, list(n.endpoints)] for n in nodes[:MAX_NODES_PER_REPLY]]


def unpack_nodes(raw: Any) -> list[Node]:
    """Parse a node list, dropping anything malformed rather than failing.

    A peer that sends one bad entry should not invalidate the useful ones, and
    a referral is untrusted input in any case: the pub is only a hint until we
    connect and verify it (§4).
    """
    if not isinstance(raw, list):
        return []
    nodes: list[Node] = []
    for entry in raw[:MAX_NODES_PER_REPLY]:
        if not (isinstance(entry, list) and len(entry) == 2):
            continue
        pub, endpoints = entry
        if not (isinstance(pub, bytes) and len(pub) == 32):
            continue
        if not isinstance(endpoints, list):
            continue
        clean = [e for e in endpoints if isinstance(e, str) and len(e) <= 64][:4]
        nodes.append(Node(pub=pub, endpoints=clean))
    return nodes
