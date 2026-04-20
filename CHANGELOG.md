# Changelog

All notable changes to Rein are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] — 2026-04-28

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
- Contact addresses moved to role-based `rein-ai.com` forwarders: `licensing@`, `security@`, `conduct@`, `john@`.
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
