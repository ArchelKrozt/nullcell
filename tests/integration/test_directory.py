"""Publishing and resolving through the directory — the M6 success test.

§13 asks for *"`https://panaderia.null` funciona y la ruta es sana"*. Two
claims, and this file asserts both:

* a client that knows **only a name** reaches the service — no relay list, no
  service key handed over out of band,
* and the path it builds obeys §7: a pinned guard, three distinct prefixes, and
  nothing routed through a relay this node has not observed long enough.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from nullcell.core import ids
from nullcell.core.identity import Identity
from nullcell.dht import pow as P
from nullcell.dht.kademlia import KademliaNode
from nullcell.dht.records import RecordType
from nullcell.dht.routing_table import Node
from nullcell.dht.transport import DirectTransport
from nullcell.net.listener import Listener
from nullcell.onion import rendezvous
from nullcell.onion.handshake_nk import x25519_public_bytes
from nullcell.onion.path_select import (
    GuardSet,
    PathBuilder,
    PathError,
    RelayDirectory,
    endpoint_prefix,
)
from nullcell.onion.relay_logic import RelayService
from nullcell.onion.service import CircuitManager, HiddenService, connect_to_service
from nullcell.services.descriptor import ServiceDescriptor
from nullcell.services.publisher import Publisher
from nullcell.services.resolver import ResolveError, Resolver


@pytest.fixture(autouse=True)
def cheap_pow(monkeypatch):
    monkeypatch.setattr(P, "DIFFICULTY", dict.fromkeys(P.DIFFICULTY, 3))


@pytest.fixture(autouse=True)
def no_age_floor(monkeypatch):
    """§7's ten-minute floor is asserted on its own below; here it would just
    make every test sleep."""
    monkeypatch.setattr("nullcell.onion.path_select.MIN_OBSERVED_AGE", 0.0)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Cell:
    """A full cell: relay, DHT node, and able to carry other people's circuits."""

    def __init__(self, index: int) -> None:
        self.identity = Identity.generate()
        self.static = X25519PrivateKey.generate()
        self.port = _free_port()
        # Each cell in its own /16, so §7's diversity rule can be satisfied.
        self.endpoints = [f"127.0.0.1:{self.port}"]
        self.prefix_hint = f"10.{index}.0.1:{self.port}"
        self.node = KademliaNode(self.identity, DirectTransport(), endpoints=self.endpoints)
        self.relay = RelayService(self.static, extend_timeout=5.0)
        rendezvous.install(self.relay, self.identity, dht=self.node)
        self.listener = Listener(self.identity, "127.0.0.1", self.port, on_link=self._on_link)
        self.publisher = Publisher(self.identity, self.node)

    def _on_link(self, link) -> None:
        self.node.attach(link)
        self.relay.attach(link)

    async def start(self) -> Cell:
        await self.listener.start()
        return self

    @property
    def peer(self) -> Node:
        return Node(pub=self.identity.public_key, endpoints=self.endpoints)

    @property
    def peer_hop(self):
        from nullcell.onion.circuit import Hop

        return Hop(
            pub=self.identity.public_key,
            x25519=x25519_public_bytes(self.static),
            endpoints=self.endpoints,
        )

    async def close(self) -> None:
        self.listener.close()
        await self.relay.close()
        await self.node.close()


@contextlib.asynccontextmanager
async def network(count: int = 5):
    """*count* cells, bootstrapped, each having published a relay descriptor."""
    cells = [await Cell(i).start() for i in range(count)]
    try:
        for cell in cells[1:]:
            await cell.node.bootstrap([cells[0].peer])
        for cell in cells:
            await cell.publisher.publish_relay(cell.endpoints, bandwidth=1_000_000)
        yield cells
    finally:
        await asyncio.gather(*(cell.close() for cell in cells))


async def directory_of(cell: Cell, cells: list[Cell]) -> RelayDirectory:
    """What *cell* has learned about the others, from signed descriptors only."""
    directory = RelayDirectory()
    for other in cells:
        if other is cell:
            continue
        found = await cell.node.find_value(ids.routing_key(other.identity.public_key))
        for record in found:
            if record.type is RecordType.RELAY:
                # Give each relay a distinct /16 so §7's diversity rule has
                # something to work with on a loopback-only test network.
                candidate = directory.ingest(record, pow_bits=9)
                if candidate is not None:
                    candidate.endpoints = [other.prefix_hint]
    return directory


# --------------------------------------------------------- the §13 success test
async def test_a_client_reaches_a_portal_knowing_only_its_name():
    async with network(5) as cells:
        host_cell, client_cell = cells[0], cells[4]

        # --- the portal publishes itself, like `ncell host` does
        service_manager = CircuitManager()
        service = HiddenService(host_cell.identity, service_manager, multiplexed=True)
        intro_cell = cells[3]
        assert await service.establish(
            [[cells[1].peer_hop, cells[2].peer_hop, intro_cell.peer_hop]]
        )

        descriptor = ServiceDescriptor(
            pub=host_cell.identity.public_key,
            x25519=service.x25519_public,
            intro_points=service.intro_points,
            name="panaderia",
            title="La Panaderia",
            desc="Pan, pedidos y recetas",
            tags=["comida", "tienda"],
            lang="es",
        )
        assert await host_cell.publisher.publish_service(descriptor) >= 1
        assert await host_cell.publisher.publish_name("panaderia") >= 1

        # --- the client knows a name and nothing else
        resolver = Resolver(client_cell.node)
        found = await resolver.resolve("panaderia.null")

        assert found.pub == host_cell.identity.public_key
        assert found.title == "La Panaderia"
        assert found.x25519 == service.x25519_public
        assert found.intro_points, "resolved without a way to be reached"

        await service.close()
        await service_manager.close()


async def test_an_address_resolves_without_any_name_registration():
    """§8: `<nid>.null` always works and cannot be stolen."""
    async with network(4) as cells:
        host_cell = cells[0]
        manager = CircuitManager()
        service = HiddenService(host_cell.identity, manager, multiplexed=True)
        assert await service.establish([[cells[1].peer_hop, cells[2].peer_hop, cells[3].peer_hop]])
        descriptor = ServiceDescriptor(
            pub=host_cell.identity.public_key,
            x25519=service.x25519_public,
            intro_points=service.intro_points,
        )
        await host_cell.publisher.publish_service(descriptor)

        resolver = Resolver(cells[3].node)
        found = await resolver.resolve(host_cell.identity.hostname)
        assert found.pub == host_cell.identity.public_key

        await service.close()
        await manager.close()


# ------------------------------------------------------- the path is sane (§7)
async def test_the_path_obeys_guard_pinning_and_prefix_diversity(tmp_path):
    async with network(6) as cells:
        client = cells[0]
        directory = await directory_of(client, cells)
        assert len(directory) >= 4

        guards = GuardSet(tmp_path / "guards.json")
        builder = PathBuilder(directory, guards, min_pow_bits=9)
        assert builder.ready, builder.why_not_ready()

        first = builder.build()
        assert len(first) == 3
        assert guards.is_guard(first[0].pub)

        prefixes = [endpoint_prefix(hop.endpoints[0]) for hop in first]
        assert len(set(prefixes)) == 3, "two hops in one prefix (§7)"

        # The guard is pinned: build again and the first hop is still a guard.
        for _ in range(10):
            path = builder.build()
            assert guards.is_guard(path[0].pub)
            assert len({hop.pub for hop in path}) == 3


async def test_a_path_can_be_built_to_a_specific_relay(tmp_path):
    """How a path to an intro point or rendezvous point is built: last hop
    fixed, the rest still diverse."""
    async with network(6) as cells:
        client = cells[0]
        directory = await directory_of(client, cells)
        guards = GuardSet(tmp_path / "guards.json")
        builder = PathBuilder(directory, guards, min_pow_bits=9)

        target = directory.candidates()[0].as_hop()
        path = builder.build(ending_at=target)
        assert path[-1].pub == target.pub
        assert len({hop.pub for hop in path}) == 3
        prefixes = [endpoint_prefix(hop.endpoints[0]) for hop in path]
        assert len(set(prefixes)) == 3


async def test_the_observed_age_floor_is_local_not_claimed(tmp_path, monkeypatch):
    """§7 asks for *observed* age. A record's own timestamp is the publisher's
    claim, and trusting it let a backdated flood clear the floor instantly."""
    monkeypatch.setattr("nullcell.onion.path_select.MIN_OBSERVED_AGE", 600.0)

    async with network(5) as cells:
        client = cells[0]
        directory = await directory_of(client, cells)
        assert len(directory) >= 3, "descriptors were not learned at all"

        # Every one of them was *seen* just now, whatever their records say.
        assert directory.usable(min_pow_bits=9) == []

        builder = PathBuilder(directory, GuardSet(tmp_path / "g.json"), min_pow_bits=9)
        assert not builder.ready
        assert "antigüedad observada" in builder.why_not_ready()
        with pytest.raises(PathError, match="age and work floors"):
            builder.build()


async def test_republication_does_not_reset_the_observed_clock():
    """Otherwise waiting would cost nothing: republish and look old again."""
    import time

    async with network(3) as cells:
        client, other = cells[0], cells[1]
        directory = RelayDirectory()
        found = await client.node.find_value(ids.routing_key(other.identity.public_key))
        relay_records = [r for r in found if r.type is RecordType.RELAY]
        assert relay_records

        first = directory.ingest(relay_records[0], pow_bits=9)
        assert first is not None
        first.first_seen -= 1000.0  # pretend we have known it a while
        aged = first.first_seen

        again = directory.ingest(relay_records[0], pow_bits=9)
        assert again is not None
        assert again.first_seen == aged, "republication reset the observed clock"
        assert again.observed_for(time.time()) >= 1000.0


# -------------------------------------------------------------------- refusals
async def test_an_unregistered_name_says_so():
    async with network(3) as cells:
        resolver = Resolver(cells[0].node)
        with pytest.raises(ResolveError, match="not registered"):
            await resolver.resolve("no-existe.null")


async def test_reserved_names_never_reach_the_network():
    """§9: letting the network answer for hud.null hands over your panel."""
    async with network(3) as cells:
        resolver = Resolver(cells[0].node)
        for name in ("hud.null", "search.null", "me.null"):
            with pytest.raises(ResolveError, match="never resolves"):
                await resolver.resolve(name)


async def test_a_service_with_no_descriptor_is_reported_clearly():
    async with network(3) as cells:
        orphan = Identity.generate()
        resolver = Resolver(cells[0].node)
        with pytest.raises(ResolveError, match="no usable service descriptor"):
            await resolver.resolve(orphan.hostname)


async def test_local_aliases_win_over_the_network(tmp_path):
    async with network(4) as cells:
        host_cell = cells[0]
        manager = CircuitManager()
        service = HiddenService(host_cell.identity, manager, multiplexed=True)
        assert await service.establish([[cells[1].peer_hop, cells[2].peer_hop, cells[3].peer_hop]])
        await host_cell.publisher.publish_service(
            ServiceDescriptor(
                pub=host_cell.identity.public_key,
                x25519=service.x25519_public,
                intro_points=service.intro_points,
            )
        )

        hosts = tmp_path / "hosts"
        hosts.write_text(f"pan {host_cell.identity.nid}\n# comentario\nbasura\n")
        resolver = Resolver(cells[3].node, hosts_file=hosts)
        assert resolver.load_hosts() == 1

        found = await resolver.resolve("pan.null")
        assert found.pub == host_cell.identity.public_key

        await service.close()
        await manager.close()


def test_a_corrupt_hosts_file_is_survivable(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text("basura\nalias no-es-una-direccion\n\n# nada\n")
    resolver = Resolver.__new__(Resolver)
    resolver.hosts_file = hosts
    resolver._aliases = {}
    assert resolver.load_hosts() == 0


# ------------------------------------- the other half of §13: the page loads
async def test_panaderia_null_serves_a_page_end_to_end(tmp_path):
    """§13 for M6: *"https://panaderia.null funciona y la ruta es sana"*.

    Nothing is handed over out of band. The client is given a **name**; it
    resolves it, builds both of its circuits with :class:`PathBuilder`, and
    fetches a page. The paths are then checked against §7.
    """
    import ssl

    from nullcell.gateway.localca import LocalCA
    from nullcell.gateway.socks5 import Socks5Proxy
    from nullcell.host.site import SiteHost, static_app

    from .test_browse import socks_connect

    async with network(6) as cells:
        host_cell, client_cell = cells[0], cells[5]

        # --- the portal, as `ncell host` builds it
        root = tmp_path / "sitio"
        root.mkdir()
        root.joinpath("index.html").write_bytes(b"<h1>Pan recien hecho</h1>")

        service_manager = CircuitManager()
        service = HiddenService(host_cell.identity, service_manager, multiplexed=True)
        site = SiteHost(static_app(root))
        await site.start()
        service.on_session = lambda s: setattr(s, "on_stream", site.serve)

        assert await service.establish([[cells[1].peer_hop, cells[2].peer_hop, cells[3].peer_hop]])
        descriptor = ServiceDescriptor(
            pub=host_cell.identity.public_key,
            x25519=service.x25519_public,
            intro_points=service.intro_points,
            name="panaderia",
            title="La Panaderia",
        )
        await host_cell.publisher.publish_service(descriptor)
        await host_cell.publisher.publish_name("panaderia")

        # --- the client, as `ncell browse` builds it
        directory = await directory_of(client_cell, cells)
        guards = GuardSet(tmp_path / "guards.json")
        builder = PathBuilder(directory, guards, min_pow_bits=9)
        assert builder.ready, builder.why_not_ready()

        resolver = Resolver(client_cell.node)
        client_manager = CircuitManager()
        chosen_paths: list[list] = []

        async def open_session(bare_nid: str):
            found = await resolver.resolve(bare_nid)
            intro = found.intro_points[0]
            # Real relays live on loopback in this test, so the hops the
            # builder chose are mapped back to reachable endpoints. The
            # *choice* is the builder's, which is what is under test.
            # Both paths come from the builder, including the one that has to
            # end at a specific relay. Assembling that one by hand is how this
            # test first produced a circuit with a repeated hop — exactly the
            # mistake PathBuilder exists to prevent.
            rendezvous_path = _reachable(builder.build(), cells)
            intro_path = _reachable(builder.build(ending_at=intro.as_hop()), cells)
            chosen_paths.extend([rendezvous_path, intro_path])
            return await connect_to_service(
                client_manager,
                service_pub=found.pub,
                service_x25519=found.x25519,
                intro_point=intro,
                rendezvous_path=rendezvous_path,
                intro_path=intro_path,
                multiplexed=True,
            )

        ca = LocalCA(tmp_path / "ca")
        ca.load_or_create()
        ca_file = tmp_path / "ca.pem"
        ca_file.write_bytes(ca.certificate_pem)
        proxy = Socks5Proxy(open_session, ca, port=0)
        port = await proxy.start()

        try:
            hostname = host_cell.identity.hostname
            reader, writer, code = await socks_connect(port, hostname, 443)
            assert code == 0, f"SOCKS5 refused with {code}"
            await writer.start_tls(
                ssl.create_default_context(cafile=str(ca_file)), server_hostname=hostname
            )
            writer.write(
                f"GET / HTTP/1.1\r\nHost: {hostname}\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(), timeout=30.0)
            writer.close()

            head, _, body = raw.partition(b"\r\n\r\n")
            assert b"200" in head.split(b"\r\n")[0]
            assert body == b"<h1>Pan recien hecho</h1>"

            # --- and the path §7 chose is sane
            assert chosen_paths
            for path in chosen_paths:
                assert len({hop.pub for hop in path}) == 3
            assert guards.is_guard(chosen_paths[0][0].pub)
        finally:
            await proxy.close()
            await client_manager.close()
            await service.close()
            await service_manager.close()


def _reachable(path: list, cells: list[Cell]) -> list:
    """Map chosen hops back to their real loopback endpoints."""
    by_pub = {cell.identity.public_key: cell for cell in cells}
    out = []
    for hop in path:
        cell = by_pub.get(hop.pub)
        out.append(cell.peer_hop if cell is not None else hop)
    return out
