"""Puente entre la app de Android y el nodo Nullcell.

La app es un **navegador**: el teléfono arma sus propios circuitos, igual que
el escritorio (`nullcell.desktop.node_thread`), pero no presta ancho de banda
ni se anuncia. La interfaz (Kotlin) habla con el nodo por la misma API local
que el panel de escritorio —`127.0.0.1`, con token— y pide las páginas de los
portales a `cargar()`.

Lo propio del móvil, todo aquí:

* **sin direcciones anunciadas**: un teléfono detrás de NAT o de datos móviles
  no es alcanzable, y anunciar su IP sólo serviría para atarla a su identidad
  en las tablas de los demás;
* el directorio de estado vive en el almacenamiento privado de la app.
"""

from __future__ import annotations

import json
import logging
import os
import threading

log = logging.getLogger("nullcell.movil")

_nodo = None
_fetcher = None
_cerrojo = threading.Lock()


def arrancar(home: str, hud_port: int = 9180, port: int = 4433) -> str:
    """Arranca el nodo (una sola vez) y devuelve cómo hablarle, en JSON."""
    global _nodo
    with _cerrojo:
        if _nodo is not None and _nodo.running:
            return json.dumps(_info())

        os.environ["NULLCELL_HOME"] = home
        os.makedirs(home, exist_ok=True)
        logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
        _sin_anunciarse()

        from nullcell.desktop.node_thread import NodeThread

        _nodo = NodeThread(port=port, host="127.0.0.1", hud_port=hud_port, hops="auto")
        _nodo.start(timeout=90)
        return json.dumps(_info())


def _sin_anunciarse() -> None:
    """Un navegador móvil no se anuncia: endpoints vacíos en su Runtime."""
    from nullcell.cli.runtime import Runtime

    if getattr(Runtime.assemble, "_movil", False):
        return
    original = Runtime.assemble.__func__

    def assemble(cls, *a, **k):
        k["endpoints"] = []
        return original(cls, *a, **k)

    assemble._movil = True
    Runtime.assemble = classmethod(assemble)


def _info() -> dict:
    n = _nodo
    return {
        "hud": f"http://127.0.0.1:{n.hud_port}",
        "token": n.token,
        "nid": n.identity.nid if n.identity is not None else None,
    }


def detener() -> None:
    global _nodo, _fetcher
    with _cerrojo:
        if _nodo is not None:
            _nodo.stop(timeout=8)
        _nodo, _fetcher = None, None


def direccion_real(host: str) -> str:
    """`nullcell.null` → la dirección de 56 caracteres que de verdad es el portal.

    Un nombre es un registro en el DHT que el primero en llegar se queda; la
    dirección es la clave del portal y no se puede suplantar. Los favoritos
    guardan ésta. Devuelve "" si no se puede resolver.
    """
    if _nodo is None or not _nodo.running:
        return ""
    try:
        return _nodo.call(lambda: _nodo.real_address(host), timeout=60)
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo resolver %s: %s", host, exc)
        return ""


def cargar(url: str, metodo: str = "GET") -> dict:
    """Una página de un portal `.null`: estado, tipo, cabeceras y cuerpo."""
    global _fetcher
    from urllib.parse import urlsplit

    from nullcell.desktop.fetcher import NullFetcher

    if _nodo is None or not _nodo.running:
        return _error(503, "El nodo no está en marcha.")
    partes = urlsplit(url)
    host = partes.hostname or ""
    ruta = partes.path or "/"
    if partes.query:
        ruta += "?" + partes.query
    if _fetcher is None:
        _fetcher = NullFetcher(_nodo.open_session)
    try:
        r = _nodo.call(lambda: _fetcher.fetch(host, ruta, method=metodo), timeout=90)
    except Exception as exc:  # noqa: BLE001 - se enseña en la página, no se traga
        log.warning("no se pudo cargar %s: %s", url, exc)
        return _error(502, _explicar(exc))
    tipo = r.content_type or "application/octet-stream"
    return {
        "status": int(r.status),
        "type": tipo.split(";")[0].strip(),
        "charset": _charset(tipo),
        "headers": json.dumps(dict(r.headers)),
        "body": bytes(r.body),
    }


def _charset(tipo: str) -> str:
    for parte in tipo.split(";")[1:]:
        k, _, v = parte.strip().partition("=")
        if k.lower() == "charset" and v:
            return v.strip('"')
    return "utf-8"


def _explicar(exc: BaseException) -> str:
    texto = str(exc) or type(exc).__name__
    if "not registered" in texto:
        return "Ese nombre no está registrado en la red."
    if "no usable service descriptor" in texto or "apagado" in texto:
        return "El portal existe, pero ahora mismo está apagado."
    if "Timeout" in type(exc).__name__ or "timed out" in texto:
        return "La red tardó demasiado en responder. Vuelve a intentarlo."
    return texto


def _error(status: int, mensaje: str) -> dict:
    cuerpo = f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>body{{background:#07090d;color:#e6edf3;font:15px/1.5 system-ui;padding:32px}}
h1{{color:#ff4fd8;font-size:20px}}p{{color:#9fb0c6}}</style>
<h1>No se pudo abrir el portal</h1><p>{mensaje}</p>"""
    return {
        "status": status,
        "type": "text/html",
        "charset": "utf-8",
        "headers": "{}",
        "body": cuerpo.encode(),
    }
