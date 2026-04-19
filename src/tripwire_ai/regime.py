"""Regime classification + sidecar task.

Classification is pure functions (testable). The sidecar task wraps them
with periodic polling of domain-specific inputs. Baselines are pluggable
via a BaselinesProvider — domain adapters implement their own data fetch.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional

from tripwire_ai.config import TripwireConfig
from tripwire_ai.persist import append_audit
from tripwire_ai.types import Regime


log = logging.getLogger("tripwire.regime")


@dataclass
class Baselines:
    """Percentile baselines used to bucket vol and liquidity. Calibrated from
    historical data on bootstrap; refreshed every 6h.
    """
    vol_p25: float
    vol_p75: float
    vol_p95: float
    depth_p25: float
    depth_p75: float

    def to_dict(self) -> dict:
        return {
            "vol_p25": self.vol_p25, "vol_p75": self.vol_p75, "vol_p95": self.vol_p95,
            "depth_p25": self.depth_p25, "depth_p75": self.depth_p75,
        }

    @classmethod
    def default(cls) -> "Baselines":
        return cls(vol_p25=0.005, vol_p75=0.020, vol_p95=0.040, depth_p25=20.0, depth_p75=200.0)


@dataclass
class RegimeInputs:
    realized_vol_1h: float
    returns_1h: float
    returns_24h: float
    book_depth_usd: float
    now_ts: float


# ----- pure classifiers -----

def classify_vol(vol: float, baselines: Baselines) -> str:
    if vol < baselines.vol_p25:
        return "low"
    if vol < baselines.vol_p75:
        return "mid"
    if vol < baselines.vol_p95:
        return "hi"
    return "extreme"


def classify_trend(returns_1h: float, returns_24h: float) -> str:
    if abs(returns_1h) >= 0.020:
        return "breakout"
    if returns_1h > 0.005 and returns_24h > 0.0:
        return "up"
    if returns_1h < -0.005 and returns_24h < 0.0:
        return "down"
    return "chop"


def classify_liquidity(book_depth_usd: float, baselines: Baselines) -> str:
    if book_depth_usd <= 0.5:
        return "dead"
    if book_depth_usd < baselines.depth_p25:
        return "thin"
    if book_depth_usd < baselines.depth_p75:
        return "normal"
    return "deep"


def classify_time(now_ts: float) -> str:
    dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    if dt.weekday() >= 5:
        return "weekend"
    # ET is UTC-4 (EDT) or UTC-5 (EST). Use UTC offset 4 as a coarse approx —
    # this is informational, not for trading decisions, so EDT/EST drift is OK.
    et_hour = (dt.hour - 4) % 24
    if 9 <= et_hour < 12:
        return "us_open"
    if 15 <= et_hour < 17:
        return "us_close"
    if 12 <= et_hour < 15:
        return "us_mid"
    return "overnight"


def classify_macro(now_ts: float, calendar: Dict[str, str]) -> Optional[str]:
    """Calendar maps 'YYYY-MM-DD' (UTC) to event name."""
    key = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    return calendar.get(key)


def classify_regime(inputs: RegimeInputs, baselines: Baselines, macro_calendar: Dict[str, str]) -> Regime:
    return Regime(
        vol_bucket=classify_vol(inputs.realized_vol_1h, baselines),
        trend_bucket=classify_trend(inputs.returns_1h, inputs.returns_24h),
        liquidity_bucket=classify_liquidity(inputs.book_depth_usd, baselines),
        time_bucket=classify_time(inputs.now_ts),
        macro_event=classify_macro(inputs.now_ts, macro_calendar),
        classified_at=inputs.now_ts,
    )


# ----- baselines provider protocol -----

BaselinesProvider = Callable[[], Awaitable[Baselines]]
"""Async callable returning calibrated Baselines for this domain.
Domain adapters (trading, LLM agents, etc.) implement their own.
The default provider returns Baselines.default()."""


async def default_baselines_provider() -> Baselines:
    return Baselines.default()


# ----- sidecar task -----

class RegimeDetector:
    """Sidecar asyncio task that classifies regime every tick."""

    def __init__(
        self,
        cfg: TripwireConfig,
        get_inputs: Callable[[], Awaitable[RegimeInputs]],
        on_regime: Callable[[Regime], None],
        macro_calendar: Optional[Dict[str, str]] = None,
        baselines_path: Optional[Path] = None,
        audit_path: Optional[Path] = None,
        baselines_provider: Optional[BaselinesProvider] = None,
    ):
        self.cfg = cfg
        self.get_inputs = get_inputs
        self.on_regime = on_regime
        self.macro_calendar = macro_calendar or {}
        self.baselines_path = baselines_path
        self.audit_path = audit_path
        self.baselines_provider = baselines_provider or default_baselines_provider
        self.baselines = self._load_baselines() or Baselines.default()
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._last_regime: Optional[Regime] = None
        self._last_baseline_refresh: float = 0.0

    def _load_baselines(self) -> Optional[Baselines]:
        if not self.baselines_path or not self.baselines_path.exists():
            return None
        try:
            d = json.loads(self.baselines_path.read_text())
            return Baselines(**d)
        except Exception:
            return None

    def _save_baselines(self) -> None:
        if not self.baselines_path:
            return
        self.baselines_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(self.baselines_path) + ".tmp"
        Path(tmp).write_text(json.dumps(self.baselines.to_dict()))
        Path(tmp).replace(self.baselines_path)

    async def start(self) -> None:
        # Refresh baselines from the provider if we have none persisted.
        if self.baselines == Baselines.default():
            self.baselines = await self.baselines_provider()
            self._save_baselines()
            self._last_baseline_refresh = time.time()
        self._task = asyncio.create_task(self._run(), name="tripwire.regime")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                inputs = await self.get_inputs()
                new_regime = classify_regime(inputs, self.baselines, self.macro_calendar)
                if self._last_regime is None or new_regime.regime_id() != self._last_regime.regime_id():
                    if self.audit_path:
                        append_audit(self.audit_path, {
                            "event": "REGIME",
                            "from": self._last_regime.regime_id() if self._last_regime else None,
                            "to": new_regime.regime_id(),
                            "at": new_regime.classified_at,
                        })
                    log.info(f"[TRIPWIRE] REGIME {self._last_regime.regime_id() if self._last_regime else 'init'}->{new_regime.regime_id()}")
                self._last_regime = new_regime
                self.on_regime(new_regime)

                # Periodic baseline refresh (6h)
                if time.time() - self._last_baseline_refresh > 6 * 3600:
                    self.baselines = await self.baselines_provider()
                    self._save_baselines()
                    self._last_baseline_refresh = time.time()
            except Exception as e:
                log.warning(f"[TRIPWIRE] RegimeDetector tick error: {e}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.cfg.regime_tick_seconds)
            except asyncio.TimeoutError:
                continue
