"""Multiplicity corrections over the primary family (spec §5.8, D11).

Holm step-down: with the ``n`` p-values sorted,
``p̃_(i) = max_{j ≤ i} min(1, (n − j + 1) p_(j))``, returned in the input
order. Missing values (None or NaN) are left out of the family and stay NaN.
"""
from __future__ import annotations

import numpy as np


def _values(p) -> tuple[np.ndarray, np.ndarray]:
    a = np.array([np.nan if x is None else x for x in p], dtype=np.float64)
    return a, np.isfinite(a)


def holm(p) -> np.ndarray:
    """Holm-adjusted p-values in the input order."""
    a, ok = _values(p)
    out = np.full(len(a), np.nan)
    v = a[ok]
    n = len(v)
    if n == 0:
        return out
    order = np.argsort(v, kind="stable")
    adj = np.maximum.accumulate(np.minimum(1.0, (n - np.arange(n)) * v[order]))
    res = np.empty(n)
    res[order] = adj
    out[ok] = res
    return out


def bonferroni(p) -> np.ndarray:
    """Bonferroni-adjusted p-values ``min(1, n p)`` (reference only; the threshold is ``alpha / n``)."""
    a, ok = _values(p)
    out = np.full(len(a), np.nan)
    out[ok] = np.minimum(1.0, ok.sum() * a[ok])
    return out
