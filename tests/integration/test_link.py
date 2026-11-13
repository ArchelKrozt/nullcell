"""Two real cells over QUIC — the M1 success test.

"2 nodos intercambian marcos, cliente anónimo" plus the §4 property that an
address cannot be impersonated.
"""

import asyncio
import contextlib
import socket

import pytest

from nullcell.core.frames import Cmd, pack_relay_payload, parse_relay_payload
from nullcell.core.identity import Identity
from nullcell.net.cert import CertError
from nullcell.net.listener import Listener
from nullcell.net.quic_link import open_link


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def running_cell():
    identity = Identity.generate()
    port = _free_port()
    listener = Listener(identity, "127.0.0.1", port)
    await listener.start()
    try:
        yield identity, port, listener
    finally:
        listener.close()


async def test_link_verifies_identity_and_negotiates_version():
    async with (
        running_cell() as (server, port, _),
        open_link(server.nid, "127.0.0.1", port) as link,
    ):
        for _ in range(50):
            if link.version is not None:
                break
            await asyncio.sleep(0.01)
        assert link.version == 1
        assert link.peer_versions == (1,)


async def test_wrong_address_is_rejected():
    """§4: the relay proves who it is; a different key must not be accepted."""
    async with running_cell() as (_, port, _):
        impostor = Identity.generate()
        with pytest.raises(CertError, match="different identity"):
            async with open_link(impostor.nid, "127.0.0.1", port):
                pass


async def test_ping_pong_roundtrip():
    async with running_cell() as (server, port, _):
        got: list[bytes] = []
        done = asyncio.Event()

        def on_frame(circ_id, cmd, payload):
            if cmd == Cmd.PONG:
                got.append(bytes(payload[:8]))
                if len(got) == 5:
                    done.set()

        async with open_link(server.nid, "127.0.0.1", port) as link:
            link.on_frame = on_frame
            for i in range(5):
                link.send_frame(0, Cmd.PING, i.to_bytes(8, "big"))
            await asyncio.wait_for(done.wait(), timeout=5.0)

        assert got == [i.to_bytes(8, "big") for i in range(5)]


async def test_relay_frames_survive_the_link_intact():
    """Frames are reassembled at 512-byte boundaries, payload untouched."""
    async with running_cell() as (server, port, listener):
        received: list[tuple[int, bytes]] = []
        done = asyncio.Event()
        expected = 40

        def server_handler(circ_id, cmd, payload):
            if cmd != Cmd.RELAY:
                return
            header = parse_relay_payload(payload)
            received.append((header.stream_id, header.payload))
            if len(received) == expected:
                done.set()

        async with open_link(server.nid, "127.0.0.1", port) as link:
            for _ in range(100):
                if listener.links:
                    break
                await asyncio.sleep(0.01)
            listener.links[-1].on_frame = server_handler

            batch = [
                (
                    7,
                    Cmd.RELAY,
                    pack_relay_payload(1, f"marco-{i}".encode(), stream_id=i),
                )
                for i in range(expected)
            ]
            link.send_frames(batch)  # coalesced into few writes (§16)
            await asyncio.wait_for(done.wait(), timeout=10.0)

        assert received == [(i, f"marco-{i}".encode()) for i in range(expected)]


async def test_client_presents_no_certificate():
    """§4: one-sided authentication is what keeps the client anonymous."""
    async with (
        running_cell() as (server, port, listener),
        open_link(server.nid, "127.0.0.1", port),
    ):
        for _ in range(100):
            if listener.links:
                break
            await asyncio.sleep(0.01)
        assert listener.links
        assert listener.links[-1].peer_certificate is None


async def test_large_payloads_are_not_merged_or_split():
    """Back-to-back full frames must come out as distinct frames."""
    async with running_cell() as (server, port, listener):
        sizes: list[int] = []
        done = asyncio.Event()

        def server_handler(circ_id, cmd, payload):
            if cmd == Cmd.RELAY:
                sizes.append(parse_relay_payload(payload).length)
                if len(sizes) == 10:
                    done.set()

        async with open_link(server.nid, "127.0.0.1", port) as link:
            for _ in range(100):
                if listener.links:
                    break
                await asyncio.sleep(0.01)
            listener.links[-1].on_frame = server_handler
            link.send_frames([(1, Cmd.RELAY, pack_relay_payload(1, b"x" * 496)) for _ in range(10)])
            await asyncio.wait_for(done.wait(), timeout=10.0)

        assert sizes == [496] * 10


async def test_a_link_carrying_circuits_is_kept_alive(monkeypatch):
    """Un punto de enlace calla hasta que llega una visita: sin PINGs, QUIC
    cerraba su enlace al minuto (idle_timeout) y el portal, aún «en línea» en
    el DHT, dejaba de ser alcanzable sin que nadie lo notara."""
    from nullcell.net import link as link_mod

    monkeypatch.setattr(link_mod, "KEEPALIVE_EVERY", 0.05)
    async with (
        running_cell() as (server, port, _),
        open_link(server.nid, "127.0.0.1", port) as link,
    ):
        await asyncio.sleep(0.2)
        assert link.keepalives == 0  # sin circuitos no hay nada que mantener
        link.route(7, lambda *a: None)
        await asyncio.sleep(0.3)
        assert link.keepalives >= 2
        assert not link.closed.done()
