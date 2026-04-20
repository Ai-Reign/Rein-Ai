"""Real Claude agent governed by Rein — v2, using the Anthropic adapter.

Same demo as `real_claude_governor.py` but uses `GovernedToolRunner` so the
gate-and-record ceremony collapses from ~15 lines per tool to 1.

Run:
    cd examples/llm_agent_governor
    python3 real_claude_governor_v2.py
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import anthropic

from rein_ai import Rein, ReinConfig
from rein_ai.adapters.anthropic import GovernedToolRunner
from rein_ai.regime import RegimeInputs


def _load_env():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    env_path = Path.home() / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                return


# ---------- Tool impls (sync — the adapter handles both sync and async) ----------

def tool_calculator(expression: str) -> str:
    if set(expression) - set("0123456789+-*/().= "):
        return "error: only arithmetic allowed"
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as e:
        return f"error: {e}"


def tool_word_count(text: str) -> str:
    return f"{len(text.split())} words, {len(text)} characters"


def tool_reverse_string(text: str) -> str:
    return text[::-1]


def tool_broken_translator(text: str, target_language: str) -> str:
    """BROKEN by design — always fails."""
    return f"error: translator service unavailable ({target_language})"


TOOL_SCHEMAS = [
    {"name": "calculator", "description": "Evaluate a simple arithmetic expression",
     "input_schema": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}},
    {"name": "word_count", "description": "Count words and characters in text",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "reverse", "description": "Reverse a string",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "translate", "description": "Translate text to another language",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}, "target_language": {"type": "string"}},
                      "required": ["text", "target_language"]}},
]

TASKS = [
    "Translate 'hello world' to French.",
    "What is 17 * 23?",
    "How many words in: 'The quick brown fox jumps over the lazy dog'?",
    "Reverse the string 'anthropic'.",
    "Translate 'good morning' to Spanish.",
    "Compute 144 / 12.",
    "Translate 'thank you' to German.",
    "Reverse 'governor'.",
    "Translate 'goodbye' to Italian.",
    "Count the words in: 'Rein watches every action.'",
    "Translate 'yes' to Japanese.",
    "What is 99 + 1?",
    "Translate 'no' to Mandarin.",
    "Reverse 'claude'.",
    "Translate 'please' to Portuguese.",
    "Word count of: 'Autonomous agents need guardrails.'",
    "Translate 'sorry' to Dutch.",
    "Compute 50 * 4.",
    "Translate 'welcome' to Korean.",
    "Reverse 'opus'.",
]


async def regime_inputs_provider() -> RegimeInputs:
    return RegimeInputs(
        realized_vol_1h=0.0, returns_1h=0.0, returns_24h=0.0,
        book_depth_usd=1000.0, now_ts=time.time(),
    )


async def main():
    _load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set")
        return

    cfg = ReinConfig(
        enabled=True, shadow_mode=False,
        min_samples_for_kill=4, min_samples_for_green=3,
        exec_yellow_fill=0.80, exec_red_fill=0.50, exec_black_fill=0.30,
        exec_min_attempts=4, debounce_seconds=0.0,
    )
    persist = Path("/tmp/meta_real_claude_v2")
    if persist.exists():
        for f in persist.iterdir():
            f.unlink()
    persist.mkdir(parents=True, exist_ok=True)

    brain = Rein(cfg=cfg, persist_dir=persist,
                      regime_inputs_provider=regime_inputs_provider)
    await brain.start()

    # *** This is the whole integration: 5 lines ***
    runner = GovernedToolRunner(
        brain=brain,
        source="claude_agent",
        impls={
            "calculator": tool_calculator,
            "word_count": tool_word_count,
            "reverse":    tool_reverse_string,
            "translate":  tool_broken_translator,
        },
    )

    client = anthropic.AsyncAnthropic()
    print(f"Real-Claude dogfood v2 (via GovernedToolRunner) — {len(TASKS)} tasks\n")

    blocked_count = 0
    for i, task in enumerate(TASKS):
        if i > 0 and i % 3 == 0:
            await brain._scorer.tick()

        response = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            tools=TOOL_SCHEMAS,
            messages=[{"role": "user", "content": task}],
        )
        tool_block = next((b for b in response.content if b.type == "tool_use"), None)
        if not tool_block:
            print(f"[{i:02d}] ? no tool selected")
            continue

        outcome = await runner.execute(tool_block)
        if outcome.blocked:
            blocked_count += 1
            print(f"[{i:02d}] 🛑 {outcome.tool_name:12s} BLOCKED  {outcome.block_reason}")
        else:
            icon = "✓" if outcome.success else "✗"
            print(f"[{i:02d}] {icon} {outcome.tool_name:12s} {json.dumps(tool_block.input)[:40]:40s} -> {str(outcome.result)[:40]}")

    print()
    print(f"Summary: {blocked_count} blocked; strategy health:")
    for key, h in sorted(brain.snapshot()["health"].items()):
        icon = {"green": "✅", "yellow": "⚠️ ", "red": "❌", "black": "⛔"}.get(h["status"], "?")
        print(f"  {icon} {key:40s} {h['status']:<6s}  {h.get('kill_reason') or ''}")

    await brain.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
