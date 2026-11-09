"""M3 performance gate — NULLCELL-ARQUITECTURA.md §16.

Targets:
  * circuit build, 3 hops on a local network: <= 150 ms
  * onion-layer efficiency: 3-hop throughput >= 80 % of (1-hop / 3)

Run: python bench/circuit_build.py
"""

from __future__ import annotations

import asyncio
import contextlib
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from integration.test_circuit import RelayCell  # noqa: E402

from nullcell.core.frames import FRAME_SIZE, RELAY_DATA_SIZE, RelayCmd  # noqa: E402
from nullcell.net.quic_link import open_link  # noqa: E402
from nullcell.onion.circuit import Circuit  # noqa: E402

TARGET_BUILD_MS = 150.0
TARGET_ONION_EFFICIENCY = 0.80


async def bench_build(rounds: int = 10) -> list[float]:
    cells = [await RelayCell().start() for _ in range(3)]
    hops = [cell.hop for cell in cells]
    guard = cells[0]
    timings: list[float] = []
    try:
        async with open_link(guard.identity.nid, "127.0.0.1", guard.port) as link:
            for _ in range(rounds):
                circuit = Circuit(link)
                t0 = time.perf_counter()
                await circuit.build(hops)
                timings.append((time.perf_counter() - t0) * 1000)
                circuit.destroy()
    finally:
        await asyncio.gather(*(cell.close() for cell in cells))
    return timings


async def bench_throughput(frames: int = 4000, hops: int = 3) -> tuple[float, float]:
    """Client to the last hop over *hops* hops. Returns (frames/s, MB/s)."""
    cells = [await RelayCell().start() for _ in range(hops)]
    guard = cells[0]
    received = 0
    done = asyncio.Event()

    def sink(leg, header) -> None:
        nonlocal received
        received += 1
        if received >= frames:
            done.set()

    cells[-1].service.on_relay_message = sink
    payload = b"x" * RELAY_DATA_SIZE
    try:
        async with open_link(guard.identity.nid, "127.0.0.1", guard.port) as link:
            circuit = Circuit(link)
            await circuit.build([cell.hop for cell in cells])
            t0 = time.perf_counter()
            for i in range(frames):
                circuit.send_relay(RelayCmd.DATA, payload, stream_id=1)
                if i % 64 == 0:
                    await asyncio.sleep(0)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(done.wait(), timeout=120.0)
            elapsed = time.perf_counter() - t0
            circuit.destroy()
    finally:
        await asyncio.gather(*(cell.close() for cell in cells))

    fps = received / elapsed
    return fps, fps * FRAME_SIZE / 1e6


def main() -> int:
    print("Nullcell — puerta de rendimiento M3 (§16)")
    print("3 saltos en loopback, un proceso\n")

    builds = asyncio.run(bench_build())
    print("construccion de circuito de 3 saltos")
    print(
        f"  min {min(builds):.1f} · mediana {statistics.median(builds):.1f} · "
        f"max {max(builds):.1f} ms  ({len(builds)} rondas)"
    )

    fps1, mbps1 = asyncio.run(bench_throughput(hops=1))
    fps3, mbps3 = asyncio.run(bench_throughput(hops=3))
    print("\nrendimiento por circuito")
    print(f"  1 salto  {fps1:>8,.0f} marcos/s  {mbps1:>5.1f} MB/s")
    print(f"  3 saltos {fps3:>8,.0f} marcos/s  {mbps3:>5.1f} MB/s")

    # Everything here shares one core: a 3-hop frame crosses three QUIC links
    # in the same event loop, so the absolute number measures the harness, not
    # the design. What the onion layer itself costs is the departure from the
    # ideal 1/3 of the one-hop figure.
    ideal = mbps1 / 3
    efficiency = mbps3 / ideal if ideal else 0.0
    print(f"  ideal de 3 saltos en 1 nucleo: {ideal:.1f} MB/s (1 salto / 3)")
    print(f"  eficiencia de la capa onion:   {efficiency * 100:.0f} %")

    print("\n--- veredicto ---")
    median = statistics.median(builds)
    checks = (
        ("construccion", median, TARGET_BUILD_MS, True, "ms"),
        ("eficiencia onion", efficiency, TARGET_ONION_EFFICIENCY, False, ""),
    )
    ok = True
    for name, value, target, lower_is_better, unit in checks:
        passed = value <= target if lower_is_better else value >= target
        mark = "PASA " if passed else "FALLA"
        if unit:
            comparison = "≤" if lower_is_better else "≥"
            print(f"  {mark} {name}: {value:,.1f} {unit} (objetivo {comparison} {target:,.0f})")
        else:
            print(f"  {mark} {name}: {value * 100:.0f} % (objetivo ≥ {target * 100:.0f} %)")
        ok &= passed

    print(
        "\nNota: el rendimiento absoluto lo fijan tres enlaces QUIC en un solo "
        "nucleo, no el diseño. Ver §16."
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
