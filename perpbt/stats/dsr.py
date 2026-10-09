"""Probabilistic and deflated Sharpe ratio (spec §5.7; Bailey and López de Prado, 2014).

All Sharpe ratios here are non-annualized (daily). With ``N`` trials whose
Sharpe ratios have variance ``V``, the expected maximum under the null is
``SR* = sqrt(V) ((1 − γ) Φ⁻¹(1 − 1/N) + γ Φ⁻¹(1 − 1/(N e)))``, ``γ`` the
Euler–Mascheroni constant (``SR* = 0`` for ``N = 1``, where the formula's
``Φ⁻¹(0)`` is −∞), and ``DSR = PSR(SR*)`` with
``PSR(SR_ref) = Φ((SR − SR_ref) sqrt(T − 1) / sqrt(1 − γ₃ SR + (γ₄ − 1)/4 SR²))``,
``γ₃``, ``γ₄`` the skew and (non-excess) kurtosis of the daily returns.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import stats as st

EULER_GAMMA = 0.5772156649015329


def expected_max_sr(n_trials: int, var: float) -> float:
    """``SR*``: the expected maximum Sharpe ratio of ``n_trials`` null trials with variance ``var``."""
    if n_trials < 1:
        raise ValueError(f"expected_max_sr: n_trials must be >= 1, got {n_trials}")
    if n_trials == 1:
        return 0.0
    g = EULER_GAMMA
    return float(math.sqrt(var) * ((1 - g) * st.norm.ppf(1 - 1 / n_trials)
                                   + g * st.norm.ppf(1 - 1 / (n_trials * math.e))))


def psr(sr: float, sr_ref: float, T: int, skew: float, kurt: float) -> float:
    """Probability that the true Sharpe exceeds ``sr_ref`` given the observed ``sr`` over ``T`` days."""
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    return float(st.norm.cdf((sr - sr_ref) * math.sqrt(T - 1) / math.sqrt(denom)))


def dsr(sr: float, T: int, skew: float, kurt: float, n_trials: int, var: float) -> float:
    """``PSR(SR*)`` with ``SR* = expected_max_sr(n_trials, var)``."""
    return psr(sr, expected_max_sr(n_trials, var), T, skew, kurt)


def sr_moments(ret) -> dict:
    """``sr`` (daily, mean / std ddof 1), ``T``, ``skew``, ``kurt`` (non-excess) of a daily return series.

    Values that are undefined (fewer than 3 days, zero std) are None.
    """
    r = np.asarray(ret, dtype=np.float64)
    T = len(r)
    sd = r.std(ddof=1) if T > 1 else 0.0
    if T < 3 or not sd > 0:
        return {"sr": None, "T": T, "skew": None, "kurt": None}
    return {"sr": float(r.mean() / sd), "T": T, "skew": float(st.skew(r)),
            "kurt": float(st.kurtosis(r, fisher=False))}


def dsr_from_trials(moments: dict, trial_srs) -> dict:
    """DSR of a variant (its ``sr_moments``) against the daily Sharpe ratios of ``N`` trials (``V`` with ddof 1)."""
    t = np.asarray([x for x in trial_srs if x is not None and np.isfinite(x)], dtype=np.float64)
    n = len(t)
    v = float(np.var(t, ddof=1)) if n > 1 else 0.0
    if moments.get("sr") is None or n == 0:
        return {"N": n, "V": v, "sr_star": None, "dsr": None}
    star = expected_max_sr(n, v)
    m = moments
    return {"N": n, "V": v, "sr_star": star, "dsr": psr(m["sr"], star, m["T"], m["skew"], m["kurt"])}
