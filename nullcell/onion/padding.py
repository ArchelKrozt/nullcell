"""Relleno — §5's two padding machines, and what they are honestly for.

§10 counts padding as a mitigation against traffic analysis, and §5 names two
machines. Neither existed until now, so that mitigation was documentation.

**What this buys, stated plainly.** §10 already says end-to-end traffic
correlation is **not solved**, in Nullcell or in Tor, and padding does not
solve it. A global adversary watching every link is outside the threat model
and stays outside it. What padding raises is the cost of the *passive, local*
observation that is actually common: an ISP that keeps NetFlow records, or
someone who can see when your link to your guard goes quiet.

Two machines, each against one of those:

* **Link padding.** An idle link disappears from flow records and reappears
  the moment you use it, which draws a picture of when you are active. Sending
  a frame now and then keeps the link present.
* **Construction padding.** Building a circuit has a recognisable temporal
  signature — three request/response pairs in quick succession, each a little
  slower than the last. A few extra frames blur its shape.

Padding frames are **indistinguishable on the wire**: 512 bytes, the same
fixed size as everything else (§5), which is the entire reason the frame size
is fixed. A relay drops them without forwarding.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from typing import Final

from nullcell.core.frames import Cmd

__all__ = [
    "CONSTRUCTION_BURST",
    "LINK_IDLE",
    "ConstructionPadding",
    "LinkPadding",
]

#: Send a padding frame after this long without traffic. Shorter than the
#: 10-second idle timeout a typical NetFlow exporter uses, so a quiet link
#: never becomes a gap in the record.
LINK_IDLE: Final[float] = 5.0
_LINK_JITTER: Final[float] = 2.0
#: Extra frames sprinkled around a circuit build.
CONSTRUCTION_BURST: Final[int] = 4

log = logging.getLogger("nullcell.padding")


class LinkPadding:
    """Keeps a link present in flow records while it is idle (§5).

    Driven by the link's own frame counters rather than by a hook on sending,
    so it cannot be forgotten at a call site and cannot double-count.
    """

    def __init__(
        self,
        link,
        *,
        idle: float = LINK_IDLE,
        jitter: float = _LINK_JITTER,
        rng: random.Random | None = None,
    ) -> None:
        self.link = link
        self.idle = idle
        self.jitter = jitter
        self.rng = rng or random.Random()
        self.sent = 0
        self._task: asyncio.Task | None = None
        self._last_total = -1
        self._last_change = time.monotonic()

    @property
    def _total(self) -> int:
        return self.link.frames_in + self.link.frames_out

    def _delay(self) -> float:
        """Jittered, because a frame exactly every five seconds is a signature.

        The floor is a fraction of ``idle``, not an absolute. An absolute floor
        of half a second silently overrode anything smaller the caller asked
        for — which is a bug dressed as a safety limit, and it made the
        parameter untestable.
        """
        jitter = self.rng.uniform(-self.jitter, self.jitter)
        return max(self.idle * 0.5, self.idle + jitter)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._delay())
            total = self._total
            if total != self._last_total:
                self._last_total = total
                self._last_change = time.monotonic()
                continue
            if time.monotonic() - self._last_change < self.idle:
                continue
            try:
                self.link.send_frame(0, Cmd.PADDING, b"")
            except Exception as exc:
                log.debug("link padding stopped: %s", exc)
                return
            self.sent += 1
            self._last_total = self._total
            self._last_change = time.monotonic()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None


class ConstructionPadding:
    """Blurs the temporal shape of building a circuit (§5).

    Three request/response pairs in quick succession, each slower than the
    last, is a recognisable signature even when every byte is encrypted. A
    handful of extra frames at random offsets makes the shape less legible
    without changing anything an endpoint sees.
    """

    def __init__(
        self,
        link,
        *,
        burst: int = CONSTRUCTION_BURST,
        rng: random.Random | None = None,
    ) -> None:
        self.link = link
        self.burst = burst
        self.rng = rng or random.Random()
        self.sent = 0

    async def around(self, build) -> object:
        """Run *build* with padding scattered through it.

        The padding task is cancelled when the build finishes, so a slow build
        gets more cover and a fast one is not delayed.
        """
        noise = asyncio.create_task(self._scatter())
        try:
            return await build
        finally:
            noise.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await noise

    async def _scatter(self) -> None:
        for _ in range(self.burst):
            await asyncio.sleep(self.rng.uniform(0.01, 0.12))
            try:
                self.link.send_frame(0, Cmd.PADDING, b"")
            except Exception:
                return
            self.sent += 1
