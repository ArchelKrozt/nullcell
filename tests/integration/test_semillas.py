"""Gestor e intercambio de semillas: que una semilla añadida por alguien llegue
a todos, sin que nadie pueda inundar la red de direcciones falsas."""

from __future__ import annotations

import asyncio
import time

import pytest

from nullcell.core import ids
from nullcell.core.identity import Identity
from nullcell.discovery import seeds as seeds_mod
from nullcell.discovery.seeds import MAX_FAILS, MAX_PER_PREFIX, SeedBook, parse_line
from tests.integration.test_browse import _free_port


def linea(host: str = "85.10.20.7", port: int = 4433, ident: Identity | None = None) -> str:
    return f"{(ident or Identity.generate()).nid}@{host}:{port}"


# ---------------------------------------------------------------- el gestor
def test_una_semilla_mal_formada_no_entra(tmp_path):
    book = SeedBook(tmp_path / "s.json")
    for mala in ("", "hola", "abc@1.2.3.4:4433", f"{Identity.generate().nid}@sinpuerto"):
        assert parse_line(mala) is None
        with pytest.raises(ValueError):
            book.add(mala)


def test_la_propia_direccion_no_es_una_semilla(tmp_path):
    yo = Identity.generate()
    book = SeedBook(tmp_path / "s.json", own_nid=yo.nid)
    with pytest.raises(ValueError):
        book.add(f"{yo.nid}@203.0.113.1:4433")
    assert book.merge([f"{yo.nid}@203.0.113.1:4433"]) == []


def test_lo_recibido_no_se_reparte_hasta_que_contesta(tmp_path):
    book = SeedBook(tmp_path / "s.json")
    [nueva] = book.merge([linea()])
    assert nueva.source == "compartida" and not nueva.verified
    assert book.shareable() == []  # nunca se reenvía de oídas
    book.mark(nueva.line, True)
    assert book.shareable() == [nueva.line]


def test_una_compartida_que_no_contesta_se_olvida_y_una_tuya_nunca(tmp_path):
    book = SeedBook(tmp_path / "s.json")
    [ajena] = book.merge([linea("91.20.30.1")])
    mia = book.add(linea("94.30.40.1"), source="tuya")
    for _ in range(MAX_FAILS):
        book.mark(ajena.line, False)
        book.mark(mia.line, False)
    assert book.get(ajena.line) is None
    assert book.get(mia.line) is not None


def test_no_se_puede_inundar_desde_una_sola_red(tmp_path):
    book = SeedBook(tmp_path / "s.json")
    misma_red = [linea(f"198.51.{i // 250}.{i % 250 + 1}") for i in range(20)]
    nuevas = book.merge(misma_red)
    assert len(nuevas) == MAX_PER_PREFIX


def test_las_direcciones_privadas_no_viajan(tmp_path):
    book = SeedBook(tmp_path / "s.json")
    assert book.merge([linea("192.168.1.10"), linea("10.0.0.5"), linea("127.0.0.1")]) == []


def test_el_gestor_sobrevive_a_un_reinicio(tmp_path):
    ruta = tmp_path / "s.json"
    book = SeedBook(ruta)
    s = book.add(linea(), source="tuya", note="Raspberry de casa")
    book.mark(s.line, True)
    book.save()
    otra = SeedBook(ruta).load()
    copia = otra.get(s.line)
    assert copia.source == "tuya" and copia.note == "Raspberry de casa" and copia.verified


def test_la_semilla_del_programa_entra_como_incluida(tmp_path):
    from nullcell.discovery.bootstrap import load_bootstrap

    book = SeedBook(tmp_path / "s.json")
    book.seed_from_bootstrap(load_bootstrap())
    assert [s.source for s in book] == ["incluida"]
    # Si el dueño la añade a mano, pasa a ser suya.
    book.add(next(iter(book)).line, source="tuya")
    assert [s.source for s in book] == ["tuya"]


# ------------------------------------------------- intercambio de verdad
async def celda(tmp_path, nombre):
    from nullcell.cli.runtime import Runtime

    puerto = _free_port()
    ep = f"127.0.0.1:{puerto}"
    rt = Runtime.assemble(Identity.generate(), tmp_path / nombre, endpoints=[ep], hops="auto")
    await rt.listen("127.0.0.1", puerto)
    return rt, ep


async def test_una_semilla_viaja_de_un_usuario_a_otro_por_el_relevo(tmp_path, monkeypatch):
    """Ana añade a mano la Raspberry; el relevo la aprende de Ana, la comprueba,
    y Bruno —que sólo conoce al relevo— la recibe."""
    monkeypatch.setattr(seeds_mod, "_public", lambda endpoint: True)  # loopback en la prueba
    relevo, ep_relevo = await celda(tmp_path, "relevo")
    raspberry, ep_rpi = await celda(tmp_path, "raspberry")
    ana, _ = await celda(tmp_path, "ana")
    bruno, _ = await celda(tmp_path, "bruno")
    try:
        relevo.start_seed_exchange()  # verifica lo que le ofrecen
        semilla_relevo = f"{relevo.identity.nid}@{ep_relevo}"
        semilla_rpi = f"{raspberry.identity.nid}@{ep_rpi}"

        # Ana: conoce al relevo y añade la Raspberry; las dos contestan.
        ana.seeds.add(semilla_relevo, source="tuya")
        ana.seeds.add(semilla_rpi, source="tuya", note="Raspberry")
        await ana.node.bootstrap(ana.seed_nodes())
        assert await ana.verify_seeds() >= 2
        assert await ana.node.exchange_seeds(relevo.seeds and _nodo(relevo, ep_relevo)) >= 0

        # El relevo la recibió sin verificar y la comprueba él mismo.
        for _ in range(100):
            s = relevo.seeds.get(semilla_rpi)
            if s is not None and s.verified:
                break
            await asyncio.sleep(0.05)
        assert relevo.seeds.get(semilla_rpi).verified, "el relevo no comprobó la semilla de Ana"

        # Bruno sólo conoce al relevo… y recibe la Raspberry.
        bruno.seeds.add(semilla_relevo, source="tuya")
        await bruno.node.bootstrap(bruno.seed_nodes())
        assert await bruno.node.exchange_seeds(_nodo(relevo, ep_relevo)) >= 1
        recibida = bruno.seeds.get(semilla_rpi)
        assert recibida is not None and recibida.source == "compartida"
        assert not recibida.verified  # Bruno la comprobará él antes de repartirla
        assert await bruno.verify_seeds() >= 1
        assert bruno.seeds.get(semilla_rpi).verified
    finally:
        for rt in (bruno, ana, raspberry, relevo):
            await rt.close()


async def test_una_semilla_falsa_no_se_reparte(tmp_path, monkeypatch):
    """Alguien ofrece una dirección que no es quien dice: no pasa del relevo."""
    monkeypatch.setattr(seeds_mod, "_public", lambda endpoint: True)
    relevo, ep_relevo = await celda(tmp_path, "relevo")
    otro, ep_otro = await celda(tmp_path, "otro")
    try:
        relevo.start_seed_exchange()
        impostora = f"{Identity.generate().nid}@{ep_otro}"  # endpoint real, identidad falsa
        nuevas = relevo.node.seeds.merge([impostora])
        relevo.node.on_seeds(nuevas)
        await asyncio.sleep(1.0)
        s = relevo.seeds.get(impostora)
        assert s is None or not s.verified
        assert impostora not in relevo.seeds.shareable()
    finally:
        await otro.close()
        await relevo.close()


def _nodo(rt, ep):
    from nullcell.dht.routing_table import Node

    return Node(pub=rt.identity.public_key, endpoints=[ep])


def test_el_estado_del_gestor_para_la_interfaz(tmp_path):
    book = SeedBook(tmp_path / "s.json")
    s = book.add(linea(), source="tuya", note="VPS")
    book.mark(s.line, True)
    [d] = book.describe()
    assert d["line"] == s.line and d["verified"] and d["note"] == "VPS"
    assert d["source"] == "tuya" and isinstance(d["public"], bool)
    assert time.time() - d["last_ok"] < 5
    assert ids.decode_nid(d["nid"])
