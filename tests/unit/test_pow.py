"""Proof of work — §10. Tests use low difficulty; real values are in DIFFICULTY."""

import os

import pytest

from nullcell.dht import pow as P

LOW = 4  # ~16 attempts, milliseconds


def test_leading_zero_bits():
    assert P.leading_zero_bits(b"\xff") == 0
    assert P.leading_zero_bits(b"\x7f") == 1
    assert P.leading_zero_bits(b"\x01") == 7
    assert P.leading_zero_bits(b"\x00\xff") == 8
    assert P.leading_zero_bits(b"\x00\x00") == 16


def test_solve_then_verify():
    challenge = b"reto"
    nonce = P.solve(challenge, LOW)
    assert len(nonce) == P.NONCE_LEN
    P.verify(challenge, nonce, LOW)  # must not raise


def test_a_proof_does_not_transfer_to_another_challenge():
    """The whole point: work is bound to the exact bytes it was done over."""
    nonce = P.solve(b"reto-a", 8)
    with pytest.raises(P.PowError):
        P.verify(b"reto-b", nonce, 8)


def test_insufficient_difficulty_rejected():
    with pytest.raises(P.PowError, match="needs"):
        P.verify(b"reto", b"\x00" * P.NONCE_LEN, 40)


def test_malformed_nonce_rejected():
    for bad in (b"", b"\x00" * 8, b"\x00" * 32):
        with pytest.raises(P.PowError, match="nonce must be"):
            P.verify(b"reto", bad, 1)


def test_zero_difficulty_always_passes():
    P.verify(b"reto", os.urandom(P.NONCE_LEN), 0)


def test_negative_difficulty_rejected():
    with pytest.raises(P.PowError):
        P.solve(b"reto", -1)


def test_gives_up_instead_of_looping_forever():
    with pytest.raises(P.PowError, match="no solution"):
        P.solve(b"reto", 64, max_attempts=4)


def test_difficulty_table_covers_every_record_type():
    from nullcell.dht.records import RecordType

    for record_type in RecordType:
        assert str(record_type) in P.DIFFICULTY
    # Scarce or spam-attractive resources must cost more than routine ones.
    assert P.DIFFICULTY["name"] > P.DIFFICULTY["keyword"] > P.DIFFICULTY["relay"]


async def test_solve_async_runs_off_the_event_loop_thread(monkeypatch):
    """§16: 400 ms of frozen loop drops live circuits.

    Asserted on *where the work runs*, not on how long the loop was quiet.
    The first version of this test ran a heartbeat every 10 ms and required
    at least one sample, and CI failed it on Python 3.12 with "heartbeat never
    ran" — because the runner solved 10 bits in under 10 ms. The best possible
    outcome was being reported as the failure, and the test got less reliable
    the faster the machine. Thread identity gives the same guarantee and does
    not race.
    """
    import threading

    real_solve = P.solve
    threads: list[int] = []

    def spy(*args, **kwargs):
        threads.append(threading.get_ident())
        return real_solve(*args, **kwargs)

    monkeypatch.setattr(P, "solve", spy)

    nonce = await P.solve_async(b"reto-async", 10)

    P.verify(b"reto-async", nonce, 10)
    assert threads, "solve was never called"
    assert threading.get_ident() not in threads, "the search ran on the event loop thread"
    assert len(set(threads)) > 1 or P.WORKERS == 1, "the search was not spread across workers"


async def test_the_loop_keeps_breathing_during_a_long_solve():
    """The other half, measured against a baseline rather than a number.

    §16's own rule, applied to its own test: a gate expressed as an absolute
    figure measures the machine, not the code. A 50 ms ceiling failed on a
    loaded machine while the property —the loop keeps getting control— was
    perfectly intact, which is the same mistake as `heartbeat never ran`
    wearing different clothes.

    So the loop is timed **idle first**, and the solve is only allowed to make
    it a bounded amount worse. On a machine where the idle loop is already
    slow, the budget grows with it, which is the honest comparison.
    """
    import asyncio
    import time

    async def latidos(duracion: float, trabajo=None):
        gaps: list[float] = []
        corriendo = True

        async def heartbeat():
            while corriendo:
                t0 = time.perf_counter()
                await asyncio.sleep(0)
                gaps.append((time.perf_counter() - t0) * 1000)

        beat = asyncio.create_task(heartbeat())
        try:
            resultado = await (trabajo if trabajo is not None else asyncio.sleep(duracion))
        finally:
            corriendo = False
            beat.cancel()
        return gaps, resultado

    base, _ = await latidos(0.4)
    assert base, "el bucle no corrió ni estando ocioso"

    # El techo sale del requisito, no de un número elegido a ojo: §16 dice que
    # **400 ms de bucle congelado tiran circuitos vivos**. 200 ms es la mitad
    # de eso, así que esta puerta sigue siendo más estricta que lo que el
    # sistema necesita, y a la vez deja de romperse en una máquina cargada.
    #
    # Es la tercera vez que una puerta de temporización de este fichero está
    # mal calibrada —primero «heartbeat never ran», luego 50 ms absolutos,
    # ahora 8× el reposo— y las tres por lo mismo: medir el entorno creyendo
    # medir el código. El factor relativo se queda sólo para subir el listón
    # en máquinas lentas, nunca para bajarlo.
    techo = max(max(base) * 20, 200.0)

    durante, nonce = await latidos(0, P.solve_async(b"reto-largo", 14))
    P.verify(b"reto-largo", nonce, 14)

    assert durante, "el bucle nunca recuperó el control"
    assert max(durante) < techo, (
        f"el bucle se paró {max(durante):.0f} ms; ocioso su peor pausa es "
        f"{max(base):.1f} ms, y §16 pone el daño real en 400 ms, "
        f"así que el techo era {techo:.0f} ms"
    )


async def test_pool_can_be_shut_down_and_reused():
    nonce = await P.solve_async(b"reto", LOW)
    P.verify(b"reto", nonce, LOW)
    P.shutdown_pool()
    nonce = await P.solve_async(b"reto", LOW)  # pool rebuilt lazily
    P.verify(b"reto", nonce, LOW)
