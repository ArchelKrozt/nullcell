"""Proof of work for DHT publication — §7, §10.

Anti-Sybil: publishing a record costs real CPU time, so flooding the network
with fake relays, names or keywords costs real money.

**Why these parameters, and why verification cost drove the choice.** argon2id
is symmetric: one solve attempt and one verification cost the same. The useful
asymmetry therefore comes from the *difficulty bits* (a solve needs ``2^bits``
attempts, a verification needs one), not from memory hardness. That matters
because every DHT node must verify every record it is offered, so an expensive
verification is a denial-of-service surface aimed at honest nodes.

Measured on the development machine (Python 3.11, 1 core):

| memoria  | ms/hash | bits para ~0,4 s | verificaciones/s | asimetría |
|----------|---------|------------------|------------------|-----------|
| 8 KiB    | 0,118   | 12               | 8465             | 4096x     |
| 64 KiB   | 0,296   | 10               | 3373             | 1024x     |
| **256 KiB** | **0,911** | **9**        | **1097**         | **512x**  |
| 1 MiB    | 3,385   | 7                | 295              | 128x      |
| 4 MiB    | 13,403  | 5                | 75               | 32x       |

256 KiB is the point where memory hardness is still meaningful (32x the
PyNaCl minimum, so GPU parallelism is not free) while a node can still verify
over a thousand records per second. Going further right collapses verification
throughput, and the extra memory hardness buys less than the DoS surface costs.

PoW is one measure among several (§7 adds route diversity, cross-verification,
observed age and opt-in curated lists). It is not meant to be unbreakable.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Final

import nacl.pwhash

__all__ = [
    "DIFFICULTY",
    "MEMLIMIT",
    "NONCE_LEN",
    "OPSLIMIT",
    "PowError",
    "bits_of",
    "leading_zero_bits",
    "shutdown_pool",
    "solve",
    "solve_async",
    "verify",
]

_ARGON = nacl.pwhash.argon2id
OPSLIMIT: Final[int] = 1
MEMLIMIT: Final[int] = 256 * 1024  # bytes
_DIGEST_LEN: Final[int] = 32
NONCE_LEN: Final[int] = 16

#: Difficulty per record type, in leading zero bits. Scarcer or more
#: spam-attractive resources cost more: a name is first-come and permanent-ish,
#: a keyword entry is the direct spam vector (§8).
DIFFICULTY: Final[dict[str, int]] = {
    # Expected solve time is 2**bits * 0,911 ms. These are medians, and the
    # distribution is geometric: individual solves vary by several times.
    "relay": 9,  # ~0,5 s — renewed hourly by every relay
    "service": 9,  # ~0,5 s — renewed within its TTL
    "private": 9,  # ~0,5 s — same cost; privacy is not a discount
    "keyword": 11,  # ~1,9 s each; a site with 10 keywords pays ~19 s
    "name": 14,  # ~15 s — first-come on a scarce global namespace
    # La presencia se renueva cada pocos minutos mientras el chat está
    # abierto, así que su coste se paga una y otra vez: barata a propósito.
    # Lo que la protege de ser inundada no es el PoW sino su TTL corto.
    "presence": 8,  # ~0,2 s
}

_MAX_ATTEMPTS_DEFAULT: Final[int] = 1 << 24


class PowError(Exception):
    """Proof of work missing, malformed, or below the required difficulty."""


def _digest(challenge: bytes, nonce: bytes) -> bytes:
    # The challenge goes into the salt so it is bound to the work without being
    # hashed twice; the nonce is the thing being searched over.
    salt = hashlib.sha256(challenge).digest()[: _ARGON.SALTBYTES]
    return _ARGON.kdf(_DIGEST_LEN, nonce, salt, opslimit=OPSLIMIT, memlimit=MEMLIMIT)


def leading_zero_bits(digest: bytes) -> int:
    """Count leading zero bits of *digest*."""
    bits = 0
    for byte in digest:
        if byte:
            return bits + 8 - byte.bit_length()
        bits += 8
    return bits


def bits_of(challenge: bytes, nonce: bytes) -> int:
    """Measure the work actually done — the digest's leading zero bits.

    This, not the required difficulty of the record type, is what eviction
    scores against (§8): it is self-evident from the record, cannot be
    overstated, and rises only by doing more work. Costs one argon2id hash.
    """
    if len(nonce) != NONCE_LEN:
        raise PowError(f"nonce must be {NONCE_LEN} bytes, got {len(nonce)}")
    return leading_zero_bits(_digest(challenge, nonce))


def verify(challenge: bytes, nonce: bytes, bits: int) -> int:
    """Check one proof of work and return the bits actually found (~0,9 ms)."""
    found = bits_of(challenge, nonce)
    if found < bits:
        raise PowError(f"proof of work is {found} bits, needs {bits}")
    return found


def solve(
    challenge: bytes,
    bits: int,
    *,
    max_attempts: int = _MAX_ATTEMPTS_DEFAULT,
    stop: threading.Event | None = None,
) -> bytes:
    """Search for a nonce meeting *bits* of difficulty.

    Blocking and CPU-bound: never call this from the event loop. Use
    :func:`solve_async`. *stop* lets several racing searches cancel each other.
    """
    if bits < 0:
        raise PowError("difficulty must not be negative")
    for _ in range(max_attempts):
        if stop is not None and stop.is_set():
            raise _Cancelled
        nonce = os.urandom(NONCE_LEN)
        if leading_zero_bits(_digest(challenge, nonce)) >= bits:
            return nonce
    raise PowError(f"no solution in {max_attempts} attempts at {bits} bits")


class _Cancelled(PowError):
    """Another racing search won."""


_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()

#: Threads used to race one proof of work. Measured on the development machine:
#: libsodium's argon2id **releases the GIL** (3,69x speedup on 4 threads, near
#: linear), and a worker thread delays the event loop by at most ~5 ms, well
#: inside the 50 ms budget of §16.
#:
#: This is why a thread pool is enough and a ProcessPoolExecutor is not needed:
#: no pickling, no spawn/fork hazards, and no requirement that the main module
#: be re-importable — which breaks a process pool in a REPL or a piped script.
WORKERS: Final[int] = min(4, (os.cpu_count() or 1))


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="ncell-pow")
        return _pool


async def solve_async(challenge: bytes, bits: int) -> bytes:
    """Solve the proof of work without blocking the event loop (§16).

    400 ms of frozen loop drops live circuits, times out links and hangs the
    HUD, so moving this off the loop is not an optimisation.

    The search is raced across :data:`WORKERS` threads; since each attempt is an
    independent random nonce, N threads cut the expected time by N. That takes a
    14-bit name registration from ~15 s to ~4 s on four cores.
    """
    loop = asyncio.get_running_loop()
    pool = _get_pool()
    stop = threading.Event()
    futures = [
        loop.run_in_executor(pool, lambda: solve(challenge, bits, stop=stop))
        for _ in range(WORKERS)
    ]
    try:
        done, pending = await asyncio.wait(futures, return_when=asyncio.FIRST_COMPLETED)
        for future in done:
            exc = future.exception()
            if exc is None:
                return future.result()
        raise next(iter(done)).exception()  # type: ignore[misc]
    finally:
        stop.set()
        for future in futures:
            future.cancel()


def shutdown_pool() -> None:
    """Release the worker threads. Called on node shutdown and by tests."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
            _pool = None
