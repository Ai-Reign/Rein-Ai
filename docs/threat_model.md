# Tripwire — Threat Model

**Version:** 0.1  |  **Last reviewed:** 2026-04-17  |  **Next review:** 2026-07-17

This document is the authoritative threat model for Tripwire. It follows the
STRIDE framework (Spoofing, Tampering, Repudiation, Information Disclosure,
Denial of Service, Elevation of Privilege) and maps every identified risk to a
specific mitigation in code or operational procedure.

Designed for enterprise security review, pentest scoping, and SOC 2 / ISO 27001
audit evidence.

---

## 1. System Description

Tripwire is a **governance layer for autonomous agents**. It wraps every
action an agent takes (trading, LLM tool call, API call) in a `gate()` check
and records the outcome. Four in-process subsystems cooperate:

| Subsystem | Purpose | Data persisted |
|---|---|---|
| `brain.gate()` | Hot-path allow/deny decision | none directly |
| `StrategyScorer` | Bayesian posterior tracking of strategy performance | `tripwire_state.json` |
| `RegimeDetector` | Classifies environmental state | `tripwire_baselines.json` |
| `CircuitBreaker` | Portfolio-level halt condition | in-memory + state |
| `TokenBucketLimiter` | Per-caller resource quota | in-memory |
| `AnomalyDetector` | Rolling-window activity analysis | in-memory |
| `secure_audit` | Hash-chained tamper-evident log | `tripwire_audit.jsonl` |
| FastAPI admin router | Operator HTTP interface | none |

**In scope:** the `tripwire_ai` Python library, its admin API, its on-disk
persistence, and the documented deployment pattern (reverse proxy + mTLS).

**Out of scope:** the host OS, the Python runtime itself, the underlying
agent framework (Claude SDK, LangChain, etc.), the reverse proxy's
implementation correctness.

---

## 2. Trust Boundaries

```
┌─────────────────────────────────────────────────────────────────┐
│  UNTRUSTED ZONE                                                 │
│  (agent calls, operator HTTP, adversary traffic)                │
└──────────────┬──────────────────────────────────────────────────┘
               │  [Boundary A — network ingress]
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  PROXY ZONE                                                     │
│  Reverse proxy terminates TLS, verifies client cert, forwards   │
│  X-Client-Cert-Subject. Bearer tokens pass through verbatim.    │
└──────────────┬──────────────────────────────────────────────────┘
               │  [Boundary B — app ingress]
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  APP ZONE (trusted)                                             │
│  FastAPI router → RBAC check → Tripwire methods                │
│  gate() / record_fill() / record_exit() → StrategyScorer        │
│  → append_signed() → tripwire_audit.jsonl                           │
└──────────────┬──────────────────────────────────────────────────┘
               │  [Boundary C — disk I/O]
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  FS ZONE                                                        │
│  tripwire_state.json (latest snapshot, atomic write)                │
│  tripwire_audit.jsonl (append-only, HMAC-signed chain)              │
│  tripwire_baselines.json (refreshed every 6h)                       │
└─────────────────────────────────────────────────────────────────┘
```

**Trust in, not out:**
- Data flowing **into** the APP zone is validated at Boundary A (TLS + cert verify)
  and Boundary B (RBAC role check).
- Data flowing **out** of the APP zone to FS is signed at Boundary C
  (`secure_audit.append_signed`).
- The APP zone never trusts the FS zone's contents on read — `verify_chain()`
  runs on demand and `load_state()` rejects corrupt or malformed JSON.

---

## 3. Assets & Sensitivity

| Asset | Sensitivity | Why |
|---|---|---|
| `META_AUDIT_KEY` (HMAC secret) | **Critical** | Allows forging audit entries |
| `META_AUTH_TOKENS` (bearer tokens) | **Critical** | Allows operator actions |
| mTLS CA private key (proxy side) | **Critical** | Allows minting client certs |
| `tripwire_audit.jsonl` | **High** | Compliance evidence; chain integrity must survive |
| `tripwire_state.json` | **Medium** | Strategy health; can be regenerated from replay |
| Strategy metadata (source/series names) | **Low** | Operational, not secret |
| Regime classifications | **Low** | Derived from public market data |

---

## 4. STRIDE Analysis

### 4.1 Spoofing

| # | Threat | Mitigation | Residual |
|---|---|---|---|
| S1 | Attacker calls admin API impersonating operator | mTLS client cert verify at proxy + bearer token RBAC in `auth.py`. Missing creds → 401. | If mTLS CA is compromised, attacker can mint valid certs. Mitigated by CRL + offline CA key. |
| S2 | Adversary replays captured bearer token | Tokens are long (≥32 chars random), scoped to role. TLS in transit. | No per-token expiry yet (**known gap — Phase 3**). Recommend rotating tokens quarterly. |
| S3 | Forged `X-Client-Cert-Subject` header bypasses mTLS | Proxy overwrites the header on every request — client-supplied values discarded. Documented in `mtls_deployment.md`. | Relies on correct proxy config. Misconfigured proxy = broken auth. |
| S4 | Upstream agent sends fake `source`/`series` strings | Accepted as opaque tags — they don't grant privileges, only segment scorecards. | Strategy-scorer can be polluted with noise. Tier-2 mitigation: per-caller namespace prefix. |

### 4.2 Tampering

| # | Threat | Mitigation | Residual |
|---|---|---|---|
| T1 | Attacker modifies historical audit entry | Hash chain (`secure_audit`): mutation changes `hash`, verification fails on next pass. HMAC adds signature-level tamper detection. | Attacker with concurrent write access AND the HMAC key can forge. Mitigated by key storage (env → secrets manager in prod). |
| T2 | Attacker deletes an audit entry | Hash chain: deletion breaks `prev_hash` link of next entry. `verify_chain()` detects first bad line. | Detection is after-the-fact. Mitigate with WORM storage or S3 object-lock for high-security deployments. |
| T3 | Attacker reorders audit entries | Hash chain: reorder = hash mismatch. Detected. | Same as T2. |
| T4 | Attacker modifies `tripwire_state.json` in flight | Atomic write via `os.replace()`; any torn write is rejected on load (invalid JSON). | If attacker has write access to the dir, they can replace with a valid-but-adversarial state. Mitigate with filesystem ACL + dir-level monitoring. |
| T5 | Attacker plants malicious baselines file | `RegimeDetector._load_baselines` accepts any valid JSON matching shape. | Could inject adversarial regime classifications. Mitigate with signing on baseline refresh (**Phase 3 item**). |

### 4.3 Repudiation

| # | Threat | Mitigation | Residual |
|---|---|---|---|
| R1 | Operator claims "I didn't send that halt command" | Every mutation endpoint records `ctx.subject` (token-id hash or cert-subject) in response and in the audit event. | Audit integrity depends on T1 mitigations. |
| R2 | Strategy claims it didn't produce losses → can't attribute | `record_exit()` is keyed on `(source, series, ticker)` with timestamp; the hash chain makes retroactive denial detectable. | Requires keeping audit log retention ≥ compliance window (typically 1 year). |

### 4.4 Information Disclosure

| # | Threat | Mitigation | Residual |
|---|---|---|---|
| I1 | Raw bearer token written to logs | `auth.py` logs only SHA-256(token)[:12] handle. `/tripwire/whoami` asserts raw token never appears in response (tested). | Careless operator logs leak via print/logger — operational hygiene. |
| I2 | Audit log read by unauthorized party | `/tripwire/audit` requires `reader` role. Direct FS read requires host access. | If host is compromised, logs are readable. Mitigate with disk encryption at rest. |
| I3 | Strategy performance leaks via metrics endpoint | `/tripwire/metrics` requires `reader` role. | Metrics aggregate — don't reveal trade-level detail. |
| I4 | Crash traceback leaks internal paths / env | FastAPI default behavior in dev mode; production should run with `debug=False`. | Responsibility of deploying app. Document in deployment guide. |

### 4.5 Denial of Service

| # | Threat | Mitigation | Residual |
|---|---|---|---|
| D1 | Runaway agent floods `gate()` | `TokenBucketLimiter` per-key + global. Configurable. | Default config has no rate limit → caller must opt in. Document strongly. |
| D2 | `tripwire_audit.jsonl` grows unbounded | Rotation is the caller's responsibility (logrotate / daily archiver). Docs explicitly call this out. | Unbounded growth eventually exhausts disk. Operational. |
| D3 | Many distinct `(source, series)` pairs exhaust memory | `_first_seen`, `_events`, `_buckets` dicts grow with cardinality. | In-process only. Worst case: OOM. Mitigate with `source` allowlist in prod (**Phase 3**). |
| D4 | Slowloris on admin API | FastAPI + uvicorn has timeouts. Reverse proxy should set aggressive read timeouts. | Config responsibility of proxy layer. |
| D5 | Malformed audit entry stops `verify_chain()` early | Design: `verify_chain` returns `first_bad_line` instead of crashing. | Attacker can append garbage to end of file — detection is on purpose. |

### 4.6 Elevation of Privilege

| # | Threat | Mitigation | Residual |
|---|---|---|---|
| E1 | `reader` role calls operator endpoints | Explicit `Depends(require_role("operator"))` on each mutation. 403 on insufficient role. Tested. | Requires correct annotation on every new endpoint. Code review must enforce. |
| E2 | Token with `["admin"]` grants silent universal access | `AuthContext.has_role` explicitly treats `admin` as implying all roles. Documented. | Intentional — simplifies ops. Admin token distribution must be tightly controlled. |
| E3 | Attacker triggers `manual_revive` to un-kill a bad strategy | Requires operator role. Every revive logged with operator identity. | Detection-based; audit log is the accountability trail. |

---

## 5. Top Risks (Ranked)

Scored Likelihood (L) × Impact (I), each 1-5.

| Rank | Risk | L | I | Score | Owner |
|---|---|---|---|---|---|
| 1 | Reverse-proxy misconfig lets forged `X-Client-Cert-Subject` through | 2 | 5 | 10 | Deployer |
| 2 | `META_AUDIT_KEY` stored as plaintext env var leaks via shell history / ps | 3 | 4 | 12 | Deployer |
| 3 | Operator runs in "open mode" (no auth) thinking they're secure | 3 | 4 | 12 | Deployer |
| 4 | Unbounded audit log growth → disk fills → gate() slows | 2 | 3 | 6 | Deployer |
| 5 | `source`/`series` injection attacks StrategyScorer with adversarial data | 2 | 3 | 6 | App |
| 6 | Concurrent writers to the same audit log cause partial-line corruption | 2 | 3 | 6 | App |

### Risk #1 response
- Action: ship ready-to-paste nginx/Envoy configs, lint them, include a test harness in `mtls_deployment.md`.
- **Status:** nginx config shipped; Envoy/Traefik examples are Phase 3.

### Risk #2 response
- Action: integrate with secrets manager (HashiCorp Vault, AWS Secrets Manager) in Phase 3.
- Interim: document that `META_AUDIT_KEY` must be loaded via systemd `EnvironmentFile=` with `0600` perms, never via shell.
- **Status:** documented in README. Integration Phase 3.

### Risk #3 response
- Action: on startup, if no auth env vars are set, log a one-line WARNING: `[META] running in OPEN MODE — no authentication`. Log once per process.
- **Status:** not implemented (**Phase 3 ticket**).

### Risk #4 response
- Action: document log-rotation policy in `README.md`. Recommend logrotate weekly, keep 1 year for compliance.
- **Status:** docs shipped. In-library auto-rotation is Phase 4.

### Risk #5 response
- Action: document that `source` and `series` are opaque tags. Recommend prefixing with caller identity when serving multi-tenant (`source="tenant123:claude_agent"`).
- **Status:** documented.

### Risk #6 response
- Action: audit append is a single `write()` call under ~4KB, which POSIX guarantees atomic. For multi-process writers, recommend advisory lock via `fcntl.flock`.
- **Status:** in-process concurrency tested (`test_concurrency.py`). Multi-process is doc-only.

---

## 6. Known Gaps (Phase 3 Backlog)

| Gap | Planned resolution |
|---|---|
| Token expiry / rotation | Add `expires_at` to token config, reject expired on auth |
| Secrets-manager integration | Vault / AWS Secrets Manager plugin |
| Signed baselines file | HMAC signature on `tripwire_baselines.json` |
| Open-mode startup warning | Log loud warning + add `/tripwire/whoami` diagnostic |
| `source` allowlist | Config-driven `allowed_sources` in `TripwireConfig` |
| Envoy / Traefik mTLS configs | Ship ready-to-paste configs in `mtls_deployment.md` |
| External pentest | Q3 2026 — scope: admin API, audit chain, rate limiter |

---

## 7. Assumptions

This threat model holds under these assumptions. If any becomes false, re-review.

1. **The host OS is not compromised.** Tripwire is not designed to defend against a root-level attacker on its own host.
2. **The reverse proxy is correctly configured.** Tripwire's trust boundary begins at the proxy's ingress.
3. **The Python runtime is not compromised.** Supply-chain attacks against `pip install anthropic` / `fastapi` / etc. are out of scope — follow the CISA software supply-chain guidelines.
4. **Logs are rotated and retained by the operator.** Tripwire does not rotate its own logs.
5. **Time is roughly correct.** Tested against clock jumps up to 24h. Larger deliberate time-skew attacks are out of scope.

---

## 8. Compliance Mapping

| Control | Framework | Implementation |
|---|---|---|
| CC6.1 — Logical access | SOC 2 | `auth.py` RBAC, mTLS at proxy |
| CC6.2 — Unique identifiers | SOC 2 | Token hash + cert subject per caller |
| CC6.3 — Role separation | SOC 2 | `reader` / `operator` / `admin` |
| CC6.6 — Resource quotas | SOC 2 | `TokenBucketLimiter` |
| CC7.2 — System monitoring | SOC 2 | `AnomalyDetector` + signed audit chain |
| CC7.3 — Event evaluation | SOC 2 | Alert callback + cooldown |
| A.9.2 — User access mgmt | ISO 27001 | Token rotation (operational) |
| A.12.4 — Logging | ISO 27001 | Signed audit chain, retention by operator |
| A.13.1 — Network security | ISO 27001 | mTLS documented |
| §164.312(b) — Audit controls | HIPAA | Tamper-evident chain |
| §164.312(c)(1) — Integrity | HIPAA | HMAC + hash chain |

---

## 9. Pentest Scoping

Recommended pentest scope (1-2 week engagement, ~$8-15K):

1. **Auth bypass attempts** — forged headers, token replay, role elevation
2. **Audit chain tampering** — can you edit past entries undetected?
3. **Race conditions** — concurrent writers, mid-replace state corruption
4. **Resource exhaustion** — gate() flooding, high-cardinality source/series
5. **Config injection** — malformed `META_AUTH_TOKENS` JSON, path traversal
6. **Dependency audit** — SCA scan of `anthropic`, `fastapi`, `pydantic`

Deliverables: report mapped to STRIDE categories above, CVSS scores, fixes.

---

## 10. Revision History

| Date | Author | Change |
|---|---|---|
| 2026-04-17 | J. Hampton | Initial document — matches v0.1 of the library |
