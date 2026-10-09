"""Buy-and-hold benchmark (spec §5.5): a constant 1× long in the perp, with funding, vol-scaled.

``ret_d = close_d / close_{d−1} − 1 − Σ rate_f`` over the funding events
of UTC day ``d`` (longs pay positive rates). The days are those with
candles in the period (the dates of the simulator's ``daily`` table);
``close_d`` is the close of the day's last candle; the first day's
previous close is the close of the candle before the period (the open of
the first candle when there is none). Funding event ``f`` belongs to day
``f // 1 day``, the day whose mark the simulator charges it to.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from perpbt.data.store import DAY_MS, Candles, Funding
from perpbt.execution.fills import CANDLE_15M_MS
from perpbt.execution.trades import utc_date
from perpbt.stats.bootstrap import block_bootstrap_sharpe, paired_sharpe_diff, percentile_ci, sharpe
from perpbt.stats.diagnostics import max_drawdown


def bh_daily(candles15: Candles, funding: Funding, first_idx: int, last_idx: int) -> pd.DataFrame:
    """``date, close, funding, ret`` per UTC day of candles ``first_idx .. last_idx``."""
    ts = candles15.ts[first_idx : last_idx + 1]
    close = candles15.c[first_idx : last_idx + 1]
    day = ts // DAY_MS
    ends = np.flatnonzero(np.append(day[1:] != day[:-1], True))
    days = day[ends]
    closes = close[ends]
    prev0 = candles15.c[first_idx - 1] if first_idx > 0 else candles15.o[first_idx]
    prev = np.concatenate(([prev0], closes[:-1]))
    lo, hi = int(ts[0]), int(ts[-1]) + CANDLE_15M_MS
    sel = (funding.ts >= lo) & (funding.ts < hi)
    fday = funding.ts[sel] // DAY_MS
    fsum = np.zeros(len(days))
    k = np.searchsorted(days, fday)
    ok = (k < len(days)) & (days[np.minimum(k, len(days) - 1)] == fday)
    np.add.at(fsum, k[ok], funding.rate[sel][ok])
    return pd.DataFrame({
        "date": [utc_date(int(d) * DAY_MS) for d in days], "close": closes, "funding": fsum,
        "ret": closes / prev - 1.0 - fsum,
    })


def buy_and_hold(
    strategy_daily: pd.DataFrame, bh: pd.DataFrame, B: int, rng_sharpe: np.random.Generator,
    rng_diff: np.random.Generator, block_len: int, *, sigma_strategy: float | None = None,
    sigma_bh: float | None = None,
) -> dict:
    """Spec §5.5 block: B&H Sharpe with CI, vol-scaled return and drawdown, paired Sharpe difference and ``p_BH``.

    The two daily series are aligned by ``date``. ``sigma_strategy`` and
    ``sigma_bh`` default to the series' own daily std (ddof 1), i.e. the
    in-sample vols; a holdout passes the in-sample values. The difference
    is strategy minus buy-and-hold.
    """
    both = strategy_daily[["date", "ret"]].merge(bh[["date", "ret"]], on="date", suffixes=("_s", "_b"))
    rs = both["ret_s"].to_numpy(dtype=np.float64)
    rb = both["ret_b"].to_numpy(dtype=np.float64)
    ss = sigma_strategy if sigma_strategy is not None else (float(rs.std(ddof=1)) if len(rs) > 1 else None)
    sb = sigma_bh if sigma_bh is not None else (float(rb.std(ddof=1)) if len(rb) > 1 else None)
    scale = ss / sb if ss is not None and sb else float("nan")
    scaled = rb * scale
    lo, hi = percentile_ci(block_bootstrap_sharpe(rb, B, rng_sharpe, block_len))
    diff = paired_sharpe_diff(rs, rb, B, rng_diff, block_len)
    return {
        "n_days": len(both), "sharpe": sharpe(rb), "sharpe_ci_lo": lo, "sharpe_ci_hi": hi,
        "total_return": float(np.prod(1.0 + rb) - 1.0) if len(rb) else None,
        "sigma_strategy": ss, "sigma_bh": sb, "scale": scale,
        "total_return_scaled": float(np.prod(1.0 + scaled) - 1.0) if len(rb) else None,
        "max_dd_scaled": max_drawdown(np.cumprod(1.0 + scaled), 1.0) if len(rb) else None,
        "sharpe_strategy": diff["sharpe_a"], "sharpe_diff": diff["diff"],
        "sharpe_diff_ci_lo": diff["ci_lo"], "sharpe_diff_ci_hi": diff["ci_hi"], "p_bh": diff["p"],
    }
