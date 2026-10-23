"""The basis sanity check (carry spec Phase 0 §0.4).

For each pair, from the stored 15m closes of both legs over the fixed window
``BASIS_START``..``BASIS_END`` (the in-sample trading period, inclusive): the
share of grid slots where both legs have a candle and the distribution of
``perp close / spot close - 1`` on those slots. It catches a wrong symbol
mapping before any strategy code exists and computes no return, P&L or
funding statistic. The window is a constant, not a parameter, so no config
can move it into the carry holdout; the loads go through the guarded stores.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

import numpy as np

from perpbt.config import DataConfig
from perpbt.data.store import DAY_MS, Candles, CandleStore, date_ms

BASIS_START = "2020-07-01"
BASIS_END = "2023-12-31"  # inclusive: the pre-registered in-sample end
MEDIAN_LIMIT = 0.005  # |median| above this is reported to the user
MAX_ABS_LIMIT = 0.20  # max |basis| above this is reported to the user
STEP_15M_MS = 900_000


def _iso_minute(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def basis_stats(perp: Candles, spot: Candles, start_ms: int, end_ms: int) -> dict:
    """Coverage and the ``perp.c / spot.c - 1`` distribution on common slots in ``[start_ms, end_ms)``."""
    p, s = perp.slice(start_ms, end_ms), spot.slice(start_ms, end_ms)
    common, ip, is_ = np.intersect1d(p.ts, s.ts, assume_unique=True, return_indices=True)
    slots = (end_ms - start_ms) // STEP_15M_MS
    out: dict = {
        "slots": int(slots), "perp_rows": len(p), "spot_rows": len(s), "common": len(common),
        "coverage": len(common) / slots,
        "median": None, "p01": None, "p99": None, "max_abs": None, "max_abs_at": None,
    }
    reasons: list[str] = []
    if len(common) == 0:
        reasons.append("no common slots")
    else:
        basis = p.c[ip] / s.c[is_] - 1.0
        k = int(np.argmax(np.abs(basis)))
        out.update(
            median=float(np.median(basis)), p01=float(np.quantile(basis, 0.01)), p99=float(np.quantile(basis, 0.99)),
            max_abs=float(abs(basis[k])), max_abs_at=_iso_minute(int(common[k])),
        )
        if abs(out["median"]) > MEDIAN_LIMIT:
            reasons.append("median")
        if out["max_abs"] > MAX_ABS_LIMIT:
            reasons.append("max_abs")
    out["flagged"] = bool(reasons)
    out["reasons"] = reasons
    return out


def basis_check(data_cfg: DataConfig, pairs: Sequence[str]) -> dict:
    """``basis_stats`` for every pair over the fixed window, both legs loaded without ``allow_holdout``."""
    start_ms, end_ms = date_ms(BASIS_START), date_ms(BASIS_END) + DAY_MS
    perps, spots = CandleStore(data_cfg, market="um"), CandleStore(data_cfg, market="spot")
    result = {}
    for pair in pairs:
        perp = perps.load(pair, "15m", start_ms, end_ms)
        spot = spots.load(pair, "15m", start_ms, end_ms)
        result[pair] = basis_stats(perp, spot, start_ms, end_ms)
    return {
        "window": [BASIS_START, BASIS_END],
        "limits": {"median_abs": MEDIAN_LIMIT, "max_abs": MAX_ABS_LIMIT},
        "pairs": result,
    }
