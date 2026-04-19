"""Anomaly detection on gate() call patterns.

Maintains rolling statistics of gate activity and flags:
  - **Runaway callers** — a (source, series) pair calling gate() far above its
    rolling baseline. Indicates an agent stuck in a loop.
  - **Deny storms** — a sudden spike in deny rate for a key. Indicates a bad
    prompt, misconfigured threshold, or attack.
  - **New caller surge** — many never-before-seen (source, series) pairs in
    a short window. Indicates enumeration or misconfigured client.

Runs entirely in-process, O(1) per event. Designed to be cheap enough to call
on the gate() hot path without adding measurable latency.

Detection signals feed a callback so operators can wire them to pager, Slack,
dashboard, or anywhere. Does NOT block gate() itself — rate limiting handles
that. Think of this as the observer, not the enforcer.

SOC 2 reference:
    CC7.2 (system monitoring) — detection of anomalous activity.
    CC7.3 (evaluation of events) — ranking and notification of alerts.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Tuple


@dataclass
class AnomalyAlert:
    severity: str  # "info" | "warn" | "critical"
    category: str  # "runaway" | "deny_storm" | "new_caller_surge"
    key: Optional[Tuple[str, str]]
    message: str
    at: float = field(default_factory=time.time)
    metrics: dict = field(default_factory=dict)


class AnomalyDetector:
    """Rolling-window anomaly detector. O(1) event handling.

    Parameters
    ----------
    window_s : float
        Rolling window size (seconds).
    runaway_multiplier : float
        A key's current-window rate / baseline rate. If exceeded and window
        rate >= runaway_min_rate, fire a `runaway` alert.
    runaway_min_rate : float
        Minimum calls-per-sec in window before runaway alert can fire. Prevents
        alerts on low-volume keys where a single burst spikes the ratio.
    deny_storm_rate : float
        If a key's deny rate (denies / total) exceeds this and deny_storm_min_count
        denies observed in the window, fire `deny_storm`.
    deny_storm_min_count : int
    new_caller_threshold : int
        Distinct new keys in the window to trigger `new_caller_surge`.
    alert_cooldown_s : float
        Minimum interval between the same alert category+key. Prevents spam.
    on_alert : callable, optional
        Callback invoked for every alert. Sync. Set to None to collect via
        `drain_alerts()`.
    """

    def __init__(
        self,
        window_s: float = 60.0,
        runaway_multiplier: float = 5.0,
        runaway_min_rate: float = 10.0,
        deny_storm_rate: float = 0.80,
        deny_storm_min_count: int = 20,
        new_caller_threshold: int = 20,
        alert_cooldown_s: float = 30.0,
        on_alert: Optional[Callable[[AnomalyAlert], None]] = None,
    ):
        self.window_s = window_s
        self.runaway_multiplier = runaway_multiplier
        self.runaway_min_rate = runaway_min_rate
        self.deny_storm_rate = deny_storm_rate
        self.deny_storm_min_count = deny_storm_min_count
        self.new_caller_threshold = new_caller_threshold
        self.alert_cooldown_s = alert_cooldown_s
        self.on_alert = on_alert

        # Events per key: deque of (ts, allowed) tuples
        self._events: Dict[Tuple[str, str], Deque[Tuple[float, bool]]] = defaultdict(deque)
        # Keys seen (ever): used for new-caller detection
        self._first_seen: Dict[Tuple[str, str], float] = {}
        # Last alert time per (category, key)
        self._last_alert: Dict[Tuple[str, Optional[Tuple[str, str]]], float] = {}
        # Buffered alerts (when no callback)
        self._buffered: List[AnomalyAlert] = []

    def record(self, source: str, series: str, allowed: bool) -> None:
        """Call this on every gate() invocation. Must stay O(1)."""
        key = (source, series)
        now = time.time()

        if key not in self._first_seen:
            self._first_seen[key] = now

        dq = self._events[key]
        dq.append((now, allowed))

        # Evict old events for this key
        cutoff = now - self.window_s
        while dq and dq[0][0] < cutoff:
            dq.popleft()

        # Check per-key anomalies
        self._check_runaway(key, dq, now)
        self._check_deny_storm(key, dq, now)
        # Cross-key check — cheap enough
        self._check_new_caller_surge(now)

    def _check_runaway(self, key, dq, now):
        in_window = len(dq)
        if in_window < self.runaway_min_rate * self.window_s:
            return
        window_rate = in_window / self.window_s

        # Baseline = calls outside the current window, averaged
        first_seen = self._first_seen[key]
        total_age = max(now - first_seen, self.window_s * 2)
        baseline_rate = in_window / total_age  # ← proxy; cheap
        if baseline_rate < 0.01:
            return

        ratio = window_rate / baseline_rate
        if ratio >= self.runaway_multiplier and window_rate >= self.runaway_min_rate:
            self._maybe_alert(AnomalyAlert(
                severity="warn" if ratio < self.runaway_multiplier * 2 else "critical",
                category="runaway",
                key=key,
                message=f"runaway: {key[0]}/{key[1]} at {window_rate:.1f}/s "
                        f"({ratio:.1f}x baseline)",
                metrics={"window_rate": window_rate, "baseline_rate": baseline_rate,
                          "ratio": ratio},
            ), now)

    def _check_deny_storm(self, key, dq, now):
        if len(dq) < self.deny_storm_min_count:
            return
        denies = sum(1 for _, allowed in dq if not allowed)
        deny_rate = denies / len(dq)
        if deny_rate >= self.deny_storm_rate and denies >= self.deny_storm_min_count:
            self._maybe_alert(AnomalyAlert(
                severity="warn",
                category="deny_storm",
                key=key,
                message=f"deny_storm: {key[0]}/{key[1]} {deny_rate*100:.0f}% denies "
                        f"({denies}/{len(dq)})",
                metrics={"deny_rate": deny_rate, "denies": denies, "total": len(dq)},
            ), now)

    def _check_new_caller_surge(self, now):
        cutoff = now - self.window_s
        recent_new = sum(1 for ts in self._first_seen.values() if ts >= cutoff)
        if recent_new >= self.new_caller_threshold:
            self._maybe_alert(AnomalyAlert(
                severity="warn",
                category="new_caller_surge",
                key=None,
                message=f"new_caller_surge: {recent_new} new (source,series) "
                        f"pairs in last {self.window_s:.0f}s",
                metrics={"new_pairs": recent_new, "window_s": self.window_s},
            ), now)

    def _maybe_alert(self, alert: AnomalyAlert, now: float) -> None:
        dedup_key = (alert.category, alert.key)
        last = self._last_alert.get(dedup_key, 0)
        if now - last < self.alert_cooldown_s:
            return
        self._last_alert[dedup_key] = now
        if self.on_alert:
            try:
                self.on_alert(alert)
            except Exception:
                pass  # never let a callback break the hot path
        else:
            self._buffered.append(alert)

    def drain_alerts(self) -> List[AnomalyAlert]:
        """Pull buffered alerts and clear the buffer."""
        out = list(self._buffered)
        self._buffered.clear()
        return out
