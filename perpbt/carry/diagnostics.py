"""Descriptive diagnostics of a carry row (carry spec Phase 4 §4.4); none has a say in the verdict.

Every function reads the executor's outputs (``PathResult`` tables, ``BatchResult`` arrays) or runs the executor on
PairFrames the caller loaded; nothing here loads data or writes files. Money is turned into fractions of equity by
dividing each day's amount by the previous day's equity (``equity0`` before the first day), so a row's
``funding − fees − slippage + basis`` equals the sum of its daily returns (to float rounding).

- ``annual_stats``: annualised return, CAGR, vol, the Sharpe and its CI (V1's), max drawdown, worst day and month.
- ``decomposition``: funding, basis P&L, fees, slippage, and of the costs the rebalance and liquidation part;
  days on and entries; in total, per calendar year and per pair.
- ``margin_events``: rebalances and liquidations, with their dates and costs.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd

from perpbt.carry.executor import PathResult
from perpbt.carry.screen import DAYS_PER_YEAR
from perpbt.stats.diagnostics import max_drawdown

COMPONENTS = ("funding", "basis", "fees", "slippage", "rebalance_cost", "liquidation_cost")


def _prev_equity(daily: pd.DataFrame, equity0: float) -> pd.Series:
    """The equity before each return day, indexed by date: ``equity0``, then the previous day's close."""
    eq = daily["equity"].to_numpy(dtype=np.float64)
    return pd.Series(np.concatenate(([float(equity0)], eq[:-1])), index=daily["date"].to_numpy())


def annual_stats(daily: pd.DataFrame, *, equity0: float, v1: Mapping) -> dict:
    """One row's headline numbers; ``v1`` is the row's V1 block (its Sharpe and CI are reported, not redrawn).

    ``ann_return`` is ``365 × mean(ret)`` (V2's statistic), ``cagr`` ``(E_T / equity0)^(365 / T) − 1`` (None when
    the equity ends at or below 0), ``ann_vol`` ``√365 × std(ret)`` (ddof 1), ``max_drawdown`` the largest fall
    from a running peak with ``equity0`` the first, ``worst_day`` and ``worst_month`` (calendar month,
    compounded) with their dates.
    """
    ret = daily["ret"].to_numpy(dtype=np.float64)
    eq = daily["equity"].to_numpy(dtype=np.float64)
    T = len(ret)
    if T == 0:
        raise ValueError("annual_stats: no return days")
    k = int(np.argmin(ret))
    monthly = (1.0 + daily["ret"]).groupby(daily["date"].str[:7], sort=True).prod() - 1.0
    m = monthly.idxmin()
    return {
        "n_days": T,
        "ann_return": DAYS_PER_YEAR * float(ret.mean()),
        "cagr": (float(eq[-1] / equity0) ** (DAYS_PER_YEAR / T) - 1.0) if eq[-1] > 0 else None,
        "ann_vol": math.sqrt(DAYS_PER_YEAR) * float(ret.std(ddof=1)) if T > 1 else None,
        "sharpe": v1["sharpe"],
        "sharpe_ci": list(v1["sharpe_ci"]),
        "max_drawdown": max_drawdown(eq, equity0),
        "worst_day": {"date": str(daily["date"].iloc[k]), "ret": float(ret[k])},
        "worst_month": {"month": str(m), "ret": float(monthly[m])},
    }


def _fill_costs(result: PathResult, prev: pd.Series, reason: str) -> pd.DataFrame:
    """The fills of ``reason`` with their cost (fee + slippage) in money and as a fraction of the day's equity."""
    f = result.fills[result.fills["reason"] == reason]
    cost = (f["fee"] + f["slippage"]).to_numpy(dtype=np.float64)
    return pd.DataFrame({"date": f["date"].to_numpy(), "pair": f["pair"].to_numpy(), "cost": cost,
                         "frac": cost / prev.loc[f["date"]].to_numpy()})


def _block(daily: pd.DataFrame, pair_rows: pd.DataFrame, reb: pd.DataFrame, liq: pd.DataFrame,
           entries: pd.DataFrame, prev: pd.Series) -> dict:
    """The decomposition of one slice: ``daily``-like rows (``date`` and the money columns) and their pieces."""
    scale = 1.0 / prev.loc[daily["date"]].to_numpy()
    out = {
        "funding": float(np.sum(daily["funding"].to_numpy() * scale)),
        "basis": float(np.sum(daily["basis_pnl"].to_numpy() * scale)),
        "fees": float(np.sum(daily["fees"].to_numpy() * scale)),
        "slippage": float(np.sum(daily["slippage"].to_numpy() * scale)),
        "rebalance_cost": float(reb["frac"].sum()),
        "liquidation_cost": float(liq["frac"].sum()),
    }
    on = int((pair_rows["perp_qty"] < 0).sum())
    out.update(days_live=len(pair_rows), pair_days_on=on, entries=len(entries))
    return out


def decomposition(result: PathResult, *, equity0: float) -> dict:
    """Where a row's return came from: ``{"total", "per_year": [...], "per_pair": [...]}``.

    Fractions of equity summed over days: ``funding`` received, ``basis`` (the legs' price P&L, a liquidation's
    loss to its price included), ``fees`` and ``slippage`` (every fill, the liquidation fee in ``fees``), and of
    those two ``rebalance_cost`` and ``liquidation_cost`` (fills with that reason: the buy-back and the later spot
    sale). ``funding − fees − slippage + basis`` is the sum of the daily returns. ``days_live`` counts pair-days
    with a frame, ``pair_days_on`` those hedged at the close, ``entries`` the perp entry fills. The total and each
    year also have ``ret_sum`` and ``ret_compounded``; each year ``n_days`` and ``share_on`` (``pair_days_on /
    days_live``); each pair ``net`` (its contribution) and ``days_on``.
    """
    d, p = result.daily, result.daily_pair
    prev = _prev_equity(d, equity0)
    reb, liq = _fill_costs(result, prev, "rebalance"), _fill_costs(result, prev, "liquidation")
    f = result.fills
    entries = f[(f["reason"] == "enter") & (f["leg"] == "perp")]

    def returns(rows: pd.DataFrame) -> dict:
        r = rows["ret"].to_numpy(dtype=np.float64)
        return {"ret_sum": float(r.sum()), "ret_compounded": float(np.prod(1.0 + r) - 1.0)}

    total = {"n_days": len(d), **returns(d), **_block(d, p, reb, liq, entries, prev)}
    per_year = []
    for year in sorted(d["date"].str[:4].unique()):
        def sel(t: pd.DataFrame) -> pd.DataFrame:
            return t[t["date"].str[:4] == year]
        dy = sel(d)
        block = _block(dy, sel(p), sel(reb), sel(liq), sel(entries), prev)
        per_year.append({"year": year, "n_days": len(dy), **returns(dy), **block,
                         "share_on": block["pair_days_on"] / block["days_live"] if block["days_live"] else None})
    per_pair = []
    for pair in sorted(p["pair"].unique()):
        rows = p[p["pair"] == pair]
        block = _block(rows, rows, reb[reb["pair"] == pair], liq[liq["pair"] == pair],
                       entries[entries["pair"] == pair], prev)
        block["net"] = block["funding"] - block["fees"] - block["slippage"] + block["basis"]
        block["days_on"] = block.pop("pair_days_on")
        per_pair.append({"pair": pair, **block})
    total["share_on"] = total["pair_days_on"] / total["days_live"] if total["days_live"] else None
    return {"total": total, "per_year": per_year, "per_pair": per_pair}


def margin_events(result: PathResult, *, equity0: float) -> dict:
    """Rebalances and liquidations: ``{kind: {"count", "cost", "cost_frac", "events": [...]}}``.

    A rebalance's cost is its fills' fees and slippage that day (``qty`` the amount each leg was reduced by). A
    liquidation's cost is the fills of reason ``liquidation`` of that pair from its date until the pair's next
    liquidation: the perp buy-back with its fee and the later spot sale (``price`` the buy-back's). ``cost`` is
    money, ``cost_frac`` the sum of each fill's cost over its day's previous equity.
    """
    prev = _prev_equity(result.daily, equity0)
    ev = result.events
    out = {}
    for kind, reason in (("rebalances", "rebalance"), ("liquidations", "liquidation")):
        costs = _fill_costs(result, prev, reason)
        rows = ev[ev["kind"] == reason]
        events = []
        for pair, group in rows.groupby("pair", sort=True):
            dates = group["date"].tolist()
            mine = costs[costs["pair"] == pair]
            for k, (date, value) in enumerate(zip(dates, group["value"].tolist())):
                if reason == "rebalance":
                    sel = mine["date"] == date
                else:
                    sel = (mine["date"] >= date) & ((mine["date"] < dates[k + 1]) if k + 1 < len(dates) else True)
                e = {"date": date, "pair": pair, ("qty" if reason == "rebalance" else "price"): float(value),
                     "cost": float(mine["cost"][sel].sum()), "cost_frac": float(mine["frac"][sel].sum())}
                events.append(e)
        events.sort(key=lambda e: (e["date"], e["pair"]))
        out[kind] = {"count": len(events), "cost": float(costs["cost"].sum()),
                     "cost_frac": float(costs["frac"].sum()), "events": events}
    return out
