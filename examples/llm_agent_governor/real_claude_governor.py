"""Real Claude agent governed by Tripwire.

Replaces the synthetic fake-agent demo with actual Claude API calls. A Claude
agent is given 4 simple "tools" (really Python functions); one of them is
intentionally broken so it always returns an error. Tripwire watches the
fill rate for each (tool, task) pair and kills the broken combo after a few
failures — preventing further wasted tokens.

Run:
    cd examples/llm_agent_governor
    python3 real_claude_governor.py

Requires: ANTHROPIC_API_KEY env var, or `source ~/.env` first.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from pathlib import Path

import anthropic

from tripwire_ai import Tripwire, TripwireConfig
from tripwire_ai.regime import RegimeInputs


# Load API key from the `.env` file if not already set
def _load_env():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    env_path = Path.home() / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                return


# ---------- Tool implementations (4 tools, one broken) ----------

def tool_calculator(expression: str) -> str:
    """Healthy tool: safe arithmetic on simple expressions."""
    try:
        allowed = set("0123456789+-*/().= ")
        if not set(expression) <= allowed:
            return "error: only arithmetic expressions allowed"
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as e:
        return f"error: {e}"


def tool_word_count(text: str) -> str:
    """Healthy tool: word and char count."""
    words = len(text.split())
    chars = len(text)
    return f"{words} words, {chars} characters"


def tool_reverse_string(text: str) -> str:
    """Healthy tool: returns reversed string."""
    return text[::-1]


def tool_broken_translator(text: str, target_language: str) -> str:
    """BROKEN tool: always returns error. Tripwire should detect + kill."""
    return f"error: translator service unavailable (attempted {target_language})"


TOOL_IMPLS = {
    "calculator":  tool_calculator,
    "word_count":  tool_word_count,
    "reverse":     tool_reverse_string,
    "translate":   tool_broken_translator,
}

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


# ---------- The task queue: a mix of tasks, some forcing the broken tool ----------

TASKS = [
    ("Translate 'hello world' to French.", "translate"),
    ("What is 17 * 23?", "calculator"),
    ("How many words in: 'The quick brown fox jumps over the lazy dog'?", "word_count"),
    ("Reverse the string 'anthropic'.", "reverse"),
    ("Translate 'good morning' to Spanish.", "translate"),
    ("Compute 144 / 12.", "calculator"),
    ("Translate 'thank you' to German.", "translate"),
    ("Reverse 'governor'.", "reverse"),
    ("Translate 'goodbye' to Italian.", "translate"),
    ("Count the words in: 'Tripwire watches every action.'", "word_count"),
    ("Translate 'yes' to Japanese.", "translate"),
    ("What is 99 + 1?", "calculator"),
    ("Translate 'no' to Mandarin.", "translate"),
    ("Reverse 'claude'.", "reverse"),
    ("Translate 'please' to Portuguese.", "translate"),
    ("Word count of: 'Autonomous agents need guardrails.'", "word_count"),
    ("Translate 'sorry' to Dutch.", "translate"),
    ("Compute 50 * 4.", "calculator"),
    ("Translate 'welcome' to Korean.", "translate"),
    ("Reverse 'opus'.", "reverse"),
]


# ---------- Glue: run a single turn against Claude ----------

async def run_one_turn(client: anthropic.AsyncAnthropic, task: str, expected_tool: str,
                       brain: Tripwire, turn_idx: int) -> tuple[str, bool, float]:
    """Ask Claude to solve `task` using the available tools. Returns (result, success, duration_s)."""
    t0 = time.time()
    messages = [{"role": "user", "content": task}]

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )
    except anthropic.APIError as e:
        return (f"api error: {e}", False, time.time() - t0)

    # Find the tool_use block
    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if not tool_block:
        text = next((b.text for b in response.content if b.type == "text"), "")
        return (f"no tool used: {text[:80]}", False, time.time() - t0)

    tool_name = tool_block.name
    tool_input = tool_block.input

    # *** Tripwire gate: should we let this tool call proceed? ***
    series = tool_name  # one (source=agent, series=tool_name) per tool
    decision = brain.gate(source="claude_agent", series=series)
    if not decision.allowed:
        return (f"GATE BLOCKED {tool_name}: {decision.reason}", False, time.time() - t0)

    # Execute the tool
    impl = TOOL_IMPLS.get(tool_name)
    if impl is None:
        return (f"unknown tool: {tool_name}", False, time.time() - t0)
    result = impl(**tool_input)
    success = not result.startswith("error:")

    # Record into Tripwire
    await brain.record_fill(
        source="claude_agent", series=series, ticker=f"turn-{turn_idx}",
        filled=success, slippage_cents=0.0, attempt_at=time.time(),
    )
    if success:
        await brain.record_exit(
            source="claude_agent", series=series, ticker=f"turn-{turn_idx}",
            realized_pnl_cents=10.0, predicted_edge_cents=5.0,
            capital_pct_of_alloc=0.01, exit_at=time.time(),
        )

    return (f"{tool_name}({json.dumps(tool_input)[:60]}) -> {result[:60]}",
            success, time.time() - t0)


async def regime_inputs_provider() -> RegimeInputs:
    """Minimal regime inputs — reuse the fields as placeholders."""
    return RegimeInputs(
        realized_vol_1h=0.0, returns_1h=0.0, returns_24h=0.0,
        book_depth_usd=1000.0, now_ts=time.time(),
    )


async def main():
    _load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. Source ~/.env first.")
        return

    cfg = TripwireConfig(
        enabled=True,
        shadow_mode=False,
        min_samples_for_kill=4,
        min_samples_for_green=3,
        exec_yellow_fill=0.80,
        exec_red_fill=0.50,
        exec_black_fill=0.30,
        exec_min_attempts=4,
        debounce_seconds=0.0,
    )

    persist = Path("/tmp/meta_real_claude")
    if persist.exists():
        for f in persist.iterdir():
            f.unlink()
    persist.mkdir(parents=True, exist_ok=True)

    brain = Tripwire(
        cfg=cfg,
        persist_dir=persist,
        regime_inputs_provider=regime_inputs_provider,
    )
    await brain.start()

    client = anthropic.AsyncAnthropic()
    print(f"Real-Claude dogfood — {len(TASKS)} tasks, Haiku 4.5\n")

    blocked = 0
    results = []
    for i, (task, expected_tool) in enumerate(TASKS):
        # Force scorer tick periodically so kills materialize
        if i > 0 and i % 3 == 0:
            await brain._scorer.tick()

        msg, success, dur = await run_one_turn(client, task, expected_tool, brain, i)
        if msg.startswith("GATE BLOCKED"):
            blocked += 1
            print(f"[{i:02d}] 🛑 {msg}  ({dur:.1f}s)")
        else:
            icon = "✓" if success else "✗"
            print(f"[{i:02d}] {icon} {msg}  ({dur:.1f}s)")
        results.append((task, expected_tool, msg, success))
        await asyncio.sleep(0.1)

    print()
    print("=" * 70)
    print(f"Summary: {sum(1 for r in results if r[3])} succeeded, "
          f"{sum(1 for r in results if not r[3] and not r[2].startswith('GATE'))} failed, "
          f"{blocked} blocked by Tripwire")
    print()
    print("Strategy health after run:")
    snap = brain.snapshot()
    for key, health in sorted(snap.get("health", {}).items()):
        status = health["status"]
        icon = {"green": "✅", "yellow": "⚠️ ", "red": "❌", "black": "⛔"}.get(status, "?")
        print(f"  {icon} {key:40s} status={status:<6s}  {health.get('kill_reason') or ''}")

    # How much would we have wasted without Tripwire?
    broken_calls_allowed = sum(1 for _, t, m, _ in results
                                if t == "translate" and not m.startswith("GATE"))
    broken_calls_blocked = sum(1 for _, t, m, _ in results
                                if t == "translate" and m.startswith("GATE"))
    print()
    print(f"Broken tool calls that executed:  {broken_calls_allowed}")
    print(f"Broken tool calls blocked:        {broken_calls_blocked}")
    print(f"Wasted tokens prevented:          ~{broken_calls_blocked * 250} tokens")

    await brain.shutdown()
    print(f"\nAudit log: {persist / 'tripwire_audit.jsonl'}")


if __name__ == "__main__":
    asyncio.run(main())
