"""Tests for RegimeDetector classification."""
import math
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from tripwire_ai.regime import (
    classify_vol, classify_trend, classify_liquidity, classify_time, classify_macro,
    Baselines, RegimeInputs, classify_regime,
)
from tripwire_ai.types import Regime


def test_classify_vol_bucketing():
    bl = Baselines(vol_p25=0.01, vol_p75=0.02, vol_p95=0.03, depth_p25=10.0, depth_p75=100.0)
    assert classify_vol(0.005, bl) == "low"
    assert classify_vol(0.015, bl) == "mid"
    assert classify_vol(0.025, bl) == "hi"
    assert classify_vol(0.04, bl) == "extreme"


def test_classify_trend():
    assert classify_trend(returns_1h=0.001, returns_24h=0.002) == "chop"
    assert classify_trend(returns_1h=0.012, returns_24h=0.005) == "up"
    assert classify_trend(returns_1h=-0.012, returns_24h=-0.008) == "down"
    assert classify_trend(returns_1h=0.025, returns_24h=0.030) == "breakout"


def test_classify_liquidity():
    bl = Baselines(vol_p25=0.0, vol_p75=0.0, vol_p95=0.0, depth_p25=10.0, depth_p75=100.0)
    assert classify_liquidity(book_depth_usd=5.0, baselines=bl) == "thin"
    assert classify_liquidity(book_depth_usd=50.0, baselines=bl) == "normal"
    assert classify_liquidity(book_depth_usd=500.0, baselines=bl) == "deep"
    assert classify_liquidity(book_depth_usd=0.0, baselines=bl) == "dead"


def test_classify_time_us_open():
    # Tuesday 14:30 UTC = 10:30 ET — US open
    ts = datetime(2026, 4, 14, 14, 30, tzinfo=timezone.utc).timestamp()
    assert classify_time(ts) == "us_open"


def test_classify_time_us_close():
    # Tuesday 20:00 UTC = 16:00 ET — US close
    ts = datetime(2026, 4, 14, 20, 0, tzinfo=timezone.utc).timestamp()
    assert classify_time(ts) == "us_close"


def test_classify_time_overnight():
    # Tuesday 04:00 UTC = 00:00 ET — overnight
    ts = datetime(2026, 4, 14, 4, 0, tzinfo=timezone.utc).timestamp()
    assert classify_time(ts) == "overnight"


def test_classify_time_weekend():
    # Sunday 14:30 UTC
    ts = datetime(2026, 4, 12, 14, 30, tzinfo=timezone.utc).timestamp()
    assert classify_time(ts) == "weekend"


def test_classify_macro_with_event():
    cal = {"2026-04-16": "FOMC"}
    ts = datetime(2026, 4, 16, 18, 0, tzinfo=timezone.utc).timestamp()
    assert classify_macro(ts, cal) == "FOMC"


def test_classify_macro_without_event():
    cal = {"2026-04-16": "FOMC"}
    ts = datetime(2026, 4, 17, 18, 0, tzinfo=timezone.utc).timestamp()
    assert classify_macro(ts, cal) is None


def test_classify_regime_combines_all():
    bl = Baselines(vol_p25=0.01, vol_p75=0.02, vol_p95=0.03, depth_p25=10.0, depth_p75=100.0)
    inputs = RegimeInputs(
        realized_vol_1h=0.025,
        returns_1h=0.001,
        returns_24h=0.0,
        book_depth_usd=50.0,
        now_ts=datetime(2026, 4, 14, 14, 30, tzinfo=timezone.utc).timestamp(),
    )
    r = classify_regime(inputs, bl, macro_calendar={})
    assert isinstance(r, Regime)
    assert r.vol_bucket == "hi"
    assert r.trend_bucket == "chop"
    assert r.liquidity_bucket == "normal"
    assert r.time_bucket == "us_open"
    assert r.macro_event is None
