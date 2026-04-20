"""Concurrency stress tests — verify Rein holds up under parallel load.

These exercise the hot path (gate + record_fill + record_exit) from many
coroutines simultaneously. If any of these fail, the library is unsafe for
production agent use.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from rein_ai import Rein, ReinConfig


@pytest.fixture
async def brain(tmp_path: Path):
    cfg = ReinConfig(enabled=True, shadow_mode=False,
                     min_samples_for_kill=5, exec_min_attempts=5,
                     debounce_seconds=0.0,
                     persist_tick_seconds=0.5, regime_tick_seconds=0.5)
    b = Rein(cfg=cfg, persist_dir=tmp_path)
    await b.start()
    yield b
    await b.shutdown()


async def test_1000_concurrent_gates(brain):
    """gate() under heavy concurrent load — no crashes, consistent results."""
    async def worker(i: int):
        return brain.gate(source="agent", series=f"tool_{i % 10}")

    results = await asyncio.gather(*[worker(i) for i in range(1000)])
    assert len(results) == 1000
    assert all(r.allowed for r in results)  # no strategies have failed yet


async def test_concurrent_fills_preserve_sample_count(brain):
    """N concurrent record_fill calls → exactly N samples counted."""
    N = 500

    async def report(i: int):
        await brain.record_fill(
            source="agent", series="tool", ticker=f"t{i}",
            filled=(i % 2 == 0), slippage_cents=0.0, attempt_at=time.time(),
        )

    await asyncio.gather(*[report(i) for i in range(N)])
    await brain._scorer.tick()

    snap = brain.snapshot()
    key = next(k for k in snap["health"] if "tool" in str(k))
    exec_samples = snap["health"][key]["scorecard"]["execution"]["samples"]
    assert exec_samples == N, f"lost {N - exec_samples} samples under concurrency"


async def test_mixed_gate_and_record_interleaved(brain):
    """Gate + record_fill + record_exit interleaved, no deadlock."""
    async def cycle(i: int):
        d = brain.gate(source="agent", series=f"s{i % 3}")
        if d.allowed:
            await brain.record_fill(source="agent", series=f"s{i % 3}",
                                    ticker=f"t{i}", filled=True,
                                    slippage_cents=0.0, attempt_at=time.time())
            await brain.record_exit(source="agent", series=f"s{i % 3}",
                                    ticker=f"t{i}", realized_pnl_cents=1.0,
                                    predicted_edge_cents=1.0,
                                    capital_pct_of_alloc=0.01, exit_at=time.time())

    await asyncio.wait_for(
        asyncio.gather(*[cycle(i) for i in range(300)]),
        timeout=5.0,
    )
    # If we got here, no deadlock. Sanity check snapshot is consistent.
    snap = brain.snapshot()
    assert len(snap["health"]) == 3  # 3 distinct series


async def test_persist_under_concurrent_writes(brain, tmp_path: Path):
    """Trigger persist while records are still coming in — no corruption."""
    async def writer():
        for i in range(50):
            await brain.record_fill(source="agent", series="t",
                                    ticker=f"t{i}", filled=True,
                                    slippage_cents=0.0, attempt_at=time.time())

    async def persister():
        for _ in range(10):
            await brain.persist_now()
            await asyncio.sleep(0.01)

    await asyncio.gather(writer(), writer(), persister())

    # Reload state file — it should parse cleanly (not corrupted mid-write)
    from rein_ai.persist import load_state
    state = load_state(tmp_path / "rein_state.json")
    assert state is not None, "persist corrupted state file"
