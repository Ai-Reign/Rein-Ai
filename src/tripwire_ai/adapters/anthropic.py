"""Anthropic SDK adapter — auto-govern Claude tool calls with Tripwire.

Given a `Tripwire` instance and a dict of tool implementations, `GovernedToolRunner`
wraps the tool-execution step: gate() checks, execution, fill recording — all in
one call per tool_use block.

Usage:
    import anthropic
    from tripwire_ai import Tripwire
    from tripwire_ai.adapters.anthropic import GovernedToolRunner

    brain = Tripwire(...)
    await brain.start()

    runner = GovernedToolRunner(
        brain=brain,
        source="my_agent",
        impls={"calculator": calc_fn, "translate": translate_fn, ...},
    )

    client = anthropic.AsyncAnthropic()
    response = await client.messages.create(
        model="claude-haiku-4-5",
        tools=tool_schemas,
        messages=[{"role": "user", "content": "..."}],
        max_tokens=512,
    )

    for block in response.content:
        if block.type == "tool_use":
            outcome = await runner.execute(block)
            # outcome.blocked, outcome.result, outcome.success
"""
from __future__ import annotations

import inspect
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Union

# The tool impl can be sync or async
ToolImpl = Union[Callable[..., Any], Callable[..., Awaitable[Any]]]


@dataclass
class ToolOutcome:
    tool_name: str
    blocked: bool
    block_reason: Optional[str]
    success: bool
    result: Any
    duration_s: float


class GovernedToolRunner:
    """Wraps tool execution with Tripwire gate() + record_fill().

    Parameters
    ----------
    brain : Tripwire
        The governance brain.
    source : str
        The `source` label for all strategies under this runner (e.g. "claude_agent").
    impls : dict[str, callable]
        Map of tool name → function. Functions may be sync or async.
    success_predicate : callable, optional
        Given (result), returns True if the call should be counted as filled.
        Default: False iff result is a string starting with "error:" or a dict
        containing `error=True`; otherwise True.
    """

    def __init__(
        self,
        *,
        brain,
        source: str,
        impls: Dict[str, ToolImpl],
        success_predicate: Optional[Callable[[Any], bool]] = None,
    ):
        self.brain = brain
        self.source = source
        self.impls = dict(impls)
        self.success_predicate = success_predicate or _default_success

    async def execute(self, tool_use_block) -> ToolOutcome:
        """Govern + run a single Anthropic tool_use content block.

        Accepts either an SDK ToolUseBlock or a dict with `.name` and `.input`.
        """
        name = _attr(tool_use_block, "name")
        tool_input = _attr(tool_use_block, "input") or {}
        series = name

        decision = self.brain.gate(source=self.source, series=series)
        if not decision.allowed:
            return ToolOutcome(
                tool_name=name, blocked=True, block_reason=decision.reason,
                success=False, result=None, duration_s=0.0,
            )

        impl = self.impls.get(name)
        ticker = uuid.uuid4().hex[:12]
        t0 = time.time()
        success = False
        result: Any = None

        try:
            if impl is None:
                result = f"error: unknown tool '{name}'"
                success = False
            else:
                if inspect.iscoroutinefunction(impl):
                    result = await impl(**tool_input)
                else:
                    result = impl(**tool_input)
                success = self.success_predicate(result)
        except Exception as e:
            result = f"error: {e}"
            success = False
        finally:
            await self.brain.record_fill(
                source=self.source, series=series, ticker=ticker,
                filled=success, slippage_cents=0.0, attempt_at=t0,
            )

        return ToolOutcome(
            tool_name=name, blocked=False, block_reason=None,
            success=success, result=result, duration_s=time.time() - t0,
        )


def _attr(obj, name):
    """Works for both SDK objects and dicts."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _default_success(result) -> bool:
    if result is None:
        return False
    if isinstance(result, str):
        return not result.lstrip().lower().startswith("error")
    if isinstance(result, dict):
        return not result.get("error")
    return True
