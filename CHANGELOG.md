# Changelog

All notable changes to Rein are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## [1.1.0] — 2026-05-13

**Polish release. No new features.**

Week 1 (4/30 → 5/7) produced 122 PyPI installs, 68 unique repo viewers, and zero filed issues, PRs, or feature requests. With no clustered feature signal, v1.1 tightens what shipped instead of inventing demand.

### Changed
- **README:** "Rein vs alternatives" comparison table moved above the quickstart so visitors can compare before reading code.
- **README:** Pro section collapsed from ~50 lines to a one-sentence summary; full Pro details (capabilities, who it's for, pricing, contacts) extracted to `PRO.md`.
- **README:** lede rewritten — value prop and decorator above the fold (`4eea9ed`).

### Added
- `PRO.md` — standalone Pro details document.
- `examples/llm_agent_governor/requirements.txt` — pinned dependency floor (`anthropic>=0.40`), verified end-to-end against `anthropic` 0.101.0 on Python 3.14.
- Python 3.14 added to the CI test matrix (`pyproject.toml` classifier + `.github/workflows/ci.yml`). Full suite now green on 3.10 / 3.11 / 3.12 / 3.13 / 3.14.

### Changed (docs)
- `docs/shadow-protocol.md` rewritten as a 4-step runbook (enable shadow → 24h observation → tune thresholds → flip enforcement). Dropped a stale reference to a `force_enforcement(source=...)` method that does not exist in the codebase. Added a symptom-to-env-var tuning table for step 3.

### Explicitly out of scope
- No new red-team attack classes. The "what should attack #6 be?" question is the community hook — adding one ourselves pre-empts that conversation.
- No new framework adapters. LangChain, Anthropic, FastAPI router exist; LlamaIndex / AutoGen / Temporal wait until someone files an issue.
- No NL policy compiler expansion until rule types get stress-tested.
- No new CLI commands.

---

## [1.0.0] — 2026-04-30

First public release. Project renamed from Tripwire to **Rein-AI** after trademark conflict research; no API-compatibility with the pre-rename internal 0.1.0 versions.

### Added
- **Rein-AI Pro tier announcement** — commercial subscription with extended attack library, trained detection models, managed service, and priority support (private repo, NDA-gated).
- **Dual-license clarification** — AGPL-3.0 for open-source/self-hosted, commercial license for proprietary/SaaS. Added confidentiality and trade-secret survival clauses to `COMMERCIAL-LICENSE.md`.
- **Contributor License Agreement (CLA)** with CLA Assistant GitHub Action that blocks PRs from unsigned contributors.
- **CODEOWNERS** file enforcing owner review on licensing, core runtime, and build paths.
- **Issue template routing** — security, licensing, and conduct concerns directed to role-based addresses rather than public issues.
- `docs/shadow-protocol.md` — recommended rollout procedure before enforcement.
- Increased test coverage: `RegimeDetector` async lifecycle coverage (regime.py 57% → 95%, brain.py 88% → 90%).
- `httpx` added to test extras for starlette `TestClient` compatibility.
- `py.typed` marker exposing type hints to downstream consumers.
- ASCII architecture diagram in README.

### Changed
- **Project name: Tripwire → Rein-AI** (trademark research documented in `~/rein-ai-legal/`).
- Package identity: PyPI `rein-ai`, import `rein_ai`, class `Rein`, config `ReinConfig`, CLI `rein`, env prefix `REIN_`, HTTP prefix `/rein/*`, log prefix `[REIN]`, state dir `rein_state/`.
- Contact addresses moved to role-based `reinai.io` forwarders: `licensing@`, `security@`, `conduct@`, `john@`.
- Repo URLs point at `github.com/Ai-Reign/Rein-Ai`.

### Performance (benchmarked on 2021 MacBook Pro, Python 3.14)
- `gate()` mean: **1.7 μs**, p95: 2.3 μs, p99: 2.7 μs
- Throughput: **500,000+ calls/sec** on a single core

### Production lineage
- Extracted from the Predbot Kalshi trading bot (live since 2026-04-16).
- Prevented 12 runaway trades in first week of live shadow mode.

### Test suite
- **135 tests**, all passing.

---

## [0.1.0] — 2026-04-18

Initial internal release under the "Tripwire" name (superseded by 1.0.0 after rename).

### Added
- Core `Rein` orchestrator with async lifecycle (`start`, `stop`, `gate`, `record_fill`, `record_exit`).
- `RegimeDetector` — classifies live signals into normal / stressed / shock regimes.
- `StrategyScorer` — Bayesian scoring with time decay per (source, series) tuple.
- `CircuitBreaker` — drawdown, error-rate, and stale-state halts.
- `RateLimiter` — token-bucket throttling with burst handling.
- `AnomalyDetector` — deny-storm, enumeration, and runaway-caller detection.
- Tamper-evident JSONL audit log with cryptographic hash chain.
- Natural-language policy compiler (`compile_policy`) — 4 rule types today.
- Adversarial red-team simulator (`run_red_team`) — 5 baseline attack classes.
- Framework adapters for Anthropic Claude and LangChain.
- FastAPI admin router (`/rein/state`, `/halt`, `/resume`, `/audit`, `/whoami`).
- RBAC + mTLS authentication for admin endpoints.
- `rein` CLI with `compile`, `redteam`, `attacks` subcommands.
- Middleware decorator `@brain.governed()` for one-line adoption.
- 130 unit tests.
- STRIDE threat model documentation.
- Dual-license (AGPL-3.0 + commercial).

### Production lineage
- Extracted from a production Kalshi trading bot (live since 2026-04-16).
- Prevented 12 runaway trades in first week of live shadow mode.
