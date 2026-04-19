"""Cold-start + clock-jump robustness tests."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tripwire_ai import Tripwire, TripwireConfig
from tripwire_ai.types import Status


@pytest.fixture
async def brain(tmp_path: Path):
    cfg = TripwireConfig(enabled=True, shadow_mode=False,
                     min_samples_for_kill=10, min_samples_for_green=20,
                     exec_min_attempts=10, debounce_seconds=0.0)
    b = Tripwire(cfg=cfg, persist_dir=tmp_path)
    await b.start()
    yield b
    await b.shutdown()


# ---------- Cold start ----------

async def test_fresh_brain_does_not_kill_before_min_samples(brain):
    """First few failures should NOT trigger a kill — not enough evidence."""
    for i in range(5):
        await brain.record_fill(source="a", series="s", ticker=f"t{i}",
                                 filled=False, slippage_cents=0.0, attempt_at=time.time())
    await brain._scorer.tick()
    d = brain.gate(source="a", series="s")
    assert d.allowed, "fresh brain killed strategy with only 5 samples (need 10 min)"
    assert d.status in (Status.GREEN, Status.YELLOW)


async def test_fresh_brain_kills_after_enough_evidence(brain):
    """Once samples exceed threshold and fail rate is bad, kill."""
    for i in range(20):
        await brain.record_fill(source="a", series="s", ticker=f"t{i}",
                                 filled=False, slippage_cents=0.0, attempt_at=time.time())
    await brain._scorer.tick()
    d = brain.gate(source="a", series="s")
    assert not d.allowed or d.status in (Status.RED, Status.BLACK)


async def test_persisted_state_demoted_to_yellow_on_stale_restart(tmp_path: Path):
    """Safe mode: stale state (>10min old) → all GREEN become YELLOW on load."""
    cfg = TripwireConfig(enabled=True, shadow_mode=False, debounce_seconds=0.0,
                     min_samples_for_kill=3, min_samples_for_green=3,
                     exec_min_attempts=3)

    # Run 1: make a strategy GREEN and persist
    b1 = Tripwire(cfg=cfg, persist_dir=tmp_path)
    await b1.start()
    for i in range(10):
        await b1.record_fill(source="a", series="s", ticker=f"t{i}",
                              filled=True, slippage_cents=0.0, attempt_at=time.time())
    await b1._scorer.tick()
    b1.force_status("a", "s", Status.GREEN, "", ttl_seconds=0)
    await b1.shutdown()

    # Backdate the state file to simulate 30-minute-old restart
    state_file = tmp_path / "tripwire_state.json"
    old_time = time.time() - 30 * 60
    import os
    os.utime(state_file, (old_time, old_time))

    # Run 2: fresh brain from stale state → should demote GREEN → YELLOW
    b2 = Tripwire(cfg=cfg, persist_dir=tmp_path)
    sh = b2._state.health.get(("a", "s"))
    assert sh is not None
    assert sh.status == Status.YELLOW, f"stale state not demoted (got {sh.status.value})"


# ---------- Clock jumps ----------

async def test_clock_backwards_no_crash(brain):
    """Clock jumps backwards mid-session (NTP correction) — no crash, no hangs."""
    real_time = time.time
    t = [real_time()]

    def mocked():
        return t[0]

    with patch("time.time", mocked):
        for _ in range(5):
            await brain.record_fill(source="a", series="s", ticker="x",
                                    filled=True, slippage_cents=0, attempt_at=t[0])
            t[0] += 1
        # Jump backwards 5 minutes
        t[0] -= 300
        for _ in range(5):
            await brain.record_fill(source="a", series="s", ticker="y",
                                    filled=True, slippage_cents=0, attempt_at=t[0])
            t[0] += 1
        await brain._scorer.tick()

    # Brain should still be responsive and snapshot-able
    snap = brain.snapshot()
    assert snap is not None


async def test_clock_large_forward_jump(brain):
    """Clock jumps forward 24h (laptop sleep/wake) — no runaway decay, consistent state."""
    t0 = time.time()
    for _ in range(5):
        await brain.record_fill(source="a", series="s", ticker="x",
                                filled=True, slippage_cents=0, attempt_at=t0)

    # Simulate 24h later
    future = t0 + 86400
    with patch("time.time", lambda: future):
        await brain._scorer.tick()
        snap = brain.snapshot()
        # Data decayed to zero samples is expected after 24h; must not crash
        assert snap is not None


async def test_gate_fast_path_microseconds():
    """gate() shouldn't do I/O — should be microseconds even with many strategies."""
    cfg = TripwireConfig(enabled=True, shadow_mode=False, debounce_seconds=0.0)
    b = Tripwire(cfg=cfg)
    await b.start()
    try:
        # Register 1000 strategies
        for i in range(1000):
            b.force_status("a", f"s{i}", Status.GREEN, "", ttl_seconds=0)

        # 10000 gate calls
        t0 = time.perf_counter()
        for i in range(10000):
            b.gate(source="a", series=f"s{i % 1000}")
        elapsed = time.perf_counter() - t0
        per_call_us = elapsed * 1e6 / 10000
        assert per_call_us < 100, f"gate() too slow: {per_call_us:.1f}µs per call"
    finally:
        await b.shutdown()
