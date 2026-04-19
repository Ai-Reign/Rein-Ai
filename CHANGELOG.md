# Changelog

All notable changes to Tripwire are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] — 2026-04-18

Initial public release.

### Added
- Core `Tripwire` orchestrator with async lifecycle (`start`, `stop`, `gate`, `record_fill`, `record_exit`).
- `RegimeDetector` — classifies live signals into normal / stressed / shock regimes.
- `StrategyScorer` — Bayesian scoring with time decay per (source, series) tuple.
- `CircuitBreaker` — drawdown, error-rate, and stale-state halts.
- `RateLimiter` — token-bucket throttling with burst handling.
- `AnomalyDetector` — deny-storm, enumeration, and runaway-caller detection.
- Tamper-evident JSONL audit log with cryptographic hash chain.
- Natural-language policy compiler (`compile_policy`) — 4 rule types today.
- Adversarial red-team simulator (`run_red_team`) — 5 baseline attack classes.
- Framework adapters for Anthropic Claude and LangChain.
- FastAPI admin router (`/tripwire/state`, `/halt`, `/resume`, `/audit`, `/whoami`).
- RBAC + mTLS authentication for admin endpoints.
- `tripwire` CLI with `compile`, `redteam`, `attacks` subcommands.
- Middleware decorator `@brain.governed()` for one-line adoption.
- 130 unit tests.
- STRIDE threat model documentation.
- Dual-license (AGPL-3.0 + commercial).

### Production lineage
- Extracted from a production Kalshi trading bot (live since 2026-04-16).
- Prevented 12 runaway trades in first week of live shadow mode.
