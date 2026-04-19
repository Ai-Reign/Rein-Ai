# Shadow-Mode Protocol

Rein ships with `REIN_SHADOW=true` as the default. In shadow mode, `gate()` always returns `allowed=True` — but the full decision pipeline runs and every decision is audited. This lets you validate the library against your real traffic **without any risk** before flipping to enforcement.

## Why shadow first

Every governance library has false positives. A regime misclassification or a scorer still warming up can block legitimate actions and cost you real money. Shadow mode lets you:

1. **Measure the false-positive rate** against your actual workload.
2. **Tune thresholds** before they matter.
3. **Build operator trust** — your team sees what Rein *would have* blocked and can validate each decision.
4. **Collect baselines** — `RegimeDetector` needs a few hours of live signals before its baseline percentiles stabilize.

## Recommended protocol

### Phase 1 — Deploy in shadow (minimum 7 days)

```bash
export REIN_ENABLED=true
export REIN_SHADOW=true
```

Run in production under real load. Rein will:

- Classify regimes and log every transition.
- Score every (source, series) action.
- Record every "would-block" decision in the audit log.
- Never actually block anything.

**Success criteria for moving to Phase 2:**

- [ ] Zero unexplained crashes in the audit log.
- [ ] Regime detector has observed at least one non-trivial transition.
- [ ] At least 3 scorer-driven "would-block" events, each of which you've reviewed and agree was correct (true positive).
- [ ] Zero false positives you disagree with, or an understood/fixed cause for any you found.
- [ ] The `/rein/state` endpoint responds consistently and the audit log is growing.

### Phase 2 — Flip to enforcement on a subset

Pick one non-critical action source to enforce first:

```python
brain = Rein(
    cfg=ReinConfig.from_env(),  # global shadow_mode = True
)
# Force one specific source to enforce
brain.force_enforcement(source="scanner")
```

Run for 48 hours. Watch the audit log for any actions that were blocked in real enforcement that shadow-mode had previously allowed. Any discrepancy means your live state diverged from shadow — investigate.

### Phase 3 — Full enforcement

```bash
export REIN_SHADOW=false
```

Continue monitoring for 2 weeks. Watch:

- False-positive rate (blocks that your ops team disagrees with)
- Latency overhead on `gate()` (should be sub-millisecond)
- Audit log growth rate — size your retention accordingly

## Rollback

If anything looks wrong, flip the env var back — no restart required if you re-read config:

```bash
export REIN_SHADOW=true
# or hot-halt the whole thing
curl -X POST http://localhost:PORT/rein/halt -d '{"reason": "investigating false positives"}'
```

## Instrumentation checklist

Before going into Phase 1, make sure you can answer:

- [ ] How do I get to `/rein/state` from my ops console?
- [ ] Where does the audit log live and how is it rotated?
- [ ] Who gets paged if the circuit breaker fires?
- [ ] What's my rollback playbook?

## Red-team before Phase 1

Don't skip this. Run the adversarial simulator against your configured policy *before* Phase 1:

```bash
rein redteam --policy your_policy.txt
```

Ensure the catch rate is 100% on all 5 baseline attacks. If any attack gets through in simulation, tune the policy before going live.
