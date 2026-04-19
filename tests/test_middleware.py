"""Tests for the @brain.governed decorator and the Anthropic adapter."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tripwire_ai import Tripwire, TripwireConfig, GateBlockedError
from tripwire_ai.adapters.anthropic import GovernedToolRunner


@pytest.fixture
async def brain(tmp_path: Path):
    cfg = TripwireConfig(
        enabled=True, shadow_mode=False,
        min_samples_for_kill=3, min_samples_for_green=2,
        exec_yellow_fill=0.80, exec_red_fill=0.50, exec_black_fill=0.30,
        exec_min_attempts=3, debounce_seconds=0.0,
    )
    b = Tripwire(cfg=cfg, persist_dir=tmp_path)
    await b.start()
    yield b
    await b.shutdown()


# ---------- @brain.governed decorator ----------

async def test_governed_decorator_allows_healthy_calls(brain):
    calls = []

    @brain.governed(source="agent")
    async def healthy(x: int) -> str:
        calls.append(x)
        return f"ok-{x}"

    for i in range(3):
        assert await healthy(i) == f"ok-{i}"
    assert calls == [0, 1, 2]


async def test_governed_decorator_blocks_after_failures(brain):
    @brain.governed(source="agent")
    async def broken(x: int) -> str:
        return "error: broken"

    # Run until gate starts blocking; must happen within the first 10 attempts
    blocks = 0
    for _ in range(10):
        try:
            await broken(1)
        except GateBlockedError:
            blocks += 1
    assert blocks > 0, "expected gate to block broken tool after failures"


async def test_governed_decorator_returns_none_when_raise_off(brain):
    @brain.governed(source="agent", raise_on_block=False)
    async def broken() -> str:
        return "error: broken"

    # Hammer the broken fn; some return the error string, some return None (blocked)
    results = [await broken() for _ in range(10)]
    assert None in results, "expected at least one blocked call to return None"
    await brain._scorer.tick()

    assert await broken() is None  # blocked silently


async def test_governed_uses_custom_series_name(brain):
    @brain.governed(source="agent", series="explicit_name")
    async def fn() -> str:
        return "ok"

    await fn()
    snap = brain.snapshot()
    assert any("explicit_name" in str(k) for k in snap["health"])


# ---------- GovernedToolRunner (Anthropic adapter) ----------

class _FakeToolUse:
    """Mimics an Anthropic SDK ToolUseBlock."""
    def __init__(self, name, tool_input):
        self.name = name
        self.input = tool_input


async def test_runner_executes_known_tool(brain):
    runner = GovernedToolRunner(
        brain=brain, source="claude",
        impls={"calc": lambda expression: str(eval(expression))},
    )
    outcome = await runner.execute(_FakeToolUse("calc", {"expression": "2+2"}))
    assert not outcome.blocked
    assert outcome.success
    assert outcome.result == "4"


async def test_runner_blocks_after_repeated_failures(brain):
    runner = GovernedToolRunner(
        brain=brain, source="claude",
        impls={"broken": lambda x: "error: always fails"},
    )

    for _ in range(5):
        await runner.execute(_FakeToolUse("broken", {"x": 1}))
    await brain._scorer.tick()

    outcome = await runner.execute(_FakeToolUse("broken", {"x": 1}))
    assert outcome.blocked
    assert outcome.block_reason is not None


async def test_runner_handles_unknown_tool_as_error(brain):
    runner = GovernedToolRunner(brain=brain, source="claude", impls={})
    outcome = await runner.execute(_FakeToolUse("ghost", {}))
    assert not outcome.blocked
    assert not outcome.success
    assert "unknown tool" in str(outcome.result)


async def test_runner_accepts_dict_tool_use(brain):
    runner = GovernedToolRunner(
        brain=brain, source="claude",
        impls={"reverse": lambda text: text[::-1]},
    )
    outcome = await runner.execute({"name": "reverse", "input": {"text": "hello"}})
    assert outcome.success
    assert outcome.result == "olleh"


async def test_runner_handles_async_impl(brain):
    async def async_tool(x: int) -> int:
        await asyncio.sleep(0.001)
        return x * 2

    runner = GovernedToolRunner(brain=brain, source="claude",
                                 impls={"double": async_tool})
    outcome = await runner.execute(_FakeToolUse("double", {"x": 21}))
    assert outcome.success
    assert outcome.result == 42


async def test_runner_exception_recorded_as_failure(brain):
    def crasher(x):
        raise ValueError("boom")

    runner = GovernedToolRunner(brain=brain, source="claude",
                                 impls={"crash": crasher})
    outcome = await runner.execute(_FakeToolUse("crash", {"x": 1}))
    assert not outcome.blocked
    assert not outcome.success
    assert "boom" in str(outcome.result)
