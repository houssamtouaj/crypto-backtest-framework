"""Per-pair calendar-day arrays from 15m candles and funding (trend spec Phase 1 §1.1).

A DayFrame starts on the pair's configured first day (``max(listing,
warmup_start)``, never the first stored candle: the shared BTC/ETH store
starts earlier) and ends on the day of its last 15m candle. Every calendar
day in between is a row; a day with no candle is ``filled`` with the
previous close. Funding events after the last candle's open are dropped
(a delisted contract's archive keeps publishing flat rates). ``ret[0]`` is
NaN: the day before the first day is not part of the pair's history.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import numpy as np

from perpbt.config import DataConfig
from perpbt.data.store import DAY_MS, CandleStore, Candles, Funding, FundingStore, date_ms
from perpbt.indicators.daily import daily_bars

log = logging.getLogger(__name__)

FILL_OFFSETS_MIN: tuple[int, ...] = (15, 60, 240, 480)  # 00:15 primary; 01:00, 04:00, 08:00 robustness rows
MAX_FILLED_RUN = 5  # a longer run of filled days is warned about and reported
_MIN_MS = 60_000


@dataclass(frozen=True, eq=False)  # eq=False: the generated __eq__ raises on ndarray fields
class DayFrame:
    """One pair's daily arrays, one row per calendar day; arrays are read-only.

    ``l`` is the daily low (the liquidation check's price). ``fill_px[h]`` is
    the close of the 15m candle closing at ``day + h`` minutes (NaN when that
    candle is missing). Funding events are flat arrays sorted by time; day
    ``k``'s are ``[funding_ptr[k], funding_ptr[k + 1])`` and ``funding_px``
    is the open of the 15m candle starting at the event (NaN when missing).
    ``filled_runs`` lists every run of filled days as ``(first day_ms, length)``.
    """

    pair: str
    day_ms: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741 - column name from the spec
    c: np.ndarray
    filled: np.ndarray
    funding: np.ndarray
    ret: np.ndarray
    fill_px: dict[int, np.ndarray]
    funding_ts: np.ndarray
    funding_rate: np.ndarray
    funding_px: np.ndarray
    funding_ptr: np.ndarray
    last_candle_ms: int
    filled_runs: tuple[tuple[int, int], ...]

    def __len__(self) -> int:
        return len(self.day_ms)

    def events(self, k: int) -> slice:
        """Indices of day ``k``'s funding events in ``funding_ts`` / ``funding_rate`` / ``funding_px``."""
        return slice(int(self.funding_ptr[k]), int(self.funding_ptr[k + 1]))


def _frozen(arr: np.ndarray) -> np.ndarray:
    arr.setflags(write=False)
    return arr


def filled_runs(day_ms: np.ndarray, filled: np.ndarray) -> tuple[tuple[int, int], ...]:
    """``(first day_ms, length)`` of every run of consecutive True in ``filled``."""
    edges = np.diff(np.concatenate(([0], np.asarray(filled, dtype=np.int8), [0])))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return tuple((int(day_ms[s]), int(e - s)) for s, e in zip(starts, ends))


def _px_at(candles: Candles, ts: np.ndarray, values: np.ndarray) -> np.ndarray:
    """``values`` of the candle opening exactly at each ``ts``; NaN where there is none."""
    i = np.searchsorted(candles.ts, ts)
    ok = i < len(candles)
    ok[ok] = candles.ts[i[ok]] == ts[ok]
    out = np.full(len(ts), np.nan)
    out[ok] = values[i[ok]]
    return out


def build_dayframe(
    candles15: Candles, funding: Funding, *, first_day: str, fill_offsets_min: tuple[int, ...] = FILL_OFFSETS_MIN,
) -> DayFrame:
    """The DayFrame of one pair from ``first_day`` (ISO date) to the day of its last candle."""
    if candles15.tf != "15m":
        raise ValueError(f"a DayFrame is built from 15m candles, got {candles15.tf!r}")
    offsets = tuple(int(h) for h in fill_offsets_min)
    bad = [h for h in offsets if h % 15 or not 15 <= h <= 1440]
    if bad:
        raise ValueError(f"fill offsets must be multiples of 15 minutes in [15, 1440], got {bad}")
    pair = candles15.pair
    first_ms = date_ms(first_day)
    cd = candles15.slice(first_ms, int(candles15.ts[-1]) + 1) if len(candles15) else candles15
    if len(cd) == 0 or cd.ts[0] >= first_ms + DAY_MS:
        raise ValueError(f"{pair}: no 15m candle on the first day {first_day}")
    last_ms = int(cd.ts[-1])
    n = (last_ms - first_ms) // DAY_MS + 1
    day_ms = first_ms + DAY_MS * np.arange(n, dtype=np.int64)

    bars = daily_bars(cd)
    has = np.zeros(n, dtype=bool)
    has[(bars.day_ms - first_ms) // DAY_MS] = True
    bar = np.cumsum(has) - 1  # the bar of the day, or of the last day with candles
    c = bars.c[bar]
    o = np.where(has, bars.o[bar], c)
    h = np.where(has, bars.h[bar], c)
    lo = np.where(has, bars.l[bar], c)
    filled = ~has

    sel = (funding.ts >= first_ms) & (funding.ts <= last_ms)
    fts = funding.ts[sel].astype(np.int64)
    frate = funding.rate[sel].astype(np.float64)
    fsum = np.zeros(n)
    np.add.at(fsum, (fts - first_ms) // DAY_MS, frate)
    ptr = np.searchsorted(fts, np.append(day_ms, day_ms[-1] + DAY_MS), side="left").astype(np.int64)

    ret = np.full(n, np.nan)
    ret[1:] = c[1:] / c[:-1] - 1.0 - fsum[1:]
    fill_px = {
        hh: _frozen(_px_at(cd, day_ms + (hh - 15) * _MIN_MS, cd.c)) for hh in offsets
    }
    runs = filled_runs(day_ms, filled)
    for start, length in runs:
        if length > MAX_FILLED_RUN:
            log.warning("%s: %d consecutive days without a 15m candle from %s", pair, length,
                        time.strftime("%Y-%m-%d", time.gmtime(start // 1000)))
    return DayFrame(
        pair, _frozen(day_ms), _frozen(o), _frozen(h), _frozen(lo), _frozen(c), _frozen(filled), _frozen(fsum),
        _frozen(ret), fill_px, _frozen(fts), _frozen(frate), _frozen(_px_at(cd, fts, cd.o)), _frozen(ptr),
        last_ms, runs,
    )


def first_day(data_cfg: DataConfig, pair: str) -> str:
    """A pair's first day: the later of its ``listing`` date and ``warmup_start``."""
    if pair not in data_cfg.listing:
        raise ValueError(f"{pair} is not in the data config's listing {sorted(data_cfg.listing)}")
    return max(data_cfg.listing[pair], data_cfg.warmup_start)


def load_dayframe(
    data_cfg: DataConfig, pair: str, *, end: str, market: str = "um", allow_holdout: bool = False,
    fill_offsets_min: tuple[int, ...] = FILL_OFFSETS_MIN,
) -> DayFrame:
    """The DayFrame of ``pair`` from its first day through ``end`` (ISO date, inclusive).

    Perps load 15m candles and funding; spot has no funding (empty events).
    ``allow_holdout`` goes to the stores unchanged: the spot store refuses
    without it every load that starts before 2020-01-01 (the trend spot holdout).
    """
    first = first_day(data_cfg, pair)
    start_ms, end_ms = date_ms(first), date_ms(end) + DAY_MS
    candles = CandleStore(data_cfg, market=market).load(pair, "15m", start_ms, end_ms, allow_holdout=allow_holdout)
    if market == "um":
        funding = FundingStore(data_cfg).load(pair, start_ms, end_ms, allow_holdout=allow_holdout)
    else:
        z = np.zeros(0)
        funding = Funding(pair, z.astype(np.int64), z, z.astype(np.int8))
    return build_dayframe(candles, funding, first_day=first, fill_offsets_min=fill_offsets_min)
