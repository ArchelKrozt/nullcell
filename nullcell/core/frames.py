"""Fixed-size protocol frames — see NULLCELL-ARQUITECTURA.md §5.

Link-level frame, always exactly 512 bytes on the wire::

    [circ_id u32][cmd u8][payload 507 B]

``RELAY`` frames carry an 11-byte subheader inside the onion layers, leaving 496
bytes of application data::

    [rcmd u8][recognized u16][stream_id u16][digest u32][length u16][data 496 B]

Three things in here are load-bearing rather than decorative:

* ``stream_id`` multiplexes application streams inside one circuit. Without it a
  20-request page would need 20 six-hop circuits.
* ``recognized`` is the cheap "this is not addressed to me" test an intermediate
  relay uses after peeling its layer.
* ``digest`` holds 4 bytes of a running digest of the stream with one specific
  hop, which is what detects the tagging attack (§5). This module packs and
  parses the field; computing the running digest belongs to the circuit layer in
  M3, so for now callers pass it explicitly.

**Hot path.** In Python the per-frame cost, not the cipher, is the binding
constraint on throughput (§16). The fast API here therefore never allocates:
:class:`FrameBuffer` packs frames into one preallocated ``bytearray`` so a whole
batch leaves in a single socket write (§16, tactic 1), and parsing works over
``memoryview`` slices rather than copying bytes.
"""

from __future__ import annotations

import secrets
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Final

__all__ = [
    "CIRC_ID_RESPONDER_BIT",
    "FRAME_SIZE",
    "HEADER_SIZE",
    "PAYLOAD_SIZE",
    "RELAY_DATA_SIZE",
    "RELAY_HEADER_SIZE",
    "Cmd",
    "FrameBuffer",
    "FrameError",
    "RelayCmd",
    "RelayHeader",
    "iter_frames",
    "new_circ_id",
    "pack",
    "pack_relay_payload",
    "parse_header",
    "parse_relay_payload",
]

FRAME_SIZE: Final[int] = 512
HEADER_SIZE: Final[int] = 5  # circ_id u32 + cmd u8
PAYLOAD_SIZE: Final[int] = FRAME_SIZE - HEADER_SIZE  # 507
RELAY_HEADER_SIZE: Final[int] = 11
RELAY_DATA_SIZE: Final[int] = PAYLOAD_SIZE - RELAY_HEADER_SIZE  # 496

#: §5 — the circuit id is local to a link, not global to the network. The side
#: that *initiated* the QUIC link allocates ids with the top bit clear, the side
#: that accepted it sets the bit. Without this, ids collide the moment a relay
#: opens a circuit of its own over a link that already existed.
CIRC_ID_RESPONDER_BIT: Final[int] = 0x8000_0000
_CIRC_ID_MAX: Final[int] = 0x7FFF_FFFF

_HEADER = struct.Struct("!IB")
_RELAY_HEADER = struct.Struct("!BHHIH")
_ZERO_PAYLOAD: Final[bytes] = bytes(PAYLOAD_SIZE)


class Cmd(IntEnum):
    """Link-level frame commands (§5).

    ``EXTEND``/``EXTENDED`` are deliberately *not* here: they are relay commands,
    because they must travel encrypted end-to-end to the hop doing the extending.
    As link commands the first hop could read and rewrite where you extend to,
    and so choose your path for you.
    """

    VERSIONS = 1
    CREATE = 2
    CREATED = 3
    RELAY = 4
    DESTROY = 5
    PADDING = 6
    PING = 7
    PONG = 8


class RelayCmd(IntEnum):
    """Commands carried inside a ``RELAY`` frame."""

    DATA = 1
    BEGIN = 2
    CONNECTED = 3
    END = 4
    SENDME = 5
    EXTEND = 6
    EXTENDED = 7
    TRUNCATED = 8
    RESOLVE = 9


class FrameError(ValueError):
    """A frame that cannot be packed or parsed."""


def new_circ_id(*, link_initiator: bool) -> int:
    """Allocate a random circuit id honouring the §5 top-bit convention."""
    circ_id = secrets.randbelow(_CIRC_ID_MAX) + 1  # never 0: reserved
    return circ_id if link_initiator else (circ_id | CIRC_ID_RESPONDER_BIT)


def pack(circ_id: int, cmd: Cmd | int, payload: bytes = b"") -> bytes:
    """Pack one frame, zero-padded to exactly :data:`FRAME_SIZE`.

    Convenience API for control frames. The hot path uses :class:`FrameBuffer`.
    """
    if len(payload) > PAYLOAD_SIZE:
        raise FrameError(f"payload is {len(payload)} bytes, max {PAYLOAD_SIZE}")
    if not 0 <= circ_id <= 0xFFFF_FFFF:
        raise FrameError(f"circ_id out of range: {circ_id}")
    frame = bytearray(FRAME_SIZE)
    _HEADER.pack_into(frame, 0, circ_id, int(cmd))
    frame[HEADER_SIZE : HEADER_SIZE + len(payload)] = payload
    return bytes(frame)


def parse_header(frame: bytes | bytearray | memoryview) -> tuple[int, int]:
    """Return ``(circ_id, cmd)``. Does not copy the payload."""
    if len(frame) < HEADER_SIZE:
        raise FrameError(f"frame is {len(frame)} bytes, need at least {HEADER_SIZE}")
    return _HEADER.unpack_from(frame, 0)  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class RelayHeader:
    """Parsed ``RELAY`` subheader. ``data`` is a view, not a copy."""

    rcmd: int
    recognized: int
    stream_id: int
    digest: int
    length: int
    data: memoryview

    @property
    def is_for_me(self) -> bool:
        """``recognized == 0`` means this frame is addressed to this hop (§5)."""
        return self.recognized == 0

    @property
    def payload(self) -> bytes:
        """The useful bytes, padding stripped. Copies — callers that care use ``data``."""
        return bytes(self.data[: self.length])


def pack_relay_payload(
    rcmd: RelayCmd | int,
    data: bytes = b"",
    *,
    stream_id: int = 0,
    digest: int = 0,
    recognized: int = 0,
) -> bytes:
    """Build the 507-byte payload of a ``RELAY`` frame."""
    if len(data) > RELAY_DATA_SIZE:
        raise FrameError(f"relay data is {len(data)} bytes, max {RELAY_DATA_SIZE}")
    payload = bytearray(PAYLOAD_SIZE)
    _RELAY_HEADER.pack_into(payload, 0, int(rcmd), recognized, stream_id, digest, len(data))
    payload[RELAY_HEADER_SIZE : RELAY_HEADER_SIZE + len(data)] = data
    return bytes(payload)


def parse_relay_payload(payload: bytes | bytearray | memoryview) -> RelayHeader:
    """Parse a ``RELAY`` payload without copying its data."""
    if len(payload) < RELAY_HEADER_SIZE:
        raise FrameError(f"relay payload is {len(payload)} bytes, need {RELAY_HEADER_SIZE}")
    rcmd, recognized, stream_id, digest, length = _RELAY_HEADER.unpack_from(payload, 0)
    if length > RELAY_DATA_SIZE:
        raise FrameError(f"relay length field says {length}, max {RELAY_DATA_SIZE}")
    view = memoryview(payload)[RELAY_HEADER_SIZE:]
    return RelayHeader(rcmd, recognized, stream_id, digest, length, view)


def iter_frames(buffer: bytes | bytearray | memoryview):
    """Yield ``memoryview`` slices for each whole frame in *buffer*.

    A QUIC stream is a byte stream, so frames arrive split and merged
    arbitrarily; the link layer buffers and calls this.
    """
    view = memoryview(buffer)
    for offset in range(0, len(view) - FRAME_SIZE + 1, FRAME_SIZE):
        yield view[offset : offset + FRAME_SIZE]


class FrameBuffer:
    """Preallocated batch of frames — the zero-allocation hot path (§16).

    Packs up to *capacity* frames into one ``bytearray`` so the whole batch can
    leave in a single write, which collapses syscall and asyncio overhead by
    roughly the batch size and changes nothing an observer can see: the wire
    still carries a stream of uniform 512-byte units.
    """

    __slots__ = ("_buf", "_view", "_capacity", "_count")

    def __init__(self, capacity: int = 64) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._buf = bytearray(capacity * FRAME_SIZE)
        self._view = memoryview(self._buf)
        self._count = 0

    def __len__(self) -> int:
        return self._count

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def full(self) -> bool:
        return self._count >= self._capacity

    def reset(self) -> None:
        """Mark the buffer empty. Does not reallocate or clear."""
        self._count = 0

    def add(self, circ_id: int, cmd: Cmd | int, payload: bytes = b"") -> None:
        """Append one frame. Raises if the batch is full."""
        if self._count >= self._capacity:
            raise FrameError(f"batch is full ({self._capacity} frames)")
        n = len(payload)
        if n > PAYLOAD_SIZE:
            raise FrameError(f"payload is {n} bytes, max {PAYLOAD_SIZE}")

        base = self._count * FRAME_SIZE
        _HEADER.pack_into(self._buf, base, circ_id, int(cmd))
        start = base + HEADER_SIZE
        self._buf[start : start + n] = payload
        # Zero only the tail that the previous occupant of this slot may have
        # left behind: padding must not leak bytes from an unrelated frame.
        if n < PAYLOAD_SIZE:
            self._buf[start + n : base + FRAME_SIZE] = _ZERO_PAYLOAD[: PAYLOAD_SIZE - n]
        self._count += 1

    def view(self) -> memoryview:
        """A view over the packed frames. Valid until the next ``reset``/``add``."""
        return self._view[: self._count * FRAME_SIZE]
