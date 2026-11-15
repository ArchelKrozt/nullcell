"""Volver a conectar cuando el enlace muere sin avisar.

El caso real: la app de Android dejaba de cargar páginas al rato y había que
cerrarla. El teléfono se dormía, la conexión QUIC con el primer salto moría
por inactividad (o al pasar de wifi a datos), y los circuitos que iban por
ella **seguían marcados como vivos**. La sesión del portal, guardada en caché,
se reutilizaba sobre un cadáver: cada carga esperaba 45 s y fallaba, para
siempre.
"""

from __future__ import annotations

import asyncio

from nullcell.desktop.fetcher import NullFetcher
from nullcell.onion.service import CircuitManager
from nullcell.onion.streams import MultiplexedSession, Stream
from tests.integration.test_dht_over_circuits import cheap_pow, mesh  # noqa: F401

PAGINA = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<h1>vuelve</h1>"


async def test_un_circuito_muere_con_su_enlace_y_su_sesion_con_el():
    async with mesh(2) as relays:
        manager = CircuitManager()
        try:
            circuit = await manager.build([relays[1].hop])
            assert circuit.ready
            session = MultiplexedSession(circuit, noise=None, is_client=True)
            stream_abierto = session.streams[1] = Stream(session, 1)

            # El enlace QUIC se corta (inactividad, cambio de red…).
            circuit.link.closed.set_result(None)
            await asyncio.sleep(0)

            assert circuit.closed and not circuit.ready
            assert session.closed, "la sesión tiene que enterarse"
            # Quien estaba leyendo recibe fin, en vez de esperar 45 s.
            assert await asyncio.wait_for(stream_abierto.read(), 1) == b""
        finally:
            await manager.close()


class _Stream:
    def __init__(self, respuesta: bytes | None) -> None:
        self.respuesta = respuesta
        self.closed = False

    async def write(self, data: bytes) -> None:
        if self.respuesta is None:
            raise ConnectionError("circuito muerto")

    async def read(self) -> bytes:
        r, self.respuesta = self.respuesta, b""
        return r or b""

    async def close(self) -> None:
        self.closed = True


class _Sesion:
    def __init__(self, respuesta: bytes | None) -> None:
        self.respuesta = respuesta
        self.closed = False

    async def open(self, target: bytes = b"") -> _Stream:
        return _Stream(self.respuesta)

    def close(self) -> None:
        self.closed = True


async def test_una_sesion_muerta_se_cambia_por_otra_y_la_pagina_carga():
    abiertas: list[_Sesion] = []

    async def abrir(bare: str) -> _Sesion:
        # La primera vez, una sesión que ya no funciona; la segunda, una buena.
        sesion = _Sesion(None if not abiertas else PAGINA)
        abiertas.append(sesion)
        return sesion

    fetcher = NullFetcher(abrir)
    host = "wuxq7zn2rr4cmze5nm34ujg2n3ehhqq5234obcdaglqjuugtuckih7ib.null"
    pagina = await fetcher.fetch(host, "/")
    assert pagina.status == 200 and b"vuelve" in pagina.body
    assert len(abiertas) == 2
    assert abiertas[0].closed, "la sesión muerta se olvida y se cierra"

    # La buena se reutiliza: no se abre una tercera.
    abiertas[1].respuesta = PAGINA
    await fetcher.fetch(host, "/otra")
    assert len(abiertas) == 2


async def test_una_sesion_que_ya_se_sabe_muerta_no_se_reutiliza():
    abiertas: list[_Sesion] = []

    async def abrir(bare: str) -> _Sesion:
        sesion = _Sesion(PAGINA)
        abiertas.append(sesion)
        return sesion

    fetcher = NullFetcher(abrir)
    host = "wuxq7zn2rr4cmze5nm34ujg2n3ehhqq5234obcdaglqjuugtuckih7ib.null"
    await fetcher.fetch(host, "/")
    abiertas[0].closed = True  # su circuito murió y avisó
    await fetcher.fetch(host, "/")
    assert len(abiertas) == 2
