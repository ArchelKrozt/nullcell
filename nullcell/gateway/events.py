"""Internal event bus for the HUD — §9, §16.

Every panel of the HUD reads a real source of protocol data (§9), and the live
console reads this. Two limits come straight from §16, because a busy relay
generates thousands of events a second and pushing them all kills the browser:

* a **ring buffer** bounded at 500 entries, so a long session cannot grow
  without limit,
* and **aggregation**: subscribers are pushed batches on a timer rather than
  one message per event, with repeated events of the same kind collapsed into a
  count.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from typing import Any, Final

__all__ = ["BUFFER_SIZE", "PUSH_HZ", "Event", "EventBus", "Level"]

#: §16: without a cap the DOM grows unbounded over a long session.
BUFFER_SIZE: Final[int] = 500
#: §16: batched pushes rather than one message per event.
PUSH_HZ: Final[float] = 10.0
_MAX_QUEUE: Final[int] = 64


class Level(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"


@dataclass(slots=True)
class Event:
    kind: str
    text: str
    level: Level = Level.INFO
    ts: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)
    count: int = 1

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["level"] = str(self.level)
        return out


class EventBus:
    """Fan-out with a bounded history and server-side aggregation."""

    def __init__(self, *, buffer_size: int = BUFFER_SIZE) -> None:
        self._history: deque[Event] = deque(maxlen=buffer_size)
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self.dropped = 0

    # ------------------------------------------------------------- publishing
    def emit(self, kind: str, text: str, *, level: Level = Level.INFO, **data: Any) -> Event:
        event = Event(kind=kind, text=text, level=level, data=data)
        self._history.append(event)
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A subscriber that cannot keep up loses events rather than
                # stalling the protocol that produced them.
                self.dropped += 1
        return event

    # ------------------------------------------------------------ subscribing
    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(queue)

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)

    # --------------------------------------------------------------- history
    def recent(self, limit: int = BUFFER_SIZE) -> list[dict[str, Any]]:
        """History, folded the same way the live push folds it (§16).

        Same treatment both ways, so a line does not appear three times in the
        backlog and once with a count after the socket connects.
        """
        return [e.as_dict() for e in collapse(list(self._history)[-limit:])]

    def __len__(self) -> int:
        return len(self._history)


def collapse(events: list[Event]) -> list[Event]:
    """Fold consecutive events of the same kind into one with a count (§16).

    A relay forwarding at full tilt emits the same line thousands of times; the
    console wants "forwarded x1843", not 1843 rows.

    Returns **new** events and never touches the ones passed in. Folding in
    place looked fine and was not: the events handed to a subscriber are the
    same objects the bus keeps in its history, so mutating them corrupted the
    history for every other reader, and folding the same run twice
    double-counted it.
    """
    folded: list[Event] = []
    for event in events:
        if folded and folded[-1].kind == event.kind and folded[-1].text == event.text:
            last = folded[-1]
            folded[-1] = replace(last, count=last.count + event.count, ts=event.ts)
            continue
        folded.append(event)
    return folded


async def drain(queue: asyncio.Queue[Event], *, period: float = 1.0 / PUSH_HZ) -> list[Event]:
    """Wait for at least one event, then take everything that piled up."""
    first = await queue.get()
    await asyncio.sleep(period)
    batch = [first]
    while True:
        try:
            batch.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return collapse(batch)
