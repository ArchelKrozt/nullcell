"""Frame transport over a QUIC stream — §4 and §16.

One QUIC stream per link carries 512-byte frames from many circuits,
multiplexed. Two details from the architecture live here:

* **Write coalescing** (§16, tactic 1): frames are packed into one preallocated
  buffer and leave in a single write. The wire still carries uniform 512-byte
  units, so nothing an observer sees changes.
* **Reassembly**: a QUIC stream is a byte stream, so frames arrive split and
  merged arbitrarily. Incoming bytes are buffered and sliced at frame
  boundaries without copying the payloads.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Final

from aioquic.asyncio import QuicConnectionProtocol
from aioquic.quic.events import (
    ConnectionTerminated,
    HandshakeCompleted,
    QuicEvent,
    StreamDataReceived,
)

from nullcell.core.frames import FRAME_SIZE, HEADER_SIZE, Cmd, FrameBuffer, parse_header

__all__ = ["DEFAULT_BATCH", "PROTOCOL_VERSIONS", "FrameProtocol"]

#: §5 — advertised in the first frame of every link. Negotiating from day one is
#: what makes it possible to revisit the frame size with M1 benchmark data.
PROTOCOL_VERSIONS: Final[tuple[int, ...]] = (1,)

DEFAULT_BATCH: Final[int] = 64

FrameHandler = Callable[[int, int, memoryview], Awaitable[None] | None]


class FrameProtocol(QuicConnectionProtocol):
    """Carries Nullcell frames over one QUIC connection.

    Subclasses or callers set :attr:`on_frame`; the default drops frames.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stream_id: int | None = None
        self._inbox = bytearray()
        self._out = FrameBuffer(DEFAULT_BATCH)
        self.on_frame: FrameHandler | None = None
        self.handshake_done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.closed: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.peer_versions: tuple[int, ...] = ()
        self.version: int | None = None
        self._versions_sent = False
        self.frames_in = 0
        self.frames_out = 0

    @property
    def is_client(self) -> bool:
        return bool(self._quic.configuration.is_client)

    # ----------------------------------------------------------- lifecycle
    def quic_event_received(self, event: QuicEvent) -> None:
        if isinstance(event, HandshakeCompleted):
            if not self.handshake_done.done():
                self.handshake_done.set_result(None)
        elif isinstance(event, StreamDataReceived):
            if self._stream_id is None:
                self._stream_id = event.stream_id
            self._feed(event.data)
        elif isinstance(event, ConnectionTerminated):
            for fut in (self.handshake_done, self.closed):
                if not fut.done():
                    fut.set_result(None)

    @property
    def peer_certificate(self):
        """The peer's X.509 certificate, or ``None`` before the handshake.

        aioquic keeps this on a private TLS attribute; it is read in exactly one
        place so a future upstream rename is a one-line fix, not a hunt.
        """
        tls = getattr(self._quic, "tls", None)
        return getattr(tls, "_peer_certificate", None) if tls is not None else None

    # ------------------------------------------------------------- sending
    def _ensure_stream(self) -> int:
        if self._stream_id is None:
            self._stream_id = self._quic.get_next_available_stream_id()
        return self._stream_id

    def send_frames(self, frames: list[tuple[int, Cmd | int, bytes]]) -> None:
        """Send frames coalesced into as few writes as the batch size allows."""
        stream_id = self._ensure_stream()
        buf = self._out
        buf.reset()
        for circ_id, cmd, payload in frames:
            if buf.full:
                self._quic.send_stream_data(stream_id, bytes(buf.view()))
                buf.reset()
            buf.add(circ_id, cmd, payload)
        if len(buf):
            self._quic.send_stream_data(stream_id, bytes(buf.view()))
        self.frames_out += len(frames)
        self.transmit()

    def send_frame(self, circ_id: int, cmd: Cmd | int, payload: bytes = b"") -> None:
        self.send_frames([(circ_id, cmd, payload)])

    def send_versions(self) -> None:
        """First frame of every link (§5). Idempotent."""
        if self._versions_sent:
            return
        self._versions_sent = True
        self.send_frame(0, Cmd.VERSIONS, bytes(PROTOCOL_VERSIONS))

    def _handle_versions(self, payload: memoryview) -> None:
        """Pick the highest version both sides speak, or tear the link down (§5).

        The side that accepted the link answers on the same stream; it must not
        greet first, or both ends would open a stream of their own and then
        disagree about which one carries the link.
        """
        self.peer_versions = tuple(b for b in payload[:16] if b)
        if not self.is_client:
            self.send_versions()

        common = set(self.peer_versions) & set(PROTOCOL_VERSIONS)
        if not common:
            self.version = None
            self._quic.close(error_code=0x1, reason_phrase="no common protocol version")
            self.transmit()
            return
        self.version = max(common)

    # ------------------------------------------------------------ receiving
    def _feed(self, data: bytes) -> None:
        inbox = self._inbox
        inbox += data
        whole = len(inbox) // FRAME_SIZE
        if not whole:
            return

        cut = whole * FRAME_SIZE
        # One copy per received batch, into an immutable block, then the inbox is
        # trimmed before anything looks at it. Two reasons, both bugs we hit:
        # a bytearray cannot be resized while a memoryview of it is alive, and a
        # view handed to a handler must not alias a buffer we are about to
        # overwrite. The zero-allocation rule of §16 covers the send and forward
        # paths, where cost is per *frame*; here it is per batch.
        block = bytes(inbox[:cut])
        del inbox[:cut]

        view = memoryview(block)
        for offset in range(0, cut, FRAME_SIZE):
            frame = view[offset : offset + FRAME_SIZE]
            circ_id, cmd = parse_header(frame)
            self.frames_in += 1
            self._dispatch(circ_id, cmd, frame[HEADER_SIZE:])

    def _dispatch(self, circ_id: int, cmd: int, payload: memoryview) -> None:
        if cmd == Cmd.VERSIONS:
            self._handle_versions(payload)
            return
        if cmd == Cmd.PING:
            # Echo the body so the initiator can match request to reply and
            # measure RTT per hop (§9 reads this as the circuit RTT).
            self.send_frame(circ_id, Cmd.PONG, bytes(payload[:8]))
            return
        handler = self.on_frame
        if handler is not None:
            result = handler(circ_id, cmd, payload)
            if result is not None:
                asyncio.ensure_future(result)
