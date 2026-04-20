"""Rate limiting + anomaly detection tests.

Covers:
- Token bucket basic + burst + refill behavior
- Per-key isolation (one caller can't starve another)
- Global ceiling overrides per-key
- Integration: brain.gate() respects the limiter
- Anomaly detector flags runaway, deny storms, new-caller surges
- gate() still runs in microseconds with both enabled
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from rein_ai import Rein, ReinConfig
from rein_ai.types import Status
from rein_ai.anomaly import AnomalyAlert, AnomalyDetector
from rein_ai.rate_limit import TokenBucketLimiter


# ---------- TokenBucketLimiter ----------

def test_limiter_allows_within_capacity():
    lim = TokenBucketLimiter(rate_per_key=10.0, burst_per_key=5.0)
    for _ in range(5):
        allowed, _ = lim.check(("a", "s"))
        assert allowed
    # 6th call exhausts burst
    allowed, reason = lim.check(("a", "s"))
    assert not allowed
    assert "rate limit" in reason


def test_limiter_refills_over_time():
    lim = TokenBucketLimiter(rate_per_key=100.0, burst_per_key=2.0)
    lim.check(("a", "s")); lim.check(("a", "s"))
    assert not lim.check(("a", "s"))[0]
    time.sleep(0.05)  # 5 tokens should refill
    assert lim.check(("a", "s"))[0]


def test_limiter_keys_are_isolated():
    lim = TokenBucketLimiter(rate_per_key=10.0, burst_per_key=2.0)
    lim.check(("a", "s1")); lim.check(("a", "s1"))
    # s1 is out, s2 is fresh
    assert not lim.check(("a", "s1"))[0]
    assert lim.check(("a", "s2"))[0]


def test_limiter_global_ceiling():
    lim = TokenBucketLimiter(rate_per_key=0, global_rate=5.0, global_burst=3.0)
    for _ in range(3):
        assert lim.check(("a", str(_)))[0]
    # 4th blocked by global
    allowed, reason = lim.check(("a", "new"))
    assert not allowed
    assert "global" in reason


def test_limiter_disabled_when_zero():
    lim = TokenBucketLimiter()  # no limits set
    for _ in range(1000):
        assert lim.check(("a", "s"))[0]


def test_limiter_inspect_reports_state():
    lim = TokenBucketLimiter(rate_per_key=10, burst_per_key=5)
    lim.check(("a", "s"))
    info = lim.inspect(("a", "s"))
    assert info is not None
    assert info["capacity"] == 5
    assert info["rate_per_second"] == 10


# ---------- Brain integration ----------

@pytest.fixture
async def brain_rate_limited(tmp_path: Path):
    cfg = ReinConfig(enabled=True, shadow_mode=False, debounce_seconds=0.0)
    lim = TokenBucketLimiter(rate_per_key=5.0, burst_per_key=3.0)
    b = Rein(cfg=cfg, persist_dir=tmp_path, rate_limiter=lim)
    await b.start()
    yield b
    await b.shutdown()


async def test_gate_respects_rate_limit(brain_rate_limited):
    # First 3 allowed (burst)
    for _ in range(3):
        d = brain_rate_limited.gate(source="a", series="s")
        assert d.allowed

    # 4th blocked by rate limiter
    d = brain_rate_limited.gate(source="a", series="s")
    assert not d.allowed
    assert "rate limit" in d.reason


async def test_rate_limit_works_even_in_shadow_mode(tmp_path: Path):
    """Rate limit is a resource-safety feature — active even in shadow mode."""
    cfg = ReinConfig(enabled=True, shadow_mode=True, debounce_seconds=0.0)
    lim = TokenBucketLimiter(rate_per_key=10.0, burst_per_key=2.0)
    brain = Rein(cfg=cfg, persist_dir=tmp_path, rate_limiter=lim)
    await brain.start()
    try:
        # Burn through burst
        brain.gate(source="a", series="s")
        brain.gate(source="a", series="s")
        d = brain.gate(source="a", series="s")
        assert not d.allowed, "rate limit should enforce even in shadow mode"
    finally:
        await brain.shutdown()


# ---------- AnomalyDetector ----------

def test_anomaly_records_without_crashing():
    det = AnomalyDetector()
    for _ in range(100):
        det.record("a", "s", allowed=True)
    # no crash = pass


def test_anomaly_detects_deny_storm():
    alerts = []
    det = AnomalyDetector(
        deny_storm_rate=0.80, deny_storm_min_count=10,
        alert_cooldown_s=0.0, on_alert=lambda a: alerts.append(a),
    )
    # 2 allows + 50 denies → 50/52 = 96% deny rate, over threshold
    for _ in range(2):
        det.record("a", "s", allowed=True)
    for _ in range(50):
        det.record("a", "s", allowed=False)

    deny_alerts = [a for a in alerts if a.category == "deny_storm"]
    assert len(deny_alerts) >= 1
    assert deny_alerts[0].key == ("a", "s")


def test_anomaly_detects_new_caller_surge():
    alerts = []
    det = AnomalyDetector(
        new_caller_threshold=5, alert_cooldown_s=0.0,
        on_alert=lambda a: alerts.append(a),
    )
    for i in range(10):
        det.record("a", f"series_{i}", allowed=True)

    surge_alerts = [a for a in alerts if a.category == "new_caller_surge"]
    assert len(surge_alerts) >= 1
    assert surge_alerts[0].metrics["new_pairs"] >= 5


def test_anomaly_buffered_when_no_callback():
    det = AnomalyDetector(
        new_caller_threshold=3, alert_cooldown_s=0.0, on_alert=None,
    )
    for i in range(5):
        det.record("a", f"s{i}", allowed=True)
    alerts = det.drain_alerts()
    assert len(alerts) >= 1
    # Draining again returns empty
    assert det.drain_alerts() == []


def test_anomaly_cooldown_prevents_alert_spam():
    alerts = []
    det = AnomalyDetector(
        new_caller_threshold=2, alert_cooldown_s=10.0,
        on_alert=lambda a: alerts.append(a),
    )
    # Fire many events that would each trigger the same category
    for i in range(20):
        det.record("a", f"s{i}", allowed=True)
    surge_alerts = [a for a in alerts if a.category == "new_caller_surge"]
    # Only one should fire despite 20 events crossing the threshold
    assert len(surge_alerts) == 1


# ---------- Full integration ----------

async def test_gate_stays_fast_with_limiter_and_anomaly(tmp_path: Path):
    cfg = ReinConfig(enabled=True, shadow_mode=False, debounce_seconds=0.0)
    lim = TokenBucketLimiter(rate_per_key=1_000_000, burst_per_key=1_000_000)
    det = AnomalyDetector()
    brain = Rein(cfg=cfg, persist_dir=tmp_path,
                       rate_limiter=lim, anomaly_detector=det)
    await brain.start()
    try:
        # Warm up
        brain.gate("a", "s")
        t0 = time.perf_counter()
        for _ in range(10_000):
            brain.gate("a", "s")
        elapsed = time.perf_counter() - t0
        per_call_us = elapsed * 1e6 / 10_000
        # Budget: 500µs per call with all guardrails on = 2000 calls/sec,
        # comfortably beyond production workload (~10 gate/sec for a trading bot).
        # Bare gate() alone is ~30-80µs; the full stack with anomaly detector
        # landing new-caller surge checks + rate limiter adds overhead.
        assert per_call_us < 500, f"gate() too slow with limiter+anomaly: {per_call_us:.1f}µs"
    finally:
        await brain.shutdown()


async def test_anomaly_fires_on_real_gate_flood(tmp_path: Path):
    alerts = []
    cfg = ReinConfig(enabled=True, shadow_mode=False, debounce_seconds=0.0)
    det = AnomalyDetector(
        new_caller_threshold=5, alert_cooldown_s=0.0,
        on_alert=lambda a: alerts.append(a),
    )
    brain = Rein(cfg=cfg, persist_dir=tmp_path, anomaly_detector=det)
    await brain.start()
    try:
        for i in range(10):
            brain.gate(source="a", series=f"new_series_{i}")
        assert any(a.category == "new_caller_surge" for a in alerts)
    finally:
        await brain.shutdown()
