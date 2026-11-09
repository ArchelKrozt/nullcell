"""SENDME flow control — §5.

QUIC already does congestion control per *link*. This is a second layer, per
*circuit* and per *stream*, and §5 asks for it for a reason QUIC cannot solve:
one link carries frames from many circuits multiplexed, so without it a single
slow circuit backs up the link and stalls every other circuit sharing it.

Windows from §5: circuit 1000 frames with increments of 100, stream 500 with
increments of 50. The sender spends credit per DATA frame and waits when it runs
out; the receiver returns a ``SENDME`` every increment it delivers.

Only ``DATA`` is metered. Control messages — ``EXTEND``, ``SENDME`` itself,
teardown — are exempt, or a circuit with an exhausted window could never be
told to close.
"""

from __future__ import annotations

import asyncio
from typing import Final

__all__ = [
    "CIRCUIT_INCREMENT",
    "CIRCUIT_WINDOW",
    "STREAM_INCREMENT",
    "STREAM_WINDOW",
    "DeliveryCounter",
    "FlowControl",
    "FlowError",
    "SendWindow",
]

CIRCUIT_WINDOW: Final[int] = 1000
CIRCUIT_INCREMENT: Final[int] = 100
STREAM_WINDOW: Final[int] = 500
STREAM_INCREMENT: Final[int] = 50


class FlowError(Exception):
    """A peer sent more credit than it could possibly be owed."""


class SendWindow:
    """Credit the sender spends, and a peer returns with ``SENDME``."""

    __slots__ = ("_available", "_initial", "_increment", "_ready")

    def __init__(self, initial: int, increment: int) -> None:
        self._initial = initial
        self._increment = increment
        self._available = initial
        self._ready = asyncio.Event()
        self._ready.set()

    @property
    def available(self) -> int:
        return self._available

    @property
    def blocked(self) -> bool:
        return self._available <= 0

    def try_consume(self) -> bool:
        """Spend one frame of credit. False if there is none."""
        if self._available <= 0:
            return False
        self._available -= 1
        if self._available <= 0:
            self._ready.clear()
        return True

    async def consume(self) -> None:
        """Spend one frame of credit, waiting for a ``SENDME`` if exhausted."""
        while not self.try_consume():
            await self._ready.wait()

    def credit(self, amount: int | None = None) -> None:
        """Apply a received ``SENDME``.

        A peer that credits beyond the initial window is either broken or
        trying to make us flood the circuit, so it is refused rather than
        clamped silently.
        """
        amount = self._increment if amount is None else amount
        if self._available + amount > self._initial:
            raise FlowError(
                f"credit of {amount} would raise the window to "
                f"{self._available + amount}, above the initial {self._initial}"
            )
        self._available += amount
        if self._available > 0:
            self._ready.set()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SendWindow {self._available}/{self._initial}>"


class DeliveryCounter:
    """Receiver side: counts deliveries and says when a ``SENDME`` is due."""

    __slots__ = ("_increment", "_since")

    def __init__(self, increment: int) -> None:
        self._increment = increment
        self._since = 0

    def record(self) -> bool:
        """Note one delivered frame. True when the peer should be credited."""
        self._since += 1
        if self._since >= self._increment:
            self._since = 0
            return True
        return False

    @property
    def pending(self) -> int:
        return self._since


class FlowControl:
    """One circuit's windows, in both directions."""

    __slots__ = ("circuit_send", "circuit_recv", "_stream_send", "_stream_recv")

    def __init__(self) -> None:
        self.circuit_send = SendWindow(CIRCUIT_WINDOW, CIRCUIT_INCREMENT)
        self.circuit_recv = DeliveryCounter(CIRCUIT_INCREMENT)
        self._stream_send: dict[int, SendWindow] = {}
        self._stream_recv: dict[int, DeliveryCounter] = {}

    def stream_send(self, stream_id: int) -> SendWindow:
        window = self._stream_send.get(stream_id)
        if window is None:
            window = self._stream_send[stream_id] = SendWindow(STREAM_WINDOW, STREAM_INCREMENT)
        return window

    def stream_recv(self, stream_id: int) -> DeliveryCounter:
        counter = self._stream_recv.get(stream_id)
        if counter is None:
            counter = self._stream_recv[stream_id] = DeliveryCounter(STREAM_INCREMENT)
        return counter

    def forget(self, stream_id: int) -> None:
        self._stream_send.pop(stream_id, None)
        self._stream_recv.pop(stream_id, None)

    @property
    def streams(self) -> int:
        return len(self._stream_send)

    async def acquire(self, stream_id: int) -> None:
        """Spend circuit and stream credit for one DATA frame."""
        await self.circuit_send.consume()
        if stream_id:
            await self.stream_send(stream_id).consume()

    def on_delivered(self, stream_id: int) -> tuple[bool, bool]:
        """Note a delivered DATA frame. Returns ``(circuit_due, stream_due)``."""
        circuit_due = self.circuit_recv.record()
        stream_due = self.stream_recv(stream_id).record() if stream_id else False
        return circuit_due, stream_due
