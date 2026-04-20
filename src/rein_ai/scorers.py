"""Pure-Python Bayesian scorers for the Rein.

No external dependencies. Uses ``math.erf`` for normal CDF and a
continued-fraction expansion of the regularized incomplete beta function for
Beta-Binomial tail probabilities.
"""
from __future__ import annotations

import math
from typing import Tuple


# ----------------------------------------------------------------------------
# Normal CDF (used for edge/capital axes posterior tail probabilities)
# ----------------------------------------------------------------------------

def normal_cdf(x: float, mu: float, sigma: float) -> float:
    """P(X <= x) for X ~ Normal(mu, sigma^2). sigma=0 → step function."""
    if sigma == 0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


# ----------------------------------------------------------------------------
# Welford's online algorithm for running mean / sample variance
# ----------------------------------------------------------------------------

def welford_init() -> Tuple[int, float, float]:
    """Return initial (count, mean, M2) state."""
    return (0, 0.0, 0.0)


def welford_update(n: int, mean: float, m2: float, x: float) -> Tuple[int, float, float]:
    """Update Welford state with one new observation x. Returns new (n, mean, M2)."""
    n += 1
    delta = x - mean
    mean += delta / n
    delta2 = x - mean
    m2 += delta * delta2
    return (n, mean, m2)


# ----------------------------------------------------------------------------
# Beta-Binomial conjugate update (used for execution / fill-rate axis)
# ----------------------------------------------------------------------------

def update_beta_binomial(alpha: float, beta: float, success: bool) -> Tuple[float, float]:
    """One observation. Posterior alpha += 1 on success, beta += 1 on failure."""
    if success:
        return (alpha + 1.0, beta)
    return (alpha, beta + 1.0)


def _ln_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for incomplete beta function. Lentz's algorithm."""
    fpmin = 1e-30
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-7:
            return h
    return h  # max iters; close enough


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """I_x(a, b) — the regularized incomplete beta function.

    Returns P(Beta(a,b) <= x) — i.e. the CDF of a Beta(a,b) at x.
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def beta_p_below_threshold(alpha: float, beta: float, threshold: float) -> float:
    """P(true rate < threshold) for posterior Beta(alpha, beta)."""
    return regularized_incomplete_beta(alpha, beta, threshold)


# ----------------------------------------------------------------------------
# Normal axis update (edge / capital): wraps Welford + tail probability
# ----------------------------------------------------------------------------

# State shape: (samples, mean, M2, last_value)
NormalAxisState = Tuple[int, float, float, float]


def update_normal_axis(state: NormalAxisState, x: float) -> NormalAxisState:
    n, mean, m2, _ = state
    n2, mean2, m22 = welford_update(n, mean, m2, x)
    return (n2, mean2, m22, x)


def normal_p_below_threshold(samples: int, mean: float, std: float, threshold: float) -> float:
    """P(true mean < threshold) using the posterior approximation.

    For samples=0 we return 0.5 (max uncertainty). For samples>=1 we use a
    Normal approximation with std equal to the posterior standard error
    (std / sqrt(n)), which is conservative without scipy's full t-distribution.
    """
    if samples <= 0:
        return 0.5
    se = std / math.sqrt(samples) if samples > 0 else std
    return normal_cdf(threshold, mean, max(se, 1e-9))
