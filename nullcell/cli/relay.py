"""``ncell relay`` — the command that makes a network exist.

A Nullcell network is relays. §1 says it plainly: *"La red necesita relays
públicos voluntarios. Un usuario doméstico puede navegar y publicar sin abrir
nada, pero alguien tiene que prestar ancho de banda alcanzable. Sin relays no
hay red."* So this is the one command meant to run for months on a machine
nobody is watching, and it is written for that:

* **Graceful shutdown.** ``SIGTERM`` — what ``systemctl stop`` and ``docker
  stop`` send — stops accepting new circuits and lets the live ones finish
  (§10's ``drain``). Dropping hundreds of circuits at once is both a bad
  experience and an observable event on the network.
* **Opt-in and bounded.** §10: the operator sets limits, and a relay cannot
  decline on content because it cannot read what it carries. Limits are on
  volume only.
* **It prints its own seed line.** The first relay on a network has nobody to
  bootstrap from, so it has to be able to hand out the line others put in
  their ``bootstrap.json``. Without that, a new network cannot start.
* **Survives restarts.** Identity, guards and peers are on disk, so a restart
  rejoins rather than becoming a new node.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import time
from pathlib import Path
from typing import Final

from nullcell.core import config, ids
from nullcell.core.identity import Identity

__all__ = ["RelayRunner", "seed_line"]

#: How long a drain waits for live circuits before exiting anyway.
DRAIN_TIMEOUT: Final[float] = 30.0
_STATUS_INTERVAL: Final[float] = 60.0

log = logging.getLogger("nullcell.relay")


def seed_line(identity: Identity, endpoint: str) -> str:
    """The line another operator puts in ``bootstrap.json``.

    ``<nid>@host:port``. It is not a secret and it is not a credential: §7 is
    explicit that seeds are *introductions*, nothing more. They sign nothing
    and vouch for nothing, and every record learned through them is verified
    independently.
    """
    return f"{identity.nid}@{endpoint}"


class RelayRunner:
    """A relay, from start to orderly stop."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        announce: str,
        seeds: list[str] | None = None,
        max_mbps: int = 0,
        max_circuits: int | None = None,
        drain_timeout: float = DRAIN_TIMEOUT,
        home: Path | None = None,
        lan: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.announce = announce
        self.seeds = seeds or []
        self.max_mbps = max_mbps
        self.max_circuits = max_circuits
        self.drain_timeout = drain_timeout
        self.home = home or config.home()
        self.lan = lan
        self.started_at = time.monotonic()
        self._stopping = asyncio.Event()
        self.runtime = None
        self.beacon = None

    # ------------------------------------------------------------------- run
    async def run(self, identity: Identity) -> int:
        """Serve until a signal arrives, then drain. Returns an exit code."""
        from nullcell.cli.runtime import Runtime
        from nullcell.discovery import pex
        from nullcell.discovery.bootstrap import load_bootstrap, load_peers, save_peers

        endpoints = [self.announce]
        runtime = Runtime.assemble(identity, self.home, endpoints=endpoints)
        self.runtime = runtime
        if self.max_circuits is not None:
            runtime.relay.max_circuits = self.max_circuits

        await runtime.listen(self.host, self.port)
        log.info("listening on [%s]:%d", self.host, self.port, extra={"public": True})
        log.info("address %s", identity.hostname)
        # Marked public: it is this relay's own address, which is in the
        # descriptor anyway, and it is the one line a new operator needs.
        log.info(
            "seed line for others: %s",
            seed_line(identity, self.announce),
            extra={"public": True},
        )

        self._install_signals()

        known = load_bootstrap() + load_peers(runtime.resolver.hosts_file.with_name("peers.db"))
        known += [_parse_seed(spec) for spec in self.seeds]
        known = [node for node in known if node is not None]
        if known:
            count = await runtime.node.bootstrap(known)
            log.info("joined the network: %d cells known", count)
            await runtime.learn_relays()
        else:
            log.warning(
                "no seeds: this relay is alone. That is correct for the first "
                "relay on a network — hand out the seed line above"
            )

        # §10: the descriptor is what makes this relay routable by others.
        try:
            stored = await runtime.publisher.publish_relay(
                endpoints, bandwidth=self.max_mbps * 125_000
            )
            log.info("relay descriptor published to %d cells", stored)
        except Exception as exc:
            log.warning("could not publish the descriptor yet: %s", exc)

        runtime.spawn(runtime.publisher.renew_forever(endpoints=endpoints))
        runtime.spawn(pex.run_periodically(runtime.node))
        runtime.spawn(runtime.keep_learning())

        if self.lan:
            from nullcell.discovery.mdns import Beacon

            self.beacon = Beacon(identity, endpoints, on_peer=runtime.node.table.add)
            await self.beacon.start()
            log.info("LAN beacon on (announces this machine on the local network)")

        try:
            await self._serve()
        finally:
            await self._drain()
            save_peers(self.home / "peers.db", runtime.node.table.all_nodes())
            if self.beacon is not None:
                self.beacon.stop()
            await runtime.close()
        return 0

    async def _serve(self) -> None:
        while not self._stopping.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=_STATUS_INTERVAL)
            if not self._stopping.is_set():
                log.info("%s", self.status_line())

    # -------------------------------------------------------------- shutdown
    def _install_signals(self) -> None:
        loop = asyncio.get_running_loop()
        for name in ("SIGTERM", "SIGINT"):
            received = getattr(signal, name, None)
            if received is None:  # pragma: no cover - Windows
                continue
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(received, self._on_signal, name)

    def _on_signal(self, name: str) -> None:
        if self._stopping.is_set():
            log.warning("%s again: exiting now, dropping live circuits", name)
            raise SystemExit(1)
        log.info("%s: draining, up to %.0f s", name, self.drain_timeout)
        self._stopping.set()

    async def _drain(self) -> None:
        """§10: stop accepting new circuits, let the live ones finish."""
        if self.runtime is None:
            return
        self.runtime.relay.drain()
        deadline = time.monotonic() + self.drain_timeout
        while self.runtime.relay.legs and time.monotonic() < deadline:
            log.info("draining: %d circuits still live", len(self.runtime.relay.legs))
            await asyncio.sleep(1.0)
        remaining = len(self.runtime.relay.legs)
        if remaining:
            log.warning("drain timed out with %d circuits live", remaining)
        else:
            log.info("drained cleanly")

    # ---------------------------------------------------------------- status
    def status_line(self) -> str:
        assert self.runtime is not None
        stats = self.runtime.relay.stats
        uptime = time.monotonic() - self.started_at
        return (
            f"up {uptime / 3600:.1f} h · {len(self.runtime.relay.legs)} circuits · "
            f"{stats['forwarded']} frames forwarded · "
            f"{stats['created']} created · {stats['extended']} extended · "
            f"{stats['refused']} refused · {len(self.runtime.node.table)} cells known"
        )


def _parse_seed(spec: str):
    """``<nid>@host:port`` -> Node, or None with a reason logged."""
    from nullcell.dht.routing_table import Node

    nid, _, endpoint = spec.partition("@")
    if not endpoint:
        log.warning("ignoring seed %r: expected <nid>@host:port", spec)
        return None
    try:
        address = ids.decode_nid(nid)
    except ids.AddressError as exc:
        log.warning("ignoring seed %s: %s", nid[:12], exc)
        return None
    return Node(pub=address.pub, endpoints=[endpoint])
