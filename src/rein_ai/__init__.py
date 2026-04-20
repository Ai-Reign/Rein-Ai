"""Rein — governance layer for autonomous agents.

Core:
    from rein_ai import Rein, ReinConfig
    brain = Rein(cfg=ReinConfig.from_env())
    await brain.start()
    decision = brain.gate(source="agent", series="tool")

Middleware (one-line adoption):
    @brain.governed(source="agent")
    async def my_tool(...): ...

Anthropic adapter (auto-govern Claude tool use):
    from rein_ai.adapters.anthropic import GovernedToolRunner
    runner = GovernedToolRunner(brain=brain, source="claude_agent", impls={...})
    result = await runner.execute(tool_use_block)
"""
from importlib.metadata import PackageNotFoundError, version as _pkg_version

from rein_ai.adversary import RedTeamReport, list_attacks, run_red_team
from rein_ai.brain import Rein
from rein_ai.config import ReinConfig
from rein_ai.middleware import GateBlockedError
from rein_ai.policy_compiler import CompiledPolicy, compile_policy
from rein_ai.types import AllowDecision, Regime

try:
    __version__ = _pkg_version("rein-ai")
except PackageNotFoundError:
    __version__ = "0.0.0+local"

__all__ = [
    "__version__",
    "Rein", "ReinConfig",
    "AllowDecision", "Regime", "GateBlockedError",
    "compile_policy", "CompiledPolicy",
    "run_red_team", "list_attacks", "RedTeamReport",
]
