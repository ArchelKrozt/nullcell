"""Una red de un solo relevo: con su semilla ya se entra.

El caso real de una red que acaba de nacer: un único servidor que es relevo
**y** aloja el portal, y un navegador que sólo conoce su semilla (la que viene
en `bootstrap.json`). Antes no podía funcionar por dos lados:

* quien publica no se cuenta a sí mismo, así que el portal no tenía ningún
  relevo ajeno donde poner un punto de enlace y se quedaba en «local»;
* el navegador, con un solo relevo, no sabía construir una ruta de un salto
  hasta el punto de enlace (salía de dos saltos en el mismo prefijo).

Con `--hops auto` el portal es su propio punto de enlace mientras no haya
otro relevo, y el navegador va directo. Todo sigue cifrado de extremo a
extremo (QUIC entre celdas, Noise NK entre navegador y portal); lo que no hay
es anonimato, y `anonymous` lo dice.

Objetos de verdad, por loopback: `Runtime`, `ControlPanel`, DHT, circuitos y
el proxy SOCKS5 con TLS contra la CA local, igual que `test_browse`.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from nullcell.core import ids
from nullcell.core.identity import Identity
from nullcell.dht.routing_table import Node
from nullcell.gateway.localca import LocalCA
from nullcell.gateway.socks5 import Socks5Proxy
from nullcell.onion.service import connect_to_service
from tests.integration.test_browse import _free_port, fetch

PAGE = b"<!doctype html><title>nullcell</title><h1>Red pequena</h1>"


@contextlib.asynccontextmanager
async def red_de_un_relevo(tmp_path, monkeypatch):
    from nullcell.cli.runtime import Runtime
    from nullcell.gateway import control as control_mod

    # En la prueba todo va por 127.0.0.1, que en producción sería «nadie te
    # alcanza». Aquí se da por alcanzable para poder ejercitar la vía directa.
    monkeypatch.setattr(control_mod.reach_mod, "classify", lambda endpoint: "internet")

    sitio = tmp_path / "sitio"
    sitio.mkdir()
    (sitio / "index.html").write_bytes(PAGE)

    # ---- el servidor: relevo + portal, en automático
    puerto = _free_port()
    endpoint = f"127.0.0.1:{puerto}"
    host_id = Identity.generate()
    host = Runtime.assemble(
        host_id, tmp_path / "host", endpoints=[endpoint], hops="auto", min_age=600
    )
    await host.listen("127.0.0.1", puerto)
    await host.publisher.publish_relay([endpoint])
    panel = control_mod.ControlPanel(host, home=tmp_path / "host")

    # ---- el navegador: sólo la semilla del servidor
    cliente = Runtime.assemble(Identity.generate(), tmp_path / "cliente", hops="auto", min_age=600)
    ca = LocalCA(tmp_path / "ca")
    ca.load_or_create()
    proxy = None
    try:
        yield host, panel, cliente, sitio, ca, endpoint
    finally:
        if proxy is not None:
            await proxy.close()
        await panel.unpublish(save=False)
        await cliente.close()
        await host.close()


async def entrar(cliente, endpoint, host_id_pub):
    """Lo que hace un navegador nuevo: presentarse a la semilla y aprender."""
    conocidos = await cliente.node.bootstrap([Node(pub=host_id_pub, endpoints=[endpoint])])
    aprendidos = await cliente.learn_relays()
    return conocidos, aprendidos


async def test_el_portal_se_publica_sin_ningun_otro_relevo(tmp_path, monkeypatch):
    async with red_de_un_relevo(tmp_path, monkeypatch) as (host, panel, cliente, sitio, ca, ep):
        # El servidor no conoce ningún relevo ajeno: antes esto era «local» para siempre.
        assert not host.paths.ready
        estado = await asyncio.wait_for(panel.publish_folder(sitio), 60)

        assert estado["state"] == "en línea", panel.describe().get("last_error")
        assert estado["direct"] is True
        descripcion = panel.describe()
        assert descripcion["anonymous"] is False  # y se dice
        assert descripcion["direct"] is True


async def test_con_una_sola_semilla_el_navegador_ve_el_portal(tmp_path, monkeypatch):
    async with red_de_un_relevo(tmp_path, monkeypatch) as (host, panel, cliente, sitio, ca, ep):
        await asyncio.wait_for(panel.publish_folder(sitio), 60)
        assert panel.portal["state"] == "en línea"

        conocidos, aprendidos = await entrar(cliente, ep, host.identity.public_key)
        assert conocidos >= 1 and aprendidos >= 1
        # Un operador: un salto, rutas listas sin esperar los 10 minutos, y sin anonimato.
        assert cliente.paths.ready
        assert cliente.paths.hops == 1
        assert cliente.paths.min_age == 0
        assert cliente.paths.anonymous is False

        async def abrir(bare_nid: str):
            encontrado = await cliente.resolver.resolve(bare_nid)
            punto = encontrado.intro_points[0]
            return await connect_to_service(
                cliente.circuits,
                service_pub=encontrado.pub,
                service_x25519=encontrado.x25519,
                intro_point=punto,
                rendezvous_path=cliente.paths.build(),
                intro_path=cliente.paths.build(ending_at=punto.as_hop()),
                multiplexed=True,
            )

        proxy = Socks5Proxy(abrir, ca, port=0)
        await proxy.start()
        try:
            status, body, head, cert = await asyncio.wait_for(
                fetch(proxy.port, host.identity.hostname, ca), 60
            )
        finally:
            await proxy.close()
        assert status == 200
        assert body == PAGE
        # Cifrado igual que siempre: TLS contra la CA local, para la dirección pedida.
        nombres = [v for key, v in cert["subject"][0] if key == "commonName"]
        assert nombres == [host.identity.hostname]


async def test_un_solo_salto_va_directo_al_punto_fijado(tmp_path, monkeypatch):
    async with red_de_un_relevo(tmp_path, monkeypatch) as (host, panel, cliente, sitio, ca, ep):
        await entrar(cliente, ep, host.identity.public_key)
        relevo = cliente.directory.get(host.identity.public_key)
        ruta = cliente.paths.build(ending_at=relevo.as_hop())
        assert [h.pub for h in ruta] == [host.identity.public_key]


async def test_con_saltos_fijos_no_hay_via_directa(tmp_path, monkeypatch):
    """Quien fija `--hops 3` pidió anonimato: el portal no se sirve a sí mismo."""
    from nullcell.cli.runtime import Runtime
    from nullcell.gateway import control as control_mod

    monkeypatch.setattr(control_mod.reach_mod, "classify", lambda endpoint: "internet")
    sitio = tmp_path / "sitio"
    sitio.mkdir()
    (sitio / "index.html").write_bytes(PAGE)
    puerto = _free_port()
    host = Runtime.assemble(
        Identity.generate(), tmp_path / "h", endpoints=[f"127.0.0.1:{puerto}"], hops=3
    )
    await host.listen("127.0.0.1", puerto)
    panel = control_mod.ControlPanel(host, home=tmp_path / "h")
    try:
        estado = await panel.publish_folder(sitio)
        assert estado["state"] == "local"
        assert not estado.get("direct")
    finally:
        await panel.unpublish(save=False)
        await host.close()


@pytest.mark.parametrize("operadores, saltos", [(1, 1), (2, 2), (3, 2)])
def test_en_automatico_no_se_espera_sin_anonimato_que_proteger(operadores, saltos):
    """Recién vistos: sin suelo de antigüedad, y como mucho dos saltos.

    Con tres operadores recién llegados no se finge anonimato: tres saltos sin
    la protección del suelo contra inundaciones de identidades no lo serían.
    """
    import time

    from nullcell.onion.path_select import GuardSet, PathBuilder, RelayDirectory
    from tests.unit.test_path_select import diverse

    directorio = RelayDirectory()
    for c in diverse(operadores, first_seen=time.time()):
        directorio._relays[c.pub] = c
    paths = PathBuilder(directorio, GuardSet(), hops="auto", min_age=600)
    assert paths.min_age == 0
    assert paths.hops == saltos
    assert paths.ready
    assert paths.anonymous is False


def test_con_tres_operadores_veteranos_vuelven_el_suelo_y_el_anonimato():
    from nullcell.onion.path_select import GuardSet, PathBuilder, RelayDirectory
    from tests.unit.test_path_select import diverse

    directorio = RelayDirectory()
    for c in diverse(3):  # first_seen antiguo por defecto
        directorio._relays[c.pub] = c
    paths = PathBuilder(directorio, GuardSet(), hops="auto", min_age=600)
    assert paths.min_age == 600
    assert paths.hops == 3
    assert paths.anonymous is True


def test_la_semilla_del_proyecto_viene_dentro_del_paquete():
    """`pip install .` no copia la raíz del repo: la semilla tiene que ir en el paquete."""
    from nullcell.discovery.bootstrap import load_bootstrap

    semillas = load_bootstrap()
    assert semillas, "bootstrap.json vacío o no encontrado"
    assert ids.encode_nid(semillas[0].pub).startswith("wuxq7z")
