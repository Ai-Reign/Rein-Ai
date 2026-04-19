"""Independent portfolio circuit breaker.

CRITICAL: this module must NEVER import tripwire_ai.types or any module that
depends on brain state types. It's the redundant safety system — it must work
even if the rest of the brain is broken or deserialised garbage.
"""
from __future__ import annotations

from dataclasses import dataclass

from tripwire_ai.config import TripwireConfig


@dataclass
class CircuitBreakerVerdict:
    halt: bool
    cancel_open: bool
    reason: str


def evaluate_circuit_breaker(
    starting_balance_usd: float,
    current_balance_usd: float,
    realized_pnl_today: float,
    cfg: TripwireConfig,
) -> CircuitBreakerVerdict:
    """Pure function. Returns whether to halt new orders and whether to cancel
    open orders. No I/O, no global state.
    """
    if starting_balance_usd <= 0 or current_balance_usd < cfg.balance_floor_usd:
        return CircuitBreakerVerdict(
            halt=True,
            cancel_open=current_balance_usd <= cfg.balance_floor_usd,
            reason=f"balance ${current_balance_usd:.2f} below floor ${cfg.balance_floor_usd:.2f}",
        )

    drawdown_pct = realized_pnl_today / starting_balance_usd
    if drawdown_pct <= cfg.portfolio_nuclear_pct:
        return CircuitBreakerVerdict(
            halt=True,
            cancel_open=True,
            reason=f"nuclear: drawdown {drawdown_pct:.2%} <= {cfg.portfolio_nuclear_pct:.2%}",
        )
    if drawdown_pct <= cfg.portfolio_floor_pct:
        return CircuitBreakerVerdict(
            halt=True,
            cancel_open=False,
            reason=f"halt: drawdown {drawdown_pct:.2%} <= {cfg.portfolio_floor_pct:.2%}",
        )

    return CircuitBreakerVerdict(halt=False, cancel_open=False, reason="")
