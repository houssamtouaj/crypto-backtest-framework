"""Trade and equity diagnostics (spec §5.9): headline numbers, exposure, drawdown, distributions, breakdowns."""
from __future__ import annotations

import numpy as np

QUANTILES = {"min": 0.0, "p05": 5.0, "p25": 25.0, "p50": 50.0, "p75": 75.0, "p95": 95.0, "max": 100.0}


def quantiles(x) -> dict | None:
    """``min, p05, p25, p50, p75, p95, max`` and ``mean`` of the finite values; None when there are none."""
    a = np.asarray(x, dtype=np.float64)
    a = a[np.isfinite(a)]
    if len(a) == 0:
        return None
    q = np.percentile(a, list(QUANTILES.values()))
    return {**{k: float(v) for k, v in zip(QUANTILES, q, strict=True)}, "mean": float(a.mean())}


def max_drawdown(equity, start: float) -> float | None:
    """Largest fall from a running peak, as a fraction: ``max(1 − equity / peak)`` with ``start`` as the first peak."""
    eq = np.asarray(equity, dtype=np.float64)
    if len(eq) == 0:
        return None
    peak = np.maximum.accumulate(np.concatenate(([start], eq)))[1:]
    return float(np.max(1.0 - eq / peak))
