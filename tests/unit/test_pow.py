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


async def test_solve_async_does_not_block_the_event_loop():
    """§16: 400 ms of frozen loop drops live circuits."""
    import asyncio
    import time

    delays: list[float] = []
    running = True

    async def heartbeat():
        while running:
            t0 = time.perf_counter()
            await asyncio.sleep(0.01)
            delays.append((time.perf_counter() - t0 - 0.01) * 1000)

    beat = asyncio.create_task(heartbeat())
    try:
        nonce = await P.solve_async(b"reto-async", 10)
    finally:
        running = False
        beat.cancel()

    P.verify(b"reto-async", nonce, 10)
    assert delays, "heartbeat never ran"
    assert max(delays) < 50, f"event loop stalled {max(delays):.0f} ms (budget 50 ms)"


async def test_pool_can_be_shut_down_and_reused():
    nonce = await P.solve_async(b"reto", LOW)
    P.verify(b"reto", nonce, LOW)
    P.shutdown_pool()
    nonce = await P.solve_async(b"reto", LOW)  # pool rebuilt lazily
    P.verify(b"reto", nonce, LOW)
