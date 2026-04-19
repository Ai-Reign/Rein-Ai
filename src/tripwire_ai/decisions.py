"""Pure decision functions: status from scorecard, revival, regime override.

These are pure functions — they take state, return new state. No I/O. No
randomness. They are the heart of the Tripwire.
"""
from __future__ import annotations

from typing import Optional

from tripwire_ai.config import TripwireConfig
from tripwire_ai.types import AxisScore, Regime, Scorecard, Status, StrategyHealth


# ----------------------------------------------------------------------------
# Status from Scorecard
# ----------------------------------------------------------------------------

def _edge_status(a: AxisScore, cfg: TripwireConfig) -> Status:
    if a.samples < cfg.min_samples_for_kill:
        return Status.YELLOW  # cold-start cap
    if a.samples >= cfg.edge_black_min_samples and a.posterior_mean < cfg.edge_black_mean_cents:
        return Status.BLACK
    if a.p_below_threshold > cfg.edge_red_p and a.samples >= 20:
        return Status.RED
    if a.p_below_threshold > cfg.edge_yellow_p:
        return Status.YELLOW
    if a.samples >= cfg.min_samples_for_green:
        return Status.GREEN
    return Status.YELLOW


def _execution_status(a: AxisScore, cfg: TripwireConfig) -> Status:
    if a.samples < cfg.min_samples_for_kill:
        return Status.YELLOW
    fill_rate = a.posterior_mean
    if a.samples >= 30 and fill_rate < cfg.exec_black_fill:
        return Status.BLACK
    if a.samples >= cfg.exec_min_attempts and fill_rate < cfg.exec_red_fill:
        return Status.RED
    # slippage check uses last_value as raw slippage cents proxy
    if a.samples >= cfg.exec_min_attempts and a.last_value > 0 and a.last_value > cfg.exec_red_slippage_cents:
        # last_value here interpreted as recent slippage cents — see strategy_scorer for encoding
        return Status.RED
    if fill_rate < cfg.exec_yellow_fill:
        return Status.YELLOW
    if a.samples >= cfg.min_samples_for_green:
        return Status.GREEN
    return Status.YELLOW


def _capital_status(a: AxisScore, cfg: TripwireConfig) -> Status:
    if a.samples < cfg.min_samples_for_kill:
        return Status.YELLOW
    pct = a.posterior_mean
    if pct < cfg.capital_black_pct:
        return Status.BLACK
    if pct < cfg.capital_red_pct:
        return Status.RED
    if pct < cfg.capital_yellow_pct:
        return Status.YELLOW
    if a.samples >= cfg.min_samples_for_green:
        return Status.GREEN
    return Status.YELLOW


def status_from_scorecard(sc: Scorecard, cfg: TripwireConfig) -> Status:
    """Return the WORST status across the three axes."""
    return Status.worst(
        _edge_status(sc.edge, cfg),
        _execution_status(sc.execution, cfg),
        _capital_status(sc.capital, cfg),
    )


# ----------------------------------------------------------------------------
# Anti-flap debounce
# ----------------------------------------------------------------------------

def can_change_status(sh: StrategyHealth, now: float, cfg: TripwireConfig) -> bool:
    """True iff sh.last_status_change is at least debounce_seconds in the past."""
    return (now - sh.last_status_change) >= cfg.debounce_seconds


# ----------------------------------------------------------------------------
# Apply status transition (records audit + cooldown)
# ----------------------------------------------------------------------------

def apply_status_change(
    sh: StrategyHealth,
    new_status: Status,
    reason: str,
    now: float,
    cfg: TripwireConfig,
) -> StrategyHealth:
    """Mutate sh in place to reflect a status change. Returns the same object."""
    old = sh.status
    sh.status = new_status
    sh.last_status_change = now
    sh.kill_reason = reason if new_status.severity > Status.GREEN.severity else None
    if new_status == Status.RED:
        sh.cooldown_until = now + cfg.red_cooldown_hours * 3600.0
    elif new_status in (Status.GREEN, Status.YELLOW):
        sh.cooldown_until = 0.0
    sh.revival_history.append({
        "from": old.value,
        "to": new_status.value,
        "reason": reason,
        "at": now,
    })
    # Bound history length to last 200 entries
    if len(sh.revival_history) > 200:
        sh.revival_history = sh.revival_history[-200:]
    return sh


# ----------------------------------------------------------------------------
# Revival rules
# ----------------------------------------------------------------------------

def revival_check(
    sh: StrategyHealth,
    proposed_status: Status,
    now: float,
    cfg: TripwireConfig,
    regime_changed_since_kill: bool = False,
) -> Optional[Status]:
    """Return the status the strategy may transition to (possibly equal to current),
    or None if revival is not yet permitted.

    Implements the inverse staircase from the spec:
      Red→Yellow: cooldown elapsed AND scorecard improving (proposed != Red)
      Red→Green: must pass through Yellow first
      Black→anything: manual only (this function returns None for Black)
      Yellow→Green: handled by status_from_scorecard already, no extra gate
      Regime-paired override: halve cooldown on regime change.
    """
    if sh.status == Status.BLACK:
        # Black requires manual revive — never auto.
        return None

    if sh.status == Status.RED:
        cooldown = sh.cooldown_until
        if regime_changed_since_kill:
            cooldown = sh.last_status_change + (sh.cooldown_until - sh.last_status_change) / 2.0
        if now < cooldown:
            return None
        if proposed_status == Status.RED:
            return None  # not improving yet
        # Red → Yellow only (no fast-track to Green)
        return Status.YELLOW if proposed_status != Status.BLACK else Status.RED

    # Yellow / Green: no special gating, scorecard rules
    return proposed_status


# ----------------------------------------------------------------------------
# Regime override
# ----------------------------------------------------------------------------

def regime_override(sh: StrategyHealth, current_regime: Regime, cfg: TripwireConfig) -> Status:
    """If the strategy's slice for the current regime is GREEN and global is YELLOW,
    promote effective status to GREEN. RED/BLACK are never overridden up.
    """
    if sh.status != Status.YELLOW:
        return sh.status
    slice_sc = sh.regime_slices.get(current_regime.regime_id())
    if slice_sc is None:
        return sh.status
    slice_status = status_from_scorecard(slice_sc, cfg)
    if slice_status == Status.GREEN:
        return Status.GREEN
    return sh.status
