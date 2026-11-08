"""M1 performance gate — NULLCELL-ARQUITECTURA.md §16.

The gates are expressed against the *measured transport ceiling* rather than an
absolute frames/s number, because the first run of this benchmark showed the
ceiling is set by aioquic's pure-Python QUIC processing (~21 MB/s on loopback,
flat for any write of 4 KB or more), while our own frame layer packs at well
over 1 GB/s. An absolute target of 40.000 frames/s turned out to be ~97 % of
what the transport can do at all, which measures aioquic, not Nullcell.

Gates:
  * frame layer      >= 500.000 marcos/s packed, 1 core
  * allocations      <  1 byte per frame in the steady state
  * link efficiency  >= 85 % of the measured raw-QUIC ceiling

Run: python bench/link_throughput.py
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import socket
import sys
import time
import tracemalloc

from nullcell.core.frames import FRAME_SIZE, Cmd, FrameBuffer, pack, pack_relay_payload
from nullcell.core.identity import Identity
from nullcell.net.listener import Listener
from nullcell.net.quic_link import open_link

TARGET_PACK_FPS = 500_000
TARGET_ALLOC_BYTES = 1.0
TARGET_EFFICIENCY = 0.85
PAYLOAD = pack_relay_payload(1, b"x" * 496)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def bench_pack_naive(n: int = 200_000) -> float:
    """The convenience API: one bytearray + one bytes per frame."""
    t0 = time.perf_counter()
    for i in range(n):
        pack(i & 0xFFFF, Cmd.RELAY, PAYLOAD)
    return n / (time.perf_counter() - t0)


def bench_pack_buffered(n: int = 200_000, batch: int = 64) -> float:
    """The hot path: pack into one preallocated buffer, no per-frame allocation."""
    buf = FrameBuffer(batch)
    t0 = time.perf_counter()
    for i in range(n):
        if buf.full:
            buf.view()  # what the link layer would hand to send_stream_data
            buf.reset()
        buf.add(i & 0xFFFF, Cmd.RELAY, PAYLOAD)
    return n / (time.perf_counter() - t0)


def measure_allocations(n: int = 20_000, batch: int = 64) -> float:
    """Allocations per frame in the steady state, excluding warm-up."""
    buf = FrameBuffer(batch)
    for i in range(batch * 4):  # warm up: let the slots be written once
        if buf.full:
            buf.reset()
        buf.add(i, Cmd.RELAY, PAYLOAD)
    buf.reset()

    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    for i in range(n):
        if buf.full:
            buf.reset()
        buf.add(i & 0xFFFF, Cmd.RELAY, PAYLOAD)
    after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    total = sum(s.size_diff for s in after.compare_to(before, "lineno"))
    return total / n


async def bench_link(n: int = 120_000, batch: int = 64) -> tuple[float, float]:
    """End-to-end over a real loopback QUIC link. Returns (frames/s, MB/s)."""
    identity = Identity.generate()
    port = _free_port()
    listener = Listener(identity, "127.0.0.1", port)
    await listener.start()

    received = 0
    done = asyncio.Event()

    def count(circ_id, cmd, payload):
        nonlocal received
        received += 1
        if received >= n:
            done.set()

    try:
        async with open_link(identity.nid, "127.0.0.1", port) as link:
            for _ in range(200):
                if listener.links:
                    break
                await asyncio.sleep(0.01)
            listener.links[-1].on_frame = count

            frames = [(1, Cmd.RELAY, PAYLOAD)] * batch
            t0 = time.perf_counter()
            sent = 0
            while sent < n:
                link.send_frames(frames)
                sent += batch
                await asyncio.sleep(0)  # let the loop drain
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(done.wait(), timeout=60.0)
            elapsed = time.perf_counter() - t0
    finally:
        listener.close()

    fps = received / elapsed
    return fps, fps * FRAME_SIZE / 1e6


async def bench_raw_ceiling(total_mb: int = 24, chunk: int = 32768) -> float:
    """Raw bytes through a QUIC stream, bypassing our frame layer entirely.

    This is the ceiling everything else is measured against: whatever aioquic
    can move, Nullcell cannot exceed.
    """
    identity = Identity.generate()
    port = _free_port()
    listener = Listener(identity, "127.0.0.1", port)
    await listener.start()

    target = total_mb * 1_000_000
    got = 0
    done = asyncio.Event()

    try:
        async with open_link(identity.nid, "127.0.0.1", port) as link:
            for _ in range(200):
                if listener.links:
                    break
                await asyncio.sleep(0.01)

            server = listener.links[-1]

            def sink(data: bytes) -> None:
                nonlocal got
                got += len(data)
                if got >= target:
                    done.set()

            server._feed = sink  # count bytes, skip reassembly

            stream_id = link._ensure_stream()
            blob = b"x" * chunk
            sent = 0
            t0 = time.perf_counter()
            while sent < target:
                link._quic.send_stream_data(stream_id, blob)
                sent += chunk
                link.transmit()
                await asyncio.sleep(0)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(done.wait(), timeout=60.0)
            elapsed = time.perf_counter() - t0
    finally:
        listener.close()

    return got / elapsed / 1e6


def main() -> int:
    print("Nullcell — puerta de rendimiento M1 (§16)")
    print(f"python {sys.version.split()[0]} · marco {FRAME_SIZE} B\n")

    naive = bench_pack_naive()
    buffered = bench_pack_buffered()
    print("empaquetado (1 nucleo, sin red)")
    print(
        f"  pack() por marco      {naive:>12,.0f} marcos/s  {naive * FRAME_SIZE / 1e6:>7.1f} MB/s"
    )
    print(
        f"  FrameBuffer (lote 64) {buffered:>12,.0f} marcos/s  "
        f"{buffered * FRAME_SIZE / 1e6:>7.1f} MB/s"
    )
    # Both are ~50x the transport ceiling, so the difference between them is
    # measurement noise and reporting a ratio would be misleading. The payoff of
    # batching is in the *write size* it enables, measured below.
    print("  (ambos ~50x por encima del techo del transporte: la diferencia es ruido)")

    per_frame = measure_allocations()
    print("\nasignaciones en estado estable")
    print(f"  bytes por marco       {per_frame:>12.2f}   objetivo: 0")

    print("\ncoalescencia: MB/s segun el tamano de escritura (§16, tactica 1)")
    small = asyncio.run(bench_raw_ceiling(total_mb=8, chunk=FRAME_SIZE))
    big = asyncio.run(bench_raw_ceiling(total_mb=8, chunk=32768))
    print(f"  1 marco por escritura {small:>12.1f} MB/s")
    print(f"  lote de 64 marcos     {big:>12.1f} MB/s   -> {big / small:.1f}x")

    ceiling = asyncio.run(bench_raw_ceiling())
    fps, mbps = asyncio.run(bench_link())
    efficiency = mbps / ceiling if ceiling else 0.0

    print("\nenlace QUIC real sobre loopback")
    print(f"  techo de aioquic      {ceiling:>12.1f} MB/s   (bytes crudos, sin marcos)")
    print(f"  con nuestros marcos   {mbps:>12.1f} MB/s   {fps:>10,.0f} marcos/s")
    print(f"  eficiencia de la capa {efficiency * 100:>12.1f} %")

    print("\n--- veredicto ---")
    checks = (
        ("capa de marcos", buffered, TARGET_PACK_FPS, "marcos/s", True),
        ("asignaciones por marco", per_frame, TARGET_ALLOC_BYTES, "bytes", False),
        ("eficiencia sobre el techo", efficiency, TARGET_EFFICIENCY, "", True),
    )
    ok = True
    for name, value, target, unit, higher_is_better in checks:
        passed = value >= target if higher_is_better else value < target
        mark = "PASA " if passed else "FALLA"
        if not higher_is_better:
            print(f"  {mark} {name}: {value:.3f} bytes (objetivo < {target:.0f})")
        elif unit:
            print(f"  {mark} {name}: {value:,.0f} {unit} (objetivo ≥ {target:,.0f})")
        else:
            print(f"  {mark} {name}: {value * 100:.1f} % (objetivo ≥ {target * 100:.0f} %)")
        ok &= passed

    print("\nNota: el rendimiento absoluto lo fija aioquic, no Nullcell. Ver §16, hallazgo de M1.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
