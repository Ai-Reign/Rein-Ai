# Tripwire

**The runtime kill-switch for autonomous agents.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](LICENSE)
[![Tests: 130 passing](https://img.shields.io/badge/tests-130%20passing-brightgreen.svg)](#status)
[![PyPI: tripwire-ai](https://img.shields.io/badge/pypi-tripwire--ai-orange.svg)](https://pypi.org/project/tripwire-ai/)

Tripwire gates every action your agent takes — LLM tool call, trade, email, API request — and halts the system when behavior degrades. Regime-aware Bayesian kill switch, natural-language policies, and a built-in adversarial simulator, all in one drop-in `gate()` call.

Originally extracted from a production Kalshi trading bot where it prevented 12 runaway trades in its first week. Framework-agnostic: works for trading bots, LLM agents, scrapers, RPA, or any system taking actions you don't want spiraling.

---

## The problem

Existing AI safety tooling validates **content** — guardrail libraries check whether an LLM's input or output contains PII, profanity, or prompt-injection strings. That's necessary but insufficient. Once an agent is actually *taking actions* — placing trades, sending emails, calling paid APIs, posting to production — content validation is too late. You need a **runtime governor** that can cut off a misbehaving agent mid-flight based on observed outcomes, not just text.

Tripwire is that governor.

---

## What Tripwire is

A Python library that sits inline with any autonomous system and gates every action. It:

1. **Classifies the current regime** (normal / stressed / shock) from live signal inputs.
2. **Scores each action source × action type** with Bayesian decay — stale performance gets discounted, recent performance compounds.
3. **Halts the system** when drawdown, error rate, stale-state, rate-limit storms, or anomaly detection trip a threshold.
4. **Writes a tamper-evident audit log** (JSONL + cryptographic chain) of every decision for compliance and postmortem.

Three things no other governance library has:

- **Natural-language policy compiler.** Write rules in English, get an enforceable config.
- **Built-in adversarial red-team simulator.** Attack your own policy *before* you ship.
- **Regime-sliced scoring.** A strategy that works in calm markets but dies in shocks is treated as two different strategies.

---

## 60-second quickstart

```bash
pip install tripwire-ai
```

```python
import asyncio, time
from tripwire_ai import Tripwire, TripwireConfig

async def main():
    brain = Tripwire(cfg=TripwireConfig.from_env())
    await brain.start()

    # Gate every action
    decision = brain.gate(source="llm_agent", series="send_email")
    if not decision.allowed:
        print(f"blocked: {decision.reason}")
        return

    # ...execute the action...

    # Report outcome so Tripwire can learn
    await brain.record_fill(
        source="llm_agent", series="send_email",
        ticker="msg-123", filled=True,
        slippage_cents=0.0, attempt_at=time.time(),
    )
    await brain.shutdown()

asyncio.run(main())
```

Or use the decorator for one-line adoption:

```python
@brain.governed(source="llm_agent")
async def send_email(to, body): ...
```

---

## Two things no one else has

### 1. Natural-language policy compiler

Stop writing YAML. Describe your policy in English:

```python
from tripwire_ai import compile_policy

policy = compile_policy([
    "Cap each caller at 8 requests per second with bursts of 16",
    "Halt the portfolio when losses exceed 5 percent",
    "Alert when deny rate exceeds 70 percent over a 60 second window",
    "Detect runaway callers at 5x baseline",
])
brain = policy.build_brain(persist_dir="./state")
```

Each line is parsed into a structured enforcement rule. Review the compiled output before deploy:

```python
for rule in policy.rules:
    print(rule.rule_type, rule.params)
```

### 2. Adversarial red-team simulator

Before you trust your policy, attack it:

```python
from tripwire_ai import run_red_team

report = await run_red_team(brain)
print(report.render())
# Red team report — catch rate: 100%
#   [BLOCKED] runaway_loop       at iter 16
#   [BLOCKED] deny_storm         at iter 9
#   [BLOCKED] enumeration        at iter 17
#   [BLOCKED] portfolio_drain    at iter 0
#   [BLOCKED] cost_bomb          at iter 0
```

Five registered attack classes out of the box (runaway, deny-storm, enumeration, portfolio-drain, cost-bomb). Ship policies you've already verified catch the obvious exploits.

Full runnable demo: `examples/policy_and_redteam/demo.py`.

---

## How it works

| Subsystem | Job |
|---|---|
| `RegimeDetector` | Classifies state of the world (normal / stressed / shock) from live signals |
| `StrategyScorer` | Bayesian scoring per (source, series) with time-decay; learns what works *now* |
| `CircuitBreaker` | Halts the system on drawdown, error rate, or staleness thresholds |
| `RateLimiter` | Per-caller token-bucket throttling with burst handling |
| `AnomalyDetector` | Deny-storm, enumeration, and runaway-caller detection |
| `AuditLog` | Tamper-evident JSONL chain — every decision timestamped and hash-linked |

---

## Integrations

- **Anthropic Claude** — drop-in `GovernedToolRunner` auto-governs all tool use
  ```python
  from tripwire_ai.adapters.anthropic import GovernedToolRunner
  runner = GovernedToolRunner(brain=brain, source="claude_agent", impls={...})
  result = await runner.execute(tool_use_block)
  ```
- **LangChain** — governed wrapper for any `BaseTool`
- **FastAPI admin router** — `/tripwire/state`, `/tripwire/halt`, `/tripwire/resume`, `/tripwire/audit` endpoints (optional extra: `pip install tripwire-ai[api]`)
- **Framework-agnostic** — `@brain.governed()` decorator works on any `async` function

See `examples/llm_agent_governor/` for non-trading use cases.

---

## CLI

```bash
tripwire compile --policy policy.txt        # show what a policy expands to
tripwire redteam --policy policy.txt        # run adversarial simulator
tripwire attacks                            # list registered attack scenarios
```

---

## Configuration

All thresholds are env-overridable. Default prefix `TRIPWIRE_`; pass a custom prefix to `TripwireConfig.from_env(prefix="MYAPP_TRIPWIRE_")` for multi-tenant apps.

Key vars:

| Var | Default | Purpose |
|---|---|---|
| `TRIPWIRE_ENABLED` | `true` | Master on/off |
| `TRIPWIRE_SHADOW` | `true` | Observe without blocking (safe default) |
| `TRIPWIRE_EDGE_RED_P` | `0.85` | Edge-axis probability threshold for RED status |
| `TRIPWIRE_PORTFOLIO_FLOOR_PCT` | `-0.05` | Drawdown % that halts the portfolio |
| `TRIPWIRE_REGIME_TICK_SECONDS` | `30.0` | How often the regime detector polls |

Full list in `src/tripwire_ai/config.py`.

---

## Status

- **130 tests**, all passing
- **Running in production** (Predbot trading bot, since 2026-04-16)
- **Low overhead** — `gate()` mean **1.5 μs**, p99 **2.6 μs**, **550k+ calls/sec** on a single core (see `scripts/bench.py`)
- **Shadow-mode protocol** recommended before enforcement — see `docs/shadow-protocol.md`

---

## Tripwire vs alternatives

| | Tripwire | Guardrails AI | NeMo Guardrails | Custom code |
|---|---|---|---|---|
| Content validation (PII, profanity) | — | ✅ | ✅ | DIY |
| Runtime action governance | ✅ | — | — | DIY |
| Regime-aware decisions | ✅ | — | — | DIY |
| Natural-language policies | ✅ | partial | ✅ | — |
| Adversarial red-team simulator | ✅ | — | — | — |
| Tamper-evident audit log | ✅ | — | — | DIY |
| Framework-agnostic | ✅ | ✅ | ✅ | — |

Tripwire and content-guardrail libraries are complementary: use Guardrails/NeMo to validate what the LLM *says*, use Tripwire to govern what the agent *does*.

---

## License

**Dual-licensed:**

- **AGPL-3.0** (default — see [`LICENSE`](LICENSE)). Free for OSS, research, and self-hosted use. If you run Tripwire as part of a network service, your service must also be released under AGPL-3.0.
- **Commercial License** (see [`COMMERCIAL-LICENSE.md`](COMMERCIAL-LICENSE.md)) for proprietary / SaaS use without copyleft obligations. Contact **firekicks@gmail.com** for a quote.

Contributors: see [`CLA.md`](CLA.md).

---

Created and maintained by **John N.W. Hampton Jr** (<firekicks@gmail.com>).
Copyright © 2026 John N.W. Hampton Jr.
