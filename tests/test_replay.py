"""Replay tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rein_ai import ReinConfig
from rein_ai.replay import compare_configs, replay_audit


def _write_audit(path: Path, events: list):
    with path.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


async def test_replay_records_all_fills(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    _write_audit(audit, [
        {"event": "FILL", "source": "a", "series": "s", "ticker": f"t{i}",
         "filled": i % 2 == 0, "slippage_cents": 0.0, "at": 1000.0 + i}
        for i in range(10)
    ])
    cfg = ReinConfig(min_samples_for_kill=3, exec_min_attempts=3, debounce_seconds=0.0)
    result = await replay_audit(audit, cfg)
    assert result.fill_events == 10


async def test_replay_config_comparison_surfaces_tradeoffs(tmp_path: Path):
    """A strict config kills more strategies than a lenient one."""
    audit = tmp_path / "audit.jsonl"
    # 20 failures for series "bad", 20 successes for series "good"
    events = []
    for i in range(20):
        events.append({"event": "FILL", "source": "a", "series": "bad",
                       "ticker": f"b{i}", "filled": False, "slippage_cents": 0, "at": float(i)})
        events.append({"event": "FILL", "source": "a", "series": "good",
                       "ticker": f"g{i}", "filled": True, "slippage_cents": 0, "at": float(i)})
    _write_audit(audit, events)

    lenient = ReinConfig(min_samples_for_kill=50, exec_min_attempts=50, debounce_seconds=0.0)
    strict  = ReinConfig(min_samples_for_kill=3, exec_min_attempts=3,
                          exec_red_fill=0.50, debounce_seconds=0.0)

    results = await compare_configs(audit, {"lenient": lenient, "strict": strict})

    # Strict should have killed "bad"; lenient should not have killed anything
    strict_red = [h for h in results["strict"].final_health.values() if h["status"] == "red"]
    assert len(strict_red) >= 1, "strict config should kill the broken series"

    lenient_red = [h for h in results["lenient"].final_health.values() if h["status"] == "red"]
    assert len(lenient_red) == 0, "lenient config should not kill anything"


async def test_replay_skips_malformed_lines(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        '{"event":"FILL","source":"a","series":"s","ticker":"t1","filled":true,"slippage_cents":0,"at":0}\n'
        'not json\n'
        '{"event":"FILL","source":"a","series":"s","ticker":"t2","filled":true,"slippage_cents":0,"at":1}\n'
    )
    result = await replay_audit(audit, ReinConfig(debounce_seconds=0.0))
    assert result.fill_events == 2
