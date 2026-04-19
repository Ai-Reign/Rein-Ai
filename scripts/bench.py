"""Micro-benchmark: measure Tripwire.gate() latency.

Run:
    python3 scripts/bench.py
"""
import asyncio
import statistics
import time

from tripwire_ai import Tripwire, TripwireConfig


async def main() -> None:
    brain = Tripwire(cfg=TripwireConfig.from_env())
    await brain.start()
    try:
        # Warmup
        for _ in range(1000):
            brain.gate(source="bench", series="tool")

        n = 100_000
        samples: list[float] = []
        t0 = time.perf_counter()
        for _ in range(n):
            s = time.perf_counter_ns()
            brain.gate(source="bench", series="tool")
            samples.append((time.perf_counter_ns() - s) / 1000.0)  # μs
        total = time.perf_counter() - t0

        samples.sort()
        p50 = samples[len(samples) // 2]
        p95 = samples[int(len(samples) * 0.95)]
        p99 = samples[int(len(samples) * 0.99)]
        mean = statistics.fmean(samples)
        qps = n / total

        print(f"n={n:,} gate() calls")
        print(f"  mean   : {mean:8.2f} μs")
        print(f"  p50    : {p50:8.2f} μs")
        print(f"  p95    : {p95:8.2f} μs")
        print(f"  p99    : {p99:8.2f} μs")
        print(f"  qps    : {qps:10,.0f}/s")
    finally:
        await brain.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
