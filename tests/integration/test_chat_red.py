"""El chat de punta a punta, con objetos de verdad: un relevo y dos celdas.

Las pruebas de `test_chat_live` usan un DHT en memoria y tuberías falsas en
lugar de sesiones, y por eso no vieron cuatro fallos que juntos hacían que dos
personas no pudieran hablar nunca en la red real:

* la presencia no cabía en una celda y el DHT por circuitos no troceaba;
* el almacén guardaba un solo miembro por canal;
* `find_value` devolvía la propia presencia de la caché y no preguntaba más;
* el canal llamaba a `send` en una sesión multiplexada, que no lo tiene.

Aquí va todo por loopback, como en `test_red_pequena`: Runtime, ControlPanel,
DHT por circuitos, rendezvous y sesiones multiplexadas.
"""

from __future__ import annotations

import asyncio
import contextlib

from nullcell.core.identity import Identity
from nullcell.dht.routing_table import Node
from tests.integration.test_browse import _free_port


async def _esperar(condicion, segundos: float = 40.0) -> bool:
    fin = asyncio.get_running_loop().time() + segundos
    while asyncio.get_running_loop().time() < fin:
        if await condicion():
            return True
        await asyncio.sleep(0.3)
    return False


@contextlib.asynccontextmanager
async def red(tmp_path, monkeypatch):
    from nullcell.cli.runtime import Runtime
    from nullcell.gateway import control as control_mod

    monkeypatch.setattr(control_mod.reach_mod, "classify", lambda endpoint: "internet")
    puerto = _free_port()
    endpoint = f"127.0.0.1:{puerto}"
    relevo = Runtime.assemble(
        Identity.generate(), tmp_path / "relevo", endpoints=[endpoint], hops="auto"
    )
    await relevo.listen("127.0.0.1", puerto)
    await relevo.publisher.publish_relay([endpoint])

    celdas, paneles = [], []
    for nombre in ("ana", "beto"):
        celda = Runtime.assemble(Identity.generate(), tmp_path / nombre, hops="auto")
        await celda.node.bootstrap([Node(pub=relevo.identity.public_key, endpoints=[endpoint])])
        await celda.learn_relays()
        assert celda.paths.ready
        celda.go_anonymous()  # como un teléfono: el DHT por circuitos
        celdas.append(celda)
        paneles.append(control_mod.ControlPanel(celda, home=tmp_path / nombre))
    try:
        yield paneles
    finally:
        for panel in paneles:
            await panel.leave()
        for celda in celdas:
            await celda.close()
        await relevo.close()


async def test_dos_celdas_se_ven_y_se_hablan_en_un_canal(tmp_path, monkeypatch):
    async with red(tmp_path, monkeypatch) as (ana, beto):
        await ana.join("#general", "neo")
        assert ana.chat_state()["ready"], ana.chat_state()["why_not_ready"]
        await beto.join("general", "neo")
        assert beto.chat_state()["ready"]

        # Apodo repetido: quien llegó después cede.
        assert ana.chat.nick == "neo"
        assert beto.chat.nick == "neo2"

        # Beto ve a Ana al entrar; Ana ve a Beto en su siguiente vuelta.
        await ana.chat.refresh()
        assert [m.nick for m in beto.chat.members] == ["neo"]
        assert [m.nick for m in ana.chat.members] == ["neo2"]

        # Los dos aparecen en el directorio de canales.
        canales = await ana.chat_channels()
        assert {"channel": "general", "members": 2, "joined": True} in canales

        # Un mensaje largo (varias celdas) cruza en los dos sentidos.
        largo = "hola desde beto " * 60
        await beto.say(largo)

        async def ana_lo_tiene() -> bool:
            return any(m["text"] == largo.strip() for m in ana.chat.recent() if not m["mine"])

        assert await _esperar(ana_lo_tiene), ana.chat.recent()

        await ana.say("hola beto")

        async def beto_lo_tiene() -> bool:
            return any(m["text"] == "hola beto" for m in beto.chat.recent() if not m["mine"])

        assert await _esperar(beto_lo_tiene), beto.chat.recent()
        recibido = next(m for m in beto.chat.recent() if not m["mine"])
        assert recibido["nick"] == "neo"


async def test_cambiar_de_canal_anuncia_en_el_nuevo(tmp_path, monkeypatch):
    """Al cambiar de canal el servicio de chat ya existía y el canal nuevo no
    lo recibía: no se anunciaba y nadie te veía allí."""
    async with red(tmp_path, monkeypatch) as (ana, beto):
        await ana.join("#uno", "ana")
        await ana.join("#dos", "ana")
        await beto.join("#dos", "beto")
        assert [m.nick for m in beto.chat.members] == ["ana"]

        await ana.leave()
        assert ana.chat_state()["channel"] is None
