# Shadow-Mode Protocol

Rein ships with `REIN_SHADOW=true` as the default. In shadow mode, `gate()` always returns `allowed=True` — but the full decision pipeline runs and every "would-block" decision is audited. This lets you validate Rein against your real traffic with zero risk before flipping to enforcement.

This is the recommended runbook for taking a brand-new Rein deployment from install to enforcement. Follow it in order. Skipping steps is the most common silent failure mode for new adopters.

---

## 1. Enable shadow mode

Set the two env vars and deploy under real load:

```bash
export REIN_ENABLED=true
export REIN_SHADOW=true
```

In your app:

```python
from rein_ai import Rein, ReinConfig
brain = Rein(cfg=ReinConfig.from_env())
```

What happens once it's running:

- Regime classification and scoring run on every action.
- Every would-block decision is recorded in the audit log.
- `gate()` always returns `allowed=True` — nothing is actually blocked.
- The `/rein/state` endpoint exposes live state for inspection.

Before you go further, also run the adversarial simulator once against your policy:

```bash
rein redteam --policy your_policy.txt
```

If the catch rate is not 100% on the baseline attacks, fix the policy before observing.

---

## 2. 24-hour observation window

Let it run for at least 24 hours under real production traffic. Longer is better — 7 days if you can afford it — but 24 hours is the floor. You need enough samples for the cold-start guards to release (defaults: 10 samples to kill, 20 to green; see `ReinConfig.min_samples_for_*`) and for `RegimeDetector` to see at least one non-trivial transition.

While it runs, watch:

- The audit log is growing and is parseable.
- `/rein/state` responds consistently.
- No unexplained crashes or repeated exceptions.
- If any regime transitions occurred, they were logged with cause and timestamp.

If the audit log isn't growing or `/rein/state` is unreachable after 24 hours, your wiring is wrong — fix it before going to step 3.

---

## 3. Tune thresholds from observed data

Pull every would-block decision from the audit log and classify each one as:

- **True positive** — you agree Rein should have blocked it.
- **False positive** — Rein would have blocked something fine.

The goal of this step is to drive the false-positive rate to a level your ops team accepts (typically <5% of blocks). You tune by overriding the relevant env vars — every threshold in `ReinConfig` is env-overridable with the `REIN_` prefix:

| If you're seeing… | Adjust |
|---|---|
| Too many edge-axis blocks on noisy data | Raise `REIN_EDGE_YELLOW_P` / `REIN_EDGE_RED_P` |
| Premature execution-axis blocks | Raise `REIN_EXEC_MIN_ATTEMPTS` |
| Capital axis trips on normal drawdowns | Set `REIN_CAPITAL_YELLOW_PCT` / `REIN_CAPITAL_RED_PCT` more negative (e.g., `-0.03` / `-0.07`) |
| Circuit breaker firing too aggressively | Set `REIN_PORTFOLIO_FLOOR_PCT` more negative (e.g., `-0.08`) |
| Rapid state-flapping | Raise `REIN_DEBOUNCE_SECONDS` (default 300) |

Re-deploy with the updated values, restart the 24-hour window, and re-classify. Do not move to step 4 until at least one full observation window passes with the false-positive rate you're willing to live with in production.

---

## 4. Flip to enforcement

Once step 3 is stable, flip shadow off:

```bash
export REIN_SHADOW=false
```

`gate()` will now actually return `allowed=False` when the pipeline says so. Watch for the first 2 weeks:

- False-positive rate in production (should match what you measured in step 3).
- `gate()` latency overhead — should remain sub-millisecond.
- Audit log growth rate — size your retention accordingly.

### Rollback

If anything looks wrong, flip back. Either flip the env var:

```bash
export REIN_SHADOW=true
```

Or hot-halt via the API (no restart, takes effect immediately):

```bash
curl -X POST http://localhost:PORT/rein/halt \
  -d '{"reason": "investigating false positives"}'
```

---

## Instrumentation checklist

Before step 1, make sure you can answer all of these:

- [ ] How do I reach `/rein/state` from my ops console?
- [ ] Where does the audit log live and how is it rotated?
- [ ] Who gets paged if the circuit breaker fires?
- [ ] What is my rollback playbook (env flip vs. `/rein/halt`)?

If any of those are blank, do not start step 1.
