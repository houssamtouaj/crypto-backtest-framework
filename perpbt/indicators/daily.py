"""Completed-day SMA and ADX aligned to the 15m index (spec §2.3, D7).

A UTC day's bar is built from the 15m candles of that day present in the
data: open of the first, max high, min low, close of the last, sum of
volume. Outage gaps inside a day do not disqualify it; a day with no candle
at all has no bar. For every 15m candle of day ``D`` the aligned value is
the indicator computed on the bars of the days before ``D`` only, so it is
constant through the day, changes at 00:00 UTC, and the last day of the
series (never completed) is never used.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from perpbt.data.store import DAY_MS, Candles
from perpbt.indicators.atr import check_period, true_range, wilder_rma


@dataclass(frozen=True, eq=False)  # eq=False: the generated __eq__ raises on ndarray fields
class DailyBars:
    """UTC daily bars. ``day_ms`` is the day's 00:00 UTC in ms, strictly increasing."""

    day_ms: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741 - column name from the spec
    c: np.ndarray
    v: np.ndarray

    def __len__(self) -> int:
        return len(self.day_ms)


def _day_of(ts: np.ndarray) -> np.ndarray:
    return (np.asarray(ts, dtype=np.int64) // DAY_MS) * DAY_MS


def daily_bars(candles15: Candles) -> DailyBars:
    """One bar per UTC day that has at least one 15m candle."""
    if candles15.tf != "15m":
        raise ValueError(f"daily bars are built from 15m candles, got {candles15.tf!r}")
    days = _day_of(candles15.ts)
    if len(days) == 0:
        z = np.zeros(0)
        return DailyBars(np.zeros(0, dtype=np.int64), z, z, z, z, z)
    day_ms, starts = np.unique(days, return_index=True)
    ends = np.append(starts[1:], len(days)) - 1
    return DailyBars(
        day_ms.astype(np.int64),
        candles15.o[starts].astype(np.float64),
        np.maximum.reduceat(candles15.h, starts).astype(np.float64),
        np.minimum.reduceat(candles15.l, starts).astype(np.float64),
        candles15.c[ends].astype(np.float64),
        np.add.reduceat(candles15.v, starts).astype(np.float64),
    )


def daily_sma(bars: DailyBars, n: int = 50) -> np.ndarray:
    """SMA(n) of daily closes per bar; NaN before bar ``n - 1``."""
    n = check_period(n)
    out = np.full(len(bars), np.nan)
    if len(bars) >= n:
        out[n - 1 :] = sliding_window_view(bars.c, n).mean(axis=1)
    return out


def daily_adx(bars: DailyBars, n: int = 14) -> np.ndarray:
    """Wilder ADX(n) per bar; NaN before bar ``2n - 1``.

    +DM/−DM from consecutive bars, Wilder RMA(n) of TR, +DM, −DM over bars
    ``1..``; ``DI± = 100 × RMA(±DM) / RMA(TR)`` (0 when ``RMA(TR) = 0``);
    ``DX = 100 × |DI+ − DI−| / (DI+ + DI−)`` (0 when the sum is 0);
    ``ADX = RMA(n)(DX)`` from the first valid DX (bar ``n``).
    """
    n = check_period(n)
    m = len(bars)
    if m < 2 * n:
        return np.full(m, np.nan)
    h, l, c = bars.h, bars.l, bars.c  # noqa: E741
    tr = true_range(h, l, c)
    up = np.diff(h)
    down = -np.diff(l)
    pdm = np.zeros(m)
    mdm = np.zeros(m)
    pdm[1:] = np.where((up > down) & (up > 0), up, 0.0)
    mdm[1:] = np.where((down > up) & (down > 0), down, 0.0)
    rtr = wilder_rma(tr, n, start=1)
    rp = wilder_rma(pdm, n, start=1)
    rm = wilder_rma(mdm, n, start=1)
    valid = slice(n, m)  # first RMA value is at bar 1 + n - 1 = n
    safe_tr = np.where(rtr[valid] > 0, rtr[valid], 1.0)
    dip = np.where(rtr[valid] > 0, 100.0 * rp[valid] / safe_tr, 0.0)
    dim = np.where(rtr[valid] > 0, 100.0 * rm[valid] / safe_tr, 0.0)
    total = dip + dim
    dx = np.full(m, np.nan)
    dx[valid] = np.where(total > 0, 100.0 * np.abs(dip - dim) / np.where(total > 0, total, 1.0), 0.0)
    return wilder_rma(dx, n, start=n)


def _align(candles15: Candles, bars: DailyBars, per_bar: np.ndarray) -> np.ndarray:
    """Value on each candle = ``per_bar`` at the last bar strictly before the candle's day."""
    pos = np.searchsorted(bars.day_ms, _day_of(candles15.ts), side="left")  # the candle's own bar
    out = np.full(len(candles15), np.nan)
    has_prev = pos >= 1
    out[has_prev] = per_bar[pos[has_prev] - 1]
    return out


def daily_sma_aligned(candles15: Candles, n: int = 50) -> np.ndarray:
    """SMA(n) of completed UTC daily closes, aligned to the 15m index. Lag 0 at the 15m level.

    The value on every candle of day ``D`` is the mean close of the ``n``
    present days before ``D``; NaN until ``n`` such days exist.
    """
    bars = daily_bars(candles15)
    return _align(candles15, bars, daily_sma(bars, n))


def daily_adx_aligned(candles15: Candles, n: int = 14) -> np.ndarray:
    """Wilder ADX(n) of completed UTC days, aligned to the 15m index. Lag 0 at the 15m level.

    The value on every candle of day ``D`` is the ADX on the present days
    before ``D``; NaN until ``2n`` such days exist (warmup ``2n - 1``).
    """
    bars = daily_bars(candles15)
    return _align(candles15, bars, daily_adx(bars, n))
