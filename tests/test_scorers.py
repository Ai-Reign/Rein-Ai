"""Tests for tripwire_ai.scorers: Bayesian posterior math."""
import math
import pytest

from tripwire_ai.scorers import (
    normal_cdf,
    welford_update,
    welford_init,
    update_beta_binomial,
    beta_p_below_threshold,
    update_normal_axis,
    normal_p_below_threshold,
)
from tripwire_ai.types import AxisScore


# --- normal_cdf ---

def test_normal_cdf_at_mean_is_half():
    assert math.isclose(normal_cdf(0.0, 0.0, 1.0), 0.5, abs_tol=1e-9)


def test_normal_cdf_known_values():
    # 1-sigma below mean ~ 0.1587
    assert math.isclose(normal_cdf(-1.0, 0.0, 1.0), 0.158655, abs_tol=1e-4)
    # 2-sigma below mean ~ 0.0228
    assert math.isclose(normal_cdf(-2.0, 0.0, 1.0), 0.022750, abs_tol=1e-4)


def test_normal_cdf_zero_sigma_step_function():
    """sigma=0 collapses to indicator."""
    assert normal_cdf(-0.001, 0.0, 0.0) == 0.0
    assert normal_cdf(0.001, 0.0, 0.0) == 1.0


# --- Welford's running mean/variance ---

def test_welford_single_sample():
    n, mean, m2 = welford_update(0, 0.0, 0.0, 5.0)
    assert n == 1
    assert mean == 5.0
    assert m2 == 0.0


def test_welford_known_sequence():
    """Mean and sample-variance match closed-form for a small sequence."""
    n, mean, m2 = welford_init()
    for x in [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]:
        n, mean, m2 = welford_update(n, mean, m2, x)
    assert n == 8
    assert math.isclose(mean, 5.0, abs_tol=1e-9)
    pop_var = m2 / n
    assert math.isclose(pop_var, 4.0, abs_tol=1e-9)


# --- Beta-Binomial (fill rate / execution axis) ---

def test_beta_binomial_zero_samples_uniform():
    """Prior alpha=beta=1 → mean=0.5."""
    a, b = 1.0, 1.0
    mean = a / (a + b)
    assert mean == 0.5


def test_update_beta_binomial_success_only():
    a, b = 1.0, 1.0
    for _ in range(10):
        a, b = update_beta_binomial(a, b, success=True)
    assert a == 11.0 and b == 1.0
    mean = a / (a + b)
    assert math.isclose(mean, 11.0 / 12.0, abs_tol=1e-9)


def test_beta_p_below_threshold_high_fill_rate():
    """If we've seen 18 fills out of 20, P(true_rate < 0.25) should be near zero."""
    a, b = 1.0 + 18.0, 1.0 + 2.0
    p = beta_p_below_threshold(a, b, 0.25)
    assert p < 0.01


def test_beta_p_below_threshold_low_fill_rate():
    """If we've seen 2 fills out of 20, P(true_rate < 0.25) should be high."""
    a, b = 1.0 + 2.0, 1.0 + 18.0
    p = beta_p_below_threshold(a, b, 0.25)
    assert p > 0.85


def test_beta_p_below_threshold_cold_start_uncertain():
    """Tiny samples → high uncertainty → moderate p."""
    a, b = 1.0 + 1.0, 1.0 + 1.0
    p = beta_p_below_threshold(a, b, 0.25)
    assert 0.15 < p < 0.5


# --- Normal axis (edge / capital) ---

def test_update_normal_axis_increments_samples():
    axis = AxisScore.empty()
    state = (axis.samples, axis.posterior_mean, 0.0, axis.last_value)
    state = update_normal_axis(state, 1.5)
    assert state[0] == 1
    assert state[1] == 1.5
    assert state[3] == 1.5


def test_normal_p_below_threshold_zero_samples():
    """Zero samples → flat prior → return 0.5 (max uncertainty)."""
    p = normal_p_below_threshold(samples=0, mean=0.0, std=0.0, threshold=0.0)
    assert p == 0.5


def test_normal_p_below_threshold_strong_negative_signal():
    """High negative mean with tight std → P(value < 0) near 1."""
    p = normal_p_below_threshold(samples=30, mean=-2.0, std=0.5, threshold=0.0)
    assert p > 0.99


def test_normal_p_below_threshold_strong_positive_signal():
    """High positive mean with tight std → P(value < 0) near 0."""
    p = normal_p_below_threshold(samples=30, mean=2.0, std=0.5, threshold=0.0)
    assert p < 0.01
