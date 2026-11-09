"""Message streams over RELAY frames — §5.

Gives request/response messaging on top of fixed 512-byte frames. A message is
a 4-byte big-endian length followed by its bytes, split across the 496-byte data
field of consecutive ``RELAY`` frames carrying the same ``stream_id``.

**Why this shape, before circuits exist.** §7 decided that all of a client's DHT
traffic travels over circuits, and circuits arrive in M3. Rather than build the
DHT on something that has to be thrown away, the DHT talks to a
:class:`~nullcell.dht.transport.Transport`, and this mux is what the M2
implementation of that transport uses: the same frames, the same ``stream_id``
multiplexing and the same message framing that a circuit will carry later. In M2
``circ_id`` is 0, meaning "straight down this link, no circuit".

Until M3 wires it through circuits, DHT lookups go out over a direct link and
**reveal this node's address to the peer being asked**. That is a temporary
state of the implementation, not the design.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
from collections.abc import Awaitable, Callable
from typing import Final

from nullcell.core.frames import (
    RELAY_DATA_SIZE,
    Cmd,
    RelayCmd,
    pack_relay_payload,
    parse_relay_payload,
)
from nullcell.net.link import FrameProtocol

__all__ = ["MAX_MESSAGE_BYTES", "NO_CIRCUIT", "StreamError", "StreamMux"]

#: §5 — frames with circ_id 0 are link-level, not carried by any circuit.
NO_CIRCUIT: Final[int] = 0

#: Bounds what one peer can make us buffer before the message is complete.
MAX_MESSAGE_BYTES: Final[int] = 64 * 1024

_LENGTH = struct.Struct("!I")
_RESPONDER_BIT: Final[int] = 0x8000
_STREAM_MAX: Final[int] = 0x7FFF

MessageHandler = Callable[[bytes], Awaitable[bytes | None]]


class StreamError(Exception):
    """Oversized message, timeout, or a peer that went away mid-message."""


class StreamMux:
    """Request/response messaging over one link."""

    __slots__ = ("_link", "_circ_id", "_next", "_pending", "_inbox", "_handler", "_tasks")

    def __init__(self, link: FrameProtocol, circ_id: int = NO_CIRCUIT) -> None:
        self._link = link
        self._circ_id = circ_id
        # Same convention as circ_id (§5): the side that initiated the link
        # allocates ids with the top bit clear, so the two sides never collide.
        self._next = 1
        self._pending: dict[int, asyncio.Future[bytes]] = {}
        self._inbox: dict[int, bytearray] = {}
        self._handler: MessageHandler | None = None
        self._tasks: set[asyncio.Task] = set()
        link.route(circ_id, self._on_frame)

    @property
    def link(self) -> FrameProtocol:
        """The link this mux runs on. A relay needs it to carry circuits (§5)."""
        return self._link

    # ------------------------------------------------------------------ setup
    def set_handler(self, handler: MessageHandler) -> None:
        """Answer incoming messages. Returning None sends no reply."""
        self._handler = handler

    def _alloc_stream_id(self) -> int:
        stream_id = self._next
        self._next = 1 if self._next >= _STREAM_MAX else self._next + 1
        return stream_id if self._link.is_client else (stream_id | _RESPONDER_BIT)

    # --------------------------------------------------------------- sending
    def send(self, stream_id: int, message: bytes) -> None:
        """Send one complete message, coalesced into as few writes as possible."""
        if len(message) > MAX_MESSAGE_BYTES:
            raise StreamError(f"message is {len(message)} bytes, max {MAX_MESSAGE_BYTES}")

        body = _LENGTH.pack(len(message)) + message
        frames = [
            (
                self._circ_id,
                Cmd.RELAY,
                pack_relay_payload(
                    RelayCmd.DATA, body[offset : offset + RELAY_DATA_SIZE], stream_id=stream_id
                ),
            )
            for offset in range(0, len(body), RELAY_DATA_SIZE)
        ]
        self._link.send_frames(frames)

    async def request(self, message: bytes, *, timeout: float = 10.0) -> bytes:
        """Send *message* and await the reply on the same stream."""
        stream_id = self._alloc_stream_id()
        future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        self._pending[stream_id] = future
        try:
            self.send(stream_id, message)
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as exc:
            raise StreamError(f"no reply within {timeout} s") from exc
        finally:
            self._pending.pop(stream_id, None)

    # ------------------------------------------------------------- receiving
    def _on_frame(self, circ_id: int, cmd: int, payload: memoryview) -> None:
        if cmd != Cmd.RELAY:
            return
        header = parse_relay_payload(payload)
        if header.rcmd == RelayCmd.END:
            self._inbox.pop(header.stream_id, None)
            return
        if header.rcmd != RelayCmd.DATA:
            return

        buffer = self._inbox.get(header.stream_id)
        if buffer is None:
            buffer = self._inbox[header.stream_id] = bytearray()
        buffer += header.data[: header.length]

        if len(buffer) < _LENGTH.size:
            return
        (length,) = _LENGTH.unpack_from(buffer, 0)
        if length > MAX_MESSAGE_BYTES:
            # A peer claiming a huge message would otherwise pin our memory.
            del self._inbox[header.stream_id]
            return
        if len(buffer) < _LENGTH.size + length:
            return

        message = bytes(buffer[_LENGTH.size : _LENGTH.size + length])
        del self._inbox[header.stream_id]
        self._deliver(header.stream_id, message)

    def _deliver(self, stream_id: int, message: bytes) -> None:
        future = self._pending.pop(stream_id, None)
        if future is not None:
            if not future.done():
                future.set_result(message)
            return
        if self._handler is None:
            return
        task = asyncio.ensure_future(self._answer(stream_id, message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _answer(self, stream_id: int, message: bytes) -> None:
        assert self._handler is not None
        try:
            reply = await self._handler(message)
        except Exception:
            return  # a failed handler must not take the link down
        if reply is not None:
            with contextlib.suppress(Exception):  # link may be gone
                self.send(stream_id, reply)

    def close(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        self._inbox.clear()
        for task in list(self._tasks):
            task.cancel()
        self._link.unroute(self._circ_id)
