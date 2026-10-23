"""Trailing funding and the on/off state (carry spec Phase 1 §1.2-§1.3).

``f̄_d`` is the mean of the pair's realised funding rates on days
``d-W+1 .. d``, each event first normalised to a per-day rate
(``rate × 24 / interval_h``, so a pair that moved to 4 h funding is
annualised correctly), times 365. It is NaN until the window lies inside the
frame and wherever the window holds no event. The state turns on above
``entry``, off below ``exit``, otherwise keeps the previous day's (initially
off); NaN is off. ``on[d]`` is the decision at day ``d``'s close for the fill
on ``d + 1``.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from perpbt.carry.config import CarrySignalConfig
from perpbt.carry.frames import PairFrame

DAYS_PER_YEAR = 365


@dataclass(frozen=True, eq=False)  # eq=False: the generated __eq__ raises on ndarray fields
class CarrySignals:
    """``fbar``: annualised trailing funding per day; ``on``: the state decided at each day's close."""

    fbar: np.ndarray
    on: np.ndarray


def _frozen(arr: np.ndarray) -> np.ndarray:
    arr.setflags(write=False)
    return arr


def trailing_funding(ptr: np.ndarray, rate: np.ndarray, interval_h: np.ndarray, lookback_days: int) -> np.ndarray:
    """``f̄`` per day from flat funding events; day ``k``'s events are ``[ptr[k], ptr[k + 1])``."""
    ptr = np.asarray(ptr, dtype=np.int64)
    rate = np.asarray(rate, dtype=np.float64)
    interval_h = np.asarray(interval_h)
    if lookback_days < 1:
        raise ValueError(f"lookback_days must be >= 1, got {lookback_days}")
    if not (len(rate) == len(interval_h) == ptr[-1]) or ptr[0] != 0 or np.any(np.diff(ptr) < 0):
        raise ValueError("ptr must run from 0 to the number of events, non-decreasing, with rate and interval_h aligned")
    n = len(ptr) - 1
    per_day = rate * (24.0 / interval_h.astype(np.float64))
    day = np.repeat(np.arange(n), np.diff(ptr))
    sums = np.zeros(n)
    np.add.at(sums, day, per_day)
    counts = np.diff(ptr).astype(np.float64)
    out = np.full(n, np.nan)
    if n >= lookback_days:
        s = sliding_window_view(sums, lookback_days).sum(axis=1)
        c = sliding_window_view(counts, lookback_days).sum(axis=1)
        has = c > 0
        tail = np.full(len(s), np.nan)
        tail[has] = s[has] / c[has] * DAYS_PER_YEAR
        out[lookback_days - 1:] = tail
    return out


def on_off(fbar: np.ndarray, entry: float, exit: float) -> np.ndarray:
    """On when ``fbar > entry``, off when ``fbar < exit``, else the previous state; initially off; NaN is off."""
    if exit > entry:
        raise ValueError(f"exit ({exit}) must be <= entry ({entry})")
    on = np.zeros(len(fbar), dtype=bool)
    state = False
    for k, f in enumerate(np.asarray(fbar, dtype=np.float64)):
        if np.isnan(f) or f < exit:
            state = False
        elif f > entry:
            state = True
        on[k] = state
    return on


def compute_signals(pf: PairFrame, cfg: CarrySignalConfig) -> CarrySignals:
    """``f̄`` and the on/off state of one pair from its perp leg's funding events."""
    fbar = trailing_funding(pf.perp.funding_ptr, pf.perp.funding_rate, pf.funding_interval_h, cfg.lookback_days)
    return CarrySignals(_frozen(fbar), _frozen(on_off(fbar, cfg.entry_annual, cfg.exit_annual)))
