"""Tests for the red-team / adversarial simulator."""
from __future__ import annotations

import pytest

from rein_ai import compile_policy, list_attacks, run_red_team
from rein_ai.brain import Rein
from rein_ai.config import ReinConfig


@pytest.mark.asyncio
async def test_list_attacks_includes_all_categories():
    names = list_attacks()
    for required in ("runaway_loop", "deny_storm", "enumeration",
                     "portfolio_drain", "cost_bomb"):
        assert required in names


@pytest.mark.asyncio
async def test_runaway_loop_caught_by_rate_limiter(tmp_path):
    policy = compile_policy([
        "Cap each caller at 5 requests per second with bursts of 10",
    ], use_llm_fallback=False)
    brain = policy.build_brain(persist_dir=tmp_path)
    report = await run_red_team(brain, attacks=["runaway_loop"])
    out = report.outcomes[0]
    assert out.blocked, f"runaway_loop slipped past: {out.notes}"
    assert out.detected_at_iter is not None
    assert out.detected_at_iter < 50


@pytest.mark.asyncio
async def test_runaway_loop_missed_without_rate_limiter(tmp_path):
    brain = Rein(cfg=ReinConfig(shadow_mode=False), persist_dir=tmp_path)
    report = await run_red_team(brain, attacks=["runaway_loop"])
    out = report.outcomes[0]
    assert not out.blocked
    assert "cap each caller" in out.suggestion.lower()


@pytest.mark.asyncio
async def test_deny_storm_caught_by_anomaly_detector(tmp_path):
    policy = compile_policy([
        "Alert when deny rate exceeds 50 percent over a 60 second window",
    ], use_llm_fallback=False)
    # Lower the min count manually so the synthetic test fires quickly
    brain = policy.build_brain(persist_dir=tmp_path)
    brain._anomaly.deny_storm_min_count = 10
    report = await run_red_team(brain, attacks=["deny_storm"])
    out = report.outcomes[0]
    assert out.blocked, f"deny_storm not detected: {out.notes}"


@pytest.mark.asyncio
async def test_enumeration_caught_by_new_caller_surge(tmp_path):
    policy = compile_policy([
        "Cap each caller at 100 requests per second",
        "Detect runaway callers at 5x baseline",
    ], use_llm_fallback=False)
    brain = policy.build_brain(persist_dir=tmp_path)
    brain._anomaly.new_caller_threshold = 20
    report = await run_red_team(brain, attacks=["enumeration"])
    out = report.outcomes[0]
    assert out.blocked, f"enumeration not detected: {out.notes}"


@pytest.mark.asyncio
async def test_portfolio_drain_trips_breaker(tmp_path):
    policy = compile_policy([
        "Halt the portfolio when losses exceed 5 percent",
    ], use_llm_fallback=False)
    cfg = policy.config
    # turn off shadow so the breaker enforces
    from dataclasses import replace
    brain = Rein(cfg=replace(cfg, shadow_mode=False), persist_dir=tmp_path)
    report = await run_red_team(brain, attacks=["portfolio_drain"])
    out = report.outcomes[0]
    assert out.blocked, f"portfolio_drain not caught: {out.notes}"


@pytest.mark.asyncio
async def test_full_battery_returns_catch_rate(tmp_path):
    policy = compile_policy([
        "Cap each caller at 5 requests per second",
        "Halt the portfolio when losses exceed 5 percent",
        "Alert when deny rate exceeds 50 percent over 60 second window",
    ], use_llm_fallback=False)
    from dataclasses import replace
    brain = Rein(cfg=replace(policy.config, shadow_mode=False),
                      persist_dir=tmp_path,
                      rate_limiter=policy.rate_limiter,
                      anomaly_detector=policy.anomaly_detector)
    brain._anomaly.deny_storm_min_count = 10
    brain._anomaly.new_caller_threshold = 20
    report = await run_red_team(brain)
    assert 0.0 <= report.catch_rate <= 1.0
    assert len(report.outcomes) == len(list_attacks())
    text = report.render()
    assert "catch rate" in text.lower()


@pytest.mark.asyncio
async def test_report_serialises_to_dict(tmp_path):
    brain = Rein(cfg=ReinConfig(shadow_mode=False), persist_dir=tmp_path)
    report = await run_red_team(brain, attacks=["runaway_loop"])
    d = report.to_dict()
    assert "catch_rate" in d
    assert "outcomes" in d
    assert d["outcomes"][0]["name"] == "runaway_loop"
