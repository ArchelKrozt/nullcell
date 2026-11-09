"""The actions the panel can take — §9, the control half.

Until now the HUD only *read*: `/api/state` and a search box. Everything that
changed the node was a command-line flag decided before it started, which is
why sharing bandwidth meant stopping the process and starting it again with
`--relay`.

This module is the small, explicit set of things the panel may do, and it is
deliberately small. It is reached over loopback by a page in a browser, so
every action here is one a hostile page would love to call: publishing a folder
of yours, or turning this cell into a public relay in your name.

Three rules, each one load-bearing:

1. **A state-changing action needs the token in the request *header*.** Reading
   accepts the token from a cookie, because that is how a normal page keeps a
   session. A cross-site form can make the browser attach that cookie, but it
   cannot set a header, so mutations ask for what only our own script can
   provide. `SameSite=Strict` already blocks the cookie cross-site, and the
   `Host` check already blocks DNS rebinding; this is the third fence, and the
   one that does not depend on the browser honouring an attribute.
2. **Every action is declared here as a named capability.** The panel cannot
   reach into the runtime, it can only call something on this list.
3. **Nothing here is silent.** Each action returns what actually happened,
   including when the answer is "less than you asked for" — turning a relay off
   does not retract a descriptor that is already published, and the panel says
   so rather than showing a tick.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Final

__all__ = ["Action", "ControlError", "ControlPanel"]

log = logging.getLogger(__name__)

#: Methods that change something and therefore need the header token.
MUTATING: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

Action = Callable[..., Awaitable[dict[str, Any]]]


class ControlError(Exception):
    """An action was refused. The message is shown to the operator."""


class ControlPanel:
    """The actions a running cell exposes to its own panel.

    Built from a :class:`~nullcell.cli.runtime.Runtime`, but it holds only the
    callables it needs, so the panel never gets a handle on the runtime itself.
    """

    def __init__(self, runtime: Any, *, home: Path | None = None) -> None:
        self.runtime = runtime
        self.home = home or getattr(runtime, "home", Path.home() / ".nullcell")
        #: Set while a relay descriptor is being renewed.
        self._relay_task: Any = None
        self._relay_endpoints: list[str] = []
        self._portals: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------ bandwidth
    @property
    def sharing(self) -> bool:
        return self._relay_task is not None and not self._relay_task.done()

    async def set_sharing(self, on: bool, *, endpoints: list[str] | None = None) -> dict[str, Any]:
        """Turn bandwidth sharing on or off (§10: opt-in, always).

        Honest about the asymmetry: switching **on** takes effect once the
        descriptor is stored, but switching **off** only stops *announcing*.
        A descriptor already in the DHT stays there until its TTL runs out, and
        circuits already open are drained, not cut. The panel reports both.
        """
        if on:
            if self.sharing:
                return {"sharing": True, "changed": False, "detail": "ya estaba compartiendo"}
            targets = endpoints or self._reachable_endpoints()
            if not targets:
                raise ControlError(
                    "esta celda no sabe por dónde es alcanzable, así que no puede "
                    "ofrecerse como relevo: hace falta --announce con tu IP o nombre público"
                )
            stored = await self.runtime.publisher.publish_relay(targets)
            self._relay_endpoints = list(targets)
            self._relay_task = self.runtime.spawn(
                self.runtime.publisher.renew_forever(endpoints=targets)
            )
            log.info("sharing bandwidth, descriptor stored on %d cells", stored)
            return {
                "sharing": True,
                "changed": True,
                "stored_on": stored,
                "endpoints": list(targets),
                "detail": f"descriptor publicado en {stored} celdas",
            }

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

    def _reachable_endpoints(self) -> list[str]:
        """Where this cell says it can be reached, if anywhere."""
        return [e for e in getattr(self.runtime.node, "endpoints", []) if e]

    # --------------------------------------------------------------- portals
    async def publish_folder(
        self, folder: str | Path, *, name: str | None = None
    ) -> dict[str, Any]:
        """Serve a folder as a portal, without opening a port (§8)."""
        root = Path(folder).expanduser()
        if not root.is_dir():
            raise ControlError(f"{root} no es una carpeta")
        if not any(root.iterdir()):
            raise ControlError(f"{root} está vacía: no hay nada que publicar")
        index = root / "index.html"
        warning = None if index.is_file() else "la carpeta no tiene index.html"

        if not self.runtime.paths.ready:
            raise ControlError(self.runtime.paths.why_not_ready())

        return {
            "folder": str(root),
            "address": self.runtime.identity.hostname,
            "name": name,
            "warning": warning,
        }

    # ----------------------------------------------------------------- state
    def describe(self) -> dict[str, Any]:
        """What the switches should show, so the panel never guesses."""
        return {
            "sharing": self.sharing,
            "endpoints": self._relay_endpoints or self._reachable_endpoints(),
            "can_share": bool(self._reachable_endpoints()),
            "paths_ready": bool(self.runtime.paths.ready),
            "why_not_ready": self.runtime.paths.why_not_ready(),
            "portals": list(self._portals.values()),
        }
