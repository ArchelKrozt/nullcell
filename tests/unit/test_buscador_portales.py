"""El buscador encuentra los portales de verdad.

Se podía entrar en `nullcell.null` y, sin embargo, buscar «nullcell» no
devolvía nada. Cuatro causas, una prueba por cada una:

* el portal publicado desde un despliegue (`ncell node --config`) nunca
  publicaba sus palabras clave, y su descriptor no llevaba título,
  descripción ni etiquetas;
* las palabras clave no se renovaban y caducaban a la hora;
* el índice del DHT es de coincidencia exacta y los acentos lo rompían;
* buscar el nombre de un portal no lo resolvía como nombre.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from nullcell.core.identity import Identity
from nullcell.search.indexer import Indexer
from nullcell.search.query import Search, _looks_like_name, _words
from nullcell.services.descriptor import ServiceDescriptor, normalize_word


def portal(**kw) -> ServiceDescriptor:
    ident = Identity.generate()
    base = dict(
        pub=ident.public_key,
        x25519=os.urandom(32),
        name="nullcell",
        title="Nullcell — qué es, cómo funciona y qué le falta",
        desc="Documentación del proyecto, servida por la propia red",
        tags=["documentacion", "nullcell", "p2p", "anonimato"],
    )
    base.update(kw)
    return ServiceDescriptor(**base)


@pytest.fixture
async def indexer(tmp_path):
    index = Indexer(tmp_path / "index.db")
    index.start()
    yield index
    index.close()


# ------------------------------------------------------------ palabras clave
def test_las_palabras_clave_empiezan_por_el_nombre_y_las_etiquetas():
    palabras = portal().keywords()
    assert palabras[0] == "nullcell"
    assert palabras[1:4] == ["documentacion", "p2p", "anonimato"]
    assert "proyecto" in palabras and "red" in palabras


def test_las_palabras_vacias_no_gastan_el_cupo():
    palabras = portal().keywords()
    for vacia in ("del", "por", "que", "como"):
        assert vacia not in palabras


def test_publicar_y_buscar_normalizan_igual_los_acentos():
    assert normalize_word("Documentación") == "documentacion"
    assert "documentacion" in portal(tags=[]).keywords()  # viene de «Documentación»
    assert _words("documentación anonimato") == ["documentacion", "anonimato"]


# ------------------------------------------------------------ por nombre
class Resolver:
    def __init__(self, descriptor):
        self.descriptor = descriptor
        self.asked: list[str] = []

    async def resolve(self, target: str):
        self.asked.append(target)
        if target.removesuffix(".null") != self.descriptor.name:
            raise LookupError("no registrado")
        return self.descriptor


@pytest.mark.parametrize("consulta", ["nullcell", "nullcell.null", "  NullCell  "])
async def test_buscar_el_nombre_encuentra_el_portal(indexer, consulta):
    d = portal()
    buscador = Search(indexer, None, resolver=Resolver(d))
    resultados = await buscador.run(consulta)
    assert [r.hit.nid for r in resultados] == [d.nid]
    assert resultados[0].hit.title.startswith("Nullcell")


async def test_lo_encontrado_por_nombre_queda_en_el_indice_local(indexer):
    d = portal()
    await Search(indexer, None, resolver=Resolver(d)).run("nullcell")
    await indexer.flush()
    # Sin resolver ya: la próxima vez lo encuentra el índice local, incluso por una palabra.
    assert [h.nid for h in await Search(indexer, None).indexer.search("anonimato")] == [d.nid]


async def test_un_nombre_que_no_existe_no_rompe_la_busqueda(indexer):
    buscador = Search(indexer, None, resolver=Resolver(portal()))
    assert await buscador.run("panaderia") == []


def test_solo_se_resuelven_cosas_con_forma_de_nombre():
    assert _looks_like_name("nullcell")
    assert _looks_like_name("mi-portal.null")
    assert not _looks_like_name("como funciona")
    assert not _looks_like_name("")


# ------------------------------------------------------------ renovación
async def test_la_renovacion_republica_tambien_las_palabras_clave(monkeypatch):
    from nullcell.services import publisher as pub_mod

    hechas: list[str] = []

    class Pub(pub_mod.Publisher):
        async def publish_service(self, descriptor, *, private=False):
            hechas.append("servicio")
            return 1

        async def publish_name(self, name):
            hechas.append("nombre")
            return 1

        async def publish_keywords(self, descriptor, *, limit=12):
            hechas.append("palabras")
            return 1

    p = Pub(Identity.generate(), dht=None, ttl=1)
    monkeypatch.setattr(pub_mod, "RENEW_MARGIN", 0.0)
    monkeypatch.setattr(pub_mod.asyncio, "sleep", _sin_espera(hechas))
    with pytest.raises(_Fin):
        await p.renew_forever(descriptor=portal(), name="nullcell", keywords=True)
    assert hechas[:3] == ["servicio", "nombre", "palabras"]


class _Fin(Exception):
    pass


def _sin_espera(hechas):
    real = asyncio.sleep

    async def sleep(_s, *a, **k):
        if hechas:  # una vuelta completa y basta
            raise _Fin
        await real(0)

    return sleep


# ------------------------------------------------------------ el portal del despliegue
async def test_el_portal_del_despliegue_publica_titulo_etiquetas_y_palabras(tmp_path, monkeypatch):
    """`ncell node --config`: lo que dice `nullcell.toml` llega al descriptor y al buscador."""
    from nullcell.cli.runtime import Runtime
    from nullcell.gateway import control as control_mod
    from tests.integration.test_red_pequena import PAGE, _free_port

    monkeypatch.setattr(control_mod.reach_mod, "classify", lambda endpoint: "internet")
    sitio = tmp_path / "sitio"
    sitio.mkdir()
    (sitio / "index.html").write_bytes(PAGE)
    puerto = _free_port()
    host = Runtime.assemble(
        Identity.generate(), tmp_path / "h", endpoints=[f"127.0.0.1:{puerto}"], hops="auto"
    )
    await host.listen("127.0.0.1", puerto)
    host.open_index()

    publicadas: list = []
    original = host.publisher.publish_keywords

    async def espiar(descriptor, **k):
        publicadas.append(descriptor)
        return await original(descriptor, **k)

    host.publisher.publish_keywords = espiar
    panel = control_mod.ControlPanel(host, home=tmp_path / "h")
    meta = {"title": "Nullcell — documentación", "desc": "La red explicada", "tags": ["p2p"]}
    try:
        estado = await asyncio.wait_for(panel.publish_folder(sitio, name="nullcell", meta=meta), 60)
        assert estado["state"] == "en línea"
        for _ in range(100):
            if publicadas:
                break
            await asyncio.sleep(0.05)
        assert publicadas, "el portal no publicó sus palabras clave"
        d = publicadas[0]
        assert (d.title, d.desc, d.tags) == (meta["title"], meta["desc"], ["p2p"])
        assert d.keywords()[:2] == ["nullcell", "p2p"]
        # Y el propio buscador de la celda lo encuentra ya, por una palabra del título.
        await host.search.indexer.flush()
        assert [r.hit.nid for r in await host.search.run("documentacion")] == [d.nid]
    finally:
        await panel.unpublish(save=False)
        await host.close()
