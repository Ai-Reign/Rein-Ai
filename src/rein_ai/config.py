"""Configurable thresholds for the Rein. All env-overridable.

Env prefix is configurable — default 'REIN_'. Pass prefix='APPNAME_REIN_'
(or whatever your domain uses) to from_env() for backwards-compat.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _envf(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


def _envi(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _envb(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ReinConfig:
    # Cold-start protection
    min_samples_for_kill: int = 10
    min_samples_for_green: int = 20

    # Edge axis thresholds (P(true_edge < 0))
    edge_yellow_p: float = 0.60
    edge_red_p:    float = 0.85
    edge_black_mean_cents: float = -2.0
    edge_black_min_samples: int = 30

    # Execution axis thresholds (fill rate)
    exec_yellow_fill: float = 0.25
    exec_red_fill:    float = 0.10
    exec_black_fill:  float = 0.05
    exec_red_slippage_cents: float = 3.0
    exec_min_attempts: int = 20

    # Capital axis thresholds (rolling PnL pct of allocation)
    capital_yellow_pct: float = -0.02
    capital_red_pct:    float = -0.05
    capital_black_pct:  float = -0.10

    # Portfolio circuit breaker
    portfolio_floor_pct: float = -0.05
    portfolio_nuclear_pct: float = -0.08
    balance_floor_usd: float = 20.0

    # Revival
    red_cooldown_hours: float = 2.0
    revival_consecutive_obs: int = 10
    revival_buffer_pct: float = 0.20

    # Anti-flap / decay
    debounce_seconds: float = 300.0
    decay_hours: float = 24.0

    # Sidecar timing
    regime_tick_seconds: float = 30.0
    persist_tick_seconds: float = 30.0

    # Modes
    enabled: bool = True
    shadow_mode: bool = True

    @classmethod
    def from_env(cls, prefix: str = "REIN_") -> "ReinConfig":
        p = prefix
        return cls(
            min_samples_for_kill=_envi(f"{p}MIN_SAMPLES_FOR_KILL", 10),
            min_samples_for_green=_envi(f"{p}MIN_SAMPLES_FOR_GREEN", 20),
            edge_yellow_p=_envf(f"{p}EDGE_YELLOW_P", 0.60),
            edge_red_p=_envf(f"{p}EDGE_RED_P", 0.85),
            edge_black_mean_cents=_envf(f"{p}EDGE_BLACK_MEAN_CENTS", -2.0),
            edge_black_min_samples=_envi(f"{p}EDGE_BLACK_MIN_SAMPLES", 30),
            exec_yellow_fill=_envf(f"{p}EXEC_YELLOW_FILL", 0.25),
            exec_red_fill=_envf(f"{p}EXEC_RED_FILL", 0.10),
            exec_black_fill=_envf(f"{p}EXEC_BLACK_FILL", 0.05),
            exec_red_slippage_cents=_envf(f"{p}EXEC_RED_SLIPPAGE_CENTS", 3.0),
            exec_min_attempts=_envi(f"{p}EXEC_MIN_ATTEMPTS", 20),
            capital_yellow_pct=_envf(f"{p}CAPITAL_YELLOW_PCT", -0.02),
            capital_red_pct=_envf(f"{p}CAPITAL_RED_PCT", -0.05),
            capital_black_pct=_envf(f"{p}CAPITAL_BLACK_PCT", -0.10),
            portfolio_floor_pct=_envf(f"{p}PORTFOLIO_FLOOR_PCT", -0.05),
            portfolio_nuclear_pct=_envf(f"{p}PORTFOLIO_NUCLEAR_PCT", -0.08),
            balance_floor_usd=_envf(f"{p}BALANCE_FLOOR_USD", 20.0),
            red_cooldown_hours=_envf(f"{p}RED_COOLDOWN_HOURS", 2.0),
            revival_consecutive_obs=_envi(f"{p}REVIVAL_CONSECUTIVE_OBS", 10),
            revival_buffer_pct=_envf(f"{p}REVIVAL_BUFFER_PCT", 0.20),
            debounce_seconds=_envf(f"{p}DEBOUNCE_SECONDS", 300.0),
            decay_hours=_envf(f"{p}DECAY_HOURS", 24.0),
            regime_tick_seconds=_envf(f"{p}REGIME_TICK_SECONDS", 30.0),
            persist_tick_seconds=_envf(f"{p}PERSIST_TICK_SECONDS", 30.0),
            enabled=_envb(f"{p}ENABLED", True),
            shadow_mode=_envb(f"{p}SHADOW", True),
        )
