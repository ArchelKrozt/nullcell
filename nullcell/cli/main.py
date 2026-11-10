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
)


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("nullcell")
    except PackageNotFoundError:  # pragma: no cover - running from a checkout
        return "desconocida (no instalado)"


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", help="Muestra la versión y sale.")] = False,
) -> None:
    """Nullcell. Las referencias §N apuntan a NULLCELL-ARQUITECTURA.md."""
    if version:
        typer.echo(f"nullcell {_version()}")
        raise typer.Exit(0)
    if ctx.invoked_subcommand is None:
        # `invoke_without_command` is what lets --version run at all, so the
        # no-arguments case has to print the help itself.
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


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


async def _runtime(identity, cfg, *, endpoints=None, seeds=None, listen=None, anonymous=False):
    """Assemble a cell, join the network, and learn which relays exist.

    Every command that touches the network goes through here, so §7's guard
    pinning and prefix diversity apply to every circuit rather than to the ones
    that happened to remember.

    ``anonymous`` sends the DHT itself over circuits (§7, decision 8). The
    bootstrap cannot be anonymous and never could be: you have to talk to a
    seed directly to learn that any relay exists, exactly as a Tor client
    fetches its first consensus directly. Everything *after* that — which names
    you resolve, which descriptors you store — is content, and goes over three
    hops.
    """
    from nullcell.cli.runtime import Runtime

    runtime = Runtime.assemble(identity, config.home(), endpoints=endpoints)
    if listen is not None:
        await runtime.listen(*listen)

    if seeds:
        known = await runtime.node.bootstrap(seeds)
        typer.echo(f"  bootstrap: {known} celdas")
        learned = await runtime.learn_relays()
        typer.echo(f"  relevos observados: {learned}")

    if anonymous:
        _require_paths(runtime)
        runtime.go_anonymous()
        typer.secho("✓ DHT por circuitos de 3 saltos (§7, decisión 8)", fg=typer.colors.GREEN)
    return runtime


def _require_paths(runtime) -> None:
    if runtime.paths.ready:
        return
    typer.secho(f"✗ {runtime.paths.why_not_ready()}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


@app.command("node")
def cmd_node(
    port: Annotated[int, typer.Option("--port", "-p")] = 4433,
    host: Annotated[str, typer.Option("--host")] = "::",
    announce: Annotated[
        str | None, typer.Option("--announce", help="host:puerto público de esta celda")
    ] = None,
    seed: Annotated[list[str] | None, typer.Option("--seed", help="<nid>@host:puerto")] = None,
    lan: Annotated[bool, typer.Option("--lan", help="baliza multicast en la LAN")] = False,
    relay: Annotated[
        bool, typer.Option("--relay/--no-relay", help="prestar ancho de banda")
    ] = False,
    hud: Annotated[bool, typer.Option("--hud", help="panel local de depuración (§9)")] = False,
    hud_port: Annotated[int, typer.Option("--hud-port")] = 9180,
    # Nombre `config_dir` y no `config`: el módulo `config` está importado
    # arriba y un parámetro homónimo lo tapa dentro de la función. Costó un
    # arranque entero con un «'str' object has no attribute 'home'».
    config_dir: Annotated[
        str | None,
        typer.Option(
            "--config",
            "-c",
            help="carpeta de despliegue: publica sus portales sin interfaz gráfica",
        ),
    ] = None,
) -> None:
    """Arranca una celda completa: enlace, DHT, circuitos y mantenimiento.

    Con `--config` publica los portales de una carpeta de despliegue **sin
    interfaz gráfica**, que es como se corre en un servidor o en una Raspberry
    Pi: `ncell desktop` necesita PySide6, que no publica ruedas para ARM, así
    que en una Pi la vía es ésta.
    """
    identity, cfg = _load_identity()

    # La dirección .null *es* la clave (§3): un despliegue que lleva la suya
    # conserva su nombre al mudarse de máquina.
    deployment = None
    if config_dir:
        from nullcell.host.deployment import Deployment

        try:
            deployment = Deployment.load(config_dir)
        except ValueError as bad:
            typer.secho(f"✗ {bad}", fg=typer.colors.RED, err=True)
            raise typer.Exit(2) from None
        for warning in deployment.warnings():
            typer.secho(f"⚠  {warning}", fg=typer.colors.YELLOW, err=True)
        if (carried := deployment.identity_path) is not None:
            identity, _ = Identity.load_or_create(carried)
            typer.echo(f"  identidad del despliegue: {identity.short}")

    endpoints = [announce] if announce else [f"127.0.0.1:{port}"]

    from nullcell.discovery import pex
    from nullcell.discovery.bootstrap import save_peers

    async def run() -> None:
        typer.secho(f"✓ {identity.short} escuchando en [{host}]:{port}", fg=typer.colors.GREEN)
        runtime = await _runtime(
            identity, cfg, endpoints=endpoints, seeds=_seeds(seed), listen=(host, port)
        )
        typer.echo(f"  dirección: {identity.hostname}")
        typer.echo(f"  anuncia:   {', '.join(endpoints)}")

        if relay:
            # §10: opt-in, always. Publishing the descriptor is what makes this
            # cell routable by other people.
            stored = await runtime.publisher.publish_relay(endpoints)
            typer.secho(f"✓ relevo opt-in: descriptor en {stored} celdas", fg=typer.colors.GREEN)
            runtime.spawn(runtime.publisher.renew_forever(endpoints=endpoints))
        else:
            typer.echo("  relevo: desactivado (--relay para prestar ancho de banda)")

        beacon = None
        if lan:
            from nullcell.discovery.mdns import Beacon

            beacon = Beacon(identity, endpoints, on_peer=runtime.node.table.add)
            await beacon.start()
            typer.echo("  baliza LAN activa")

        panel = None
        if deployment is not None:
            from nullcell.gateway.control import ControlPanel

            panel = ControlPanel(runtime, home=config.home(), deployment=deployment)
            await panel.restore()
            runtime.spawn(panel.run_forever())
            estado = panel.describe().get("portal")
            if estado:
                typer.secho(
                    f"✓ portal {estado['state']}: {estado['folder']}", fg=typer.colors.GREEN
                )
                typer.echo(f"  dirección: {estado['address']}")
                if estado["state"] != "en línea":
                    typer.echo("  sale a la red solo en cuanto haya relevos suficientes (§7)")
            else:
                typer.secho("⚠  el despliegue no declara ningún portal", fg=typer.colors.YELLOW)

        local_ui = None
        if hud:
            from nullcell.gateway.events import EventBus
            from nullcell.gateway.localui import LocalUI

            bus = EventBus()
            # The HUD is also el Oráculo (§9), so it needs the index.
            runtime.open_index()
            await runtime.refresh_portals()
            runtime.spawn(runtime.keep_portals_fresh())
            local_ui = LocalUI(
                runtime.status, bus, port=hud_port, search=runtime.search, control=panel
            )
            url = await local_ui.start()
            bus.emit("node.up", f"{identity.short} escuchando en [{host}]:{port}")
            typer.secho(f"  HUD: {url}", fg=typer.colors.CYAN)

        runtime.spawn(pex.run_periodically(runtime.node))
        runtime.spawn(runtime.keep_learning())
        try:
            while True:
                await asyncio.sleep(30)
                status = runtime.status()
                typer.echo(
                    f"  [{status['peers']} celdas · {status['relays']} relevos usables · "
                    f"{status['legs']} circuitos servidos] {status['phase']}"
                )
        finally:
            if local_ui is not None:
                await local_ui.close()
            if beacon is not None:
                beacon.stop()
            save_peers(cfg.path_peers, runtime.node.table.all_nodes())
            await runtime.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        typer.echo("\nparado")


@app.command("publish")
def cmd_publish(
    name: Annotated[str | None, typer.Argument(help="Nombre legible a registrar")] = None,
    announce: Annotated[str | None, typer.Option("--announce")] = None,
    seed: Annotated[list[str] | None, typer.Option("--seed")] = None,
) -> None:
    """Publica el descriptor de esta celda, y opcionalmente un nombre."""
    identity, cfg = _load_identity()

    async def run() -> None:
        runtime = await _runtime(
            identity,
            cfg,
            endpoints=[announce] if announce else None,
            seeds=_seeds(seed),
            anonymous=True,
        )
        try:
            if announce:
                typer.echo("resolviendo PoW del descriptor de relevo…")
                count = await runtime.publisher.publish_relay([announce])
                typer.secho(f"✓ descriptor en {count} celdas", fg=typer.colors.GREEN)
            if name:
                typer.echo(f"resolviendo PoW de {name}.null (el más caro, §10)…")
                count = await runtime.publisher.publish_name(name)
                if count:
                    typer.secho(f"✓ {name}.null en {count} celdas", fg=typer.colors.GREEN)
                else:
                    typer.secho(
                        f"✗ {name}.null no fue aceptado: puede estar ya registrado (§8)",
                        fg=typer.colors.YELLOW,
                    )
        finally:
            await runtime.close()

    asyncio.run(run())


@app.command("resolve")
def cmd_resolve(
    target: Annotated[str, typer.Argument(help="Nombre o dirección .null")],
    seed: Annotated[list[str] | None, typer.Option("--seed")] = None,
    private: Annotated[bool, typer.Option("--private", help="Servicio privado (§6)")] = False,
) -> None:
    """Resuelve un nombre o dirección a sus puntos de enlace."""
    identity, cfg = _load_identity()
    from nullcell.services.resolver import ResolveError

    async def run() -> None:
        runtime = await _runtime(identity, cfg, seeds=_seeds(seed), anonymous=True)
        try:
            typer.echo(f"resolviendo {target}…")
            try:
                found = await runtime.resolver.resolve(target, private=private)
            except ResolveError as exc:
                typer.secho(f"✗ {exc}", fg=typer.colors.YELLOW, err=True)
                raise typer.Exit(1) from exc
            typer.echo(f"  celda        {found.nid}")
            typer.echo(f"  tipo         {found.kind}")
            if found.title:
                typer.echo(f"  título       {found.title}")
            if found.desc:
                typer.echo(f"  descripción  {found.desc}")
            if found.tags:
                typer.echo(f"  etiquetas    {', '.join(found.tags)}")
            typer.echo(f"  enlaces      {', '.join(p.short for p in found.intro_points)}")
        finally:
            await runtime.close()

    asyncio.run(run())


# ------------------------------------------------------------------- M5/M6: web
@app.command("host")
def cmd_host(
    directory: Annotated[str, typer.Argument(help="Carpeta del sitio estático")],
    announce: Annotated[str | None, typer.Option("--announce")] = None,
    seed: Annotated[list[str] | None, typer.Option("--seed")] = None,
    name: Annotated[str | None, typer.Option("--name", help="Nombre legible a registrar")] = None,
    title: Annotated[str, typer.Option("--title")] = "",
    desc: Annotated[str, typer.Option("--desc")] = "",
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
    private: Annotated[bool, typer.Option("--private", help="Servicio privado (§6)")] = False,
    index: Annotated[
        bool, typer.Option("--index/--no-index", help="Indexar en el buscador")
    ] = True,
    wsgi: Annotated[
        str | None, typer.Option("--wsgi", help="app:application en lugar de una carpeta")
    ] = None,
) -> None:
    """Publica una carpeta o una app WSGI como portal, sin abrir puertos (§8)."""
    identity, cfg = _load_identity()
    root = Path(directory).expanduser().resolve()
    if wsgi is None and not root.is_dir():
        typer.secho(f"✗ {root} no es una carpeta", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    from nullcell.host.site import SiteHost, static_app, wsgi_app
    from nullcell.onion.rendezvous_msgs import INTRO_POINTS
    from nullcell.onion.service import HiddenService
    from nullcell.services.descriptor import ServiceDescriptor

    if wsgi is not None:
        try:
            application = wsgi_app(wsgi)
        except (ValueError, ImportError, AttributeError) as exc:
            typer.secho(f"✗ --wsgi {wsgi}: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
    else:
        application = static_app(root)

    async def run() -> None:
        runtime = await _runtime(
            identity,
            cfg,
            endpoints=[announce] if announce else None,
            seeds=_seeds(seed),
            anonymous=True,
        )
        try:
            _require_paths(runtime)
            service = HiddenService(identity, runtime.circuits, multiplexed=True)
            site = SiteHost(application)
            await site.start()
            service.on_session = lambda session: setattr(session, "on_stream", site.serve)

            typer.echo(f"construyendo {INTRO_POINTS} circuitos de 3 saltos…")
            paths = []
            for _ in range(INTRO_POINTS):
                path = runtime.paths.build()
                paths.append(path)
            established = await service.establish(paths)
            if not established:
                typer.secho("✗ ningún punto de enlace se estableció", fg=typer.colors.RED)
                raise typer.Exit(1)

            descriptor = ServiceDescriptor(
                pub=identity.public_key,
                x25519=service.x25519_public,
                intro_points=service.intro_points,
                name=name or "",
                title=title,
                desc=desc,
                tags=list(tag or []),
            )
            typer.echo("publicando el descriptor…")
            stored = await runtime.publisher.publish_service(descriptor, private=private)

            typer.secho(f"✓ portal publicado: {identity.hostname}", fg=typer.colors.GREEN)
            typer.echo(f"  sirviendo:        {wsgi if wsgi else root}")
            typer.echo(f"  puntos de enlace: {established}/{INTRO_POINTS}")
            typer.echo(f"  descriptor en:    {stored} celdas")
            typer.echo(f"  clase:            {'privado (§6)' if private else 'portal público'}")
            typer.echo("  sin IP pública, sin UPnP, sin un puerto abierto (§0)")

            if name and not private:
                count = await runtime.publisher.publish_name(name)
                if count:
                    typer.secho(
                        f"✓ {name}.null registrado en {count} celdas", fg=typer.colors.GREEN
                    )
                else:
                    typer.secho(f"⚠  {name}.null ya está tomado (§8)", fg=typer.colors.YELLOW)
            if index and not private:
                words = await runtime.publisher.publish_keywords(descriptor)
                typer.echo(f"  indexado bajo {words} entradas de palabra clave (§8)")

            runtime.spawn(
                runtime.publisher.renew_forever(descriptor=descriptor, private=private, name=name)
            )
            typer.echo("\n  Ctrl-C para parar")
            while True:
                await asyncio.sleep(30)
                typer.echo(f"  [{site.stats['streams']} streams servidos]")
        finally:
            await runtime.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        typer.echo("\nparado")


@app.command("browse")
def cmd_browse(
    target: Annotated[str | None, typer.Argument(help="Nombre o dirección .null")] = None,
    seed: Annotated[list[str] | None, typer.Option("--seed")] = None,
    socks_port: Annotated[int, typer.Option("--socks-port")] = 9150,
    http_port: Annotated[
        int, typer.Option("--http-port", help="proxy HTTP para clientes sin SOCKS; 0 lo desactiva")
    ] = 9151,
    bind: Annotated[
        str,
        typer.Option(
            "--bind",
            help="dónde escuchan los proxies. Loopback, o la IP de una VPN "
            "para tus propios dispositivos. Una dirección pública se rechaza (docs/VPS.md)",
        ),
    ] = "127.0.0.1",
    launch: Annotated[bool, typer.Option("--launch/--no-launch")] = True,
) -> None:
    """Levanta el proxy y abre un navegador con perfil aislado (§8)."""
    identity, cfg = _load_identity()
    home = config.home()

    from nullcell.gateway.bind import BindRefused, check_bind, is_loopback

    try:
        bind = check_bind(bind)
    except BindRefused as refused:
        typer.secho(f"✗ {refused}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from None

    remote = not is_loopback(bind)
    if remote:
        # Said before anything starts, because it changes who is trusted.
        typer.secho(
            f"⚠  escuchando en {bind}, fuera de loopback.\n"
            "   Quien alcance esa dirección lee el texto claro de todo lo que pase por\n"
            "   aquí y sale a la red en nombre de este nodo. Que la VPN sea la única\n"
            "   vía hasta ella es tu parte del trato (docs/VPS.md).",
            fg=typer.colors.YELLOW,
            err=True,
        )
        launch = False  # no hay navegador que abrir en un servidor

    from nullcell.gateway.browser_profile import BrowserProfile
    from nullcell.gateway.localca import LocalCA
    from nullcell.gateway.socks5 import Socks5Proxy
    from nullcell.onion.service import connect_to_service

    ca = LocalCA(home / "ca")
    ca.load_or_create()
    if warning := ca.permissions_warning():
        typer.secho(f"⚠  {warning}", fg=typer.colors.YELLOW, err=True)

    profile = BrowserProfile(home / "browser-profile", socks_port=socks_port)
    profile.write()
    typer.secho(f"✓ perfil aislado en {profile.directory}", fg=typer.colors.GREEN)
    typer.echo(f"  {profile.install_ca(ca.cert_path)}")

    async def run() -> None:
        runtime = await _runtime(identity, cfg, seeds=_seeds(seed), anonymous=True)
        try:
            _require_paths(runtime)

            async def open_session(bare_nid: str):
                """Resolve, then build our own paths for both circuits (§6, §7)."""
                found = await runtime.resolver.resolve(bare_nid)
                intro = found.intro_points[0]
                return await connect_to_service(
                    runtime.circuits,
                    service_pub=found.pub,
                    service_x25519=found.x25519,
                    intro_point=intro,
                    rendezvous_path=runtime.paths.build(),
                    intro_path=runtime.paths.build(ending_at=intro.as_hop()),
                    multiplexed=True,
                )

            proxy = Socks5Proxy(open_session, ca, host=bind, port=socks_port)
            port = await proxy.start()
            typer.secho(f"✓ SOCKS5 en {bind}:{port}", fg=typer.colors.GREEN)
            typer.echo("  resolución de nombres remota obligatoria (§8)")

            http = None
            if http_port:
                from nullcell.gateway.http_proxy import HttpProxy

                http = HttpProxy(open_session, ca, host=bind, port=http_port)
                typer.echo(f"✓ proxy HTTP en {bind}:{await http.start()} (sólo CONNECT, §8)")
            typer.echo(
                f"  guards fijados: {len(runtime.guards)} · relevos usables: "
                f"{len(runtime.directory.usable(min_pow_bits=runtime.paths.min_pow_bits))}"
            )

            if remote:
                typer.echo(
                    f"\n  Para que otro dispositivo abra https://…{ids.SUFFIX} necesita esta CA:\n"
                    f"    {ca.cert_path}\n"
                    f"  Instalarla allí es system-wide: lee el apartado de riesgos de docs/VPS.md."
                )

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
                typer.echo(f"→ sin navegador. URL: {url}")

            runtime.spawn(runtime.keep_learning())
            typer.echo("\n  Ctrl-C para parar")
            while True:
                await asyncio.sleep(30)
                typer.echo(f"  [socks {proxy.stats}]")
        finally:
            if http is not None:
                await http.close()
            await proxy.close()
            await runtime.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        typer.echo("\nparado")


@app.command("update")
def cmd_update(
    check: Annotated[
        bool, typer.Option("--check/--no-check", help="preguntar por la última versión")
    ] = True,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="no preguntar antes de contactar")
    ] = False,
) -> None:
    """Dice si hay una versión nueva y cómo instalarla (§9, §12).

    **Nunca se comprueba solo.** No hay tarea de fondo ni comprobación al
    arrancar: una petición periódica a un servidor ajeno le diría a ese
    servidor que hay un nodo Nullcell en esta IP, todos los días. Que haya que
    escribir este comando *es* el consentimiento.
    """
    import sys

    from nullcell.cli import updates

    installed = _version()
    typer.echo(f"instalada: {installed}")

    if not check:
        typer.echo(f"para actualizar:\n  {updates.upgrade_command(sys.executable)}")
        return

    typer.secho(
        f"⚠  Esto contacta {updates.HOST}, que verá tu dirección IP. Es la única "
        "petición a internet que hace Nullcell, y sólo cuando la pides.",
        fg=typer.colors.YELLOW,
    )
    if not yes and not typer.confirm("¿Preguntar por la última versión?", default=True):
        typer.echo(f"para actualizar a mano:\n  {updates.upgrade_command(sys.executable)}")
        return

    try:
        latest = updates.fetch_latest()
    except updates.NoReleases as todavia:
        typer.secho(f"· {todavia}", fg=typer.colors.YELLOW)
        typer.echo(
            f"\npara ponerte al día con el código:\n  {updates.upgrade_command(sys.executable)}"
        )
        return
    except ConnectionError as exc:
        typer.secho(f"✗ {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None

    verdict = updates.compare(installed, latest.version)
    if verdict < 0:
        typer.secho(f"✓ hay una versión nueva: {latest.version}", fg=typer.colors.GREEN)
        if latest.url:
            typer.echo(f"  {latest.url}")
        typer.echo(f"\npara actualizar:\n  {updates.upgrade_command(sys.executable)}")
    elif verdict > 0:
        # Correr desde el repositorio es normal, y decir «estás al día» ahí
        # sería engañoso.
        typer.echo(f"tu copia ({installed}) es más nueva que la publicada ({latest.version})")
    else:
        typer.secho("✓ estás al día", fg=typer.colors.GREEN)


@app.command("invite")
def cmd_invite(
    announce: Annotated[
        str, typer.Option("--announce", "-a", help="host:puerto público de esta celda")
    ],
    portal: Annotated[
        str | None, typer.Option("--portal", help="nombre .null al que invitas")
    ] = None,
    extra: Annotated[
        list[str] | None,
        typer.Option("--seed", help="otra semilla a incluir; repetible"),
    ] = None,
) -> None:
    """Imprime una invitación: una sola cosa que copiar para entrar (§7).

    Lleva cómo entrar y, si lo pides, a qué portal. No es una credencial: no
    firma nada y no avala nada, y todo lo aprendido a través de ella se
    verifica por separado.
    """
    from nullcell.cli.relay import seed_line
    from nullcell.core import invite

    identity, _ = _load_identity()
    seeds = [seed_line(identity, announce), *(extra or [])]
    try:
        typer.echo(invite.build(seeds, portal=portal))
    except invite.InviteError as bad:
        typer.secho(f"✗ {bad}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from None


# ------------------------------------------------ M9: el escritorio (§9)
@app.command("desktop")
def cmd_desktop(
    port: Annotated[int, typer.Option("--port", "-p")] = 4433,
    host: Annotated[str, typer.Option("--host")] = "::",
    announce: Annotated[
        str | None, typer.Option("--announce", help="host:puerto público, para poder ser relevo")
    ] = None,
    seed: Annotated[list[str] | None, typer.Option("--seed", help="<nid>@host:puerto")] = None,
    hud_port: Annotated[int, typer.Option("--hud-port")] = 9180,
    lan: Annotated[bool, typer.Option("--lan", help="baliza multicast en la LAN")] = False,
    config_dir: Annotated[
        str | None,
        typer.Option(
            "--config",
            "-c",
            help="carpeta de despliegue portátil: portales, nombres e identidad",
        ),
    ] = None,
    log_level: Annotated[str, typer.Option("--log-level")] = "warning",
) -> None:
    """Icono en la barra de menús, panel con interruptores y navegador (§9).

    Es el mismo nodo de `ncell node`, sin terminal: cerrar una ventana no lo
    mata y los interruptores son estado de un proceso vivo en lugar de flags
    que hay que recordar entre arranques.
    """
    from nullcell.cli.logging_setup import setup as setup_logging

    setup_logging(log_level)
    try:
        from nullcell.desktop.app import run
    except ImportError:
        from nullcell.desktop import MISSING_QT

        typer.secho(MISSING_QT, fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(2) from None

    raise typer.Exit(
        run(
            port=port,
            host=host,
            announce=announce,
            seeds=_seeds(seed),
            hud_port=hud_port,
            lan=lan,
            config=config_dir,
        )
    )


# ------------------------------------------------- M8: operar un relevo (§1, §10)
@app.command("relay")
def cmd_relay(
    announce: Annotated[
        str, typer.Option("--announce", "-a", help="host:puerto público de esta celda")
    ],
    port: Annotated[int, typer.Option("--port", "-p")] = 4433,
    host: Annotated[str, typer.Option("--host")] = "::",
    seed: Annotated[list[str] | None, typer.Option("--seed", help="<nid>@host:puerto")] = None,
    max_mbps: Annotated[int, typer.Option("--max-mbps", help="0 = sin límite declarado")] = 0,
    max_circuits: Annotated[
        int, typer.Option("--max-circuits", help="tope de circuitos simultáneos")
    ] = 512,
    drain_timeout: Annotated[float, typer.Option("--drain-timeout")] = 30.0,
    lan: Annotated[bool, typer.Option("--lan", help="baliza multicast en la LAN")] = False,
    log_level: Annotated[str, typer.Option("--log-level")] = "info",
    print_seed: Annotated[
        bool, typer.Option("--print-seed", help="imprime la línea de bootstrap y sale")
    ] = False,
) -> None:
    """Presta ancho de banda a la red. El comando para un servidor (§1, §10).

    Opt-in y acotado: un relevo no puede leer lo que reenvía, así que sólo
    puede negarse por volumen. `systemctl stop` o `docker stop` envían SIGTERM,
    que vacía los circuitos vivos antes de salir en lugar de tirarlos de golpe.
    """
    from nullcell.cli.logging_setup import setup as setup_logging
    from nullcell.cli.relay import RelayRunner, seed_line

    setup_logging(log_level)
    identity, cfg = _load_identity()

    if print_seed:
        typer.echo(seed_line(identity, announce))
        raise typer.Exit(0)

    if warning := config.cloud_synced_warning():
        typer.secho(f"⚠  {warning}", fg=typer.colors.YELLOW, err=True)

    runner = RelayRunner(
        host=host,
        port=port,
        announce=announce,
        seeds=seed,
        max_mbps=max_mbps,
        max_circuits=max_circuits,
        drain_timeout=drain_timeout,
        lan=lan,
    )
    try:
        raise typer.Exit(asyncio.run(runner.run(identity)))
    except KeyboardInterrupt:  # pragma: no cover - handled by the signal path
        raise typer.Exit(0) from None
