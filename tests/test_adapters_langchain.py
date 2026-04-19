"""Tests for the LangChain/CrewAI adapter.

Doesn't require LangChain installed — uses a minimal stub that mimics the
`invoke`/`ainvoke` surface. That's the point: we work with anything that has
those methods.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rein_ai import Rein, ReinConfig
from rein_ai.adapters.langchain import govern_tool


@pytest.fixture
async def brain(tmp_path: Path):
    cfg = ReinConfig(
        enabled=True, shadow_mode=False,
        min_samples_for_kill=3, exec_min_attempts=3,
        exec_red_fill=0.50, debounce_seconds=0.0,
    )
    b = Rein(cfg=cfg, persist_dir=tmp_path)
    await b.start()
    yield b
    await b.shutdown()


class _FakeLangChainTool:
    """Mimics the LangChain BaseTool surface."""
    def __init__(self, name, fn, afn=None):
        self.name = name
        self.description = "fake"
        self._fn = fn
        self._afn = afn

    def invoke(self, input, config=None, **kwargs):
        return self._fn(input)

    async def ainvoke(self, input, config=None, **kwargs):
        if self._afn:
            return await self._afn(input)
        return self._fn(input)


async def test_plain_async_callable_governed(brain):
    async def ok(x): return f"ok-{x}"
    governed = govern_tool(ok, brain=brain, source="lc")
    assert (await governed("a")) == "ok-a"


async def test_plain_async_callable_blocked_after_failures(brain):
    async def broken(x): return "error: nope"
    governed = govern_tool(broken, brain=brain, source="lc", series="b")

    blocks = 0
    for _ in range(10):
        try:
            await governed(1)
        except RuntimeError as e:
            if "blocked" in str(e):
                blocks += 1
    assert blocks > 0


async def test_langchain_tool_wrapper_ainvoke(brain):
    async def ok(x): return f"ok-{x}"
    inner = _FakeLangChainTool("lc_tool", fn=lambda x: "sync", afn=ok)
    wrapped = govern_tool(inner, brain=brain, source="lc")
    assert (await wrapped.ainvoke("a")) == "ok-a"
    assert wrapped.name == "lc_tool"
    assert wrapped.description == "fake"


async def test_langchain_tool_wrapper_blocks_after_errors(brain):
    def broken(x): return "error: still broken"
    inner = _FakeLangChainTool("broken_tool", fn=broken)
    wrapped = govern_tool(inner, brain=brain, source="lc")

    blocks = 0
    for _ in range(12):
        try:
            await wrapped.ainvoke({"x": 1})
        except RuntimeError as e:
            if "blocked" in str(e):
                blocks += 1
    assert blocks > 0


async def test_exception_from_tool_recorded_as_failure(brain):
    async def crash(x): raise ValueError("boom")
    governed = govern_tool(crash, brain=brain, source="lc", series="crash")
    with pytest.raises(ValueError):
        await governed(1)

    snap = brain.snapshot()
    crash_key = next(k for k in snap["health"] if "crash" in str(k))
    assert snap["health"][crash_key]["scorecard"]["execution"]["samples"] == 1
