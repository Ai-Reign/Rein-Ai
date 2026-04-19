"""Adversarial agent simulator (red team).

Runs a battery of synthetic attack scenarios against a configured Rein
and reports which ones the governance config catches.

Attack patterns are inspired by real failure modes seen in production agent
deployments:

  - **runaway_loop**: a single (source, series) hammers gate() at high rate,
    simulating a stuck retry loop. Caught by rate limiter or anomaly detector.
  - **deny_storm**: a caller whose policy denies almost every action, simulating
    a misconfigured tool or prompt-injection victim spraying the agent. Caught
    by anomaly deny_storm.
  - **enumeration**: many never-seen-before (source, series) pairs in a short
    window, simulating prompt-injection-driven discovery / lateral movement.
    Caught by anomaly new_caller_surge.
  - **slow_bleed**: a strategy whose realised PnL is steadily negative but never
    catastrophic, simulating a quietly losing strategy that should be killed.
    Caught by capital axis + min_samples thresholds.
  - **cost_bomb**: a single caller that, if not gated, would burn through a
    spend budget in seconds. Caught by rate limit (proxies cost as call count).
  - **portfolio_drain**: rapid losses that should trip the portfolio circuit
    breaker. Caught by portfolio_floor_pct.

A `RedTeamReport` summarises catch rates, time-to-detect, and gives concrete
remediation suggestions when an attack slips past.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional


@dataclass
class AttackOutcome:
    name: str
    description: str
    blocked: bool
    detected_at_iter: Optional[int]
    iterations: int
    notes: str = ""
    suggestion: str = ""


@dataclass
class RedTeamReport:
    outcomes: List[AttackOutcome]
    started_at: float = field(default_factory=time.time)

    @property
    def catch_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(1 for o in self.outcomes if o.blocked) / len(self.outcomes)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "catch_rate": self.catch_rate,
            "outcomes": [o.__dict__ for o in self.outcomes],
        }

    def render(self) -> str:
        lines = [f"Red team report — catch rate: {self.catch_rate:.0%}", ""]
        for o in self.outcomes:
            mark = "BLOCKED" if o.blocked else "MISSED "
            iter_s = f" at iter {o.detected_at_iter}" if o.detected_at_iter is not None else ""
            lines.append(f"  [{mark}] {o.name:22s}{iter_s} — {o.description}")
            if o.notes:
                lines.append(f"            {o.notes}")
            if not o.blocked and o.suggestion:
                lines.append(f"            HINT: {o.suggestion}")
        return "\n".join(lines)


# ---------- attack scenarios ----------

async def _runaway_loop(brain, iterations: int = 200) -> AttackOutcome:
    src, ser = "redteam_runaway", "tool_x"
    blocked_at: Optional[int] = None
    for i in range(iterations):
        d = brain.gate(src, ser)
        if not d.allowed:
            blocked_at = i
            break
    return AttackOutcome(
        name="runaway_loop",
        description="single caller hammers gate() at full speed",
        blocked=blocked_at is not None,
        detected_at_iter=blocked_at,
        iterations=iterations,
        notes=f"stopped after {blocked_at + 1 if blocked_at is not None else iterations} calls",
        suggestion="add 'cap each caller at N requests per second' to your policy",
    )


async def _deny_storm(brain, iterations: int = 100) -> AttackOutcome:
    """Trip the anomaly deny_storm by feigning denials.

    We can't force gate() to deny without state; instead we drive the anomaly
    detector directly if present. If no detector, we report 'no detector'.
    """
    detector = getattr(brain, "_anomaly", None)
    if detector is None:
        return AttackOutcome(
            name="deny_storm",
            description="high deny rate from a single caller",
            blocked=False,
            detected_at_iter=None,
            iterations=0,
            notes="no anomaly detector configured",
            suggestion="add 'alert when deny rate exceeds 80 percent over 60 seconds'",
        )
    src, ser = "redteam_deny", "tool_x"
    fired_at: Optional[int] = None
    for i in range(iterations):
        detector.record(src, ser, allowed=False)
        for a in detector.drain_alerts():
            if a.category == "deny_storm":
                fired_at = i
                break
        if fired_at is not None:
            break
    return AttackOutcome(
        name="deny_storm",
        description="high deny rate from a single caller",
        blocked=fired_at is not None,
        detected_at_iter=fired_at,
        iterations=iterations,
        notes="alert fired" if fired_at is not None else "no alert in window",
        suggestion="lower deny_storm_min_count or deny_storm_rate",
    )


async def _enumeration(brain, iterations: int = 60) -> AttackOutcome:
    detector = getattr(brain, "_anomaly", None)
    if detector is None:
        return AttackOutcome(
            name="enumeration",
            description="many never-seen-before callers in a short window",
            blocked=False,
            detected_at_iter=None,
            iterations=0,
            notes="no anomaly detector configured",
            suggestion="add 'alert on more than 20 new callers in 60 seconds'",
        )
    fired_at: Optional[int] = None
    for i in range(iterations):
        detector.record(f"redteam_new_{i}", "probe", allowed=True)
        for a in detector.drain_alerts():
            if a.category == "new_caller_surge":
                fired_at = i
                break
        if fired_at is not None:
            break
    return AttackOutcome(
        name="enumeration",
        description="many never-seen-before callers in a short window",
        blocked=fired_at is not None,
        detected_at_iter=fired_at,
        iterations=iterations,
        suggestion="add 'alert on more than 20 new callers in 60 seconds'",
    )


async def _portfolio_drain(brain) -> AttackOutcome:
    """Simulate rapid drawdown via the portfolio update API."""
    update = getattr(brain, "update_portfolio", None)
    if update is None:
        return AttackOutcome(
            name="portfolio_drain",
            description="rapid drawdown should trip portfolio breaker",
            blocked=False,
            detected_at_iter=None,
            iterations=0,
            notes="brain.update_portfolio not available",
            suggestion="add a 'halt portfolio when losses exceed 5 percent' rule",
        )
    starting = 1000.0
    drawdown_pct = abs(brain.cfg.portfolio_floor_pct) + 0.01
    drained = starting * (1.0 - drawdown_pct)
    update(starting_balance_usd=starting,
           current_balance_usd=drained,
           realized_pnl_today=drained - starting)
    decision = brain.gate("redteam_drain", "tool_x")
    blocked = (not decision.allowed) and not brain.cfg.shadow_mode
    return AttackOutcome(
        name="portfolio_drain",
        description="rapid drawdown should trip portfolio breaker",
        blocked=blocked or decision.reason.startswith("portfolio") or "halt" in decision.reason,
        detected_at_iter=0 if blocked else None,
        iterations=1,
        notes=f"verdict: {decision.reason}",
        suggestion="disable shadow_mode or tighten portfolio_floor_pct",
    )


async def _cost_bomb(brain, iterations: int = 50) -> AttackOutcome:
    """Same shape as runaway_loop but framed as a cost-control test."""
    out = await _runaway_loop(brain, iterations=iterations)
    out.name = "cost_bomb"
    out.description = "high call rate that would burn a spend budget"
    out.suggestion = "add a 'global limit of N requests per second' rule"
    return out


_ATTACKS: Dict[str, Callable[..., Awaitable[AttackOutcome]]] = {
    "runaway_loop": _runaway_loop,
    "deny_storm": _deny_storm,
    "enumeration": _enumeration,
    "portfolio_drain": _portfolio_drain,
    "cost_bomb": _cost_bomb,
}


async def run_red_team(
    brain,
    attacks: Optional[List[str]] = None,
) -> RedTeamReport:
    names = attacks or list(_ATTACKS.keys())
    outcomes: List[AttackOutcome] = []
    for n in names:
        fn = _ATTACKS.get(n)
        if fn is None:
            outcomes.append(AttackOutcome(
                name=n, description="unknown attack",
                blocked=False, detected_at_iter=None,
                iterations=0, notes="no scenario registered",
            ))
            continue
        outcomes.append(await fn(brain))
    return RedTeamReport(outcomes=outcomes)


def list_attacks() -> List[str]:
    return list(_ATTACKS.keys())
