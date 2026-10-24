"""ETF signals (ETF spec Phase 1 §1.4-§1.5): the crypto trend members, exposure, √252 vol forecast, target weights.

The member functions and ``target_weight`` are imported from
``perpbt/trend/signals.py``, never copied or edited (a test checks they are
the same objects). They are pure functions of the frame's ``c, h, l`` arrays,
so they run on an ``EtfFrame`` as they do on a crypto ``DayFrame``; lookbacks
count rows, here US trading days. Members read the split-adjusted close (a
price trend, not a total-return index), so the Donchian member's high and low
stay consistent with its close.

``σ̂`` is this module's ``vol_forecast252``: ``trend.signals.vol_forecast``
hard-codes √365. ``target_weight`` reads only ``target_vol`` and
``max_weight`` of its sizing argument, so it takes an ``EtfSizingConfig``;
``target_vol`` None (the ``no_vol_target`` row) cannot go through it and
gives ``w* = e × max_weight``.

Only long/flat is supported: a cash account cannot short (ED13).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from perpbt.data.store import date_ms
from perpbt.etf.config import ANNUALISE_DAYS, EtfSizingConfig
from perpbt.etf.frame import EtfFrame
from perpbt.etf.store import day_iso
from perpbt.trend.config import SignalConfig
from perpbt.trend.signals import donchian, ma_filter, return_sign, target_weight

ANNUALISE = np.sqrt(float(ANNUALISE_DAYS))  # trading days

__all__ = ["ANNUALISE", "EtfSignals", "compute_signals", "donchian", "etf_target_weight", "first_valid_index",
           "ma_filter", "member_matrix", "return_sign", "summarise", "target_weight", "vol_forecast252"]


def vol_forecast252(ret: np.ndarray, window: int) -> np.ndarray:
    """``σ̂_d = std(ret_{d−window+1..d}, ddof=1) × √252``; NaN until ``window`` finite returns exist."""
    ret = np.asarray(ret, dtype=np.float64)
    out = np.full(len(ret), np.nan)
    if len(ret) >= window:
        out[window - 1:] = sliding_window_view(ret, window).std(axis=1, ddof=1) * ANNUALISE
    return out


def first_valid_index(signal: SignalConfig, sizing: EtfSizingConfig) -> int:
    """The first row index where every lookback has its ``N`` prior bars and ``σ̂`` its window (``ret[0]`` is NaN)."""
    return max(max(signal.lookbacks), sizing.vol_window)


def member_matrix(frame: EtfFrame, signal: SignalConfig) -> tuple[tuple[str, ...], np.ndarray]:
    """Every member at every lookback, long/flat: names ``f"{member}_{n}"`` and values of shape ``(K, D)``."""
    names, rows = [], []
    for member in signal.members:
        for n in signal.lookbacks:
            if member == "donchian":
                row = donchian(frame.c, frame.h, frame.l, n)
            elif member == "return_sign":
                row = return_sign(frame.c, n)
            else:
                row = ma_filter(frame.c, n)
            names.append(f"{member}_{n}")
            rows.append(row)
    return tuple(names), np.vstack(rows)


def etf_target_weight(e: np.ndarray, sigma: np.ndarray, sizing: EtfSizingConfig) -> np.ndarray:
    """``w* = e × min(target_vol / σ̂, max_weight)``, or ``e × max_weight`` without a target; NaN where ``σ̂`` is."""
    if sizing.target_vol is not None:
        return target_weight(e, sigma, sizing)
    w = np.asarray(e, dtype=np.float64) * sizing.max_weight
    return np.where(np.isnan(np.asarray(sigma, dtype=np.float64)), np.nan, w)


@dataclass(frozen=True, eq=False)  # eq=False: the generated __eq__ raises on ndarray fields
class EtfSignals:
    """One ETF's signals per frame row. ``exposure`` and ``weight`` are NaN where not ``valid``."""

    ticker: str
    day_ms: np.ndarray
    names: tuple[str, ...]
    members: np.ndarray  # (K, D)
    exposure: np.ndarray
    sigma: np.ndarray
    weight: np.ndarray
    valid: np.ndarray

    @property
    def first_valid_ms(self) -> int:
        if not self.valid.any():
            raise ValueError(f"EtfSignals({self.ticker}): no valid day")
        return int(self.day_ms[np.argmax(self.valid)])


def compute_signals(frame: EtfFrame, signal: SignalConfig, sizing: EtfSizingConfig) -> EtfSignals:
    """Members, exposure ``e``, ``σ̂`` and ``w*`` for every row of ``frame`` (long/flat only)."""
    if signal.direction != "long_flat":
        raise ValueError(f"compute_signals: the ETF strategy is long_flat only, got {signal.direction!r}")
    names, members = member_matrix(frame, signal)
    sigma = vol_forecast252(frame.ret, sizing.vol_window)
    valid = (np.arange(len(frame)) >= first_valid_index(signal, sizing)) & np.isfinite(sigma)
    exposure = np.where(valid, members.mean(axis=0), np.nan)
    weight = np.where(valid, etf_target_weight(exposure, sigma, sizing), np.nan)
    return EtfSignals(frame.ticker, frame.day_ms, names, members, exposure, sigma, weight, valid)


def summarise(signals: Mapping[str, EtfSignals], start: str, end: str) -> dict[str, dict]:
    """Descriptive signal facts per ticker over ``start <= day <= end`` (Phase 1 §1.7); no returns.

    ``exposure_hist[k]`` counts the days with exposure ``k/12``. Raises when a
    day in the window is not valid (the period must start after the warmup).
    """
    lo, hi = date_ms(start), date_ms(end)
    out = {}
    for t, s in signals.items():
        window = (s.day_ms >= lo) & (s.day_ms <= hi)
        if not window.any():
            raise ValueError(f"summarise({t}): no day in {start}..{end}")
        if not s.valid[window].all():
            bad = day_iso(s.day_ms[window & ~s.valid][0])
            raise ValueError(f"summarise({t}): {bad} is not valid (the warmup reaches into {start}..{end})")
        e, w = s.exposure[window], s.weight[window]
        k = np.rint(e * 12).astype(int)
        out[t] = {
            "first_valid": day_iso(s.first_valid_ms), "days": int(window.sum()),
            "exposure_hist": np.bincount(k, minlength=13).tolist(),
            "mean_exposure": float(e.mean()), "mean_weight": float(w.mean()),
        }
    return out
