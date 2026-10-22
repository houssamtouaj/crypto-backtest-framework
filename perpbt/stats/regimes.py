"""Trend and volatility regime labels per trade (spec §5.9), as of the day before entry.

- ``regime_trend``: ``"trend"`` if the daily ADX(14) of the completed days
  before the entry day (Phase 2's aligned array at ``entry_idx``) is above
  25, ``"no_trend"`` otherwise, None while it is NaN.
- ``regime_vol``: ``"high"`` if the 30-day std (ddof 1) of daily log
  returns of the completed days before the entry day is above the pair's
  in-sample median, ``"low"`` otherwise, None while it is NaN. The median
  is over the as-of values of the in-sample period's days; it is fixed and
  applied as-is to the holdout.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from perpbt.data.store import DAY_MS, Candles
from perpbt.indicators.daily import _align, daily_adx_aligned, daily_bars

TREND_ADX = 25.0
VOL_DAYS = 30


def daily_vol_asof(candles15: Candles, n: int = VOL_DAYS) -> np.ndarray:
    """Per 15m candle: std (ddof 1) of the last ``n`` daily log returns of the days before its day."""
    bars = daily_bars(candles15)
    per_bar = np.full(len(bars), np.nan)
    if len(bars) > n:
        lr = np.diff(np.log(bars.c))  # lr[k - 1] is the return of bar k
        per_bar[n:] = sliding_window_view(lr, n).std(axis=1, ddof=1)
    return _align(candles15, bars, per_bar)


def vol_median(candles15: Candles, start_ms: int, end_ms: int, n: int = VOL_DAYS) -> float | None:
    """Median of the as-of vol over the UTC days with candles in ``[start_ms, end_ms)``; None if all NaN."""
    v = daily_vol_asof(candles15, n)
    ts = candles15.ts
    sel = np.flatnonzero((ts >= start_ms) & (ts < end_ms))
    if len(sel) == 0:
        return None
    _, first = np.unique(ts[sel] // DAY_MS, return_index=True)
    x = v[sel[first]]
    x = x[np.isfinite(x)]
    return float(np.median(x)) if len(x) else None


def label_regimes(trades: pd.DataFrame, candles15: Candles, vol_median_: float | None) -> pd.DataFrame:
    """A copy of ``trades`` with ``regime_trend`` and ``regime_vol`` filled from ``entry_idx``."""
    out = trades.copy()
    e = out["entry_idx"].to_numpy(dtype=np.int64)
    adx = daily_adx_aligned(candles15)[e]
    vol = daily_vol_asof(candles15)[e]
    out["regime_trend"] = pd.Series(
        [None if np.isnan(a) else ("trend" if a > TREND_ADX else "no_trend") for a in adx], index=out.index,
        dtype=object)
    out["regime_vol"] = pd.Series(
        [None if np.isnan(v) or vol_median_ is None else ("high" if v > vol_median_ else "low") for v in vol],
        index=out.index, dtype=object)
    return out
