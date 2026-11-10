"""Chat tipo IRC — M10.

Lo que se prueba aquí es lo que distingue un chat de un canal de ruido: que un
mensaje sea de quien dice ser, que repartirlo a todos no lo multiplique en
pantalla, y que un mensaje hostil no pueda estropear la ventana de quien lo
recibe.
"""

from __future__ import annotations

import time

import pytest

from nullcell.chat.channel import Channel
from nullcell.chat.protocol import MAX_TEXT, Message, MessageError, channel_name, clean_nick
from nullcell.core.identity import Identity


@pytest.fixture
def yo():
    return Identity.generate()


# ------------------------------------------------------------------- nombres
@pytest.mark.parametrize(
    ("raw", "esperado"),
    [("#General", "general"), ("general", "general"), ("  #Pan-Cito ", "pan-cito")],
)
def test_a_channel_name_is_normalised(raw, esperado):
    """Sin normalizar, `#General` y `#general` son canales distintos con la
    misma pinta: la forma más barata de partir una conversación en dos sin que
    nadie entienda por qué."""
    assert channel_name(raw) == esperado


@pytest.mark.parametrize("raw", ["", "   ", "#", "###", "!!!"])
def test_a_channel_needs_a_name(raw):
    with pytest.raises(MessageError):
        channel_name(raw)


def test_a_nick_cannot_rewrite_the_line_next_to_it():
    """Un apodo con un retorno de carro reescribe la línea de al lado en
    cualquier vista de texto, y las marcas bidireccionales reordenan lo que se
    lee sin cambiar lo que dice."""
    assert "\r" not in clean_nick("malo\r\nSYSTEM:")
    assert "\n" not in clean_nick("malo\r\nSYSTEM:")
    assert clean_nick("a‮b") == "ab"
    assert clean_nick("") == "anon"
    assert len(clean_nick("x" * 200)) <= 24


# ------------------------------------------------------------------ mensajes
def test_a_message_round_trips(yo):
    original = Message.create(yo, "#general", "Diego", "hola a todos")
    back = Message.decode(original.encode())
    assert (back.text, back.nick, back.channel) == ("hola a todos", "Diego", "general")
    assert back.author == original.author


def test_a_tampered_message_is_refused(yo):
    """La firma no está por confidencialidad —la sesión ya cifra— sino porque
    en un canal cualquiera puede entregarte un mensaje y decir que lo escribió
    otro."""
    original = Message.create(yo, "general", "Diego", "hola")
    raw = bytearray(original.encode())
    raw[raw.index(b"hola") : raw.index(b"hola") + 4] = b"adio"
    with pytest.raises(MessageError, match="firma"):
        Message.decode(bytes(raw))


def test_a_message_cannot_be_attributed_to_somebody_else(yo):
    """Reusar una firma válida bajo otra clave no cuela."""
    otro = Identity.generate()
    original = Message.create(yo, "general", "Diego", "hola")
    forged = Message(
        channel=original.channel,
        nick=original.nick,
        text=original.text,
        ts=original.ts,
        pub=otro.public_key,
        sig=original.sig,
    )
    with pytest.raises(MessageError, match="firma"):
        Message.decode(forged.encode())


def test_an_empty_or_enormous_message_is_refused(yo):
    with pytest.raises(MessageError):
        Message.create(yo, "general", "d", "   ")
    with pytest.raises(MessageError, match="caracteres"):
        Message.create(yo, "general", "d", "x" * (MAX_TEXT + 1))


def test_a_message_from_the_future_is_refused(yo):
    """Sin esto se puede reordenar una conversación a voluntad."""
    m = Message.create(yo, "general", "d", "hola")
    m.ts = time.time() + 3600
    m.sig = yo.sign(m._signed_bytes())  # noqa: SLF001
    with pytest.raises(MessageError, match="futuro"):
        Message.decode(m.encode())


def test_a_very_old_message_is_refused(yo):
    m = Message.create(yo, "general", "d", "hola")
    m.ts = time.time() - 200_000
    m.sig = yo.sign(m._signed_bytes())  # noqa: SLF001
    with pytest.raises(MessageError, match="viejo"):
        Message.decode(m.encode())


@pytest.mark.parametrize("raw", [b"", b"no json", b"[]", b"{}", b'{"c":"x"}'])
def test_rubbish_is_refused_without_crashing(raw):
    with pytest.raises(MessageError):
        Message.decode(raw)


def test_the_identifier_comes_from_the_content(yo):
    a = Message.create(yo, "general", "d", "hola")
    assert Message.decode(a.encode()).mid == a.mid
    time.sleep(0.002)
    b = Message.create(yo, "general", "d", "hola")
    assert a.mid != b.mid, "dos mensajes distintos no comparten identificador"


# ------------------------------------------------------------------- canal
def test_the_same_message_twice_appears_once(yo):
    """Repartir a todos significa que vuelven copias: sin deduplicar, cada
    mensaje aparecería tantas veces como miembros haya."""
    canal = Channel("general", yo, nick="Diego")
    otro = Identity.generate()
    raw = Message.create(otro, "general", "Ana", "hola").encode()

    assert canal.accept(raw) is not None
    assert canal.accept(raw) is None
    assert len(canal.history) == 1


def test_a_message_for_another_channel_is_not_shown(yo):
    canal = Channel("general", yo)
    otro = Identity.generate()
    assert canal.accept(Message.create(otro, "privado", "Ana", "hola").encode()) is None
    assert canal.stats["rejected"] == 1


def test_rubbish_arriving_on_a_session_does_not_break_the_channel(yo):
    canal = Channel("general", yo)
    assert canal.accept(b"\x00\xff basura") is None
    assert canal.stats["rejected"] == 1
    assert canal.accept(Message.create(Identity.generate(), "general", "A", "ok").encode())


async def test_what_you_say_is_kept_even_with_nobody_listening(yo):
    """Un canal vacío no debe tragarse lo que escribes."""
    canal = Channel("general", yo, nick="Diego")
    said = await canal.say("hola a nadie")
    assert canal.recent()[-1]["text"] == "hola a nadie"
    assert canal.recent()[-1]["mine"] is True
    assert canal.accept(said.encode()) is None, "lo propio no vuelve a entrar"


async def test_a_message_is_delivered_to_every_member(yo):
    """Sin servidor: cada miembro recibe de quien escribe."""

    class FakeSession:
        def __init__(self) -> None:
            self.got: list[bytes] = []

        async def send(self, data: bytes) -> None:
            self.got.append(data)

    canal = Channel("general", yo, nick="Diego")
    sesiones = [FakeSession() for _ in range(3)]
    for i, s in enumerate(sesiones):
        canal._sessions[bytes([i]) * 32] = s  # noqa: SLF001

    await canal.say("hola")
    assert all(len(s.got) == 1 for s in sesiones)
    assert canal.stats["delivered"] == 3


async def test_one_member_failing_does_not_stop_the_others(yo):
    class Rota:
        async def send(self, data: bytes) -> None:
            raise ConnectionError("caída")

    class Buena:
        def __init__(self) -> None:
            self.got: list[bytes] = []

        async def send(self, data: bytes) -> None:
            self.got.append(data)

    canal = Channel("general", yo)
    buena = Buena()
    canal._sessions[b"\x01" * 32] = Rota()  # noqa: SLF001
    canal._sessions[b"\x02" * 32] = buena  # noqa: SLF001

    await canal.say("hola")
    assert len(buena.got) == 1
    assert canal.stats["failed"] == 1
    assert b"\x01" * 32 not in canal._sessions, "la sesión caída se olvida"  # noqa: SLF001


def test_history_is_bounded(yo):
    from nullcell.chat.channel import HISTORY

    canal = Channel("general", yo)
    otro = Identity.generate()
    for i in range(HISTORY + 50):
        canal.accept(Message.create(otro, "general", "A", f"mensaje {i}").encode())
    assert len(canal.history) == HISTORY
    assert canal.recent(1)[0]["text"] == f"mensaje {HISTORY + 49}"
