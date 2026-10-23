"""PairFrame: one pair's perp and spot DayFrames on one calendar (carry spec Phase 1 §1.1).

Both legs are trend DayFrames (``trend.daily.build_dayframe``, the builder
of ``load_dayframe``). The frame runs from the pair's first day (``listing``,
as trend) to the day of ``last_common_ms``, the last 15m slot where both
legs have a candle; both legs' candles are cut there before the DayFrames
are built, so the last day's bars of both legs stop at that candle and the
perp's funding events after it are dropped.

The perp leg's ``DayFrame.ret`` subtracts funding for a long; carry code
never uses it: the perp's return is its price return and funding is added
explicitly.

``load_pairframe`` refuses an ``end`` after ``CARRY_INSAMPLE_END`` without
``allow_holdout`` whatever the ``DataConfig`` (Phase 0 §0.2): the trend perp
config lets perps load through 2026-10-20.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from perpbt.config import DataConfig
from perpbt.data.store import DAY_MS, CandleStore, Candles, Funding, FundingStore, HoldoutAccessError, date_ms
from perpbt.trend.daily import FILL_OFFSETS_MIN, DayFrame, build_dayframe, first_day

CARRY_INSAMPLE_END = "2023-12-31"  # inclusive; the pre-registered in-sample end (spec overview CD5)
LEGS = ("perp", "spot")
_STEP_MS = 900_000
_MIN_MS = 60_000


@dataclass(frozen=True, eq=False)  # eq=False: the generated __eq__ raises on ndarray fields
class PairFrame:
    """One pair's two legs, one row per calendar day; arrays are read-only.

    ``basis`` is ``perp.c / spot.c - 1`` at the day close. ``filled`` is true
    where either leg is filled. ``fill_ok[h]`` is true where both legs have the
    candle closing at ``day + h`` minutes. ``fill_px_late[leg][h]`` is the close
    of the first candle of the day that closes at or after ``day + h`` and
    ``fill_late_ms[leg][h]`` its close time (the fill time; ``day + h`` when on
    time), both NaN when the day has none. ``funding_interval_h`` is the
    published interval of each of ``perp.funding_ts``'s events.
    """

    pair: str
    day_ms: np.ndarray
    perp: DayFrame
    spot: DayFrame
    basis: np.ndarray
    filled: np.ndarray
    fill_ok: dict[int, np.ndarray]
    fill_px_late: dict[str, dict[int, np.ndarray]]
    fill_late_ms: dict[str, dict[int, np.ndarray]]
    funding_interval_h: np.ndarray
    last_common_ms: int

    def __len__(self) -> int:
        return len(self.day_ms)


def _frozen(arr: np.ndarray) -> np.ndarray:
    arr.setflags(write=False)
    return arr


def _late_fills(candles: Candles, day_ms: np.ndarray, h: int) -> tuple[np.ndarray, np.ndarray]:
    """Close and close time of each day's first candle closing at or after ``day + h``; NaN when none."""
    i = np.searchsorted(candles.ts, day_ms + (h - 15) * _MIN_MS, side="left")
    ok = i < len(candles)
    ok[ok] = candles.ts[i[ok]] < day_ms[ok] + DAY_MS
    px, at = np.full(len(day_ms), np.nan), np.full(len(day_ms), np.nan)
    px[ok] = candles.c[i[ok]]
    at[ok] = candles.ts[i[ok]] + _STEP_MS
    return px, at


def build_pairframe(
    perp: Candles, funding: Funding, spot: Candles, *, first_day: str,
    fill_offsets_min: tuple[int, ...] = FILL_OFFSETS_MIN,
) -> PairFrame:
    """The PairFrame of one pair from ``first_day`` (ISO date) to the day of its last common candle."""
    if not perp.pair == spot.pair == funding.pair:
        raise ValueError(f"perp, spot and funding must be the same pair, got {perp.pair}, {spot.pair}, {funding.pair}")
    first_ms = date_ms(first_day)
    common = np.intersect1d(perp.ts[perp.ts >= first_ms], spot.ts[spot.ts >= first_ms], assume_unique=True)
    if len(common) == 0:
        raise ValueError(f"{perp.pair}: the perp and spot legs have no common 15m candle from {first_day}")
    last = int(common[-1])
    perp = perp.slice(int(perp.ts[0]), last + 1)
    spot = spot.slice(int(spot.ts[0]), last + 1)
    z = np.zeros(0)
    no_funding = Funding(spot.pair, z.astype(np.int64), z, z.astype(np.int8))
    p = build_dayframe(perp, funding, first_day=first_day, fill_offsets_min=fill_offsets_min)
    s = build_dayframe(spot, no_funding, first_day=first_day, fill_offsets_min=fill_offsets_min)
    if not np.array_equal(p.day_ms, s.day_ms):  # both legs end at ``last``: cannot happen
        raise AssertionError(f"{perp.pair}: the legs' calendars differ after the cut")

    k = np.searchsorted(funding.ts, p.funding_ts)
    if not np.array_equal(funding.ts[k], p.funding_ts):
        raise AssertionError(f"{perp.pair}: the DayFrame's funding events are not the loaded ones")
    late_px: dict[str, dict[int, np.ndarray]] = {leg: {} for leg in LEGS}
    late_ms: dict[str, dict[int, np.ndarray]] = {leg: {} for leg in LEGS}
    for leg, candles in (("perp", perp), ("spot", spot)):
        for h in p.fill_px:
            px, at = _late_fills(candles, p.day_ms, h)
            late_px[leg][h], late_ms[leg][h] = _frozen(px), _frozen(at)
    fill_ok = {h: _frozen(np.isfinite(p.fill_px[h]) & np.isfinite(s.fill_px[h])) for h in p.fill_px}
    return PairFrame(
        perp.pair, p.day_ms, p, s, _frozen(p.c / s.c - 1.0), _frozen(p.filled | s.filled), fill_ok, late_px, late_ms,
        _frozen(funding.interval_h[k].copy()), last,
    )


def load_pairframe(
    data_cfg: DataConfig, pair: str, *, end: str, allow_holdout: bool = False,
    fill_offsets_min: tuple[int, ...] = FILL_OFFSETS_MIN,
) -> PairFrame:
    """The PairFrame of ``pair`` from its first day through ``end`` (ISO date, inclusive).

    Refuses ``end`` after ``CARRY_INSAMPLE_END`` unless ``allow_holdout`` (only
    the carry holdout and forward runs pass it), before reading anything;
    ``allow_holdout`` also goes to the stores unchanged.
    """
    if date_ms(end) > date_ms(CARRY_INSAMPLE_END) and not allow_holdout:
        raise HoldoutAccessError(
            f"{pair}: end {end} is after the carry in-sample end {CARRY_INSAMPLE_END}; "
            "only the carry holdout and forward runs may load past it (allow_holdout=True)"
        )
    first = first_day(data_cfg, pair)
    start_ms, end_ms = date_ms(first), date_ms(end) + DAY_MS
    perp = CandleStore(data_cfg, market="um").load(pair, "15m", start_ms, end_ms, allow_holdout=allow_holdout)
    funding = FundingStore(data_cfg).load(pair, start_ms, end_ms, allow_holdout=allow_holdout)
    spot = CandleStore(data_cfg, market="spot").load(pair, "15m", start_ms, end_ms, allow_holdout=allow_holdout)
    return build_pairframe(perp, funding, spot, first_day=first, fill_offsets_min=fill_offsets_min)
