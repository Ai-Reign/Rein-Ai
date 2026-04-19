"""Tripwire — governance layer for autonomous agents.

Core:
    from tripwire_ai import Tripwire, TripwireConfig
    brain = Tripwire(cfg=TripwireConfig.from_env())
    await brain.start()
    decision = brain.gate(source="agent", series="tool")

Middleware (one-line adoption):
    @brain.governed(source="agent")
    async def my_tool(...): ...

Anthropic adapter (auto-govern Claude tool use):
    from tripwire_ai.adapters.anthropic import GovernedToolRunner
    runner = GovernedToolRunner(brain=brain, source="claude_agent", impls={...})
    result = await runner.execute(tool_use_block)
"""
from importlib.metadata import PackageNotFoundError, version as _pkg_version

from tripwire_ai.adversary import RedTeamReport, list_attacks, run_red_team
from tripwire_ai.brain import Tripwire
from tripwire_ai.config import TripwireConfig
from tripwire_ai.middleware import GateBlockedError
from tripwire_ai.policy_compiler import CompiledPolicy, compile_policy
from tripwire_ai.types import AllowDecision, Regime

try:
    __version__ = _pkg_version("tripwire-ai")
except PackageNotFoundError:
    __version__ = "0.0.0+local"

__all__ = [
    "__version__",
    "Tripwire", "TripwireConfig",
    "AllowDecision", "Regime", "GateBlockedError",
    "compile_policy", "CompiledPolicy",
    "run_red_team", "list_attacks", "RedTeamReport",
]
