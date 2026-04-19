"""Replay: run a recorded audit log through a fresh Rein under a new
ReinConfig. Answers: 'if I tighten edge_red_p to 0.70, how many more strategies
get killed? Any that would have worked net-positive?'

Usage:
    from rein_ai import ReinConfig
    from rein_ai.replay import replay_audit
    baseline = ReinConfig()
    aggressive = ReinConfig(edge_red_p=0.70, exec_red_fill=0.60)
    result = await replay_audit("./rein_state/rein_audit.jsonl", aggressive)
    print(result.summary())
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from rein_ai import Rein, ReinConfig


@dataclass
class ReplayResult:
    config: ReinConfig
    total_events: int
    fill_events: int
    exit_events: int
    status_changes: List[dict] = field(default_factory=list)
    kills: int = 0
    revivals: int = 0
    final_health: Dict[str, dict] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"Replay summary: {self.total_events} events "
            f"({self.fill_events} fills, {self.exit_events} exits)",
            f"  Strategies killed:   {self.kills}",
            f"  Strategies revived:  {self.revivals}",
            f"  Final status: "
            f"{sum(1 for h in self.final_health.values() if h['status'] == 'green')} green, "
            f"{sum(1 for h in self.final_health.values() if h['status'] == 'yellow')} yellow, "
            f"{sum(1 for h in self.final_health.values() if h['status'] == 'red')} red, "
            f"{sum(1 for h in self.final_health.values() if h['status'] == 'black')} black",
        ]
        return "\n".join(lines)


async def replay_audit(
    audit_path: str | Path,
    cfg: ReinConfig,
    persist_dir: Optional[Path] = None,
) -> ReplayResult:
    """Replay an audit log file under `cfg`. Returns aggregated results.

    The audit log is the .jsonl written by Rein during live operation.
    We replay FILL and EXIT events in order; REGIME and STATUS events are
    reconstructed by the replayed brain.
    """
    audit_path = Path(audit_path)
    if not audit_path.exists():
        raise FileNotFoundError(audit_path)

    persist_dir = persist_dir or Path(tempfile.mkdtemp(prefix="meta_replay_"))

    # Disable shadow so kills are real in replay
    cfg_active = ReinConfig(**{**cfg.__dict__, "shadow_mode": False,
                                "debounce_seconds": 0.0})

    brain = Rein(cfg=cfg_active, persist_dir=persist_dir)
    await brain.start()

    result = ReplayResult(config=cfg, total_events=0, fill_events=0, exit_events=0)

    with audit_path.open() as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            result.total_events += 1
            kind = ev.get("event")

            if kind == "FILL":
                result.fill_events += 1
                await brain.record_fill(
                    source=ev["source"], series=ev["series"],
                    ticker=ev.get("ticker", ""),
                    filled=ev.get("filled", False),
                    slippage_cents=ev.get("slippage_cents", 0.0),
                    attempt_at=ev.get("at", 0.0),
                )
            elif kind == "EXIT":
                result.exit_events += 1
                await brain.record_exit(
                    source=ev["source"], series=ev["series"],
                    ticker=ev.get("ticker", ""),
                    realized_pnl_cents=ev.get("realized_pnl_cents", 0.0),
                    predicted_edge_cents=ev.get("predicted_edge_cents", 0.0),
                    capital_pct_of_alloc=ev.get("capital_pct_of_alloc", 0.0),
                    exit_at=ev.get("at", 0.0),
                )
            elif kind == "STATUS":
                result.status_changes.append(ev)
                if ev.get("to") in ("red", "black"):
                    result.kills += 1
                elif ev.get("to") == "green" and ev.get("from") in ("red", "black"):
                    result.revivals += 1

    await brain._scorer.tick()
    result.final_health = brain.snapshot().get("health", {})

    await brain.shutdown()
    return result


async def compare_configs(audit_path: str | Path,
                          configs: Dict[str, ReinConfig]) -> Dict[str, ReplayResult]:
    """Replay the same audit log under multiple configs. Returns {name: result}."""
    out = {}
    for name, cfg in configs.items():
        out[name] = await replay_audit(audit_path, cfg)
    return out
