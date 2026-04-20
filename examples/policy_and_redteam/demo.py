"""End-to-end demo: write a policy in English, simulate attacks against it.

Run:
    cd ~/Desktop/rein-ai
    pip install -e .
    python3 examples/policy_and_redteam/demo.py

What you'll see:
    1. Three operator sentences become an enforceable governance config
    2. Five red-team attacks run against that config
    3. A catch-rate report tells you which attacks slipped past
"""
from __future__ import annotations

import asyncio
import tempfile
from dataclasses import replace
from pathlib import Path

from rein_ai import compile_policy, run_red_team
from rein_ai.brain import Rein


POLICY = [
    "Cap each caller at 8 requests per second with bursts of 16",
    "Halt the portfolio when losses exceed 5 percent",
    "Alert when deny rate exceeds 70 percent over a 60 second window",
    "Detect runaway callers at 5x baseline",
]


async def main() -> None:
    print("=" * 64)
    print("REIN — natural-language policy + red team demo")
    print("=" * 64)
    print()
    print("Operator policy (English):")
    for line in POLICY:
        print(f"  - {line}")
    print()

    policy = compile_policy(POLICY, use_llm_fallback=False)
    print(policy.explain())
    print()

    with tempfile.TemporaryDirectory() as td:
        brain = Rein(
            cfg=replace(policy.config, shadow_mode=False),
            persist_dir=Path(td),
            rate_limiter=policy.rate_limiter,
            anomaly_detector=policy.anomaly_detector,
        )
        # The synthetic attacks fire faster than real workloads. Tighten
        # detector min-counts so the demo finishes in <1s.
        brain._anomaly.deny_storm_min_count = 10
        brain._anomaly.new_caller_threshold = 20

        print("Running red-team attacks...")
        print()
        report = await run_red_team(brain)
        print(report.render())
        print()
        print(f"Final catch rate: {report.catch_rate:.0%}")


if __name__ == "__main__":
    asyncio.run(main())
