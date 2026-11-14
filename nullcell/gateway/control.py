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
from nullcell.core.identity import Identity
from nullcell.onion.path_select import anonymity_warning

__all__ = ["Action", "ControlError", "ControlPanel"]

log = logging.getLogger(__name__)

#: Methods that change something and therefore need the header token.
MUTATING: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: How often the node tries to make the declared intents true.
RECONCILE_EVERY: Final[float] = 20.0
#: Cada cuánto un portal servido por la vía directa (red pequeña) intenta
#: pasar a la normal si han aparecido relevos ajenos.
DIRECT_RETRY: Final[float] = 600.0

Action = Any


def _clean_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Título, descripción y etiquetas de un portal, acotados (§8)."""
    meta = meta or {}
    tags = meta.get("tags") or []
    return {
        "title": str(meta.get("title") or "")[:120],
        "desc": str(meta.get("desc") or "")[:400],
        "tags": [str(t)[:40] for t in tags if isinstance(t, str)][:8]
        if isinstance(tags, list)
        else [],
    }


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
        #: El canal al que esta celda está unida, si lo está.
        self.chat: Any = None
        self.on_chat: Any = None
        self._chat_identity: Any = None
        self._chat_service_obj: Any = None

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
                    deployment.folder_for(portal),
                    name=portal.name,
                    save=False,
                    meta={"title": portal.title, "desc": portal.desc, "tags": portal.tags},
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

        Takes an invitation or a bare seed line, because asking somebody to
        tell the two apart is asking them to understand a difference that does
        not concern them.
        """
        from nullcell.cli.relay import _parse_seed
        from nullcell.core import invite

        try:
            invitation = invite.parse(line)
        except invite.InviteError as bad:
            raise ControlError(str(bad)) from None

        # `bootstrap` quiere objetos `Node`, no texto. Pasarle las cadenas
        # hacía que reventara en `seed.pub` con un AttributeError que salía
        # como un HTTP 500: la casilla de semillas del panel —lo que más
        # necesita una celda sola— no había funcionado nunca.
        nodes = [node for raw in invitation.seeds if (node := _parse_seed(raw)) is not None]
        if not nodes:
            raise ControlError("ninguna de esas semillas se pudo interpretar")

        try:
            known = await self.runtime.node.bootstrap(nodes)
            learned = await self.runtime.learn_relays()
        except Exception as exc:  # noqa: BLE001 - se enseña, no se traga
            log.warning("el bootstrap falló: %s", exc, exc_info=True)
            raise ControlError(f"no se pudo entrar con esa semilla: {exc}") from None
        # Una semilla que funciona vale más recordada que pegada otra vez.
        if known and (remember := getattr(self.runtime, "remember_peers", None)):
            remember()
        cuantas = len(invitation.seeds)
        return {
            "peers": known,
            "relays": learned,
            "portal": invitation.portal,
            "detail": (
                f"{known} celdas conocidas, {learned} relevos observados"
                if known
                else (
                    "no contestó ninguna de las "
                    + (f"{cuantas} celdas" if cuantas > 1 else "celda")
                    + " de la invitación: ¿están encendidas y con su puerto UDP abierto?"
                )
            ),
        }

    def seed_line(self) -> str | None:
        """Our own introduction, to give to somebody else."""
        endpoints = self._reachable_endpoints()
        if not endpoints:
            return None
        from nullcell.cli.relay import seed_line

        return seed_line(self.runtime.identity, endpoints[0])

    def invitation(self) -> str | None:
        """One thing to copy: how to get in, and what to look at once inside.

        Carries this cell's own seed plus a few of the relays it already
        knows, so the invitation keeps working on a day this machine happens
        to be off — and so no single cell sees everybody arrive.
        """
        from nullcell.core import invite

        seeds: list[str] = []
        if mine := self.seed_line():
            seeds.append(mine)
        for relay in self.runtime.directory.candidates()[: invite.MAX_SEEDS]:
            endpoints = [e for e in relay.endpoints if reach_mod.classify(e) != "loopback"]
            if endpoints and len(seeds) < invite.MAX_SEEDS:
                seeds.append(f"{ids.encode_nid(relay.pub)}@{endpoints[0]}")
        if not seeds:
            return None
        try:
            return invite.build(seeds, portal=(self.portal or {}).get("name"))
        except invite.InviteError:
            return None

    # --------------------------------------------------------------- portales
    async def publish_folder(
        self,
        folder: str | Path,
        *,
        name: str | None = None,
        save: bool = True,
        meta: dict[str, Any] | None = None,
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
            # Título, descripción y etiquetas: lo que el buscador indexa (§8).
            # Vienen de `nullcell.toml`; sin ellos el portal sólo se encontraba
            # por su nombre, y ni eso, porque no se publicaban palabras clave.
            "meta": _clean_meta(meta),
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

        # §7, decisión 8: en cuanto haya rutas, el DHT deja de ir en claro.
        # El escritorio construye su Runtime a mano, así que no pasaba por el
        # `_runtime(anonymous=True)` de los comandos y se quedaba en directo
        # para siempre — la misma decisión 8 sin conectar, por otro camino.
        if self.runtime.paths.ready and not getattr(self.runtime, "anonymous", True):
            with contextlib.suppress(Exception):
                self.runtime.go_anonymous()

        if self.chat is not None and self._chat_service_obj is None:
            with contextlib.suppress(Exception):
                await self._chat_service()

        if self.portal is None:
            return
        if self.portal["state"] == "en línea":
            await self._maybe_leave_direct()
            return
        if not self.runtime.paths.ready and not self._can_serve_alone():
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

        established = 0
        if self.runtime.paths.ready:
            try:
                paths = [self.runtime.paths.build() for _ in range(INTRO_POINTS)]
            except Exception as exc:  # noqa: BLE001 - PathError y compañía
                log.info("sin rutas por otros relevos: %s", exc)
                paths = []
            if paths:
                # En automático, un intento por punto: si los relevos ajenos no
                # aceptan circuitos, quedan aún la vía directa y otro intento
                # en la próxima pasada, sin bloquear minutos.
                established = await service.establish(
                    paths, attempts=1 if self.runtime.paths.auto else 3
                )
        direct = False
        if not established and self._can_serve_alone():
            # Red pequeña: ningún relevo ajeno lleva circuitos, así que la
            # propia celda —que es relevo y alcanzable— es su punto de enlace.
            # Sigue cifrado de extremo a extremo (QUIC + Noise NK); lo que no
            # hay es anonimato, y `describe()` lo dice.
            me = self.runtime.self_hop()
            established = await service.establish([[me]], attempts=2)
            direct = bool(established)
        if not established:
            await site.close()
            raise ControlError("ningún punto de enlace se estableció todavía")

        meta = self.portal.get("meta") or {}
        descriptor = ServiceDescriptor(
            pub=self.runtime.identity.public_key,
            x25519=service.x25519_public,
            intro_points=service.intro_points,
            name=self.portal.get("name") or "",
            title=meta.get("title") or self.portal.get("name") or "",
            desc=meta.get("desc", ""),
            tags=list(meta.get("tags", [])),
        )
        stored = await self.runtime.publisher.publish_service(descriptor)
        self._site, self._service = site, service
        # Que el propio índice lo conozca desde ya: el buscador de esta celda
        # lo encuentra aunque el DHT tarde.
        if (search := getattr(self.runtime, "search", None)) is not None:
            with contextlib.suppress(Exception):
                search.ingest(descriptor)
        self._portal_task = self.runtime.spawn(
            self.runtime.publisher.renew_forever(
                descriptor=descriptor, name=self.portal.get("name"), keywords=True
            )
        )
        # Las palabras clave cuestan prueba de trabajo cada una: en segundo
        # plano, para no retrasar que el portal esté en línea.
        self.runtime.spawn(self._publish_keywords(descriptor))
        self.portal.update(
            {
                "state": "en línea",
                "intro_points": established,
                "stored_on": stored,
                "direct": direct,
                "online_since": time.time(),
            }
        )
        if self.portal.get("name"):
            taken = await self.runtime.publisher.publish_name(self.portal["name"])
            self.portal["name_registered"] = bool(taken)
        log.info("portal en línea: %s", self.portal["address"])

    # ------------------------------------------------------- red pequeña
    def _can_serve_alone(self) -> bool:
        """¿Puede el portal ser su propio punto de enlace?

        Sólo en modo automático —quien fija los saltos a mano pidió otra
        cosa—, y sólo si esta celda escucha como relevo con una dirección que
        otros puedan alcanzar: un punto de enlace al que nadie llega no sirve.
        """
        if not getattr(self.runtime.paths, "auto", False):
            return False
        hop = getattr(self.runtime, "self_hop", lambda: None)()
        if hop is None:
            return False
        return any(reach_mod.classify(e) in ("internet", "lan") for e in hop.endpoints)

    async def _maybe_leave_direct(self) -> None:
        """Pasar de la vía directa a la normal en cuanto haya relevos ajenos.

        Como mucho una vez cada 10 minutos, para no tirar el portal en bucle si
        los relevos que aparecen no aceptan circuitos de verdad.
        """
        if not self.portal or not self.portal.get("direct"):
            return
        if not self.runtime.paths.ready:
            return
        if time.time() - self.portal.get("online_since", 0) < DIRECT_RETRY:
            return
        folder, name = self.portal["folder"], self.portal.get("name")
        log.info("hay relevos ajenos: el portal deja la vía directa")
        await self.publish_folder(folder, name=name, save=False, meta=self.portal.get("meta"))

    async def _publish_keywords(self, descriptor: Any) -> None:
        """Indexar el portal bajo sus propias palabras (§8, mecanismo 1)."""
        try:
            stored = await self.runtime.publisher.publish_keywords(descriptor)
            if self.portal is not None:
                self.portal["keywords"] = descriptor.keywords()[:12]
            log.info(
                "palabras clave publicadas: %s (%d copias)",
                ", ".join(descriptor.keywords()[:12]),
                stored,
            )
        except Exception as exc:  # noqa: BLE001 - se reintenta en la renovación
            log.warning("no se pudieron publicar las palabras clave: %s", exc)

    async def run_forever(self) -> None:
        while True:
            await asyncio.sleep(RECONCILE_EVERY)
            with contextlib.suppress(Exception):
                await self.reconcile()

    # ------------------------------------------------------------- chat §M10
    @property
    def chat_identity(self) -> Any:
        """La identidad del chat, **distinta** de la del nodo.

        Separada a propósito: la pertenencia a un canal es pública (cualquiera
        puede consultar su clave en el DHT), así que si el chat hablara con la
        identidad del nodo, ver tu apodo en #general revelaría que eres quien
        publica `nullcell.null`. Son dos cosas que no tienen por qué ir juntas.
        """
        if self._chat_identity is None:
            from nullcell.chat import CHAT_IDENTITY_FILE

            self.home.mkdir(parents=True, exist_ok=True)
            self._chat_identity, _ = Identity.load_or_create(self.home / CHAT_IDENTITY_FILE)
        return self._chat_identity

    async def join(self, channel: str, nick: str = "") -> dict[str, Any]:
        """Entra en un canal. Funciona sin red; hablar con otros, no."""
        from nullcell.chat.channel import Channel
        from nullcell.chat.protocol import MessageError

        try:
            if self.chat is not None:
                await self.chat.close()
            self.chat = Channel(
                channel,
                self.chat_identity,
                nick=nick,
                dht=self.runtime.node,
                open_session=self._open_chat_session,
            )
        except MessageError as bad:
            raise ControlError(str(bad)) from None

        self.chat.on_message = self._on_chat_message
        self.chat.spawn(self.runtime)
        await self._chat_service()
        return self.chat.describe()

    async def _chat_service(self) -> None:
        """Hace alcanzable el chat, si hay rutas. Si no, se reintenta solo."""
        if self.chat is None or self._chat_service_obj is not None:
            return
        if not self.runtime.paths.ready:
            return
        from nullcell.onion.rendezvous_msgs import INTRO_POINTS
        from nullcell.onion.service import HiddenService

        service = HiddenService(self.chat_identity, self.runtime.circuits, multiplexed=True)
        service.on_session = self._attach_chat_session
        established = await service.establish(
            [self.runtime.paths.build() for _ in range(INTRO_POINTS)]
        )
        if not established:
            return
        self._chat_service_obj = service
        self.chat.service = service
        await self.chat.refresh()

    def _attach_chat_session(self, session: Any) -> None:
        if self.chat is not None:
            self.chat.attach(session)

    async def _open_chat_session(self, member: Any) -> Any:
        """Alcanza a otro miembro por su punto de enlace (§6)."""
        from nullcell.onion.rendezvous_msgs import IntroPointRef
        from nullcell.onion.service import connect_to_service

        intro = next((ref for raw in member.intro if (ref := IntroPointRef.from_dict(raw))), None)
        if intro is None:
            raise ControlError(f"{member.nick} no publicó un punto de enlace usable")
        return await connect_to_service(
            self.runtime.circuits,
            service_pub=member.pub,
            service_x25519=member.x25519,
            intro_point=intro,
            rendezvous_path=self.runtime.paths.build(),
            intro_path=self.runtime.paths.build(ending_at=intro.as_hop()),
            multiplexed=True,
        )

    def _on_chat_message(self, message: Any) -> None:
        if self.on_chat is not None:
            self.on_chat(message)

    async def say(self, text: str) -> dict[str, Any]:
        from nullcell.chat.protocol import MessageError

        if self.chat is None:
            raise ControlError("todavía no estás en ningún canal")
        try:
            await self.chat.say(text)
        except MessageError as bad:
            raise ControlError(str(bad)) from None
        return self.chat.describe()

    def chat_state(self, limit: int = 100) -> dict[str, Any]:
        if self.chat is None:
            return {"channel": None, "messages": [], "members": [], "ready": False}
        state = self.chat.describe()
        state["messages"] = self.chat.recent(limit)
        state["ready"] = self._chat_service_obj is not None
        state["why_not_ready"] = (
            "" if state["ready"] else self.runtime.paths.why_not_ready() or "preparando el chat"
        )
        return state

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

    # ------------------------------------------------------ quién hay ahí
    def cells(self) -> list[dict[str, Any]]:
        """Las celdas que esta conoce, con **por qué** sirven o no.

        «3 relevos conocidos, 0 usables» no dice cuál es cuál ni qué le pasa a
        cada uno. Enseñarlas una por una convierte un número desconcertante en
        una lista en la que se ve que a dos les faltan minutos y una no tiene
        dirección comparable.
        """
        from nullcell.onion.path_select import MIN_OBSERVED_AGE

        now = time.time()
        floor = self.runtime.paths.min_pow_bits
        guards = {ids.encode_nid(pub) for pub in getattr(self.runtime.guards, "pubs", [])}

        out: list[dict[str, Any]] = []
        for relay in self.runtime.directory.candidates(now=now):
            address = ids.encode_nid(relay.pub)
            age = relay.observed_for(now)
            falta = max(0.0, MIN_OBSERVED_AGE - age)
            if relay.pow_bits < floor:
                estado, detalle = "rechazado", "no hizo bastante trabajo (§10)"
            elif not relay.prefixes:
                estado, detalle = "rechazado", "su dirección no se puede comparar con otras"
            elif falta > 0:
                # En segundos, no en texto: la página cuenta hacia atrás sola.
                # «usable en 10 min» no se movía nunca y parecía colgado.
                estado, detalle = "esperando", ""
            else:
                estado, detalle = "usable", "puede llevar circuitos"
            out.append(
                {
                    "address": address,
                    "short": ids.short(address),
                    "endpoint": relay.observed or (relay.endpoints[0] if relay.endpoints else ""),
                    "observed_for": round(age),
                    "usable_in": round(falta) if estado == "esperando" else 0,
                    "state": estado,
                    "detail": detalle,
                    "guard": address in guards,
                    "operator": next(iter(relay.prefixes), ""),
                }
            )
        # Y lo que se conoce pero todavía no tiene descriptor de relevo.
        #
        # El panel listaba sólo el directorio de relevos, así que una semilla
        # guardada y perfectamente viva era INVISIBLE hasta que alguien
        # consiguiera su descriptor. Quien la acababa de pegar veía su lista
        # igual que antes y concluía, con razón, que no se había guardado.
        conocidos = {c["address"] for c in out}
        for node in self._known_cells():
            address = node.nid
            if address in conocidos:
                continue
            conocidos.add(address)
            out.append(
                {
                    "address": address,
                    "short": ids.short(address),
                    "endpoint": node.endpoints[0] if node.endpoints else "",
                    "observed_for": 0,
                    "usable_in": 0,
                    "state": "sin datos",
                    "detail": "guardada, pero todavía no publica descriptor de relevo",
                    "guard": False,
                    "operator": "",
                }
            )

        order = {"usable": 0, "esperando": 1, "sin datos": 2, "rechazado": 3}
        return sorted(out, key=lambda c: (order[c["state"]], -c["observed_for"]))

    def _known_cells(self) -> list[Any]:
        """Celdas que esta conoce: la tabla de rutas y lo guardado en disco.

        Las dos, porque dicen cosas distintas: la tabla es quién responde
        *ahora* y el fichero es quién respondió alguna vez. Una semilla recién
        pegada que todavía no ha contestado está en la segunda y no en la
        primera, y seguir sin enseñarla sería repetir el mismo silencio.
        """
        from nullcell.discovery.bootstrap import load_peers

        nodes: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            for node in self.runtime.node.table.all_nodes():
                nodes[node.nid] = node
        # El fichero **de este nodo**, no el del usuario que ejecuta: un panel
        # pertenece a una celda concreta, y leer la configuración global hacía
        # que enseñara los pares de otra.
        with contextlib.suppress(Exception):
            for node in load_peers(self.home / "peers.db"):
                nodes.setdefault(node.nid, node)
        return list(nodes.values())

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
            "invitation": self.invitation(),
            "reach": self.reach,
            "reach_detail": reach_mod.describe(self.reach),
            "paths_ready": bool(self.runtime.paths.ready),
            # Anónimo de verdad: tres saltos **y** el portal no se sirve a sí
            # mismo. El candado del panel sale de aquí, no de `hops`.
            "anonymous": bool(self.runtime.paths.anonymous)
            and not bool((self.portal or {}).get("direct")),
            "direct": bool((self.portal or {}).get("direct")),
            "anonymity_warning": anonymity_warning(
                self.runtime.paths.hops, auto=getattr(self.runtime.paths, "auto", False)
            ),
            "hops": self.runtime.paths.hops,
            "auto_hops": getattr(self.runtime.paths, "auto", False),
            "why_not_ready": missing["why"],
            "network": missing,
            "portal": self.portal,
            "cells": self.cells(),
            "last_error": self._last_error,
        }
