"""Decorators and wrappers that make Tripwire adoption one line.

Instead of:
    decision = brain.gate(source="agent", series=tool_name)
    if not decision.allowed: return
    try:
        result = await my_tool(...)
        await brain.record_fill(source="agent", series=tool_name, ticker=id, filled=True, ...)
    except Exception:
        await brain.record_fill(..., filled=False, ...)

You write:
    @brain.governed(source="agent")
    async def my_tool(...): ...

And get the same behavior. The function name becomes the default `series`.
"""
from __future__ import annotations

import functools
import time
import uuid
from typing import Any, Awaitable, Callable, Optional


class GateBlockedError(RuntimeError):
    """Raised when brain.gate() denies a governed call."""

    def __init__(self, source: str, series: str, reason: str):
        super().__init__(f"gate blocked {source}/{series}: {reason}")
        self.source = source
        self.series = series
        self.reason = reason


def make_governed(brain):
    """Return a decorator bound to `brain`. Usually called as `brain.governed(...)`.

    Usage:
        @brain.governed(source="my_agent")          # series = func.__name__
        async def run_tool(args): ...

        @brain.governed(source="my_agent", series="custom_series")
        async def run_tool(args): ...

        @brain.governed(source="my_agent", raise_on_block=False)  # return None instead
        async def run_tool(args): ...
    """

    def decorator(
        _func: Optional[Callable[..., Awaitable[Any]]] = None,
        *,
        source: str = "default",
        series: Optional[str] = None,
        raise_on_block: bool = True,
    ):
        def wrap(func: Callable[..., Awaitable[Any]]):
            _series = series or func.__name__

            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                decision = brain.gate(source=source, series=_series)
                if not decision.allowed:
                    if raise_on_block:
                        raise GateBlockedError(source, _series, decision.reason)
                    return None

                ticker = uuid.uuid4().hex[:12]
                t0 = time.time()
                filled = False
                try:
                    result = await func(*args, **kwargs)
                    # Heuristic: treat dict/str result without "error" prefix as success
                    if isinstance(result, str):
                        filled = not result.lstrip().lower().startswith("error")
                    elif isinstance(result, dict):
                        filled = not result.get("error")
                    else:
                        filled = result is not None
                    return result
                except Exception:
                    filled = False
                    raise
                finally:
                    await brain.record_fill(
                        source=source, series=_series, ticker=ticker,
                        filled=filled, slippage_cents=0.0, attempt_at=t0,
                    )

            return wrapper

        # Support both @brain.governed and @brain.governed(...)
        if _func is not None and callable(_func):
            return wrap(_func)
        return wrap

    return decorator
