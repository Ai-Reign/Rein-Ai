"""Tripwire as an LLM agent governor.

Problem: an autonomous Claude/GPT agent is taking expensive actions in a loop.
You want to prevent runaway cost, bad-state spirals, and actions nobody
can audit later.

Tripwire treats each (tool, task) pair as a "strategy". The scorer learns
which tool+task combos are paying off (fills = successful completion,
slippage = cost overrun vs estimate, pnl = task reward - token cost).

Run this example:
    cd examples/llm_agent_governor && python3 governor.py
"""
from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path

from tripwire_ai import Tripwire, TripwireConfig
from tripwire_ai.regime import RegimeInputs


# ---------- Domain adapter: an LLM agent ----------

class FakeLLMAgent:
    """Stand-in for a real Claude/GPT agent. Emits token spend + completions.

    In a real system, wire this to Anthropic SDK / OpenAI SDK usage callbacks.
    """

    def __init__(self) -> None:
        self.hourly_tokens = 0.0
        self.daily_budget_usd = 100.0
        self.spent_usd = 0.0

    async def regime_inputs(self) -> RegimeInputs:
        # Reuse RegimeInputs fields for LLM domain:
        # realized_vol_1h → token-spend velocity (proxy for chaos)
        # returns_1h      → recent budget burn rate (negative = wasting tokens)
        # book_depth_usd  → remaining budget (proxy for liquidity)
        return RegimeInputs(
            realized_vol_1h=self.hourly_tokens / 10000.0,
            returns_1h=-self.spent_usd / max(self.daily_budget_usd, 1.0),
            returns_24h=-self.spent_usd / max(self.daily_budget_usd, 1.0),
            book_depth_usd=max(0.0, self.daily_budget_usd - self.spent_usd),
            now_ts=time.time(),
        )

    async def execute_tool(self, tool: str, task: str) -> tuple[bool, float, float]:
        """Returns (success, cost_usd, reward_usd)."""
        broken = (tool == "web_search" and task == "stock_price")
        if broken:
            success = False
            cost = random.uniform(0.03, 0.08)
        else:
            success = random.random() > 0.08      # healthy tools: ~92% fill
            cost = random.uniform(0.002, 0.010)
        reward = 0.10 if success else 0.0
        self.spent_usd += cost
        self.hourly_tokens += random.uniform(500, 2000)
        return success, cost, reward


# ---------- Glue ----------

async def main():
    agent = FakeLLMAgent()
    cfg = TripwireConfig.from_env(prefix="LLM_META_")
    # Force shadow mode OFF so we actually see gate() blocking in the example
    cfg = TripwireConfig(
        enabled=True,
        shadow_mode=False,
        min_samples_for_kill=5,
        min_samples_for_green=3,
        exec_yellow_fill=0.80,
        exec_red_fill=0.50,
        exec_black_fill=0.30,
        exec_min_attempts=5,
        debounce_seconds=0.0,     # demo runs in seconds; disable anti-flap
    )

    persist = Path("/tmp/meta_llm_example")
    persist.mkdir(parents=True, exist_ok=True)

    brain = Tripwire(
        cfg=cfg,
        persist_dir=persist,
        regime_inputs_provider=agent.regime_inputs,
    )
    await brain.start()

    tools_tasks = [
        ("web_search", "stock_price"),   # broken — should get killed
        ("web_search", "general_q"),     # ok
        ("code_exec",  "data_analysis"), # ok
        ("email_send", "summary"),       # ok
    ]

    blocked = 0
    for i in range(80):
        # Force scorer tick periodically so kills materialize in the demo
        if i > 0 and i % 10 == 0:
            await brain._scorer.tick()

        tool, task = random.choice(tools_tasks)
        decision = brain.gate(source=tool, series=task)
        if not decision.allowed:
            blocked += 1
            print(f"[{i:02d}] BLOCKED {tool:12s}/{task:15s}  reason={decision.reason}")
            continue

        success, cost, reward = await agent.execute_tool(tool, task)
        slippage = cost * 100  # model cost overrun as slippage (cents)
        await brain.record_fill(
            source=tool, series=task, ticker=f"call-{i}",
            filled=success, slippage_cents=slippage, attempt_at=time.time(),
        )
        if success:
            await brain.record_exit(
                source=tool, series=task, ticker=f"call-{i}",
                realized_pnl_cents=(reward - cost) * 100,
                predicted_edge_cents=5.0,
                capital_pct_of_alloc=cost / cfg.balance_floor_usd,
                exit_at=time.time(),
            )
        mark = "✓" if success else "✗"
        print(f"[{i:02d}] {mark} {tool:12s}/{task:15s}  cost=${cost:.3f}  reward=${reward:.2f}")

        await asyncio.sleep(0.05)

    print()
    print(f"Summary: {blocked} blocked by gate, ${agent.spent_usd:.2f} spent of ${agent.daily_budget_usd}")
    print()
    print("Strategy health after run:")
    snap = brain.snapshot()
    for key, health in snap.get("health", {}).items():
        print(f"  {key:40s} status={health['status']:<6s}  reason={health.get('kill_reason') or '-'}")

    await brain.shutdown()
    print(f"\nAudit log written to {persist / 'tripwire_audit.jsonl'}")


if __name__ == "__main__":
    asyncio.run(main())
