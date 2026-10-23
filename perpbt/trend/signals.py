"""The 12 ensemble members, exposure, vol forecast and target weights (trend spec Phase 1 §1.2-§1.5).

Every function reads daily arrays indexed by calendar day (a DayFrame's
rows) and uses only days ``<= d`` for day ``d``. A member is NaN on the days
it cannot be evaluated. Ties (``=``) count as "not above": 0 in both
directions.
"""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def _sign(x: np.ndarray, long_short: bool) -> np.ndarray:
    return np.sign(x) if long_short else (x > 0).astype(np.float64)


def return_sign(c: np.ndarray, n: int, *, long_short: bool = False) -> np.ndarray:
    """``R_N``: ``c_d`` against ``c_{d−N}``; NaN for ``d < N``."""
    c = np.asarray(c, dtype=np.float64)
    out = np.full(len(c), np.nan)
    if len(c) > n:
        out[n:] = _sign(c[n:] - c[:-n], long_short)
    return out


def ma_filter(c: np.ndarray, n: int, *, long_short: bool = False) -> np.ndarray:
    """``M_N``: ``c_d`` against the mean of ``c_{d−N+1..d}``; NaN for ``d < N − 1``."""
    c = np.asarray(c, dtype=np.float64)
    out = np.full(len(c), np.nan)
    if len(c) >= n:
        out[n - 1:] = _sign(c[n - 1:] - sliding_window_view(c, n).mean(axis=1), long_short)
    return out


def _prior(x: np.ndarray, n: int, reduce) -> np.ndarray:
    """``reduce(x_{d−n..d−1})`` per day; NaN for ``d < n``."""
    out = np.full(len(x), np.nan)
    if len(x) > n:
        out[n:] = reduce(sliding_window_view(x[:-1], n), axis=1)
    return out


def donchian_states(c: np.ndarray, h: np.ndarray, l: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:  # noqa: E741
    """Long and short Donchian states per day; both off before day ``N``.

    Long turns on when ``c_d > max(h_{d−N..d−1})`` and off when
    ``c_d < min(l_{d−N/2..d−1})``; short turns on when
    ``c_d < min(l_{d−N..d−1})`` and off when ``c_d > max(h_{d−N/2..d−1})``.
    Otherwise each keeps its previous value; the initial state is off.
    """
    c, h, l = (np.asarray(a, dtype=np.float64) for a in (c, h, l))  # noqa: E741
    hi, lo = _prior(h, n, np.max), _prior(l, n, np.min)
    hi_x, lo_x = _prior(h, n // 2, np.max), _prior(l, n // 2, np.min)
    long = np.zeros(len(c), dtype=bool)
    short = np.zeros(len(c), dtype=bool)
    on_l = on_s = False
    for d in range(n, len(c)):
        on_l = not c[d] < lo_x[d] if on_l else bool(c[d] > hi[d])
        on_s = not c[d] > hi_x[d] if on_s else bool(c[d] < lo[d])
        long[d], short[d] = on_l, on_s
    return long, short


def donchian(c: np.ndarray, h: np.ndarray, l: np.ndarray, n: int, *, long_short: bool = False) -> np.ndarray:  # noqa: E741
    """``D_N``: the long state (long/flat) or long − short (long/short); NaN for ``d < N``."""
    long, short = donchian_states(c, h, l, n)
    out = long.astype(np.float64) - (short if long_short else 0)
    out[:n] = np.nan
    return out
