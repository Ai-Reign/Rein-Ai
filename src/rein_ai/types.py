"""Core data structures for the Rein.

All dataclasses are JSON-serialisable via dataclasses.asdict() and reloadable
via the ``from_dict`` classmethods. No external dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional, Tuple


StrategyKey = Tuple[str, str]      # (source, series)
FineKey     = Tuple[str, str, str] # (source, series, time_bucket)


class Status(Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    BLACK = "black"

    @property
    def severity(self) -> int:
        return {"green": 0, "yellow": 1, "red": 2, "black": 3}[self.value]

    @classmethod
    def worst(cls, *statuses: "Status") -> "Status":
        return max(statuses, key=lambda s: s.severity)


@dataclass
class AxisScore:
    samples: int
    posterior_mean: float
    posterior_std: float
    p_below_threshold: float   # P(true value < kill threshold for this axis)
    last_value: float

    @classmethod
    def empty(cls) -> "AxisScore":
        return cls(0, 0.0, 0.0, 0.0, 0.0)

    @classmethod
    def from_dict(cls, d: dict) -> "AxisScore":
        return cls(**d)


@dataclass
class Scorecard:
    edge: AxisScore
    execution: AxisScore
    capital: AxisScore
    updated_at: float

    @classmethod
    def empty(cls, now: float) -> "Scorecard":
        return cls(AxisScore.empty(), AxisScore.empty(), AxisScore.empty(), now)

    @classmethod
    def from_dict(cls, d: dict) -> "Scorecard":
        return cls(
            edge=AxisScore.from_dict(d["edge"]),
            execution=AxisScore.from_dict(d["execution"]),
            capital=AxisScore.from_dict(d["capital"]),
            updated_at=d["updated_at"],
        )


@dataclass
class StrategyHealth:
    key: StrategyKey
    status: Status
    scorecard: Scorecard
    regime_slices: Dict[str, Scorecard] = field(default_factory=dict)
    last_status_change: float = 0.0
    cooldown_until: float = 0.0
    kill_reason: Optional[str] = None
    revival_history: List[dict] = field(default_factory=list)

    @classmethod
    def fresh(cls, key: StrategyKey, now: float) -> "StrategyHealth":
        return cls(
            key=key,
            status=Status.YELLOW,   # safe-mode default
            scorecard=Scorecard.empty(now),
            last_status_change=now,
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = list(self.key)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyHealth":
        return cls(
            key=tuple(d["key"]),  # type: ignore[arg-type]
            status=Status(d["status"]),
            scorecard=Scorecard.from_dict(d["scorecard"]),
            regime_slices={k: Scorecard.from_dict(v) for k, v in d.get("regime_slices", {}).items()},
            last_status_change=d.get("last_status_change", 0.0),
            cooldown_until=d.get("cooldown_until", 0.0),
            kill_reason=d.get("kill_reason"),
            revival_history=d.get("revival_history", []),
        )


@dataclass
class Regime:
    vol_bucket: str          # "low" | "mid" | "hi" | "extreme"
    trend_bucket: str        # "chop" | "up" | "down" | "breakout"
    liquidity_bucket: str    # "deep" | "normal" | "thin" | "dead"
    time_bucket: str         # "us_open" | "us_close" | "overnight" | "weekend"
    macro_event: Optional[str]
    classified_at: float

    def regime_id(self) -> str:
        return f"vol:{self.vol_bucket}/liq:{self.liquidity_bucket}/time:{self.time_bucket}"

    @classmethod
    def unknown(cls, now: float = 0.0) -> "Regime":
        return cls("mid", "chop", "normal", "overnight", None, now)

    @classmethod
    def from_dict(cls, d: dict) -> "Regime":
        return cls(**d)


@dataclass
class AllowDecision:
    allowed: bool
    status: Status
    reason: str

    def size_multiplier(self) -> float:
        if not self.allowed:
            return 0.0
        return {Status.GREEN: 1.0, Status.YELLOW: 0.25, Status.RED: 0.0, Status.BLACK: 0.0}[self.status]


@dataclass
class ReinState:
    health: Dict[StrategyKey, StrategyHealth] = field(default_factory=dict)
    regime: Regime = field(default_factory=Regime.unknown)
    portfolio_pnl_today: float = 0.0
    portfolio_drawdown_today: float = 0.0
    halted: bool = False
    halted_reason: Optional[str] = None
    version: int = 0

    @classmethod
    def fresh(cls) -> "ReinState":
        return cls()

    def is_allowed(self, source: str, series: str) -> AllowDecision:
        if self.halted:
            return AllowDecision(False, Status.BLACK, f"portfolio halted: {self.halted_reason}")
        key = (source, series)
        sh = self.health.get(key)
        if sh is None:
            return AllowDecision(True, Status.YELLOW, "unknown strategy — safe-mode YELLOW")
        if sh.status in (Status.RED, Status.BLACK):
            return AllowDecision(False, sh.status, sh.kill_reason or sh.status.value)
        return AllowDecision(True, sh.status, "ok")

    def to_dict(self) -> dict:
        return {
            "health": {f"{k[0]}|{k[1]}": v.to_dict() for k, v in self.health.items()},
            "regime": asdict(self.regime),
            "portfolio_pnl_today": self.portfolio_pnl_today,
            "portfolio_drawdown_today": self.portfolio_drawdown_today,
            "halted": self.halted,
            "halted_reason": self.halted_reason,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ReinState":
        health: Dict[StrategyKey, StrategyHealth] = {}
        for k_str, v in d.get("health", {}).items():
            source, series = k_str.split("|", 1)
            health[(source, series)] = StrategyHealth.from_dict(v)
        return cls(
            health=health,
            regime=Regime.from_dict(d.get("regime", asdict(Regime.unknown()))),
            portfolio_pnl_today=d.get("portfolio_pnl_today", 0.0),
            portfolio_drawdown_today=d.get("portfolio_drawdown_today", 0.0),
            halted=d.get("halted", False),
            halted_reason=d.get("halted_reason"),
            version=d.get("version", 0),
        )
