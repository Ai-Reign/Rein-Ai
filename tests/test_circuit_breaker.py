"""Tests for the independent portfolio circuit breaker."""
import pytest

from tripwire_ai.config import TripwireConfig
from tripwire_ai.circuit_breaker import (
    CircuitBreakerVerdict, evaluate_circuit_breaker,
)


CFG = TripwireConfig()


def test_no_halt_when_drawdown_above_floor():
    v = evaluate_circuit_breaker(starting_balance_usd=100.0, current_balance_usd=98.0,
                                 realized_pnl_today=-2.0, cfg=CFG)
    assert v.halt is False
    assert v.cancel_open is False
    assert v.reason == ""


def test_halt_at_minus_5_pct():
    v = evaluate_circuit_breaker(starting_balance_usd=100.0, current_balance_usd=94.0,
                                 realized_pnl_today=-6.0, cfg=CFG)
    assert v.halt is True
    assert v.cancel_open is False
    assert "drawdown" in v.reason.lower()


def test_nuclear_at_minus_8_pct_cancels_open():
    v = evaluate_circuit_breaker(starting_balance_usd=100.0, current_balance_usd=91.0,
                                 realized_pnl_today=-9.0, cfg=CFG)
    assert v.halt is True
    assert v.cancel_open is True


def test_balance_floor_triggers_halt():
    v = evaluate_circuit_breaker(starting_balance_usd=100.0, current_balance_usd=15.0,
                                 realized_pnl_today=-1.0, cfg=CFG)
    assert v.halt is True
    assert "balance" in v.reason.lower()


def test_zero_starting_balance_no_div_by_zero():
    v = evaluate_circuit_breaker(starting_balance_usd=0.0, current_balance_usd=0.0,
                                 realized_pnl_today=0.0, cfg=CFG)
    assert v.halt is True  # treat as floor breach for safety
    assert "balance" in v.reason.lower()


def test_does_not_depend_on_metastate():
    """Sanity check: function signature accepts only primitives + cfg, no TripwireState import."""
    import inspect
    from tripwire_ai import circuit_breaker
    src = inspect.getsource(circuit_breaker)
    assert "TripwireState" not in src
    assert "from tripwire_ai.types" not in src
