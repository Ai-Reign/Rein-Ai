"""LangChain adapter — govern LangChain tool calls via Tripwire.

Wraps any `langchain.tools.BaseTool` (or a callable) so every invocation flows
through brain.gate() + record_fill(). Works with LangChain, CrewAI, LlamaIndex
agents, or any framework that calls `.invoke()`/`.ainvoke()` on a tool object.

Usage:
    from langchain_core.tools import tool
    from tripwire_ai.adapters.langchain import govern_tool

    @tool
    def my_tool(x: str) -> str:
        return do_work(x)

    governed = govern_tool(my_tool, brain=brain, source="langchain_agent")
    # Use `governed` wherever you'd use `my_tool` in your agent setup

Or wrap at construction:
    from tripwire_ai.adapters.langchain import GovernedTool
    governed = GovernedTool(brain=brain, source="agent", inner=my_tool)
"""
from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from typing import Any, Callable


def _success(result: Any) -> bool:
    if result is None:
        return False
    if isinstance(result, str):
        return not result.lstrip().lower().startswith("error")
    if isinstance(result, dict):
        return not result.get("error")
    return True


def govern_tool(tool, *, brain, source: str, series: str | None = None,
                success_predicate: Callable[[Any], bool] | None = None):
    """Wrap a LangChain tool (or any object with `.invoke()`/`.ainvoke()`).

    Returns a new tool-like object that routes through Tripwire first.
    If `tool` is a plain callable, we wrap it as a callable.
    """
    name = series or getattr(tool, "name", None) or getattr(tool, "__name__", "tool")
    predicate = success_predicate or _success

    is_langchain_tool = hasattr(tool, "invoke") or hasattr(tool, "ainvoke")

    async def _record(success: bool, t0: float):
        await brain.record_fill(
            source=source, series=name, ticker=uuid.uuid4().hex[:12],
            filled=success, slippage_cents=0.0, attempt_at=t0,
        )

    def _gate_or_raise():
        d = brain.gate(source=source, series=name)
        if not d.allowed:
            raise RuntimeError(f"tripwire_ai blocked {source}/{name}: {d.reason}")
        return d

    if is_langchain_tool:
        # Return a lightweight wrapper object mimicking invoke/ainvoke
        class _Wrapped:
            name = tool.name if hasattr(tool, "name") else name
            description = getattr(tool, "description", "")
            args_schema = getattr(tool, "args_schema", None)

            def invoke(self, input, config=None, **kwargs):
                _gate_or_raise()
                t0 = time.time()
                try:
                    result = tool.invoke(input, config=config, **kwargs)
                    asyncio.run(_record(predicate(result), t0))
                    return result
                except Exception:
                    asyncio.run(_record(False, t0))
                    raise

            async def ainvoke(self, input, config=None, **kwargs):
                _gate_or_raise()
                t0 = time.time()
                try:
                    if hasattr(tool, "ainvoke"):
                        result = await tool.ainvoke(input, config=config, **kwargs)
                    else:
                        result = await asyncio.to_thread(tool.invoke, input,
                                                          config=config, **kwargs)
                    await _record(predicate(result), t0)
                    return result
                except Exception:
                    await _record(False, t0)
                    raise

        return _Wrapped()

    # Plain callable fallback
    if inspect.iscoroutinefunction(tool):
        async def wrapper(*args, **kwargs):
            _gate_or_raise()
            t0 = time.time()
            try:
                result = await tool(*args, **kwargs)
                await _record(predicate(result), t0)
                return result
            except Exception:
                await _record(False, t0)
                raise
        return wrapper
    else:
        def wrapper(*args, **kwargs):
            _gate_or_raise()
            t0 = time.time()
            try:
                result = tool(*args, **kwargs)
                asyncio.run(_record(predicate(result), t0))
                return result
            except Exception:
                asyncio.run(_record(False, t0))
                raise
        return wrapper


# CrewAI convenience — identical mechanics, different name for discoverability.
govern_crewai_tool = govern_tool
