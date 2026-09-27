"""Wilder ATR on 15m candles (spec §2.1) and the Wilder recursion it shares with ADX."""
from __future__ import annotations

import operator

import numpy as np

from perpbt.data.store import Candles


def check_period(n: object) -> int:
    """``n`` as an int >= 1; TypeError for a non-integer, ValueError below 1."""
    try:
        n = operator.index(n)
    except TypeError:
        raise TypeError(f"period must be an integer, got {type(n).__name__}") from None
    if n < 1:
        raise ValueError(f"period must be >= 1, got {n}")
    return n


def wilder_rma(x: np.ndarray, n: int, start: int = 0) -> np.ndarray:
    """Wilder's running mean of ``x[start:]``: seeded by the mean of the first ``n``.

    ``out[start + n - 1] = mean(x[start : start + n])`` and afterwards
    ``out[t] = (out[t-1] * (n - 1) + x[t]) / n``. Earlier rows are NaN, and
    so is everything when fewer than ``n`` values follow ``start``. Lag 0.
    """
    n = check_period(n)
    x = np.asarray(x, dtype=np.float64)
    out = np.full(len(x), np.nan)
    first = start + n - 1
    if first >= len(x):
        return out
    acc = float(x[start : start + n].mean())
    out[first] = acc
    for t in range(first + 1, len(x)):
        acc = (acc * (n - 1) + float(x[t])) / n
        out[t] = acc
    return out


def true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:  # noqa: E741
    """``TR_0 = h_0 - l_0``; ``TR_i = max(h_i - l_i, |h_i - c_{i-1}|, |l_i - c_{i-1}|)``."""
    h = np.asarray(h, dtype=np.float64)
    l = np.asarray(l, dtype=np.float64)  # noqa: E741
    c = np.asarray(c, dtype=np.float64)
    tr = h - l
    if len(tr) > 1:
        pc = c[:-1]
        tr[1:] = np.maximum(tr[1:], np.maximum(np.abs(h[1:] - pc), np.abs(l[1:] - pc)))
    return tr


def atr(candles: Candles, n: int = 14) -> np.ndarray:
    """Wilder ATR(n) aligned to the candle index. Lag 0: the value at ``i`` is known at the close of ``i``.

    ``ATR_{n-1}`` is the mean of the first ``n`` true ranges, then
    ``ATR_i = (ATR_{i-1} * (n - 1) + TR_i) / n``. NaN for ``i < n - 1``.
    "Previous" is the previous row: timestamp gaps are ignored.
    """
    n = check_period(n)
    return wilder_rma(true_range(candles.h, candles.l, candles.c), n)
