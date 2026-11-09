"""``ncell`` — Nullcell command line. M0/M1 subset."""

from __future__ import annotations

import asyncio
import contextlib
import statistics
import time
from pathlib import Path
from typing import Annotated

import typer

from nullcell.core import config, ids
from nullcell.core.frames import Cmd
from nullcell.core.identity import Identity
from nullcell.net.link import PROTOCOL_VERSIONS

app = typer.Typer(
    add_completion=False,
    help="Nullcell — red P2P cifrada y anónima. Binario: ncell (no nc, que es netcat).",
    no_args_is_help=True,
)


def _load_identity(passphrase: str | None = None) -> tuple[Identity, config.Config]:
    cfg = config.load()
    warning = config.cloud_synced_warning()
    if warning:
        typer.secho(f"⚠  {warning}", fg=typer.colors.YELLOW, err=True)
    identity, created = Identity.load_or_create(cfg.path_identity, passphrase)
    if created:
        typer.secho(f"✓ identidad nueva creada en {cfg.path_identity}", fg=typer.colors.GREEN)
    if perm := identity.check_permissions(cfg.path_identity):
        typer.secho(f"⚠  {perm}", fg=typer.colors.YELLOW, err=True)
    return identity, cfg


@app.command("id")
def cmd_id(
    passphrase: Annotated[str | None, typer.Option(help="Si la clave está cifrada")] = None,
    show_x25519: Annotated[bool, typer.Option("--x25519")] = False,
) -> None:
    """Muestra la identidad de esta celda."""
    identity, cfg = _load_identity(passphrase)
    typer.echo(f"NID       {identity.nid}")
    typer.echo(f"corto     {identity.short}")
    typer.echo(f"dirección {identity.hostname}")
    typer.echo(f"alt       {identity.nid}{ids.SUFFIX_ALT}")
    typer.echo(f"ed25519   {identity.public_key.hex()}")
    if show_x25519:
        typer.echo(f"x25519    {identity.x25519_public.hex()}")
    typer.echo(f"dht key   {identity.routing_key.hex()}")
    typer.echo(f"clase     {'privado' if ids.decode_nid(identity.nid).private else 'público'}")
    typer.echo(f"versiones {', '.join(map(str, PROTOCOL_VERSIONS))}")
    typer.echo(f"estado    {config.home()}")


@app.command("addr")
def cmd_addr(nid: Annotated[str, typer.Argument(help="Dirección a validar")]) -> None:
    """Valida y descompone una dirección .null sin tocar la red."""
    try:
        address = ids.decode_nid(nid)
    except ids.AddressError as exc:
        typer.secho(f"✗ {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"válida    {address.nid}")
    typer.echo(f"ed25519   {address.pub.hex()}")
    typer.echo(f"versión   {address.version}")
    typer.echo(f"clase     {'privado' if address.private else 'público'}")
    typer.echo(f"dht key   {address.routing_key.hex()}")


@app.command("listen")
def cmd_listen(
    port: Annotated[int, typer.Option("--port", "-p")] = 4433,
    host: Annotated[str, typer.Option("--host")] = "::",
    passphrase: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Escucha enlaces QUIC entrantes (celda alcanzable)."""
    identity, _ = _load_identity(passphrase)
    from nullcell.net.listener import Listener

    async def run() -> None:
        listener = Listener(identity, host, port)
        await listener.start()
        typer.secho(f"✓ {identity.short} escuchando en [{host}]:{port}", fg=typer.colors.GREEN)
        typer.echo(f"  dirección: {identity.hostname}")
        typer.echo("  Ctrl-C para parar")
        await asyncio.Event().wait()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        typer.echo("\nparado")


@app.command("ping")
def cmd_ping(
    nid: Annotated[str, typer.Argument(help="NID de la celda destino")],
    addr: Annotated[str, typer.Option("--addr", "-a", help="host:puerto (hasta el DHT de M2)")],
    count: Annotated[int, typer.Option("--count", "-c")] = 5,
) -> None:
    """Abre un enlace verificado y mide el RTT con marcos PING."""
    try:
        ids.decode_nid(nid)
    except ids.AddressError as exc:
        typer.secho(f"✗ dirección inválida: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    host, _, port_s = addr.rpartition(":")
    if not host:
        typer.secho("✗ --addr debe ser host:puerto", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    host = host.strip("[]")

    from nullcell.net.cert import CertError
    from nullcell.net.quic_link import open_link

    async def run() -> int:
        rtts: list[float] = []
        pending: dict[bytes, float] = {}
        done = asyncio.Event()

        def on_frame(circ_id: int, cmd: int, payload: memoryview) -> None:
            if cmd != Cmd.PONG:
                return
            token = bytes(payload[:8])
            sent = pending.pop(token, None)
            if sent is None:
                return
            rtt = (time.perf_counter() - sent) * 1000
            rtts.append(rtt)
            typer.echo(f"  pong {len(rtts)}/{count}  {rtt:.2f} ms")
            if len(rtts) >= count:
                done.set()

        t0 = time.perf_counter()
        async with open_link(nid, host, int(port_s)) as link:
            handshake_ms = (time.perf_counter() - t0) * 1000
            typer.secho(
                f"✓ identidad verificada: {ids.short(nid)} ({handshake_ms:.0f} ms)",
                fg=typer.colors.GREEN,
            )
            link.on_frame = on_frame
            for i in range(count):
                token = i.to_bytes(8, "big")
                pending[token] = time.perf_counter()
                link.send_frame(0, Cmd.PING, token)
                await asyncio.sleep(0.05)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(done.wait(), timeout=5.0)

        if not rtts:
            typer.secho("✗ sin respuesta", fg=typer.colors.RED, err=True)
            return 1
        typer.echo(
            f"\n{len(rtts)}/{count} respuestas · "
            f"min {min(rtts):.2f} · mediana {statistics.median(rtts):.2f} · "
            f"max {max(rtts):.2f} ms"
        )
        return 0

    try:
        raise typer.Exit(asyncio.run(run()))
    except CertError as exc:
        typer.secho(f"✗ verificación fallida: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    except OSError as exc:
        typer.secho(f"✗ no se pudo conectar a {addr}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


if __name__ == "__main__":
    app()


# --------------------------------------------------------------------- DHT (M2)
def _parse_seed(spec: str):
    """``<nid>@host:port`` -> Node."""
    from nullcell.dht.routing_table import Node
    from nullcell.net.link_manager import parse_endpoint

    nid, _, endpoint = spec.partition("@")
    if not endpoint:
        raise typer.BadParameter("un seed es <nid>@host:puerto")
    address = ids.decode_nid(nid)
    parse_endpoint(endpoint)  # validate now, fail clearly
    return Node(pub=address.pub, endpoints=[endpoint])


def _seeds(specs: list[str] | None):
    from nullcell.discovery.bootstrap import load_bootstrap, load_peers

    cfg = config.load()
    nodes = list(load_bootstrap()) + list(load_peers(cfg.path_peers))
    for spec in specs or []:
        nodes.append(_parse_seed(spec))
    return nodes


async def _transient_node(identity, seeds):
    """A short-lived client node: bootstrap, do one thing, go away."""
    from nullcell.dht.kademlia import KademliaNode
    from nullcell.dht.transport import DirectTransport

    node = KademliaNode(identity, DirectTransport())
    if not seeds:
        typer.secho(
            "✗ sin semillas. Usa --seed <nid>@host:puerto, o rellena bootstrap.json",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    known = await node.bootstrap(seeds)
    if known == 0:
        typer.secho("✗ ninguna semilla respondió", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho(f"✓ en la red: {known} celdas conocidas", fg=typer.colors.GREEN)
    return node


@app.command("node")
def cmd_node(
    port: Annotated[int, typer.Option("--port", "-p")] = 4433,
    host: Annotated[str, typer.Option("--host")] = "::",
    announce: Annotated[
        str | None, typer.Option("--announce", help="host:puerto público de esta celda")
    ] = None,
    seed: Annotated[list[str] | None, typer.Option("--seed", help="<nid>@host:puerto")] = None,
    lan: Annotated[bool, typer.Option("--lan", help="baliza multicast en la LAN")] = False,
    hud: Annotated[bool, typer.Option("--hud", help="panel local de depuración (§9)")] = False,
    hud_port: Annotated[int, typer.Option("--hud-port")] = 9180,
) -> None:
    """Arranca una celda completa: enlace, DHT y mantenimiento."""
    identity, cfg = _load_identity()

    from nullcell.dht.kademlia import KademliaNode
    from nullcell.dht.transport import DirectTransport
    from nullcell.discovery import pex
    from nullcell.discovery.bootstrap import save_peers
    from nullcell.net.listener import Listener

    endpoints = [announce] if announce else [f"127.0.0.1:{port}"]

    async def run() -> None:
        node = KademliaNode(identity, DirectTransport(), endpoints=endpoints)
        listener = Listener(identity, host, port, on_link=node.attach)
        await listener.start()
        typer.secho(f"✓ {identity.short} escuchando en [{host}]:{port}", fg=typer.colors.GREEN)
        typer.echo(f"  dirección: {identity.hostname}")
        typer.echo(f"  anuncia:   {', '.join(endpoints)}")

        beacon = None
        if lan:
            from nullcell.discovery.mdns import Beacon

            beacon = Beacon(identity, endpoints, on_peer=node.table.add)
            await beacon.start()
            typer.echo("  baliza LAN activa")

        seeds = _seeds(seed)
        if seeds:
            known = await node.bootstrap(seeds)
            typer.echo(f"  bootstrap: {known} celdas")

        local_ui = None
        if hud:
            from nullcell.gateway.events import EventBus
            from nullcell.gateway.localui import LocalUI

            bus = EventBus()

            def hud_snapshot() -> dict:
                return {
                    "nid": identity.nid,
                    "phase": "listo" if len(node.table) else "conectando",
                    "peers": len(node.table),
                    "relays": len(node.table),
                    "guards": [],
                    "records": node.store.stats["records"],
                    "legs": 0,
                    "circuits": [],
                }

            local_ui = LocalUI(hud_snapshot, bus, port=hud_port)
            url = await local_ui.start()
            bus.emit("node.up", f"{identity.short} escuchando en [{host}]:{port}")
            typer.secho(f"  HUD: {url}", fg=typer.colors.CYAN)
            typer.echo("        (el token va en la URL una sola vez; luego es una cookie)")

        maintenance = asyncio.create_task(pex.run_periodically(node))
        try:
            while True:
                await asyncio.sleep(30)
                typer.echo(
                    f"  [{len(node.table)} celdas] {node.store.stats} rpc={node.stats['rpc_in']}"
                    f"/{node.stats['rpc_out']}"
                )
        finally:
            maintenance.cancel()
            if local_ui is not None:
                await local_ui.close()
            if beacon is not None:
                beacon.stop()
            save_peers(cfg.path_peers, node.table.all_nodes())
            listener.close()
            await node.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        typer.echo("\nparado")


@app.command("publish")
def cmd_publish(
    name: Annotated[str | None, typer.Argument(help="Nombre legible a registrar")] = None,
    announce: Annotated[str | None, typer.Option("--announce")] = None,
    seed: Annotated[list[str] | None, typer.Option("--seed")] = None,
    ttl: Annotated[int, typer.Option("--ttl")] = 3600,
) -> None:
    """Publica el descriptor de esta celda, y opcionalmente un nombre."""
    identity, _ = _load_identity()
    from nullcell.dht import records as R

    async def run() -> None:
        node = await _transient_node(identity, _seeds(seed))
        try:
            if announce:
                body = R.RelayBody(endpoints=[announce], x25519=identity.x25519_public).as_dict()
                typer.echo("resolviendo PoW del descriptor…")
                record = await R.build(identity, R.RecordType.RELAY, body, ttl=ttl)
                count = await node.store_record(record)
                typer.secho(f"✓ descriptor en {count} celdas", fg=typer.colors.GREEN)

            if name:
                typer.echo(f"resolviendo PoW de {name}.null (el más caro, §10)…")
                record = await R.build(identity, R.RecordType.NAME, {"name": name}, ttl=ttl)
                count = await node.store_record(record)
                if count:
                    typer.secho(f"✓ {name}.null en {count} celdas", fg=typer.colors.GREEN)
                else:
                    typer.secho(
                        f"✗ {name}.null no fue aceptado: puede estar ya registrado",
                        fg=typer.colors.YELLOW,
                    )
        finally:
            await node.close()

    asyncio.run(run())


@app.command("resolve")
def cmd_resolve(
    target: Annotated[str, typer.Argument(help="Nombre o dirección .null")],
    seed: Annotated[list[str] | None, typer.Option("--seed")] = None,
) -> None:
    """Busca en el DHT un nombre o una dirección."""
    identity, _ = _load_identity()
    from nullcell.dht import records as R

    async def run() -> None:
        node = await _transient_node(identity, _seeds(seed))
        try:
            bare = ids.parse_hostname(target)
            try:
                key = ids.decode_nid(bare).routing_key
                kind = "dirección"
            except ids.AddressError:
                key = R.name_key(bare)
                kind = "nombre"

            typer.echo(f"buscando {kind} {bare}…")
            found = await node.find_value(key)
            if not found:
                typer.secho("✗ sin resultados", fg=typer.colors.YELLOW)
                raise typer.Exit(1)
            for record in found:
                typer.echo(f"  tipo      {record.type}")
                typer.echo(f"  celda     {record.nid}")
                typer.echo(f"  cuerpo    {record.body}")
                typer.echo(f"  edad      {record.age}s · ttl {record.ttl}s")
        finally:
            await node.close()

    asyncio.run(run())


# ------------------------------------------------------------------- M5: hosting
def _relay_hops(specs: list[str] | None):
    """``<nid>@host:puerto:<x25519-hex>`` -> Hop, for a lab.

    §7's path selection needs a populated DHT and relays observed for ten
    minutes. ``--relay`` bypasses that for a laboratory, and says so.
    """
    from nullcell.onion.circuit import Hop

    hops = []
    for spec in specs or []:
        try:
            nid, _, rest = spec.partition("@")
            endpoint, _, x25519 = rest.rpartition(":")
            address = ids.decode_nid(nid)
            hops.append(
                Hop(
                    pub=address.pub,
                    x25519=bytes.fromhex(x25519),
                    endpoints=[endpoint],
                )
            )
        except (ids.AddressError, ValueError) as exc:
            raise typer.BadParameter(
                f"--relay espera <nid>@host:puerto:<x25519-hex>: {exc}"
            ) from exc
    return hops


@app.command("host")
def cmd_host(
    directory: Annotated[str, typer.Argument(help="Carpeta del sitio estático")],
    relay: Annotated[
        list[str] | None,
        typer.Option("--relay", help="<nid>@host:puerto:<x25519-hex> (laboratorio)"),
    ] = None,
    name: Annotated[str | None, typer.Option("--name", help="Nombre legible a registrar")] = None,
    seed: Annotated[list[str] | None, typer.Option("--seed")] = None,
) -> None:
    """Publica una carpeta como portal, sin abrir ningún puerto (§8)."""
    identity, _ = _load_identity()
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        typer.secho(f"✗ {root} no es una carpeta", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    hops = _relay_hops(relay)
    if len(hops) < 3:
        typer.secho(
            "✗ hacen falta 3 relevos. Pásalos con --relay mientras no haya red pública;\n"
            "  la selección automática de §7 exige un DHT poblado y 10 min de antigüedad "
            "observada.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    from nullcell.host.site import SiteHost, static_app
    from nullcell.onion.service import CircuitManager, HiddenService

    async def run() -> None:
        manager = CircuitManager()
        service = HiddenService(identity, manager, multiplexed=True)
        host = SiteHost(static_app(root))
        await host.start()
        service.on_session = lambda session: setattr(session, "on_stream", host.serve)

        typer.echo(f"construyendo circuitos hacia {len(hops)} relevos…")
        established = await service.establish([hops])
        if not established:
            typer.secho("✗ no se pudo establecer ningún punto de enlace", fg=typer.colors.RED)
            raise typer.Exit(1)

        typer.secho(f"✓ portal publicado: {identity.hostname}", fg=typer.colors.GREEN)
        typer.echo(f"  carpeta:          {root}")
        typer.echo(f"  puntos de enlace: {established}")
        typer.echo("  sin IP pública, sin UPnP, sin un puerto abierto (§0)")
        if name:
            typer.echo(f"  nombre {name}.null: publícalo con `ncell publish {name}`")
        typer.echo("\n  Ctrl-C para parar")
        try:
            while True:
                await asyncio.sleep(30)
                typer.echo(f"  [{host.stats['streams']} streams servidos]")
        finally:
            await service.close()
            await manager.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        typer.echo("\nparado")


@app.command("browse")
def cmd_browse(
    target: Annotated[str | None, typer.Argument(help="Dirección .null a abrir")] = None,
    relay: Annotated[list[str] | None, typer.Option("--relay")] = None,
    service_x25519: Annotated[
        str | None, typer.Option("--service-key", help="Clave X25519 del servicio (hex)")
    ] = None,
    socks_port: Annotated[int, typer.Option("--socks-port")] = 9150,
    launch: Annotated[bool, typer.Option("--launch/--no-launch")] = True,
) -> None:
    """Levanta el proxy y abre un navegador con perfil aislado (§8)."""
    identity, _ = _load_identity()
    home = config.home()

    from nullcell.gateway.browser_profile import BrowserProfile
    from nullcell.gateway.localca import LocalCA
    from nullcell.gateway.socks5 import Socks5Proxy
    from nullcell.onion.service import CircuitManager, connect_to_service

    ca = LocalCA(home / "ca")
    ca.load_or_create()
    if warning := ca.permissions_warning():
        typer.secho(f"⚠  {warning}", fg=typer.colors.YELLOW, err=True)

    profile = BrowserProfile(home / "browser-profile", socks_port=socks_port)
    profile.write()
    typer.secho(f"✓ perfil aislado en {profile.directory}", fg=typer.colors.GREEN)
    typer.echo(f"  {profile.install_ca(ca.cert_path)}")

    hops = _relay_hops(relay)
    manager = CircuitManager()

    async def open_session(bare_nid: str):
        if len(hops) < 3 or not service_x25519:
            raise RuntimeError(
                "hacen falta 3 relevos (--relay) y la clave del servicio "
                "(--service-key) mientras no haya descubrimiento por DHT"
            )
        from nullcell.onion.rendezvous_msgs import IntroPointRef

        intro = IntroPointRef(pub=hops[2].pub, x25519=hops[2].x25519, endpoints=hops[2].endpoints)
        return await connect_to_service(
            manager,
            service_pub=ids.decode_nid(bare_nid).pub,
            service_x25519=bytes.fromhex(service_x25519),
            intro_point=intro,
            rendezvous_path=hops,
            intro_path=hops,
            multiplexed=True,
        )

    async def run() -> None:
        proxy = Socks5Proxy(open_session, ca, port=socks_port)
        port = await proxy.start()
        typer.secho(f"✓ SOCKS5 en 127.0.0.1:{port}", fg=typer.colors.GREEN)
        typer.echo("  resolución de nombres remota obligatoria (§8)")

        url = f"https://{ids.parse_hostname(target)}{ids.SUFFIX}" if target else "about:blank"
        if launch:
            if profile.launch(url) is None:
                typer.secho(
                    "⚠  Firefox no encontrado. Ábrelo a mano con:\n"
                    f"   firefox -profile {profile.directory} -no-remote {url}",
                    fg=typer.colors.YELLOW,
                )
            else:
                typer.echo(f"→ abriendo {url}")
        else:
            typer.echo(f"→ no se lanza navegador. URL: {url}")

        typer.echo("\n  Ctrl-C para parar")
        try:
            while True:
                await asyncio.sleep(30)
                typer.echo(f"  [socks {proxy.stats}]")
        finally:
            await proxy.close()
            await manager.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        typer.echo("\nparado")
