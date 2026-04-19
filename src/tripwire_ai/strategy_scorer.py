"""StrategyScorer sidecar: consumes fill/exit events, updates scorecards.

Maintains a small per-(source,series) sliding window of raw observations for
24h decay. Recomputes Bayesian posteriors and status decisions on each event.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Dict, Optional, Tuple

from tripwire_ai.config import TripwireConfig
from tripwire_ai.decisions import (
    apply_status_change, can_change_status, regime_override,
    revival_check, status_from_scorecard,
)
from tripwire_ai.persist import append_audit
from tripwire_ai.scorers import (
    beta_p_below_threshold, normal_p_below_threshold,
    update_beta_binomial, welford_init, welford_update,
)
from tripwire_ai.types import (
    AxisScore, TripwireState, Regime, Scorecard, Status, StrategyHealth, StrategyKey,
)


log = logging.getLogger("tripwire.scorer")


@dataclass
class FillEvent:
    source: str
    series: str
    ticker: str
    filled: bool
    slippage_cents: float
    attempt_at: float


@dataclass
class ExitEvent:
    source: str
    series: str
    ticker: str
    realized_pnl_cents: float
    predicted_edge_cents: float
    capital_pct_of_alloc: float
    exit_at: float


# Internal raw observation stores per (source,series). Each entry: (ts, kind, value)
@dataclass
class _RawObs:
    fills: Deque[Tuple[float, bool, float]] = field(default_factory=deque)         # (ts, filled, slippage_cents)
    edges: Deque[Tuple[float, float, float]] = field(default_factory=deque)        # (ts, realized, predicted)
    capitals: Deque[Tuple[float, float]] = field(default_factory=deque)            # (ts, pct_of_alloc)


class StrategyScorer:
    """Update TripwireState in place from observed events. No internal persistence —
    the caller (Tripwire) owns saving state.
    """

    def __init__(
        self,
        cfg: TripwireConfig,
        state: TripwireState,
        audit_path: Optional[Path] = None,
        debounce_override_for_tests: Optional[float] = None,
    ):
        self.cfg = cfg
        self.state = state
        self.audit_path = audit_path
        self._raw: Dict[StrategyKey, _RawObs] = {}
        self._raw_per_regime: Dict[Tuple[StrategyKey, str], _RawObs] = {}
        self._debounce = debounce_override_for_tests if debounce_override_for_tests is not None else cfg.debounce_seconds
        self._lock = asyncio.Lock()

    # ----- public API -----

    async def record_fill(self, ev: FillEvent) -> None:
        async with self._lock:
            self._record_fill_locked(ev)

    async def record_exit(self, ev: ExitEvent) -> None:
        async with self._lock:
            self._record_exit_locked(ev)

    async def tick(self) -> None:
        """Periodic re-evaluation tick (regime might have changed even with no events)."""
        async with self._lock:
            for key in list(self.state.health.keys()):
                self._recompute_status(key, ev_at=time.time())

    # ----- internals -----

    def _ensure_health(self, key: StrategyKey, now: float) -> StrategyHealth:
        sh = self.state.health.get(key)
        if sh is None:
            sh = StrategyHealth.fresh(key, now)
            self.state.health[key] = sh
        return sh

    def _ensure_raw(self, key: StrategyKey) -> _RawObs:
        r = self._raw.get(key)
        if r is None:
            r = _RawObs()
            self._raw[key] = r
        return r

    def _ensure_raw_regime(self, key: StrategyKey, regime_id: str) -> _RawObs:
        rk = (key, regime_id)
        r = self._raw_per_regime.get(rk)
        if r is None:
            r = _RawObs()
            self._raw_per_regime[rk] = r
        return r

    def _decay_cutoff(self, now: float) -> float:
        return now - self.cfg.decay_hours * 3600.0

    def _decay_deque(self, dq: Deque, cutoff: float) -> None:
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def _record_fill_locked(self, ev: FillEvent) -> None:
        key = (ev.source, ev.series)
        sh = self._ensure_health(key, ev.attempt_at)
        raw = self._ensure_raw(key)
        raw.fills.append((ev.attempt_at, ev.filled, ev.slippage_cents))
        # Regime slice
        regime_id = self.state.regime.regime_id()
        slice_raw = self._ensure_raw_regime(key, regime_id)
        slice_raw.fills.append((ev.attempt_at, ev.filled, ev.slippage_cents))
        # Decay
        cutoff = self._decay_cutoff(ev.attempt_at)
        self._decay_deque(raw.fills, cutoff)
        self._decay_deque(slice_raw.fills, cutoff)
        # Recompute axes
        sh.scorecard.execution = self._build_execution_axis(raw)
        sh.scorecard.updated_at = ev.attempt_at
        # Per-regime slice scorecard (lazy create, only execution updated for fill)
        slice_sc = sh.regime_slices.setdefault(regime_id, Scorecard.empty(ev.attempt_at))
        slice_sc.execution = self._build_execution_axis(slice_raw)
        slice_sc.updated_at = ev.attempt_at
        self._recompute_status(key, ev_at=ev.attempt_at)

    def _record_exit_locked(self, ev: ExitEvent) -> None:
        key = (ev.source, ev.series)
        sh = self._ensure_health(key, ev.exit_at)
        raw = self._ensure_raw(key)
        raw.edges.append((ev.exit_at, ev.realized_pnl_cents, ev.predicted_edge_cents))
        raw.capitals.append((ev.exit_at, ev.capital_pct_of_alloc))
        regime_id = self.state.regime.regime_id()
        slice_raw = self._ensure_raw_regime(key, regime_id)
        slice_raw.edges.append((ev.exit_at, ev.realized_pnl_cents, ev.predicted_edge_cents))
        slice_raw.capitals.append((ev.exit_at, ev.capital_pct_of_alloc))
        cutoff = self._decay_cutoff(ev.exit_at)
        for dq in (raw.edges, raw.capitals, slice_raw.edges, slice_raw.capitals):
            self._decay_deque(dq, cutoff)
        sh.scorecard.edge = self._build_edge_axis(raw)
        sh.scorecard.capital = self._build_capital_axis(raw)
        sh.scorecard.updated_at = ev.exit_at
        slice_sc = sh.regime_slices.setdefault(regime_id, Scorecard.empty(ev.exit_at))
        slice_sc.edge = self._build_edge_axis(slice_raw)
        slice_sc.capital = self._build_capital_axis(slice_raw)
        slice_sc.updated_at = ev.exit_at
        self._recompute_status(key, ev_at=ev.exit_at)

    def _build_edge_axis(self, raw: _RawObs) -> AxisScore:
        if not raw.edges:
            return AxisScore.empty()
        n, mean, m2 = welford_init()
        last = 0.0
        for _ts, realized, _pred in raw.edges:
            n, mean, m2 = welford_update(n, mean, m2, realized)
            last = realized
        var = m2 / (n - 1) if n > 1 else 0.0
        std = max(var, 0.0) ** 0.5
        p_below = normal_p_below_threshold(samples=n, mean=mean, std=std, threshold=0.0)
        return AxisScore(samples=n, posterior_mean=mean, posterior_std=std, p_below_threshold=p_below, last_value=last)

    def _build_execution_axis(self, raw: _RawObs) -> AxisScore:
        if not raw.fills:
            return AxisScore.empty()
        # Beta-Binomial on fill rate
        a, b = 1.0, 1.0
        last_slippage = 0.0
        for _ts, filled, slip in raw.fills:
            a, b = update_beta_binomial(a, b, success=filled)
            if filled and slip > last_slippage:
                last_slippage = slip
        n = len(raw.fills)
        mean = a / (a + b)
        var = (a * b) / ((a + b) ** 2 * (a + b + 1.0))
        std = var ** 0.5
        p_below = beta_p_below_threshold(a, b, self.cfg.exec_yellow_fill)
        # Pack last_value with last_slippage so the slippage Red rule can read it
        return AxisScore(samples=n, posterior_mean=mean, posterior_std=std, p_below_threshold=p_below, last_value=last_slippage)

    def _build_capital_axis(self, raw: _RawObs) -> AxisScore:
        if not raw.capitals:
            return AxisScore.empty()
        n, mean, m2 = welford_init()
        last = 0.0
        for _ts, pct in raw.capitals:
            n, mean, m2 = welford_update(n, mean, m2, pct)
            last = pct
        var = m2 / (n - 1) if n > 1 else 0.0
        std = max(var, 0.0) ** 0.5
        p_below = normal_p_below_threshold(samples=n, mean=mean, std=std, threshold=self.cfg.capital_yellow_pct)
        return AxisScore(samples=n, posterior_mean=mean, posterior_std=std, p_below_threshold=p_below, last_value=last)

    def _recompute_status(self, key: StrategyKey, ev_at: float) -> None:
        sh = self.state.health.get(key)
        if sh is None:
            return
        proposed = status_from_scorecard(sh.scorecard, self.cfg)
        # Apply revival gating before applying change
        target = revival_check(sh, proposed, ev_at, self.cfg, regime_changed_since_kill=False)
        if target is None or target == sh.status:
            # Apply regime override (may upgrade YELLOW→GREEN for current regime)
            effective = regime_override(sh, self.state.regime, self.cfg)
            if effective != sh.status and (ev_at - sh.last_status_change) >= self._debounce:
                _audit_change(self.audit_path, sh, effective, "regime_override", ev_at)
                apply_status_change(sh, effective, "regime_override", ev_at, self.cfg)
            return
        if (ev_at - sh.last_status_change) < self._debounce:
            return
        reason = self._reason(sh.scorecard, target)
        _audit_change(self.audit_path, sh, target, reason, ev_at)
        apply_status_change(sh, target, reason, ev_at, self.cfg)

    def _reason(self, sc: Scorecard, status: Status) -> str:
        # Cheap: name the worst axis and its key metric
        e, x, c = sc.edge, sc.execution, sc.capital
        if e.samples >= self.cfg.min_samples_for_kill and e.p_below_threshold > self.cfg.edge_yellow_p:
            return f"edge p_below={e.p_below_threshold:.2f} mean={e.posterior_mean:.2f} samples={e.samples}"
        if x.samples >= self.cfg.min_samples_for_kill and x.posterior_mean < self.cfg.exec_yellow_fill:
            return f"execution fill_rate={x.posterior_mean:.2f} samples={x.samples}"
        if c.samples >= self.cfg.min_samples_for_kill and c.posterior_mean < self.cfg.capital_yellow_pct:
            return f"capital pct={c.posterior_mean:.3f} samples={c.samples}"
        return status.value


def _audit_change(path: Optional[Path], sh: StrategyHealth, new_status: Status, reason: str, at: float) -> None:
    if not path:
        return
    append_audit(path, {
        "event": "STATUS",
        "source": sh.key[0],
        "series": sh.key[1],
        "from": sh.status.value,
        "to": new_status.value,
        "reason": reason,
        "at": at,
    })
