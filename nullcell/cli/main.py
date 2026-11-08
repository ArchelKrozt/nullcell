"""``ncell`` — Nullcell command line. M0/M1 subset."""

from __future__ import annotations

import asyncio
import contextlib
import statistics
import time
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
