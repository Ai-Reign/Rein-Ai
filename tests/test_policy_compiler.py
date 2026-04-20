"""Tests for the natural-language policy compiler."""
from __future__ import annotations

import pytest

from rein_ai.policy_compiler import compile_policy


def test_rate_per_key_with_burst():
    p = compile_policy(["Cap each caller at 5 requests per second with bursts of 10"],
                       use_llm_fallback=False)
    assert p.rate_limiter is not None
    assert p.rate_limiter.rate_per_key == 5.0
    assert p.rate_limiter.burst_per_key == 10.0
    assert any(r.target == "rate_per_key" for r in p.rules)


def test_global_rate_limit():
    p = compile_policy(["Set a global limit of 100 requests per second across the system"],
                       use_llm_fallback=False)
    assert p.rate_limiter is not None
    assert p.rate_limiter.global_rate == 100.0


def test_portfolio_loss_halt():
    p = compile_policy(["Halt the portfolio when losses exceed 5 percent"],
                       use_llm_fallback=False)
    assert p.config.portfolio_floor_pct == pytest.approx(-0.05)


def test_balance_floor():
    p = compile_policy(["Stop when balance falls below $25"], use_llm_fallback=False)
    assert p.config.balance_floor_usd == 25.0


def test_deny_storm_alert():
    p = compile_policy(["Alert when deny rate exceeds 80 percent over a 60 second window"],
                       use_llm_fallback=False)
    assert p.anomaly_detector is not None
    assert p.anomaly_detector.deny_storm_rate == pytest.approx(0.80)
    assert p.anomaly_detector.window_s == 60.0


def test_runaway_multiplier():
    p = compile_policy(["Detect runaway callers at 5x baseline"], use_llm_fallback=False)
    assert p.anomaly_detector is not None
    assert p.anomaly_detector.runaway_multiplier == 5.0


def test_debounce():
    p = compile_policy(["Debounce status changes for 120 seconds"], use_llm_fallback=False)
    assert p.config.debounce_seconds == 120.0


def test_min_samples_for_kill():
    p = compile_policy(["Require 15 samples before killing a strategy"], use_llm_fallback=False)
    assert p.config.min_samples_for_kill == 15


def test_unparsed_collected():
    p = compile_policy(["Make the agent more helpful and friendlier"], use_llm_fallback=False)
    assert "Make the agent more helpful and friendlier" in p.unparsed
    assert p.rate_limiter is None
    assert p.anomaly_detector is None


def test_compose_multiple_rules():
    p = compile_policy([
        "Cap each caller at 10 requests per second",
        "Halt the portfolio when losses exceed 8 percent",
        "Alert when deny rate exceeds 70 percent over 30 second window",
    ], use_llm_fallback=False)
    assert p.rate_limiter.rate_per_key == 10.0
    assert p.config.portfolio_floor_pct == pytest.approx(-0.08)
    assert p.anomaly_detector.deny_storm_rate == pytest.approx(0.70)
    assert len(p.rules) == 3


def test_explain_returns_human_readable():
    p = compile_policy(["Cap each caller at 3 requests per second"], use_llm_fallback=False)
    out = p.explain()
    assert "rate_per_key" in out
    assert "Cap each caller" in out


def test_build_brain_returns_rein(tmp_path):
    p = compile_policy([
        "Cap each caller at 5 requests per second",
        "Halt the portfolio when losses exceed 5 percent",
    ], use_llm_fallback=False)
    brain = p.build_brain(persist_dir=tmp_path)
    assert brain.cfg.portfolio_floor_pct == pytest.approx(-0.05)
    assert brain._rate_limiter is not None
