"""Token-bucket rate limiting for gate() calls.

Two levels:
  - Per (source, series) — protects individual strategies from a runaway caller
  - Global — last-resort ceiling across all callers in this process

Both are microsecond-fast: each check is a couple of arithmetic ops on an
in-memory dict. No locks on the hot path — the atomicity we need is weaker
than thread safety (we're OK with a couple of extra calls slipping through
during a race; the enforcement is statistical).

Disabled by default (gate_rps_per_key=0 means "no limit"). Enable via
ReinConfig or env vars.

Design:
    A token bucket has `capacity` tokens and refills at `rate` per second.
    Each call subtracts 1. If empty, the call is denied.

Why this over fixed-window counters:
    Fixed windows allow bursts at window boundaries (2x the rate limit for
    one tick). Token buckets are burst-aware but smoothly bounded.

SOC 2 reference:
    CC6.6 (logical access over networks) — resource quotas as a defense
    against runaway or malicious clients.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass
class _Bucket:
    tokens: float
    last_refill: float
    capacity: float
    rate: float


class TokenBucketLimiter:
    """Per-key rate limiter. Thread-safe under GIL for single-process use.

    Parameters
    ----------
    rate_per_key : float
        Steady-state allowed calls per second per unique key.
    burst_per_key : float
        Max burst size per key. Defaults to 2x rate_per_key.
    global_rate : float
        Process-wide ceiling. 0 = no global limit.
    global_burst : float
        Global burst size. Defaults to 2x global_rate.
    """

    def __init__(
        self,
        rate_per_key: float = 0.0,
        burst_per_key: Optional[float] = None,
        global_rate: float = 0.0,
        global_burst: Optional[float] = None,
    ):
        self.rate_per_key = max(0.0, rate_per_key)
        self.burst_per_key = burst_per_key if burst_per_key is not None else self.rate_per_key * 2
        self.global_rate = max(0.0, global_rate)
        self.global_burst = global_burst if global_burst is not None else self.global_rate * 2

        self._buckets: Dict[Tuple[str, str], _Bucket] = {}
        self._global: Optional[_Bucket] = None
        if self.global_rate > 0:
            self._global = _Bucket(
                tokens=self.global_burst, last_refill=time.monotonic(),
                capacity=self.global_burst, rate=self.global_rate,
            )

    def check(self, key: Tuple[str, str]) -> tuple[bool, str]:
        """Return (allowed, reason). Consumes a token on allow."""
        now = time.monotonic()

        # Global ceiling
        if self._global is not None:
            if not self._consume(self._global, now):
                return False, f"rate limit: global ({self.global_rate}/s)"

        # Per-key
        if self.rate_per_key > 0:
            b = self._buckets.get(key)
            if b is None:
                b = _Bucket(tokens=self.burst_per_key, last_refill=now,
                            capacity=self.burst_per_key, rate=self.rate_per_key)
                self._buckets[key] = b
            if not self._consume(b, now):
                return False, (f"rate limit: {key[0]}/{key[1]} "
                               f"({self.rate_per_key}/s)")

        return True, "ok"

    @staticmethod
    def _consume(b: _Bucket, now: float) -> bool:
        elapsed = now - b.last_refill
        if elapsed > 0:
            b.tokens = min(b.capacity, b.tokens + elapsed * b.rate)
            b.last_refill = now
        if b.tokens >= 1.0:
            b.tokens -= 1.0
            return True
        return False

    def reset(self, key: Optional[Tuple[str, str]] = None) -> None:
        """Clear state for a key, or all keys."""
        if key is None:
            self._buckets.clear()
        else:
            self._buckets.pop(key, None)

    def inspect(self, key: Tuple[str, str]) -> Optional[dict]:
        """Diagnostic — returns current bucket state or None if unseen."""
        b = self._buckets.get(key)
        if b is None:
            return None
        now = time.monotonic()
        elapsed = now - b.last_refill
        projected = min(b.capacity, b.tokens + elapsed * b.rate)
        return {
            "tokens_available": projected,
            "capacity": b.capacity,
            "rate_per_second": b.rate,
            "last_refill_age_s": elapsed,
        }
