"""End-to-end orchestrator tests for Rein.gate flow."""
import asyncio
from pathlib import Path

import pytest

from rein_ai.brain import Rein
from rein_ai.config import ReinConfig
from rein_ai.types import Regime, Status


CFG = ReinConfig(shadow_mode=False, persist_tick_seconds=10.0, regime_tick_seconds=10.0)


@pytest.mark.asyncio
async def test_gate_unknown_strategy_yellow():
    brain = Rein(cfg=CFG, persist_dir=Path("/tmp/test_brain_unk"))
    decision = brain.gate("math", "KXBTCD")
    assert decision.allowed is True
    assert decision.status == Status.YELLOW


@pytest.mark.asyncio
async def test_gate_returns_red_after_repeated_unfilled():
    brain = Rein(cfg=ReinConfig(shadow_mode=False, debounce_seconds=0.0), persist_dir=Path("/tmp/test_brain_red"))
    for i in range(25):
        await brain.record_fill(source="math", series="K", ticker=f"t{i}",
                                filled=False, slippage_cents=0.0, attempt_at=float(i))
    d = brain.gate("math", "K")
    assert d.status in (Status.RED, Status.BLACK)
    assert d.allowed is False


@pytest.mark.asyncio
async def test_shadow_mode_returns_allowed_but_records_status():
    """Shadow mode: gate() always returns ALLOWED but actual state still moves."""
    brain = Rein(cfg=ReinConfig(shadow_mode=True, debounce_seconds=0.0), persist_dir=Path("/tmp/test_brain_shadow"))
    for i in range(25):
        await brain.record_fill(source="math", series="K", ticker=f"t{i}",
                                filled=False, slippage_cents=0.0, attempt_at=float(i))
    d = brain.gate("math", "K")
    assert d.allowed is True  # shadow override
    assert d.status == Status.GREEN  # shadow forces GREEN to caller
    snap = brain.snapshot()
    # But the actual state shows the strategy is in trouble
    assert snap["health"]["math|K"]["status"] in ("red", "black")


@pytest.mark.asyncio
async def test_portfolio_halt_blocks_gate(tmp_path):
    brain = Rein(cfg=CFG, persist_dir=tmp_path)
    brain.update_portfolio(starting_balance_usd=100.0, current_balance_usd=90.0, realized_pnl_today=-9.0)
    assert brain.portfolio_halted() is True
    d = brain.gate("math", "K")
    assert d.allowed is False
    assert d.status == Status.BLACK


@pytest.mark.asyncio
async def test_persist_writes_state_file(tmp_path):
    brain = Rein(cfg=CFG, persist_dir=tmp_path)
    await brain.record_fill(source="math", series="K", ticker="t",
                            filled=True, slippage_cents=0.0, attempt_at=1.0)
    await brain.persist_now()
    assert (tmp_path / "rein_state.json").exists()


@pytest.mark.asyncio
async def test_safe_mode_on_stale_state(tmp_path):
    """Stale state file (>10min old by mtime trick) demotes Green → Yellow."""
    state_path = tmp_path / "rein_state.json"
    # First brain: write a Green strategy
    b1 = Rein(cfg=ReinConfig(shadow_mode=False, debounce_seconds=0.0), persist_dir=tmp_path)
    sh = b1._state.health.setdefault(("math", "K"),
        __import__("rein_ai.types", fromlist=["StrategyHealth"]).StrategyHealth.fresh(("math", "K"), 0.0))
    sh.status = Status.GREEN
    await b1.persist_now()
    # Force file mtime into the past
    import os, time
    old = time.time() - 11 * 60
    os.utime(state_path, (old, old))
    # New brain instance: should demote
    b2 = Rein(cfg=ReinConfig(shadow_mode=False, debounce_seconds=0.0), persist_dir=tmp_path)
    assert b2._state.health[("math", "K")].status == Status.YELLOW
