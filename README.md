# Rein

**Rein in your agents — before they run.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](LICENSE)
[![Tests: 135 passing](https://img.shields.io/badge/tests-135%20passing-brightgreen.svg)](#status)
[![PyPI: rein-ai](https://img.shields.io/badge/pypi-rein--ai-orange.svg)](https://pypi.org/project/rein-ai/)

Rein gates every action your agent takes — LLM tool call, trade, email, API request — and halts the system when behavior degrades. Regime-aware Bayesian kill switch, natural-language policies, and a built-in adversarial simulator, all in one drop-in `gate()` call.

Originally extracted from a production Kalshi trading bot where it prevented 12 runaway trades in its first week. Framework-agnostic: works for trading bots, LLM agents, scrapers, RPA, or any system taking actions you don't want spiraling.

> **How this library came to exist:** [The origin story](ORIGIN.md) — a ten-year merchant mariner teaching himself Python, a BTC trading bot that didn't make money, and the governance layer that turned out to be the real product.

---

## The problem

Existing AI safety tooling validates **content** — guardrail libraries check whether an LLM's input or output contains PII, profanity, or prompt-injection strings. That's necessary but insufficient. Once an agent is actually *taking actions* — placing trades, sending emails, calling paid APIs, posting to production — content validation is too late. You need a **runtime governor** that can cut off a misbehaving agent mid-flight based on observed outcomes, not just text.

Rein is that governor.

---

## What Rein is

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
pip install rein-ai
```

```python
import asyncio, time
from rein_ai import Rein, ReinConfig

async def main():
    brain = Rein(cfg=ReinConfig.from_env())
    await brain.start()

    # Gate every action
    decision = brain.gate(source="llm_agent", series="send_email")
    if not decision.allowed:
        print(f"blocked: {decision.reason}")
        return

    # ...execute the action...

    # Report outcome so Rein can learn
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
from rein_ai import compile_policy

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
from rein_ai import run_red_team

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

```
         ┌─────────────────────────────────────────────────────────┐
         │                    Your Agent / App                     │
         │   (LLM tool call, trade, email, scraper, RPA step…)     │
         └───────────────────────────┬─────────────────────────────┘
                                     │  brain.gate(source, series)
                                     ▼
         ┌─────────────────────────────────────────────────────────┐
         │                          Rein                          │
         │                                                         │
         │  ┌─────────────┐  ┌─────────────┐  ┌────────────────┐   │
         │  │  Regime     │  │  Strategy   │  │   Rate         │   │
         │  │  Detector   │→ │  Scorer     │  │   Limiter      │   │
         │  │ normal/     │  │  Bayesian,  │  │   token-bucket │   │
         │  │ stress/     │  │  time-decay │  │   per caller   │   │
         │  │ shock       │  │             │  │                │   │
         │  └─────────────┘  └─────────────┘  └────────────────┘   │
         │         │                 │                 │           │
         │         ▼                 ▼                 ▼           │
         │  ┌───────────────────────────────────────────────────┐  │
         │  │              Decision Engine                      │  │
         │  │   GREEN (allow) · YELLOW (shadow) · RED (block)   │  │
         │  └───────────────────────────┬───────────────────────┘  │
         │                              │                          │
         │  ┌─────────────┐  ┌──────────▼──────────┐  ┌──────────┐ │
         │  │  Circuit    │  │   Anomaly           │  │  Audit   │ │
         │  │  Breaker    │  │   Detector          │  │  Log     │ │
         │  │ drawdown/   │  │ deny-storm,         │  │ JSONL    │ │
         │  │ error-rate/ │  │ enumeration,        │  │ hash-    │ │
         │  │ staleness   │  │ runaway callers     │  │ linked   │ │
         │  └─────────────┘  └─────────────────────┘  └──────────┘ │
         └───────────────────────────┬─────────────────────────────┘
                                     │  AllowDecision(allowed, reason)
                                     ▼
                         Execute action  /  Halt  /  Log
```

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
  from rein_ai.adapters.anthropic import GovernedToolRunner
  runner = GovernedToolRunner(brain=brain, source="claude_agent", impls={...})
  result = await runner.execute(tool_use_block)
  ```
- **LangChain** — governed wrapper for any `BaseTool`
- **FastAPI admin router** — `/rein/state`, `/rein/halt`, `/rein/resume`, `/rein/audit` endpoints (optional extra: `pip install rein-ai[api]`)
- **Framework-agnostic** — `@brain.governed()` decorator works on any `async` function

See `examples/llm_agent_governor/` for non-trading use cases.

---

## CLI

```bash
rein compile --policy policy.txt        # show what a policy expands to
rein redteam --policy policy.txt        # run adversarial simulator
rein attacks                            # list registered attack scenarios
```

---

## Configuration

All thresholds are env-overridable. Default prefix `REIN_`; pass a custom prefix to `ReinConfig.from_env(prefix="MYAPP_REIN_")` for multi-tenant apps.

Key vars:

| Var | Default | Purpose |
|---|---|---|
| `REIN_ENABLED` | `true` | Master on/off |
| `REIN_SHADOW` | `true` | Observe without blocking (safe default) |
| `REIN_EDGE_RED_P` | `0.85` | Edge-axis probability threshold for RED status |
| `REIN_PORTFOLIO_FLOOR_PCT` | `-0.05` | Drawdown % that halts the portfolio |
| `REIN_REGIME_TICK_SECONDS` | `30.0` | How often the regime detector polls |

Full list in `src/rein_ai/config.py`.

---

## Status

- **135 tests**, all passing
- **Running in production** (Predbot trading bot, since 2026-04-16)
- **Low overhead** — `gate()` mean **1.7 μs**, p99 **2.7 μs**, **500k+ calls/sec** on a single core (see `scripts/bench.py`; 2021 MacBook Pro, Python 3.14)
- **Shadow-mode protocol** recommended before enforcement — see `docs/shadow-protocol.md`

---

## Rein vs alternatives

| | Rein | Guardrails AI | NeMo Guardrails | Custom code |
|---|---|---|---|---|
| Content validation (PII, profanity) | — | ✅ | ✅ | DIY |
| Runtime action governance | ✅ | — | — | DIY |
| Regime-aware decisions | ✅ | — | — | DIY |
| Natural-language policies | ✅ | partial | ✅ | — |
| Adversarial red-team simulator | ✅ | — | — | — |
| Tamper-evident audit log | ✅ | — | — | DIY |
| Framework-agnostic | ✅ | ✅ | ✅ | — |

Rein and content-guardrail libraries are complementary: use Guardrails/NeMo to validate what the LLM *says*, use Rein to govern what the agent *does*.

---

## Rein-AI Pro

**The open-source library is the foundation. Rein-AI Pro is the production layer built on top of it.**

Pro is for teams running real money, real users, or real compliance surface area through their agents — where a missed runaway costs more than a subscription.

### What you get

| Capability | OSS | Pro |
|---|---|---|
| Regime-aware Bayesian governor | ✅ | ✅ |
| Natural-language policy compiler | ✅ | ✅ |
| 5 baseline red-team attacks | ✅ | ✅ |
| Tamper-evident audit log | ✅ | ✅ |
| **Extended attack library** (20+ adversarial scenarios) | — | ✅ |
| **Production-calibrated detector presets** (LLM-agent, trading, RPA, scraping) | — | ✅ |
| **Trained anomaly detection models** (no hand-tuning) | — | ✅ |
| **Managed Rein Cloud** (hosted, multi-tenant, SOC 2 roadmap) | — | ✅ |
| **Prebuilt integrations** (OpenAI, Anthropic, LangChain, AutoGen, Temporal, n8n) | — | ✅ |
| **Policy library** — battle-tested policy packs per vertical | — | ✅ |
| **Private incident support** with same-day response SLAs | — | ✅ |
| **Compliance bundle** — SOC 2 artifacts, audit-ready exports, DPA | — | ✅ |
| **Commercial license** (AGPL waiver for closed-source products) | — | ✅ |

### Who it's for

- **Trading and fintech teams** shipping autonomous strategies where drawdown limits, regime awareness, and audit trails are non-negotiable.
- **LLM agent platforms** putting Claude, GPT-4, or open-weights models in front of real users with paid API spend, tool calls, or customer data.
- **Enterprise automation teams** where RPA, scraping, or workflow bots touch production systems and a runaway script means a Monday-morning postmortem.
- **Regulated industries** (healthcare, financial services, legal tech) needing tamper-evident decision logs and compliance-ready evidence.

### Why the split exists

Rein is AGPL-3.0 because strong copyleft is the right default for a governance library — it forces downstream systems that depend on it to stay inspectable. Pro exists because some capabilities are only valuable *because* they're not public: an attack library everyone can read is an attack library nobody can use, and detection thresholds calibrated from real deployments lose their edge the moment they're published.

**The OSS version is production-ready on its own.** Pro is for teams that want the time-savings, the calibrated defaults, and the commercial license — not for teams that can't ship without it.

### Pricing

- **Startup** (&lt;50 employees): from **$1,500/month** or **$25,000/year** (perpetual commercial license variant available).
- **Mid-market** (50–500 employees): **$50K–$100K/year** with priority support.
- **Enterprise** (500+): custom, typically **$100K–$250K/year** with dedicated engineer hours and custom attack-library additions.
- **Perpetual commercial-only licenses** (no Pro features, just the AGPL waiver): **$10K one-time** for startups.

### Get Pro

- **Book a demo / request access:** [licensing@reinai.io](mailto:licensing@reinai.io)
- **Commercial license inquiries only:** [licensing@reinai.io](mailto:licensing@reinai.io)
- **Security issues in the OSS:** [security@reinai.io](mailto:security@reinai.io)

Pro access and extended attack-library materials are distributed through a private repository under signed NDA. The OSS repo is and always will be public.

---

## License

**Dual-licensed:**

- **AGPL-3.0** (default — see [`LICENSE`](LICENSE)). Free for OSS, research, and self-hosted use. If you run Rein as part of a network service, your service must also be released under AGPL-3.0.
- **Commercial License** (see [`COMMERCIAL-LICENSE.md`](COMMERCIAL-LICENSE.md)) for proprietary / SaaS use without copyleft obligations. Contact **licensing@reinai.io** for a quote.

Contributors: see [`CLA.md`](CLA.md).

---

Created and maintained by **John N.W. Hampton Jr** (<john@reinai.io>).
Copyright © 2026 John N.W. Hampton Jr.
