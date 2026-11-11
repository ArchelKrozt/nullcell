"""The cell, running behind the window — §9.

Qt owns the main thread and asyncio owns the node, and neither will give it
up. So the node gets its own thread with its own event loop, and the interface
talks to it the way §9 already intended: over the local HTTP API, on loopback,
with a token. That is not a workaround — it is the same boundary the HUD
already crossed, which is why no new trust surface appears here.

What this buys, and the reason the desktop exists at all: the node's life is
no longer tied to a terminal. Closing a window does not kill it, and the
operator's choices (sharing bandwidth, a published folder) are state in a
running process instead of flags remembered between restarts.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__all__ = ["NodeThread"]

PANEL_ROOT = Path(__file__).resolve().parent / "panel"


class NodeThread:
    """Runs a full cell on its own event loop, in its own thread."""

    def __init__(
        self,
        *,
        port: int = 4433,
        host: str = "::",
        hud_port: int = 9180,
        announce: str | None = None,
        seeds: list[str] | None = None,
        lan: bool = False,
        config: str | None = None,
        hops: int = 3,
    ) -> None:
        self.port = port
        self.host = host
        self.hud_port = hud_port
        self.announce = announce
        self.seeds = seeds or []
        self.lan = lan
        self.config = config
        self.hops = hops

        self.url: str | None = None
        self.reach: str = "unknown"
        self.deployment_error: str | None = None
        self._deployment_cache: Any = False  # False = aún no se ha buscado
        self.peers_path: Path | None = None
        self.token: str | None = None
        self.error: BaseException | None = None
        self.identity: Any = None

        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._thread = threading.Thread(target=self._run, name="nullcell-node", daemon=True)

    # ------------------------------------------------------------- lifecycle
    def start(self, timeout: float = 30.0) -> str:
        """Start the cell and return the panel URL, or raise what went wrong."""
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError(f"el nodo no arrancó en {timeout:.0f} s")
        if self.error is not None:
            raise self.error
        assert self.url is not None
        return self.url

    def request_stop(self) -> None:
        """Pedir la parada y volver **inmediatamente**.

        La interfaz no puede esperar aquí: `stop()` hace `join`, y hacer
        `join` en el hilo de Qt congela la ventana el plazo entero. Con el
        navegador abierto eso eran diez segundos de aplicación muerta al
        pulsar «Salir».
        """
        if self._loop is None or self._stop is None:
            return
        self._loop.call_soon_threadsafe(self._stop.set)

    def stop(self, timeout: float = 10.0) -> None:
        """Pedir la parada y esperarla. Para la terminal, no para la interfaz."""
        self.request_stop()
        self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    # ----------------------------------------------------------------- inner
    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as exc:  # reported to the UI, not swallowed
            log.exception("el nodo se detuvo")
            self.error = exc
        finally:
            self._ready.set()

    async def _serve(self) -> None:
        from nullcell.cli.runtime import Runtime
        from nullcell.core import config
        from nullcell.core import reach as reach_mod
        from nullcell.core.identity import Identity
        from nullcell.discovery import pex
        from nullcell.gateway.control import ControlPanel
        from nullcell.gateway.events import EventBus
        from nullcell.gateway.localui import LocalUI

        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()

        home = config.home()
        cfg = config.load()
        # `load_or_create` returns (identity, created): unpacked here because
        # taking the tuple as an identity fails much later, inside the first
        # handshake, with a message about neither identities nor tuples.
        key_path = cfg.path_identity
        deployment = self._deployment()
        if deployment is not None:
            carried = deployment.identity_path
            if carried is not None:
                # The address *is* the key (§3). A deployment that carries its
                # own identity keeps its .null name when it moves; one that
                # does not becomes a different cell on the new machine.
                key_path = carried
        identity, created = Identity.load_or_create(key_path)
        if created:
            log.info("identidad nueva en %s", key_path)
        self.identity = identity
        if warning := config.cloud_synced_warning():
            log.warning("%s", warning)
        if perm := identity.check_permissions(key_path):
            log.warning("%s", perm)
        # Anunciar 127.0.0.1 a ciegas era peor que no anunciar nada: el panel
        # decía que sí podías compartir, y al activarlo publicabas un relevo
        # que apunta a tu propio loopback —inalcanzable para todos y basura en
        # el directorio de los demás— además de ofrecerte copiar una semilla
        # que nadie podía usar.
        endpoint, self.reach = reach_mod.best_endpoint(self.port, announce=self.announce)
        endpoints = [endpoint]

        runtime = Runtime.assemble(identity, home, endpoints=endpoints, hops=self.hops)
        # Publicado de inmediato, no al final: el guardado de pares ocurre
        # justo después del bootstrap y lo leía de aquí, así que asignarlo
        # tarde hacía que ese guardado fallara —en silencio, porque el
        # `except` que lo protege de morir al salir también se tragaba esto.
        self.runtime = runtime
        await runtime.listen(self.host, self.port)
        # El escritorio no cargaba bootstrap.json ni los pares guardados, y
        # nunca los guardaba: pegabas una semilla, encontrabas celdas, cerrabas
        # y al volver a abrir empezaba de cero. Es la diferencia entre "entras
        # una vez" y "entras una vez y ya".
        from nullcell.discovery.bootstrap import load_bootstrap, load_peers

        self.peers_path = cfg.path_peers
        bus = EventBus()
        runtime.open_index()
        await runtime.refresh_portals()
        deployment = self._deployment()
        panel = ControlPanel(runtime, home=home, deployment=deployment)
        ui = LocalUI(
            runtime.status,
            bus,
            port=self.hud_port,
            search=runtime.search,
            control=panel,
            panel_root=PANEL_ROOT,
        )
        await ui.start()
        self.token = ui.token
        self.url = f"http://127.0.0.1:{self.hud_port}/panel?token={ui.token}"
        self.panel = panel
        self.runtime = runtime
        # Para que el panel pueda pedir que se recuerde lo aprendido.
        runtime.remember_peers = self._save_peers
        bus.emit("node.up", f"{identity.short} en [{self.host}]:{self.port}")
        # `extra={"public": True}` for the same reason the relay's seed line
        # needed it: §12's filter drops any long line with an IP and a port in
        # it, and this one is our own loopback panel, not somebody else's
        # address. Without the marker the URL the operator most needs is the
        # one line the log refuses to print — which is exactly how it was
        # found, by a log that came out empty.
        log.info("panel en %s", self.url, extra={"public": True})

        # Las intenciones guardadas vuelven, y el nodo las persigue solo: con
        # una red joven nada de esto puede cumplirse al arrancar, y obligar a
        # la persona a volver a pulsar el botón cuando aparezcan relevos es
        # pedirle que vigile lo que el programa puede vigilar.
        await panel.restore()
        # **Después** de levantar la interfaz, no antes. Entrar a la red puede
        # tardar: una semilla muerta se reintenta durante segundos, y con el
        # bootstrap por delante la ventana no aparecía hasta terminarlo. Para
        # quien mira, eso es un programa colgado que además «ha perdido» sus
        # semillas, porque todavía no ha leído ninguna.
        runtime.spawn(self._join_network(load_bootstrap, load_peers))
        runtime.spawn(panel.run_forever())
        runtime.spawn(pex.run_periodically(runtime.node))
        runtime.spawn(runtime.keep_learning())
        runtime.spawn(runtime.keep_portals_fresh())
        self._ready.set()

        runtime.spawn(self._remember_peers())
        try:
            await self._stop.wait()
        finally:
            self._save_peers()
            # Acotado: cerrar circuitos y servidores puede quedarse esperando
            # a algo que ya no va a llegar, y entonces el hilo no termina
            # nunca. Un apagado que tarda mucho es un apagado que no ocurre.
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._shutdown(ui, runtime), timeout=5.0)

    @staticmethod
    async def _shutdown(ui: Any, runtime: Any) -> None:
        with contextlib.suppress(Exception):
            await ui.close()
        with contextlib.suppress(Exception):
            await runtime.close()

    async def _join_network(self, load_bootstrap, load_peers) -> None:
        """Entrar a la red, en segundo plano."""
        known = list(load_bootstrap()) + list(load_peers(self.peers_path))
        if not (known or self.seeds):
            return
        try:
            found = await self.runtime.node.bootstrap(known + list(self.seeds))
            log.info("bootstrap: %d celdas conocidas", found)
            await self.runtime.learn_relays()
        except Exception as exc:  # noqa: BLE001 - la interfaz ya está viva
            log.warning("no se pudo entrar a la red: %s", exc)
            return
        # Guardado aquí, no sólo al salir ni cada minuto: una app de escritorio
        # no siempre muere por donde uno espera.
        self._save_peers()

    def _save_peers(self, path: Path | None = None) -> None:
        """Write down who answered, so the next start needs no seed."""
        from nullcell.discovery.bootstrap import save_peers

        path = path or self.peers_path
        if path is None:
            return
        runtime = getattr(self, "runtime", None)
        if runtime is None:
            log.debug("todavía no hay nodo del que recordar pares")
            return
        try:
            nodes = [n for n in runtime.node.table.all_nodes() if n.endpoints]
        except Exception as exc:  # noqa: BLE001 - guardar nunca debe impedir salir
            log.warning("no se pudieron listar los pares: %s", exc)
            return
        if not nodes:
            return
        try:
            save_peers(path, nodes)
            log.info("%d celdas recordadas para el próximo arranque", len(nodes))
        except OSError as exc:
            log.warning("no se pudieron guardar los pares: %s", exc)

    async def _remember_peers(self, interval: float = 60.0) -> None:
        """Periodically, not only on exit: a crash should not cost the network."""
        while True:
            await asyncio.sleep(interval)
            self._save_peers()
            # Igual que los pares: un SIGTERM no pasa por el cierre ordenado.
            with contextlib.suppress(Exception):
                self.runtime.remember_observations()

    # ----------------------------------------------------------- despliegue
    def _deployment(self):
        """The deployment this cell runs, found once and kept.

        Three ways, in order: the folder the operator named, the one found by
        looking in the usual places, and none — which is a real answer, not a
        reason to invent one. The shell asks when it is none.
        """
        if self._deployment_cache is not False:
            return self._deployment_cache

        from nullcell.host.deployment import Deployment

        found = None
        try:
            found = Deployment.load(self.config) if self.config else Deployment.discover()
        except ValueError as exc:
            # A broken declaration is reported, never skipped: moving on
            # quietly would hide a typo in the file being edited right now.
            self.deployment_error = str(exc)
            log.error("%s", exc)

        if found is not None:
            for warning in found.warnings():
                log.warning("%s", warning)
            found.remember_location()
        self._deployment_cache = found
        return found

    # --------------------------------------------------------------- reaching
    async def open_session(self, bare_nid: str):
        """A multiplexed session to a service, over paths we chose (§6, §7).

        The same handshake `ncell browse` performs, kept here so the desktop
        does not grow a second way of reaching a service — two code paths that
        build circuits is how §7's guard pinning quietly stops applying to one
        of them.
        """
        from nullcell.onion.service import connect_to_service

        runtime = self.runtime
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

    # -------------------------------------------------------- thread-safe ops
    def call(self, coroutine_factory, timeout: float = 30.0):
        """Run a coroutine on the node's loop and wait for it, from Qt's thread."""
        if self._loop is None:
            raise RuntimeError("el nodo no está corriendo")
        future = asyncio.run_coroutine_threadsafe(coroutine_factory(), self._loop)
        return future.result(timeout)
