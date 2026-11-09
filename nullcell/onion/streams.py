"""Application streams over one session — §5, §8.

A browser opens several connections to one site at once. If each of them became
its own rendezvous — a six-hop splice, two circuit builds, a Noise handshake —
loading a page would be unusable. So one session carries many streams,
multiplexed by the ``stream_id`` of §5's RELAY subheader, and §16's note that
HTTP/2 would collapse a page's requests onto one circuit is satisfied by the
same mechanism one layer down.

The end-to-end Noise cipher is **per session, not per stream**: it is a single
keystream with a counter, so every message must be processed in the order it
was produced. The circuit delivers in order, which is what makes that safe.
One consequence worth knowing: the streams of a session are not independent
under loss — but there is no loss to speak of, because QUIC already
retransmitted underneath.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Final

from nullcell.core.frames import RelayCmd
from nullcell.onion.circuit import Circuit, CircuitError

__all__ = ["MAX_STREAMS", "MultiplexedSession", "Stream", "StreamClosed"]

#: Per session. A browser uses six or so; this bounds what one peer can make
#: us allocate.
MAX_STREAMS: Final[int] = 64
_FIRST_CLIENT_STREAM: Final[int] = 1
_FIRST_SERVER_STREAM: Final[int] = 0x8000

log = logging.getLogger("nullcell.streams")


class StreamClosed(Exception):
    """The other end closed this stream, or the session went away."""


class Stream:
    """One bidirectional byte stream inside a session."""

    def __init__(self, session: MultiplexedSession, stream_id: int) -> None:
        self.session = session
        self.stream_id = stream_id
        self.closed = False
        self._inbox: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.on_data: Callable[[bytes], None] | None = None

    async def write(self, data: bytes) -> None:
        if self.closed:
            raise StreamClosed(f"stream {self.stream_id} is closed")
        await self.session._send(self.stream_id, data)  # noqa: SLF001

    async def read(self) -> bytes:
        """Next chunk, or ``b""`` once the far end has finished."""
        chunk = await self._inbox.get()
        return b"" if chunk is None else chunk

    def _deliver(self, data: bytes) -> None:
        if self.on_data is not None:
            self.on_data(data)
        else:
            self._inbox.put_nowait(data)

    def _finish(self) -> None:
        self.closed = True
        self._inbox.put_nowait(None)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self.session._end(self.stream_id)  # noqa: SLF001
        self._inbox.put_nowait(None)


class MultiplexedSession:
    """A rendezvous session carrying many application streams (§8)."""

    def __init__(
        self,
        circuit: Circuit,
        noise,
        *,
        is_client: bool,
        max_streams: int = MAX_STREAMS,
    ) -> None:
        self.circuit = circuit
        self._noise = noise
        self.is_client = is_client
        self.max_streams = max_streams
        self.streams: dict[int, Stream] = {}
        self._next = _FIRST_CLIENT_STREAM if is_client else _FIRST_SERVER_STREAM
        self.closed = False
        #: Called with a new Stream when the far end opens one.
        self.on_stream: Callable[[Stream], None] | None = None
        #: Serialises *encrypt-then-send*. The end-to-end Noise cipher has a
        #: sequential nonce, so the order messages are encrypted in must be the
        #: order they reach the wire. Encrypting before awaiting flow-control
        #: credit broke that the moment two streams were in flight at once: the
        #: nonces diverged from the wire order and the far end could not
        #: decrypt anything. It only showed up with parallel requests, which is
        #: exactly what a browser does.
        self._cipher_lock = asyncio.Lock()
        circuit.on_data = self._on_relay

    # ------------------------------------------------------------------ opening
    def _alloc(self) -> int:
        # Same split as circ_id (§5): each side allocates from its own range so
        # the two never collide.
        limit = _FIRST_SERVER_STREAM if self.is_client else 0x10000
        for _ in range(limit):
            stream_id = self._next
            self._next += 1
            if self._next >= limit:
                self._next = _FIRST_CLIENT_STREAM if self.is_client else _FIRST_SERVER_STREAM
            if stream_id not in self.streams:
                return stream_id
        raise CircuitError("no free stream id")  # pragma: no cover

    async def open(self, target: bytes = b"") -> Stream:
        """Open a stream and tell the far end about it."""
        if self.closed:
            raise StreamClosed("session is closed")
        if len(self.streams) >= self.max_streams:
            raise StreamClosed(f"session already has {self.max_streams} streams")
        stream_id = self._alloc()
        stream = self.streams[stream_id] = Stream(self, stream_id)
        await self._relay(RelayCmd.BEGIN, stream_id, target)
        return stream

    # ----------------------------------------------------------------- sending
    async def _send(self, stream_id: int, data: bytes) -> None:
        from nullcell.onion.service import MAX_E2E_PAYLOAD

        for offset in range(0, max(len(data), 1), MAX_E2E_PAYLOAD):
            await self._relay(RelayCmd.DATA, stream_id, data[offset : offset + MAX_E2E_PAYLOAD])

    async def _end(self, stream_id: int) -> None:
        if not self.closed:
            # Telling the far end is a courtesy; the link may already be gone.
            with contextlib.suppress(Exception):
                await self._relay(RelayCmd.END, stream_id, b"")
        self.streams.pop(stream_id, None)

    async def _relay(self, rcmd: RelayCmd, stream_id: int, payload: bytes) -> None:
        """Encrypt end to end, then hand it to the circuit.

        Credit is acquired **before** the cipher lock on purpose. Taking the
        lock first would mean a stream waiting for a SENDME holds the cipher
        and stalls every other stream on the session; this way the lock is held
        only for the encryption and the synchronous handover, with no await
        inside it, so neither reordering nor head-of-line blocking can happen.
        """
        if not stream_id:
            raise CircuitError("application streams need a non-zero stream id")
        await self.circuit.flow.acquire(stream_id)
        async with self._cipher_lock:
            self.circuit.send_relay(
                RelayCmd.DATA,
                bytes([int(rcmd)]) + self._noise.encrypt(payload),
                stream_id=stream_id,
            )
            self.circuit.bytes_sent += len(payload)
            if self.circuit.first_use_at is None:
                import time

                self.circuit.first_use_at = time.monotonic()
            self.circuit.streams.add(stream_id)

    # --------------------------------------------------------------- receiving
    def _on_relay(self, stream_id: int, rcmd: int, payload: bytes) -> None:
        if rcmd != RelayCmd.DATA or not payload:
            return
        try:
            inner, plaintext = payload[0], self._noise.decrypt(bytes(payload[1:]))
        except Exception as exc:
            log.debug("end-to-end decryption failed on stream %d: %s", stream_id, exc)
            return

        if inner == RelayCmd.BEGIN:
            self._accept(stream_id)
            return
        stream = self.streams.get(stream_id)
        if stream is None:
            return
        if inner == RelayCmd.END:
            stream._finish()  # noqa: SLF001
            self.streams.pop(stream_id, None)
            return
        if inner == RelayCmd.DATA:
            stream._deliver(plaintext)  # noqa: SLF001

    def _accept(self, stream_id: int) -> None:
        if stream_id in self.streams or len(self.streams) >= self.max_streams:
            return
        stream = self.streams[stream_id] = Stream(self, stream_id)
        if self.on_stream is not None:
            self.on_stream(stream)

    # ---------------------------------------------------------------- teardown
    def close(self) -> None:
        self.closed = True
        for stream in list(self.streams.values()):
            stream._finish()  # noqa: SLF001
        self.streams.clear()
