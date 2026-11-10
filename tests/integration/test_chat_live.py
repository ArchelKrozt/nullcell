"""Dos celdas hablando por la red — M10.

El test que distingue un protocolo de chat de un montón de clases: dos nodos
que no se conocían se encuentran por el DHT y se entregan un mensaje por una
sesión de rendezvous, sin servidor y sin que ninguno sepa dónde está el otro.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from nullcell.chat import presence
from nullcell.chat.channel import Channel
from nullcell.chat.protocol import Message
from nullcell.core.identity import Identity
from nullcell.dht import records as rec

pytestmark = pytest.mark.asyncio


class FakeDHT:
    """Un DHT compartido en memoria: suficiente para probar el acuerdo."""

    def __init__(self) -> None:
        self.store: dict[bytes, list] = {}

    async def store_record(self, record) -> int:
        self.store.setdefault(record.key, []).append(record)
        return 1

    async def find_value(self, key: bytes) -> list:
        return list(self.store.get(key, []))


async def test_two_cells_find_each_other_in_a_channel():
    """La presencia es el «ping» que busca a otros programas activos."""
    dht = FakeDHT()
    ana, beto = Identity.generate(), Identity.generate()

    class FakeService:
        x25519_public = b"\x01" * 32
        intro_points = [{"pub": b"\x02" * 32, "x": b"\x03" * 32, "ep": ["203.0.113.9:4433"]}]

    await presence.announce(dht, ana, "#general", nick="Ana", service=FakeService())
    await presence.announce(dht, beto, "#general", nick="Beto", service=FakeService())

    vistos = await presence.members(dht, "general", exclude=ana.public_key)
    assert [m.nick for m in vistos] == ["Beto"]
    assert vistos[0].pub == beto.public_key


async def test_presence_in_one_channel_is_not_presence_in_another():
    dht = FakeDHT()
    ana = Identity.generate()

    class FakeService:
        x25519_public = b"\x01" * 32
        intro_points = [{"pub": b"\x02" * 32, "x": b"\x03" * 32, "ep": ["203.0.113.9:4433"]}]

    await presence.announce(dht, ana, "#general", nick="Ana", service=FakeService())
    assert await presence.members(dht, "privado") == []


async def test_stale_presence_is_not_shown():
    """Un registro puede sobrevivir a su TTL en una celda que no ha limpiado, y
    mostrar como presente a quien se fue hace horas es peor que no mostrarlo."""
    dht = FakeDHT()
    ana = Identity.generate()
    body = {
        "ch": "general",
        "n": "Ana",
        "x": b"\x01" * 32,
        "ip": [{"pub": b"\x02" * 32, "x": b"\x03" * 32, "ep": ["203.0.113.9:4433"]}],
    }
    record = await rec.build(ana, rec.RecordType.PRESENCE, body, ttl=presence.PRESENCE_TTL)
    object.__setattr__(record, "ts", record.ts - presence.PRESENCE_TTL - 60)
    await dht.store_record(record)

    assert await presence.members(dht, "general") == []


async def test_a_message_crosses_between_two_channels():
    """Dos canales unidos por un par de sesiones: lo que escribe una, la otra
    lo ve, verificado y sin pasar por ningún servidor."""

    class Pipe:
        """Una sesión que entrega al canal del otro extremo."""

        def __init__(self, deliver) -> None:
            self.deliver = deliver
            self.on_data = None

        async def send(self, data: bytes) -> None:
            await asyncio.sleep(0)
            self.deliver(bytes(data))

        def close(self) -> None:
            pass

    ana, beto = Identity.generate(), Identity.generate()
    canal_a = Channel("general", ana, nick="Ana")
    canal_b = Channel("general", beto, nick="Beto")

    canal_a._sessions[beto.public_key] = Pipe(canal_b.accept)  # noqa: SLF001
    canal_b._sessions[ana.public_key] = Pipe(canal_a.accept)  # noqa: SLF001

    try:
        await canal_a.say("hola Beto")
        await asyncio.sleep(0.05)

        recibido = [m for m in canal_b.recent() if not m["mine"]]
        assert [m["text"] for m in recibido] == ["hola Beto"]
        assert recibido[0]["nick"] == "Ana"
        assert recibido[0]["author"] == ana.hostname.removesuffix(".null")

        await canal_b.say("hola Ana")
        await asyncio.sleep(0.05)
        assert [m["text"] for m in canal_a.recent() if not m["mine"]] == ["hola Ana"]
    finally:
        await canal_a.close()
        await canal_b.close()


async def test_a_member_cannot_speak_as_somebody_else():
    """Lo que un canal abierto necesita: cualquiera puede entregarte un
    mensaje, así que la firma decide de quién es."""
    ana, beto = Identity.generate(), Identity.generate()
    canal = Channel("general", ana)

    legitimo = Message.create(beto, "general", "Beto", "hola")
    falso = Message(
        channel="general",
        nick="Beto",
        text="yo nunca dije esto",
        ts=legitimo.ts,
        pub=beto.public_key,
        sig=legitimo.sig,
    )
    assert canal.accept(falso.encode()) is None
    assert canal.accept(legitimo.encode()) is not None


async def test_closing_a_channel_releases_its_sessions():
    cerradas = []

    class Sesion:
        def close(self) -> None:
            cerradas.append(True)

        async def send(self, data: bytes) -> None:
            pass

    canal = Channel("general", Identity.generate())
    canal._sessions[b"\x01" * 32] = Sesion()  # noqa: SLF001
    with contextlib.suppress(Exception):
        await canal.close()
    assert cerradas == [True]
