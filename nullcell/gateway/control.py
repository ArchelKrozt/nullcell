"""The actions the panel can take — §9, the control half.

Until M9 the HUD only *read*. Everything that changed the node was a flag
decided before it started, which is why sharing bandwidth meant stopping the
process and starting it again.

**Everything here works by intent, not by command.** "Publish this folder" is
not an instruction to publish right now; it is a standing wish that the node
makes true as soon as it can, and keeps true afterwards. That is not a
convenience: a fresh cell on a young network *cannot* build a circuit — §7
needs three relays at three operators, each observed for ten minutes — so a
panel whose buttons only work once the network is healthy is a panel that does
nothing on the day you install it. Declaring the intent and reconciling later
turns a dead button into a plan.

The state machine, for a portal:

    (nada) ─publish_folder()─► local ──relays appear──► en línea
                                 │                         │
                                 └── se ve en loopback,  se ve desde la red,
                                     nadie más lo ve      con descriptor y
                                                          puntos de enlace

The local step is not a simulation. It is the same `aiohttp` application the
real portal serves, on loopback, so the work of making a site is useful before
there is anyone to show it to — and the panel says plainly which of the two
states it is in, because telling someone they are published when nobody can
reach them is the one thing this must never do.

Security, for the routes that reach this: see `localui._guard`. A state-changing
request needs the token in a **header**, which a cross-site form cannot set.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Final

from nullcell.core import ids
from nullcell.core import reach as reach_mod

__all__ = ["Action", "ControlError", "ControlPanel"]

log = logging.getLogger(__name__)

#: Methods that change something and therefore need the header token.
MUTATING: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: How often the node tries to make the declared intents true.
RECONCILE_EVERY: Final[float] = 20.0

Action = Any


class ControlError(Exception):
    """An action was refused. The message is shown to the operator."""


class ControlPanel:
    """What a running cell lets its own panel do, and remember."""

    def __init__(self, runtime: Any, *, home: Path | None = None, deployment: Any = None) -> None:
        self.runtime = runtime
        self.home = Path(home) if home else Path.home() / ".nullcell"
        #: A portable deployment folder, if one was given. When present it is
        #: the source of truth instead of `~/.nullcell/desktop.json`, which is
        #: what makes a move to another machine a copy rather than a rebuild.
        self.deployment = deployment
        self.state_path = self.home / "desktop.json"

        self._relay_task: Any = None
        self._relay_endpoints: list[str] = []
        #: The operator asked to share, whether or not it has taken effect.
        self.wants_sharing = False

        #: The portal, as intent plus whatever has been achieved so far.
        self.portal: dict[str, Any] | None = None
        self._site: Any = None
        self._preview: Any = None
        self._service: Any = None
        self._portal_task: Any = None
        self._last_error: str | None = None

    # -------------------------------------------------------------- memoria
    def remember(self) -> None:
        """Persist the intents, so a restart resumes instead of forgetting.

        Only the *wishes* are stored, never anything derived: descriptors,
        intro points and circuit state are rebuilt from the network, and
        writing them down would mean restoring a view of the network that was
        true once.
        """
        if self.deployment is not None:
            self._remember_in_deployment()
            return
        wish = {
            "sharing": self.wants_sharing,
            "portal": (
                {k: self.portal[k] for k in ("folder", "name") if self.portal.get(k)}
                if self.portal
                else None
            ),
        }
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(wish, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("no se pudo guardar %s: %s", self.state_path, exc)

    def _remember_in_deployment(self) -> None:
        """Write the wishes back into the portable folder."""
        import os

        from nullcell.host.deployment import Portal

        deployment = self.deployment
        deployment.sharing = self.wants_sharing
        if self.portal is None:
            deployment.portals = []
        else:
            folder = Path(self.portal["folder"])
            # Relative to the deployment, `..` included. `Path.relative_to`
            # refuses to walk upwards, so it turned a hand-written
            # `../null-mi-web` into an absolute path on the first save — which
            # is precisely the thing `warnings()` tells people not to write,
            # written by the program itself.
            try:
                stored = os.path.relpath(folder, deployment.root)
            except ValueError:  # otra unidad en Windows
                stored = str(folder)

            # Keep whatever the file already said. A save that only knows the
            # folder and the name must not delete the title, the description
            # and the tags: an update that does not carry a field is not an
            # instruction to erase it.
            previous = next(
                (
                    q
                    for q in deployment.portals
                    if deployment.folder_for(q).resolve() == folder.resolve()
                ),
                None,
            )
            portal = previous or Portal(folder=stored)
            portal.folder = stored
            portal.name = self.portal.get("name") or (previous.name if previous else None)
            deployment.portals = [portal]
        try:
            deployment.save()
        except OSError as exc:
            log.warning("no se pudo guardar %s: %s", deployment.config_path, exc)

    async def restore(self) -> None:
        """Pick the intents back up. Never fatal: a bad file is not a reason
        to refuse to start, it is a reason to start empty and say so."""
        if self.deployment is not None:
            await self._restore_from_deployment()
            return
        try:
            wish = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("%s ilegible, se empieza en limpio: %s", self.state_path, exc)
            return

        if wish.get("sharing"):
            self.wants_sharing = True
        portal = wish.get("portal")
        if isinstance(portal, dict) and portal.get("folder"):
            with contextlib.suppress(ControlError):
                await self.publish_folder(portal["folder"], name=portal.get("name"), save=False)

    async def _restore_from_deployment(self) -> None:
        deployment = self.deployment
        for warning in deployment.warnings():
            log.warning("%s", warning)
            self._last_error = warning
        self.wants_sharing = bool(deployment.sharing)
        for portal in deployment.portals:
            with contextlib.suppress(ControlError):
                await self.publish_folder(
                    deployment.folder_for(portal), name=portal.name, save=False
                )
            break  # one cell, one address, one portal (§3)

    # ------------------------------------------------------------ bandwidth
    @property
    def sharing(self) -> bool:
        return self._relay_task is not None and not self._relay_task.done()

    async def set_sharing(self, on: bool, *, endpoints: list[str] | None = None) -> dict[str, Any]:
        """Turn bandwidth sharing on or off (§10: opt-in, always).

        Honest about the asymmetry: switching **on** takes effect once the
        descriptor is stored, but switching **off** only stops *announcing*. A
        descriptor already in the DHT stays until its TTL runs out, and open
        circuits are drained, not cut.
        """
        self.wants_sharing = on
        self.remember()

        if not on:
            if not self.sharing:
                return {"sharing": False, "changed": False, "detail": "no estaba compartiendo"}
            self._relay_task.cancel()
            self._relay_task = None
            ttl = getattr(self.runtime.publisher, "ttl", None)
            detail = "se deja de anunciar"
            if ttl:
                detail += f"; el descriptor ya publicado caduca en ~{int(ttl) // 60} min"
            detail += ". Los circuitos abiertos se vacían, no se cortan"
            return {"sharing": False, "changed": True, "detail": detail}

        if self.sharing:
            return {"sharing": True, "changed": False, "detail": "ya estaba compartiendo"}

        targets = endpoints or self._reachable_endpoints()
        if not targets:
            raise ControlError(
                "esta celda no sabe por dónde es alcanzable, así que no puede ofrecerse "
                "como relevo. Arráncala con --announce y tu IP o nombre público"
            )
        stored = await self.runtime.publisher.publish_relay(targets)
        self._relay_endpoints = list(targets)
        self._relay_task = self.runtime.spawn(
            self.runtime.publisher.renew_forever(endpoints=targets)
        )
        detail = f"descriptor publicado en {stored} celdas"
        if stored == 0:
            detail = (
                "nadie a quien anunciarlo todavía: esta celda no conoce otras. "
                "Se reintenta solo en cuanto aparezca alguna"
            )
        return {"sharing": True, "changed": True, "stored_on": stored, "detail": detail}

    def _reachable_endpoints(self) -> list[str]:
        """Endpoints somebody **else** could dial.

        Loopback is filtered out rather than reported: a relay descriptor
        pointing at 127.0.0.1 is an unreachable entry in everyone's directory
        and a seed line nobody can use, so offering the switch at all would be
        offering harm.
        """
        return [
            e
            for e in getattr(self.runtime.node, "endpoints", [])
            if e and reach_mod.classify(e) != "loopback"
        ]

    @property
    def reach(self) -> str:
        endpoints = [e for e in getattr(self.runtime.node, "endpoints", []) if e]
        if not endpoints:
            return "unknown"
        # The furthest any of them goes is how far this cell reaches.
        order = {"loopback": 0, "lan": 1, "unknown": 1, "internet": 2}
        return max((reach_mod.classify(e) for e in endpoints), key=lambda r: order[r])

    # --------------------------------------------------------------- semilla
    async def add_seed(self, line: str) -> dict[str, Any]:
        """Join through somebody else's cell (§7: a seed is an introduction).

        The single most useful thing a lone node can be told, which is why it
        is a first-class action instead of a start-up flag.
        """
        from nullcell.cli.relay import _parse_seed

        line = (line or "").strip()
        node = _parse_seed(line)
        if node is None:
            raise ControlError(
                "eso no parece una semilla. El formato es <dirección>@host:puerto, "
                "tal cual lo imprime `ncell relay --print-seed`"
            )
        known = await self.runtime.node.bootstrap([line])
        learned = await self.runtime.learn_relays()
        # Una semilla que funciona vale más recordada que pegada otra vez.
        if known and (remember := getattr(self.runtime, "remember_peers", None)):
            remember()
        return {
            "peers": known,
            "relays": learned,
            "detail": (
                f"{known} celdas conocidas, {learned} relevos observados"
                if known
                else "no contestó: ¿está encendida esa celda y abierto su puerto UDP?"
            ),
        }

    def seed_line(self) -> str | None:
        """Our own introduction, to give to somebody else."""
        endpoints = self._reachable_endpoints()
        if not endpoints:
            return None
        from nullcell.cli.relay import seed_line

        return seed_line(self.runtime.identity, endpoints[0])

    # --------------------------------------------------------------- portales
    async def publish_folder(
        self, folder: str | Path, *, name: str | None = None, save: bool = True
    ) -> dict[str, Any]:
        """Serve a folder as a portal, now locally and on the network when able."""
        root = Path(folder).expanduser().resolve()
        if not root.is_dir():
            raise ControlError(f"{root} no es una carpeta")
        if not any(root.iterdir()):
            raise ControlError(f"{root} está vacía: no hay nada que publicar")

        await self.unpublish(save=False)
        self.portal = {
            "folder": str(root),
            "name": name or None,
            "address": self.runtime.identity.hostname,
            "state": "local",
            "warning": (
                None if (root / "index.html").is_file() else "la carpeta no tiene index.html"
            ),
            "since": time.time(),
        }
        await self._start_local(root)
        if save:
            self.remember()
        await self.reconcile()
        return self.portal

    async def _start_local(self, root: Path) -> None:
        """The same application the portal serves, on loopback.

        Not a mock-up: identical code path, so what you see locally is what the
        network will serve.
        """
        from aiohttp import web

        from nullcell.host.site import static_app

        application = static_app(root)
        runner = web.AppRunner(application, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        self._preview = runner
        self.portal["preview"] = f"http://127.0.0.1:{port}/"

    async def unpublish(self, *, save: bool = True) -> dict[str, Any]:
        """Stop serving. Honest about the descriptor it cannot retract."""
        had = self.portal is not None
        state = (self.portal or {}).get("state")
        if self._portal_task is not None:
            self._portal_task.cancel()
            self._portal_task = None
        if self._preview is not None:
            await self._preview.cleanup()
            self._preview = None
        if self._site is not None:
            with contextlib.suppress(Exception):
                await self._site.close()
            self._site = None
        self._service = None
        self.portal = None
        if save:
            self.remember()

        detail = "no había ningún portal" if not had else "portal retirado"
        if state == "en línea":
            ttl = getattr(self.runtime.publisher, "ttl", None)
            if ttl:
                detail += f"; el descriptor caduca en ~{int(ttl) // 60} min"
        return {"portal": None, "detail": detail}

    # ------------------------------------------------------------ reconciliar
    async def reconcile(self) -> None:
        """Make the declared intents true, as far as the network allows today.

        Called on a timer, so the operator never has to come back and press
        the button again once relays appear. Failures are recorded and
        retried, not raised: this runs unattended.
        """
        try:
            if self.wants_sharing and not self.sharing and self._reachable_endpoints():
                await self.set_sharing(True)
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            log.debug("no se pudo activar el relevo todavía: %s", exc)

        if self.portal is None or self.portal["state"] == "en línea":
            return
        if not self.runtime.paths.ready:
            return
        try:
            await self._go_live()
            self._last_error = None
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            log.info("el portal sigue local: %s", exc)

    async def _go_live(self) -> None:
        """Establish intro points and publish the descriptor (§6, §8)."""
        from nullcell.host.site import SiteHost, static_app
        from nullcell.onion.rendezvous_msgs import INTRO_POINTS
        from nullcell.onion.service import HiddenService
        from nullcell.services.descriptor import ServiceDescriptor

        root = Path(self.portal["folder"])
        service = HiddenService(self.runtime.identity, self.runtime.circuits, multiplexed=True)
        site = SiteHost(static_app(root))
        await site.start()
        # Per *session*, not per service: every rendezvous gets its own
        # multiplexed session and the streams arrive on it, so hanging the
        # handler off the service would serve only whoever connected first.
        service.on_session = lambda session: setattr(session, "on_stream", site.serve)

        paths = [self.runtime.paths.build() for _ in range(INTRO_POINTS)]
        established = await service.establish(paths)
        if not established:
            await site.close()
            raise ControlError("ningún punto de enlace se estableció todavía")

        descriptor = ServiceDescriptor(
            pub=self.runtime.identity.public_key,
            x25519=service.x25519_public,
            intro_points=service.intro_points,
            name=self.portal.get("name") or "",
        )
        stored = await self.runtime.publisher.publish_service(descriptor)
        self._site, self._service = site, service
        self._portal_task = self.runtime.spawn(
            self.runtime.publisher.renew_forever(
                descriptor=descriptor, name=self.portal.get("name")
            )
        )
        self.portal.update({"state": "en línea", "intro_points": established, "stored_on": stored})
        if self.portal.get("name"):
            taken = await self.runtime.publisher.publish_name(self.portal["name"])
            self.portal["name_registered"] = bool(taken)
        log.info("portal en línea: %s", self.portal["address"])

    async def run_forever(self) -> None:
        while True:
            await asyncio.sleep(RECONCILE_EVERY)
            with contextlib.suppress(Exception):
                await self.reconcile()

    # --------------------------------------------------- tu propio portal
    def local_url_for(self, host: str) -> str | None:
        """The loopback address of **our own** portal, or None.

        Visiting your own site needs no circuit and no network: you are both
        ends, so there is no third party and nothing to anonymise. Without
        this, typing your own `nullcell.null` on a one-node network answers
        "ese nombre no existe en la red" — about a portal that is running on
        this very machine.

        Scoped to this cell's own address and its own declared name, and that
        limit is the whole safety of it. Any other host must go through the
        circuits, or the shortcut would become a way to serve something local
        under somebody else's name.
        """
        if self.portal is None or not self.portal.get("preview"):
            return None
        wanted = host.strip().lower().removesuffix(ids.SUFFIX)
        ours = {self.runtime.identity.hostname.lower().removesuffix(ids.SUFFIX)}
        if name := self.portal.get("name"):
            ours.add(str(name).strip().lower())
        return self.portal["preview"] if wanted in ours else None

    # ----------------------------------------------------------------- estado
    def describe(self) -> dict[str, Any]:
        """Everything the panel needs, including what is missing and for how long."""
        missing = self.runtime.paths.missing()
        return {
            "sharing": self.sharing,
            "wants_sharing": self.wants_sharing,
            "can_share": bool(self._reachable_endpoints()),
            "endpoints": self._relay_endpoints or self._reachable_endpoints(),
            "seed": self.seed_line(),
            "reach": self.reach,
            "reach_detail": reach_mod.describe(self.reach),
            "paths_ready": bool(self.runtime.paths.ready),
            "why_not_ready": missing["why"],
            "network": missing,
            "portal": self.portal,
            "last_error": self._last_error,
        }
