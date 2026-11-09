"""Fetching a site over six hops — the M5 success test.

§13 asks for *"Firefox abre https://<nid>.null con candado"*. A browser cannot
be launched in CI, so the test does exactly what one does: SOCKS5 with a
hostname, TLS validated against the local CA, then HTTP/1.1 — and asserts that
the page came back and that the certificate is for the address asked for.

What that leaves untested here is the browser itself: the profile preferences
and the CA import. Those are exercised by `ncell browse` and need a real
Firefox, so they are not claimed as passing on the strength of this file.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import ssl
import tempfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from nullcell.core import ids
from nullcell.core.identity import Identity
from nullcell.gateway.localca import LocalCA
from nullcell.gateway.socks5 import Socks5Proxy
from nullcell.host.site import SiteHost, static_app
from nullcell.net.listener import Listener
from nullcell.onion import rendezvous
from nullcell.onion.circuit import Hop
from nullcell.onion.handshake_nk import x25519_public_bytes
from nullcell.onion.relay_logic import RelayService
from nullcell.onion.service import CircuitManager, HiddenService, connect_to_service

PAGE = b"<!doctype html><title>La Panaderia</title><h1>Pan recien hecho</h1>"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Relay:
    def __init__(self) -> None:
        self.identity = Identity.generate()
        self.static = X25519PrivateKey.generate()
        self.port = 0  # the kernel picks it; start() reports it
        self.endpoints = [f"127.0.0.1:{self.port}"]
        self.service = RelayService(self.static, extend_timeout=5.0)
        self.intro, self.rendezvous = rendezvous.install(self.service, self.identity)
        self.listener = Listener(self.identity, "127.0.0.1", self.port, on_link=self.service.attach)

    async def start(self):

        self.port = await self.listener.start()
        # Mutated in place, not rebound: KademliaNode and anything else built
        # in __init__ holds a reference to *this list*, so assigning a new one
        # left them advertising port 0 for ever — every connection to them
        # then timed out, which showed up as a suite that got slower rather
        # than as a failure.
        self.endpoints[:] = [f"127.0.0.1:{self.port}"]
        return self

    @property
    def hop(self) -> Hop:
        return Hop(
            pub=self.identity.public_key,
            x25519=x25519_public_bytes(self.static),
            endpoints=self.endpoints,
        )

    async def close(self) -> None:
        self.listener.close()
        await self.service.close()


@contextlib.asynccontextmanager
async def site(tmp_path: Path):
    """Five relays, a hosted site, a local CA and a SOCKS5 proxy."""
    relays = [await Relay().start() for _ in range(5)]
    root = tmp_path / "sitio"
    root.mkdir()
    (root / "index.html").write_bytes(PAGE)
    (root / "estilo.css").write_bytes(b"body{color:#22d3ee}")

    manager = CircuitManager()
    service = HiddenService(Identity.generate(), manager, multiplexed=True)
    host = SiteHost(static_app(root))
    await host.start()
    service.on_session = lambda session: setattr(session, "on_stream", host.serve)

    ca = LocalCA(tmp_path / "ca")
    ca.load_or_create()
    client_manager = CircuitManager()

    async def open_session(bare_nid: str):
        return await connect_to_service(
            client_manager,
            service_pub=ids.decode_nid(bare_nid).pub,
            service_x25519=service.x25519_public,
            intro_point=service.intro_points[0],
            rendezvous_path=[relays[1].hop, relays[2].hop, relays[4].hop],
            intro_path=[relays[0].hop, relays[3].hop, service.intro_points[0].as_hop()],
            multiplexed=True,
        )

    proxy = Socks5Proxy(open_session, ca, port=0)
    try:
        assert await service.establish([[relays[0].hop, relays[1].hop, relays[2].hop]]) == 1
        await proxy.start()
        yield service, proxy, ca, host
    finally:
        await proxy.close()
        await client_manager.close()
        await service.close()
        await manager.close()
        await asyncio.gather(*(relay.close() for relay in relays))


# ------------------------------------------------------------- a browser's steps
async def socks_connect(port: int, hostname: str, target_port: int):
    """What a browser does: SOCKS5 CONNECT with a *hostname* (§8)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"\x05\x01\x00")
    await writer.drain()
    assert await reader.readexactly(2) == b"\x05\x00"

    name = hostname.encode()
    writer.write(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + target_port.to_bytes(2, "big"))
    await writer.drain()
    reply = await reader.readexactly(10)
    return reader, writer, reply[1]


async def fetch(port: int, hostname: str, ca: LocalCA, path: str = "/"):
    """SOCKS5, then TLS against the local CA, then HTTP/1.1."""
    reader, writer, code = await socks_connect(port, hostname, 443)
    assert code == 0, f"SOCKS5 refused with {code}"

    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as handle:
        handle.write(ca.certificate_pem)
        ca_file = handle.name
    context = ssl.create_default_context(cafile=ca_file)
    await writer.start_tls(context, server_hostname=hostname)

    writer.write(f"GET {path} HTTP/1.1\r\nHost: {hostname}\r\nConnection: close\r\n\r\n".encode())
    await writer.drain()
    peer_cert = writer.get_extra_info("peercert")

    # Read to EOF rather than once: a response larger than one end-to-end frame
    # arrives in pieces, and a single read() returns only the first of them.
    # `Connection: close` is what makes EOF the end of the response.
    raw = await asyncio.wait_for(reader.read(), timeout=30.0)
    writer.close()

    head, _, body = raw.partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1])
    return status, body, head, peer_cert


# ---------------------------------------------------------------- the §13 test
async def test_a_page_is_fetched_over_six_hops(tmp_path):
    async with site(tmp_path) as (service, proxy, ca, host):
        status, body, head, cert = await fetch(proxy.port, service.identity.hostname, ca)

        assert status == 200
        assert body == PAGE
        assert b"text/html" in head
        # The lock in §13's wording: TLS validated against our own CA, for the
        # address that was asked for.
        names = [v for key, v in cert["subject"][0] if key == "commonName"]
        assert names == [service.identity.hostname]
        assert host.stats["streams"] >= 1


async def test_a_second_resource_reuses_the_same_splice(tmp_path):
    """§8: a page's several connections become streams, not rendezvous."""
    async with site(tmp_path) as (service, proxy, ca, host):
        hostname = service.identity.hostname
        first = await fetch(proxy.port, hostname, ca, "/index.html")
        second = await fetch(proxy.port, hostname, ca, "/estilo.css")

        assert first[0] == 200
        assert second[0] == 200
        assert second[1] == b"body{color:#22d3ee}"
        assert host.stats["streams"] >= 2


async def test_a_missing_page_is_a_404_not_a_failure(tmp_path):
    async with site(tmp_path) as (service, proxy, ca, _host):
        status, _, _, _ = await fetch(proxy.port, service.identity.hostname, ca, "/no-existe")
        assert status == 404


async def test_a_site_cannot_serve_outside_its_root(tmp_path):
    async with site(tmp_path) as (service, proxy, ca, _host):
        status, _, _, _ = await fetch(
            proxy.port, service.identity.hostname, ca, "/../../etc/passwd"
        )
        assert status in (403, 404)


# -------------------------------------------------------------------- refusals
async def test_an_ip_literal_is_refused_with_the_reason(tmp_path):
    """§8 calls socks_remote_dns critical: a browser that resolves .null
    itself leaks every name to the ISP's resolver and does not work."""
    async with site(tmp_path) as (_service, proxy, _ca, _host):
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        await reader.readexactly(2)
        writer.write(b"\x05\x01\x00\x01" + bytes([1, 2, 3, 4]) + (443).to_bytes(2, "big"))
        await writer.drain()
        reply = await reader.readexactly(10)
        writer.close()
        assert reply[1] == 0x08  # address type not supported


async def test_a_name_that_is_not_an_address_is_refused_locally(tmp_path):
    async with site(tmp_path) as (_service, proxy, _ca, _host):
        _reader, writer, code = await socks_connect(proxy.port, "google.com", 443)
        writer.close()
        assert code == 0x04  # host unreachable
        assert proxy.stats["refused"] >= 1


async def test_a_non_connect_command_is_refused(tmp_path):
    async with site(tmp_path) as (_service, proxy, _ca, _host):
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        await reader.readexactly(2)
        writer.write(b"\x05\x02\x00\x03\x01a" + (443).to_bytes(2, "big"))  # BIND
        await writer.drain()
        reply = await reader.readexactly(10)
        writer.close()
        assert reply[1] == 0x07  # command not supported


@pytest.mark.parametrize("hostname", ["", "x" * 60 + ".null", "no-es-una-direccion.null"])
async def test_malformed_addresses_never_touch_the_network(tmp_path, hostname):
    async with site(tmp_path) as (_service, proxy, _ca, _host):
        if not hostname:
            pytest.skip("an empty name cannot be encoded in SOCKS5")
        before = proxy.stats["opened"]
        _reader, writer, code = await socks_connect(proxy.port, hostname, 443)
        writer.close()
        assert code != 0
        assert proxy.stats["opened"] == before


# ------------------------------------------------- regressions, all found by hand
def test_the_end_to_end_byte_budget_adds_up_exactly():
    """One byte over and a frame is refused inside a task nobody awaits, so a
    response simply never arrives. Pinned so the arithmetic cannot drift."""
    from nullcell.core.frames import RELAY_DATA_SIZE
    from nullcell.onion.handshake_nk import TRANSPORT_OVERHEAD
    from nullcell.onion.service import E2E_HEADER, MAX_E2E_PAYLOAD

    assert E2E_HEADER + TRANSPORT_OVERHEAD + MAX_E2E_PAYLOAD == RELAY_DATA_SIZE


@pytest.mark.parametrize("size", [1, 479, 480, 1000, 40_000])
async def test_responses_of_every_size_survive(tmp_path, size):
    """A response larger than one end-to-end frame has to be reassembled, and
    the one-byte budget error showed up only above 479 bytes."""
    async with site(tmp_path) as (service, proxy, ca, _host):
        page = tmp_path / "sitio" / "grande.html"
        page.write_bytes(b"X" * size)
        status, body, _, _ = await fetch(proxy.port, service.identity.hostname, ca, "/grande.html")
        assert status == 200
        assert len(body) == size


async def test_parallel_requests_share_one_rendezvous(tmp_path):
    """§8's whole point, and §16's tactic 7 one layer down: a page's parallel
    connections become streams on one six-hop splice, not six splices."""
    async with site(tmp_path) as (service, proxy, ca, host):
        hostname = service.identity.hostname
        for index in range(6):
            (tmp_path / "sitio" / f"r{index}.txt").write_bytes(f"recurso {index}".encode())

        await fetch(proxy.port, hostname, ca)  # warm the session
        results = await asyncio.gather(
            *(fetch(proxy.port, hostname, ca, f"/r{i}.txt") for i in range(6))
        )

        assert [status for status, *_ in results] == [200] * 6
        assert [body for _, body, *_ in results] == [f"recurso {i}".encode() for i in range(6)]
        assert proxy.stats["sessions"] == 1, "a second rendezvous was built"
        assert host.stats["streams"] >= 7


async def test_concurrent_writes_keep_the_noise_nonces_in_order(tmp_path):
    """The end-to-end cipher has a sequential nonce. Encrypting before awaiting
    flow-control credit let two concurrent streams swap order, and then nothing
    decrypted at all."""
    async with site(tmp_path) as (service, proxy, ca, _host):
        hostname = service.identity.hostname
        for index in range(8):
            (tmp_path / "sitio" / f"c{index}.txt").write_bytes(b"Y" * (600 + index))

        await fetch(proxy.port, hostname, ca)
        results = await asyncio.gather(
            *(fetch(proxy.port, hostname, ca, f"/c{i}.txt") for i in range(8))
        )
        for index, (status, body, _, _) in enumerate(results):
            assert status == 200
            assert body == b"Y" * (600 + index)


async def test_a_hostname_that_is_not_ascii_is_refused_not_hung(tmp_path):
    """The idna codec rejects errors='replace' by raising, and the exception
    escaped the handler — leaving the browser waiting for a reply for ever."""
    async with site(tmp_path) as (_service, proxy, _ca, _host):
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        await reader.readexactly(2)
        name = "paneño.null".encode()  # not ASCII
        writer.write(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + (443).to_bytes(2, "big"))
        await writer.drain()
        reply = await asyncio.wait_for(reader.readexactly(10), timeout=5.0)
        writer.close()
        assert reply[1] != 0  # refused, and refused *promptly*
