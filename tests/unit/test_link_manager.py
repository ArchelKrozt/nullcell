"""A cached link that QUIC already closed must not be handed out again.

Found on the first public relay: it joined the network, and a few minutes
later its routing table was empty. QUIC closes a link after 60 s of silence,
the cache kept returning it, every request failed, and three failures drop a
peer from the table.
"""

from __future__ import annotations

import asyncio
import contextlib

from nullcell.net import link_manager as lm

PUB = b"\x01" * 32


class FakeLink:
    def __init__(self) -> None:
        self.closed: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    def route(self, circ_id, handler) -> None:
        pass

    def unroute(self, circ_id) -> None:
        pass


def _patch_open(monkeypatch, opened: list[FakeLink]) -> None:
    @contextlib.asynccontextmanager
    async def fake_open_link(nid, host, port):
        link = FakeLink()
        opened.append(link)
        yield link

    monkeypatch.setattr(lm, "open_link", fake_open_link)
    monkeypatch.setattr(lm, "peer_address", lambda link: None)


async def test_live_link_is_reused(monkeypatch):
    opened: list[FakeLink] = []
    _patch_open(monkeypatch, opened)
    links = lm.LinkManager()

    first = await links.connect(PUB, ["198.51.100.7:4433"])
    second = await links.connect(PUB, ["198.51.100.7:4433"])

    assert first is second
    assert len(opened) == 1


async def test_closed_link_is_replaced(monkeypatch):
    opened: list[FakeLink] = []
    _patch_open(monkeypatch, opened)
    links = lm.LinkManager()

    first = await links.connect(PUB, ["198.51.100.7:4433"])
    opened[0].closed.set_result(None)  # idle timeout: ConnectionTerminated

    second = await links.connect(PUB, ["198.51.100.7:4433"])

    assert second is not first
    assert len(opened) == 2
    assert links.get(PUB) is second
    assert len(links) == 1
