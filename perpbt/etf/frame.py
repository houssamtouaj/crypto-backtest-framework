"""The ETF day frame (ETF spec Phase 1 §1.1-§1.3): one ticker's read-only arrays, one row per trading day.

Rows run from ``warmup_start`` to the period end; day ``d`` is a NYSE trading
day keyed by its date at 00:00 UTC. With ``C_{d−1}`` the previous close,
``O_d``, ``C_d`` today's open and close and ``D_d`` the dividend going ex
today (§1.2)::

    r_on_d = (O_d + D_d) / C_{d−1} − 1     the holder at yesterday's close is owed the dividend at the open
    r_id_d = C_d / O_d − 1                 what a position bought at today's open earns today
    ret_d  = (1 + r_on_d)(1 + r_id_d) − 1  (validate.total_return; signals and σ̂ use this)

and the T-bill cash return (§1.3), ``cash_d = DTB3_{d−1} / 100 × days(d−1 → d) / 360``,
with ``DTB3_{d−1}`` the last rate published on or before the previous trading
day, used as published (negative prints included). Index 0 of every return is
NaN. Prices are Yahoo's split-adjusted series, so a split day needs nothing
here (the IWM 2:1 split of 2005-06-09 is inside the warmup).

``stale_open`` flags ``O_d == C_{d−1}``: about 1% of Yahoo's opens copy the
previous close (Phase 0 §0.9); the prices are kept, the flag lets later
phases count them.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields

import numpy as np
import pandas as pd

from perpbt.data.store import DAY_MS, date_ms
from perpbt.etf.config import EtfDataConfig
from perpbt.etf.store import EtfStore, day_iso
from perpbt.etf.validate import total_return

MAX_RATE_AGE_DAYS = 10  # a cash day refuses a rate older than this (rule 7 caps real gaps at 5 business days)
RATE_LOOKBACK_DAYS = 14  # rates are loaded from this many calendar days before the first row

__all__ = ["EtfFrame", "MAX_RATE_AGE_DAYS", "RATE_LOOKBACK_DAYS", "build_frame", "cash_returns", "check_aligned",
           "load_frames"]


@dataclass(frozen=True, eq=False)  # eq=False: the generated __eq__ raises on ndarray fields
class EtfFrame:
    """One ticker's trading-day arrays (read-only)."""

    ticker: str
    day_ms: np.ndarray  # int64, the date at 00:00 UTC
    o: np.ndarray
    h: np.ndarray  # high and low feed the Donchian member only
    l: np.ndarray  # noqa: E741
    c: np.ndarray
    div: np.ndarray  # cash dividend going ex this day, 0 if none
    ret: np.ndarray  # close-to-close total return
    r_on: np.ndarray  # previous close to this open, dividend included
    r_id: np.ndarray  # this open to this close
    cash: np.ndarray  # T-bill return from the previous trading day's close to this one
    stale_open: np.ndarray  # bool: open equals the previous close

    def __post_init__(self) -> None:
        for f in fields(self):
            a = getattr(self, f.name)
            if isinstance(a, np.ndarray):
                a.setflags(write=False)

    def __len__(self) -> int:
        return len(self.day_ms)


def cash_returns(day_ms: np.ndarray, rates: pd.DataFrame) -> np.ndarray:
    """``cash_d = rate_{d−1} / 100 × days(d−1 → d) / 360`` per trading day; ``cash[0]`` NaN.

    ``rates`` has ``day_ms`` (published days, increasing) and ``rate_pct``.
    Raises when a step has no rate on or before ``d−1``, or only one older than
    ``MAX_RATE_AGE_DAYS`` calendar days.
    """
    day_ms = np.asarray(day_ms, dtype=np.int64)
    out = np.full(len(day_ms), np.nan)
    if len(day_ms) < 2:
        return out
    rate_ms = rates["day_ms"].to_numpy(np.int64)
    rate = rates["rate_pct"].to_numpy(np.float64)
    prev = day_ms[:-1]
    k = np.searchsorted(rate_ms, prev, side="right") - 1
    if (k < 0).any():
        raise ValueError(f"cash_returns: no published rate on or before {day_iso(prev[np.argmax(k < 0)])}")
    age = (prev - rate_ms[k]) // DAY_MS
    if (age > MAX_RATE_AGE_DAYS).any():
        j = int(np.argmax(age > MAX_RATE_AGE_DAYS))
        raise ValueError(f"cash_returns: the rate for {day_iso(prev[j])} is {int(age[j])} days old "
                         f"(more than {MAX_RATE_AGE_DAYS})")
    out[1:] = rate[k] / 100.0 * (np.diff(day_ms) // DAY_MS) / 360.0
    return out


def build_frame(ticker: str, prices: pd.DataFrame, rates: pd.DataFrame, *, first_day: str) -> EtfFrame:
    """The frame from ``EtfStore.load`` rows (prices with actions) and the published rates.

    The first row must be ``first_day`` (the config's ``warmup_start``), not
    whatever the store happens to start with.
    """
    if len(prices) == 0 or int(prices["day_ms"].iloc[0]) != date_ms(first_day):
        first = day_iso(prices["day_ms"].iloc[0]) if len(prices) else None
        raise ValueError(f"build_frame({ticker}): the first row is {first}, expected the first day {first_day}")
    day_ms = prices["day_ms"].to_numpy(np.int64)
    o, h, l, c = (prices[k].to_numpy(np.float64) for k in ("open", "high", "low", "close"))  # noqa: E741
    div = prices["dividend"].to_numpy(np.float64)
    px = np.concatenate((o, h, l, c))
    if not (np.isfinite(px).all() and (px > 0).all()):
        raise ValueError(f"build_frame({ticker}): prices must be positive and finite")
    if not (np.isfinite(div).all() and (div >= 0).all()):
        raise ValueError(f"build_frame({ticker}): dividends must be finite and non-negative")
    r_on = np.full(len(c), np.nan)
    r_id = np.full(len(c), np.nan)
    r_on[1:] = (o[1:] + div[1:]) / c[:-1] - 1.0
    r_id[1:] = c[1:] / o[1:] - 1.0
    stale = np.zeros(len(c), dtype=bool)
    stale[1:] = o[1:] == c[:-1]
    return EtfFrame(ticker, day_ms, o, h, l, c, div, total_return(o, c, div), r_on, r_id,
                    cash_returns(day_ms, rates), stale)


def check_aligned(frames: Mapping[str, EtfFrame]) -> None:
    """Every frame has the same trading days; raises naming the first ticker and date that differ."""
    items = list(frames.items())
    if not items:
        return
    ref_t, ref = items[0]
    for t, f in items[1:]:
        if len(f) == len(ref) and np.array_equal(f.day_ms, ref.day_ms):
            continue
        n = min(len(f), len(ref))
        diff = np.flatnonzero(f.day_ms[:n] != ref.day_ms[:n])
        at = diff[0] if len(diff) else n
        day = day_iso((ref.day_ms if at < len(ref) else f.day_ms)[at])
        raise ValueError(f"check_aligned: {t}'s trading days differ from {ref_t}'s at {day}")


def load_frames(store: EtfStore, cfg: EtfDataConfig, *, end: str, allow_holdout: bool = False) -> dict[str, EtfFrame]:
    """Every configured ticker's frame from ``warmup_start`` through ``end``, aligned.

    ``allow_holdout`` goes to the store unchanged (its guard refuses an ``end``
    on or after 2018-01-01 without it). An ``end`` past the store's
    ``holdout_end`` is refused: the data would stop short of it silently.
    """
    manifest = store.manifest()
    if manifest is not None and end > manifest["holdout_end"]:
        raise ValueError(f"load_frames: end {end} is after the store's holdout_end {manifest['holdout_end']}")
    start = cfg.warmup_start
    rates_from = day_iso(date_ms(start) - RATE_LOOKBACK_DAYS * DAY_MS)
    rates = store.load_rates(rates_from, end, allow_holdout=allow_holdout)
    frames = {t: build_frame(t, store.load(t, start, end, allow_holdout=allow_holdout), rates, first_day=start)
              for t in cfg.tickers}
    check_aligned(frames)
    return frames
