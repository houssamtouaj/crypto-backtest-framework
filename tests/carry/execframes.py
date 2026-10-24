"""PairFrames from per-day prices for the executor tests (synthetic; nothing here touches market data).

On day ``k`` of a leg the 00:00 candle opens at the previous close and closes at ``fill[k]`` (the 00:15 fill
price), every later candle is flat at ``close[k]``, and the 12:00 candle's high is ``high[k]`` when given. So the
00:00 funding event is priced at the previous close and the 08:00 and 16:00 events at ``close[k]``.
"""
from __future__ import annotations

import numpy as np

from perpbt.carry.config import CarryCostConfig, CarryRebalanceConfig, MarginConfig, SleeveConfig
from perpbt.carry.executor import ExecSpec
from perpbt.carry.frames import build_pairframe
from perpbt.config import from_dict
from perpbt.data.store import DAY_MS, Candles, Funding
from tests.synthetic import STEP_15M_MS, T0_MS

PER_DAY = 96
H_MS = 3_600_000
FIRST = "2020-01-01"  # day 0 = T0_MS
SLOT_8H = 32


def leg(pair: str, fill, close, *, high=None, start_ms: int = T0_MS, drop=()) -> Candles:
    """A leg's 15m candles; ``drop`` lists ``(day, slot)`` candles to remove (slot 0 is 00:00)."""
    fill, close = list(map(float, fill)), list(map(float, close))
    n = len(close)
    o, h, lo, c = (np.empty(n * PER_DAY) for _ in range(4))
    prev = fill[0]
    for k in range(n):
        s = slice(k * PER_DAY, (k + 1) * PER_DAY)
        o[s], c[s] = close[k], close[k]
        o[k * PER_DAY], c[k * PER_DAY] = prev, fill[k]
        o[k * PER_DAY + 1] = fill[k]
        prev = close[k]
    h[:], lo[:] = np.maximum(o, c), np.minimum(o, c)
    if high is not None:
        for k, hk in enumerate(high):
            if hk is not None:
                h[k * PER_DAY + 48] = max(h[k * PER_DAY + 48], float(hk))
    ts = start_ms + STEP_15M_MS * np.arange(n * PER_DAY, dtype=np.int64)
    keep = np.ones(len(ts), dtype=bool)
    for day, slot in drop:
        keep[day * PER_DAY + slot] = False
    return Candles(pair, "15m", ts[keep], o[keep], h[keep], lo[keep], c[keep], np.ones(int(keep.sum())))


def funding8h(pair: str, rates, *, start_ms: int = T0_MS) -> Funding:
    """Events at 00:00, 08:00, 16:00 of each day; ``rates`` is one per event (3 per day)."""
    rates = np.asarray(rates, dtype=np.float64)
    ts = start_ms + 8 * H_MS * np.arange(len(rates), dtype=np.int64)
    return Funding(pair, ts, rates, np.full(len(rates), 8, dtype=np.int8))


def pairframe(pair: str, *, spot, perp, rates=None, spot_fill=None, perp_fill=None, perp_high=None,
              drop_spot=(), drop_perp=(), perp_start_ms: int = T0_MS, first_day: str = FIRST):
    """A PairFrame from per-day closes (and optional fills and perp highs); ``rates`` default 0."""
    days = len(spot)
    rates = np.zeros(3 * days) if rates is None else rates
    s = leg(pair, spot if spot_fill is None else spot_fill, spot, drop=drop_spot)
    p = leg(pair, perp if perp_fill is None else perp_fill, perp, high=perp_high, start_ms=perp_start_ms,
            drop=drop_perp)
    return build_pairframe(p, funding8h(pair, rates, start_ms=perp_start_ms), s, first_day=first_day)


def spec(*, start: str = "2020-01-02", end: str = "2020-01-05", lev: float = 2.0, perp_taker=0.0005,
         spot_taker=0.001, slippage=0.0005, mmr=0.01, liquidation_fee=0.01, rebalance_below=0.5,
         fill_offset_min: int = 15) -> ExecSpec:
    return ExecSpec(
        sleeve=from_dict(SleeveConfig, {"perp_leverage": lev}),
        margin=from_dict(MarginConfig, {"rebalance_below": rebalance_below, "mmr": {"default": mmr},
                                        "liquidation_fee": liquidation_fee}),
        costs=from_dict(CarryCostConfig, {"perp_taker": perp_taker, "spot_taker": spot_taker,
                                          "slippage": {"default": slippage}}),
        rebalance=from_dict(CarryRebalanceConfig, {"fill_offset_min": fill_offset_min}),
        start=start, end=end,
    )


def zero_cost_spec(**kw) -> ExecSpec:
    return spec(perp_taker=0.0, spot_taker=0.0, slippage=0.0, **kw)


def states(frames, on_rows: dict[str, list[int]]) -> dict[str, np.ndarray]:
    """Bool states per pair: on at the listed PairFrame rows."""
    out = {}
    for pair, pf in frames.items():
        arr = np.zeros(len(pf), dtype=bool)
        arr[list(on_rows.get(pair, []))] = True
        out[pair] = arr
    return out


__all__ = ["DAY_MS", "FIRST", "H_MS", "PER_DAY", "SLOT_8H", "T0_MS", "funding8h", "leg", "pairframe", "spec",
           "states", "zero_cost_spec"]
