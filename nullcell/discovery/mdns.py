"""LAN discovery by multicast beacon — §7.

§7 calls this step "mDNS". This is **not** standard mDNS/DNS-SD: it is a plain
UDP multicast beacon on a Nullcell-specific group and port, carrying a signed
announcement. Said plainly because the difference matters — it does not appear
in `dns-sd` or Bonjour browsers, and it needs no zeroconf dependency. Proper
DNS-SD can be layered on later without changing anything else.

The beacon is **off by default**. It announces, on the local network, that this
machine runs a Nullcell node — useful for a lab or a household, and an
unnecessary disclosure anywhere else.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import struct
import time
from typing import Final

import cbor2

from nullcell.core import ids
from nullcell.core.identity import Identity
from nullcell.dht.routing_table import Node

__all__ = ["GROUP", "PORT", "Beacon"]

GROUP: Final[str] = "239.255.77.33"  # administratively scoped, link-local use
PORT: Final[int] = 47733
INTERVAL: Final[float] = 30.0
MAX_DATAGRAM: Final[int] = 512
_MAGIC: Final[bytes] = b"NCELL1"
_MAX_SKEW: Final[int] = 60


class Beacon:
    """Announces this node on the LAN and collects announcements from others."""

    def __init__(
        self,
        identity: Identity,
        endpoints: list[str],
        *,
        on_peer: object = None,
        interval: float = INTERVAL,
    ) -> None:
        self.identity = identity
        self.endpoints = endpoints
        self.interval = interval
        self.on_peer = on_peer
        self.peers: dict[bytes, Node] = {}
        self._sock: socket.socket | None = None
        self._task: asyncio.Task | None = None
        self.log = logging.getLogger("nullcell.mdns")

    # ------------------------------------------------------------------ wire
    def _announcement(self) -> bytes:
        payload = cbor2.dumps(
            {"pub": self.identity.public_key, "ep": self.endpoints[:2], "ts": int(time.time())},
            canonical=True,
        )
        return _MAGIC + self.identity.sign(payload) + payload

    @staticmethod
    def _parse(datagram: bytes) -> Node | None:
        """Parse and authenticate a beacon. Returns None for anything off."""
        if not datagram.startswith(_MAGIC) or len(datagram) > MAX_DATAGRAM:
            return None
        body = datagram[len(_MAGIC) :]
        if len(body) < 64:
            return None
        signature, payload = body[:64], body[64:]
        try:
            data = cbor2.loads(payload)
            pub = data["pub"]
            endpoints = data["ep"]
            ts = int(data["ts"])
        except Exception:
            return None
        if not (isinstance(pub, bytes) and len(pub) == 32 and isinstance(endpoints, list)):
            return None
        if abs(ts - int(time.time())) > _MAX_SKEW:
            return None  # a replayed beacon is not a live peer
        if not Identity.verify(pub, payload, signature):
            return None
        clean = [e for e in endpoints if isinstance(e, str)][:2]
        return Node(pub=pub, endpoints=clean) if clean else None

    # --------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with contextlib.suppress(AttributeError, OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("", PORT))
        sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_ADD_MEMBERSHIP,
            struct.pack("4sl", socket.inet_aton(GROUP), socket.INADDR_ANY),
        )
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)  # never leaves the LAN
        sock.setblocking(False)
        self._sock = sock
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        assert self._sock is not None
        last = 0.0
        while True:
            now = loop.time()
            if now - last >= self.interval:
                last = now
                with contextlib.suppress(OSError):
                    self._sock.sendto(self._announcement(), (GROUP, PORT))
            try:
                datagram, _ = await asyncio.wait_for(
                    loop.sock_recvfrom(self._sock, MAX_DATAGRAM), timeout=1.0
                )
            except (TimeoutError, OSError):
                continue
            node = self._parse(datagram)
            if node is None or node.pub == self.identity.public_key:
                continue
            fresh = node.pub not in self.peers
            self.peers[node.pub] = node
            if fresh:
                self.log.info("LAN: %s en %s", ids.short(node.nid), node.endpoints)
                if callable(self.on_peer):
                    self.on_peer(node)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
