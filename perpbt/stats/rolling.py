"""Alpha decay (spec §5.9): rolling 6-month trade windows, rolling daily Sharpe, the per-year table.

Windows are ``[month m, month m + 6)`` by entry date, for every month start
from the period's first month whose window ends at or before the period
end. The rolling Sharpe is taken over the 182 days ending at each month
end that has 182 days of history. Per-year fill rate is filled / placed
entry orders (leverage-capped intents are not orders) by placement year.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from perpbt.execution.trades import utc_date
from perpbt.stats.bootstrap import sharpe
from perpbt.stats.diagnostics import max_drawdown


def _add_months(d: date, k: int) -> date:
    y, m = divmod(d.month - 1 + k, 12)
    return date(d.year + y, m + 1, 1)


def _ms(d: date) -> int:
    return int(pd.Timestamp(d, tz="UTC").value // 1_000_000)


def _trade_stats(net: np.ndarray) -> dict:
    n = len(net)
    return {"n": n, "mean_net_r": float(net.mean()) if n else None, "win_rate": float((net > 0).mean()) if n else None}


def rolling_windows(trades_r: pd.DataFrame, period_start_ms: int, period_end_ms: int, months: int = 6) -> list[dict]:
    """``start``, ``end`` (ISO, end exclusive), ``n``, ``mean_net_r``, ``win_rate`` per window."""
    first = utc_date(period_start_ms).replace(day=1)
    entry = trades_r["entry_ms"].to_numpy(dtype=np.int64)
    net = trades_r["net_r"].to_numpy(dtype=np.float64)
    out = []
    k = 0
    while True:
        a, b = _add_months(first, k), _add_months(first, k + months)
        if _ms(b) > period_end_ms:
            return out
        sel = (entry >= _ms(a)) & (entry < _ms(b))
        out.append({"start": a.isoformat(), "end": b.isoformat(), **_trade_stats(net[sel])})
        k += 1


def rolling_sharpe(daily: pd.DataFrame, window_days: int = 182) -> list[dict]:
    """``end`` (ISO month-end date) and the daily Sharpe over the ``window_days`` days ending there."""
    if len(daily) == 0:
        return []
    dates = np.array(daily["date"].tolist(), dtype="datetime64[D]")
    ret = daily["ret"].to_numpy(dtype=np.float64)
    first, last = daily["date"].iloc[0], daily["date"].iloc[-1]
    out = []
    m = _add_months(first.replace(day=1), 1)
    while m - timedelta(days=1) <= last:
        end = np.datetime64(m - timedelta(days=1))
        sel = (dates > end - np.timedelta64(window_days, "D")) & (dates <= end)
        if sel.sum() == window_days:
            out.append({"end": str(end), "sharpe": sharpe(ret[sel])})
        m = _add_months(m, 1)
    return out


def per_year(trades_r: pd.DataFrame, daily: pd.DataFrame, orders: pd.DataFrame, start_equity: float) -> list[dict]:
    """One row per calendar year of ``daily``: ``n``, ``win_rate``, ``mean_net_r``, ``sharpe``, ``max_dd``, ``fill_rate``."""
    years = np.array([d.year for d in daily["date"]], dtype=np.int64)
    equity = daily["equity"].to_numpy(dtype=np.float64)
    ret = daily["ret"].to_numpy(dtype=np.float64)
    t_year = np.array([utc_date(int(x)).year for x in trades_r["entry_ms"]], dtype=np.int64)
    net = trades_r["net_r"].to_numpy(dtype=np.float64)
    entries = orders[(orders["kind"] == "entry_limit") & (orders["cancel_reason"] != "leverage_cap")]
    o_year = np.array([utc_date(int(x)).year for x in entries["placed_ms"]], dtype=np.int64)
    o_filled = (entries["status"] == "filled").to_numpy()
    out = []
    prev = start_equity
    for y in np.unique(years).tolist():
        sel = years == y
        placed = int((o_year == y).sum())
        out.append({
            "year": y, **_trade_stats(net[t_year == y]), "sharpe": sharpe(ret[sel]),
            "max_dd": max_drawdown(equity[sel], prev),
            "fill_rate": float(o_filled[o_year == y].sum() / placed) if placed else None,
        })
        prev = float(equity[sel][-1])
    return out
