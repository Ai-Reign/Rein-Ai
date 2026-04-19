"""Rein orchestrator: starts sidecars, exposes gate(), records events,
persists state, exposes a snapshot.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from rein_ai.anomaly import AnomalyAlert, AnomalyDetector
from rein_ai.circuit_breaker import CircuitBreakerVerdict, evaluate_circuit_breaker
from rein_ai.config import ReinConfig
from rein_ai.persist import load_state, save_state
from rein_ai.rate_limit import TokenBucketLimiter
from rein_ai.regime import RegimeDetector, RegimeInputs
from rein_ai.strategy_scorer import ExitEvent, FillEvent, StrategyScorer
from rein_ai.types import AllowDecision, ReinState, Regime, Status, StrategyHealth


log = logging.getLogger("rein.brain")


SAFE_MODE_STALE_SECONDS = 10 * 60


class Rein:
    def __init__(
        self,
        cfg: Optional[ReinConfig] = None,
        persist_dir: Optional[Path] = None,
        regime_inputs_provider: Optional[Callable[[], Awaitable[RegimeInputs]]] = None,
        rate_limiter: Optional[TokenBucketLimiter] = None,
        anomaly_detector: Optional[AnomalyDetector] = None,
    ):
        self.cfg = cfg or ReinConfig.from_env()
        self.persist_dir = Path(persist_dir) if persist_dir else Path("./rein_state")
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.persist_dir / "rein_state.json"
        self.audit_path = self.persist_dir / "rein_audit.jsonl"
        self.baselines_path = self.persist_dir / "rein_baselines.json"

        self._state = self._load_or_fresh()
        self._scorer = StrategyScorer(cfg=self.cfg, state=self._state, audit_path=self.audit_path)
        self._regime_inputs_provider = regime_inputs_provider
        self._detector: Optional[RegimeDetector] = None
        self._persist_task: Optional[asyncio.Task] = None
        self._scorer_tick_task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._portfolio = {
            "starting_balance_usd": 0.0,
            "current_balance_usd": 0.0,
            "realized_pnl_today": 0.0,
        }
        # Optional guardrails — no-ops by default, enabled when constructed with real limits
        self._rate_limiter = rate_limiter
        self._anomaly = anomaly_detector

    # ---------- lifecycle ----------

    def _load_or_fresh(self) -> ReinState:
        loaded = load_state(self.state_path)
        if loaded is None:
            log.info("[REIN] no prior state — starting fresh in safe mode")
            return ReinState.fresh()
        try:
            mtime = self.state_path.stat().st_mtime
        except FileNotFoundError:
            mtime = 0.0
        if time.time() - mtime > SAFE_MODE_STALE_SECONDS:
            log.warning("[REIN] state file stale; demoting all GREEN → YELLOW for safety")
            for sh in loaded.health.values():
                if sh.status == Status.GREEN:
                    sh.status = Status.YELLOW
                    sh.kill_reason = "safe-mode demotion (stale state on restart)"
        return loaded

    async def start(self) -> None:
        if not self.cfg.enabled:
            log.info("[REIN] disabled by config — gate() always GREEN")
            return
        if self._regime_inputs_provider is not None:
            self._detector = RegimeDetector(
                cfg=self.cfg,
                get_inputs=self._regime_inputs_provider,
                on_regime=self._on_regime,
                baselines_path=self.baselines_path,
                audit_path=self.audit_path,
            )
            await self._detector.start()
        self._persist_task = asyncio.create_task(self._persist_loop(), name="rein.persist")
        self._scorer_tick_task = asyncio.create_task(self._scorer_tick_loop(), name="rein.scorer.tick")

    async def shutdown(self) -> None:
        self._stop.set()
        if self._detector:
            await self._detector.stop()
        for t in (self._persist_task, self._scorer_tick_task):
            if t:
                try:
                    await asyncio.wait_for(t, timeout=5.0)
                except asyncio.TimeoutError:
                    t.cancel()
        await self.persist_now()

    async def _persist_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.cfg.persist_tick_seconds)
            except asyncio.TimeoutError:
                pass
            await self.persist_now()

    async def _scorer_tick_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.cfg.regime_tick_seconds)
            except asyncio.TimeoutError:
                pass
            await self._scorer.tick()

    async def persist_now(self) -> None:
        try:
            self._state.version += 1
            save_state(self._state, self.state_path)
        except Exception as e:
            log.warning(f"[REIN] persist failed: {e}")

    def _on_regime(self, r: Regime) -> None:
        self._state.regime = r

    # ---------- public API ----------

    def gate(self, source: str, series: str) -> AllowDecision:
        """The single hot-path call from the host application. Microseconds.

        Order of checks:
          1. Rate limiter (cheap, early rejection protects downstream)
          2. Portfolio halt
          3. Strategy-level decision from scorecard
        Anomaly detector records every gate() call after the decision is made.
        """
        # 1. Rate-limit check (if configured). Always enforced — even in shadow
        # mode — because rate limiting is a resource safety concern, not a
        # strategy-correctness concern.
        if self._rate_limiter is not None:
            allowed, reason = self._rate_limiter.check((source, series))
            if not allowed:
                decision = AllowDecision(False, Status.RED, reason)
                if self._anomaly is not None:
                    self._anomaly.record(source, series, allowed=False)
                return decision

        # 2. Portfolio halt
        if self._state.halted:
            if self.cfg.shadow_mode:
                decision = AllowDecision(True, Status.GREEN, "shadow: would-halt")
            else:
                decision = AllowDecision(False, Status.BLACK,
                                          f"portfolio halted: {self._state.halted_reason}")
        else:
            # 3. Strategy scorecard
            raw = self._state.is_allowed(source, series)
            if self.cfg.shadow_mode:
                decision = AllowDecision(True, Status.GREEN,
                                          f"shadow: would-be {raw.status.value}")
            else:
                decision = raw

        # Anomaly detector observes every call. Never blocks.
        if self._anomaly is not None:
            self._anomaly.record(source, series, allowed=decision.allowed)

        return decision

    def portfolio_halted(self) -> bool:
        return self._state.halted

    def update_portfolio(
        self,
        starting_balance_usd: float,
        current_balance_usd: float,
        realized_pnl_today: float,
    ) -> CircuitBreakerVerdict:
        self._portfolio["starting_balance_usd"] = starting_balance_usd
        self._portfolio["current_balance_usd"] = current_balance_usd
        self._portfolio["realized_pnl_today"] = realized_pnl_today
        verdict = evaluate_circuit_breaker(
            starting_balance_usd, current_balance_usd, realized_pnl_today, self.cfg
        )
        self._state.portfolio_pnl_today = realized_pnl_today
        self._state.portfolio_drawdown_today = (
            realized_pnl_today / starting_balance_usd if starting_balance_usd > 0 else 0.0
        )
        if verdict.halt and not self._state.halted:
            self._state.halted = True
            self._state.halted_reason = verdict.reason
            log.warning(f"[REIN] HALT {verdict.reason} cancel_open={verdict.cancel_open}")
        return verdict

    def manual_resume(self) -> None:
        self._state.halted = False
        self._state.halted_reason = None
        log.info("[REIN] manual resume")

    def manual_revive(self, source: str, series: str, operator_reason: str = "") -> bool:
        sh = self._state.health.get((source, series))
        if sh is None:
            return False
        sh.status = Status.YELLOW
        sh.kill_reason = None
        sh.cooldown_until = 0.0
        sh.last_status_change = time.time()
        sh.revival_history.append({
            "from": "black",
            "to": "yellow",
            "reason": f"manual_revive: {operator_reason}",
            "at": time.time(),
        })
        log.info(f"[REIN] REVIVE {source} {series} ({operator_reason})")
        return True

    def force_status(self, source: str, series: str, status: Status, reason: str, ttl_seconds: float = 0.0) -> None:
        sh = self._state.health.setdefault((source, series), StrategyHealth.fresh((source, series), time.time()))
        sh.status = status
        sh.kill_reason = reason if status != Status.GREEN else None
        sh.last_status_change = time.time()
        if ttl_seconds > 0:
            sh.cooldown_until = time.time() + ttl_seconds

    async def record_fill(self, *, source: str, series: str, ticker: str,
                          filled: bool, slippage_cents: float, attempt_at: float) -> None:
        await self._scorer.record_fill(FillEvent(source, series, ticker, filled, slippage_cents, attempt_at))

    async def record_exit(self, *, source: str, series: str, ticker: str,
                          realized_pnl_cents: float, predicted_edge_cents: float,
                          capital_pct_of_alloc: float, exit_at: float) -> None:
        await self._scorer.record_exit(ExitEvent(source, series, ticker,
                                                 realized_pnl_cents, predicted_edge_cents,
                                                 capital_pct_of_alloc, exit_at))

    def snapshot(self) -> dict:
        return self._state.to_dict()

    def governed(self, *args, **kwargs):
        """Decorator factory: `@brain.governed(source="agent")`.

        Wraps an async function so the gate is consulted before each call and
        fill/exit are recorded automatically. Raises GateBlockedError on deny
        unless `raise_on_block=False`.
        """
        from rein_ai.middleware import make_governed
        return make_governed(self)(*args, **kwargs)
