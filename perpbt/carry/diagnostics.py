"""Descriptive diagnostics of a carry row (carry spec Phase 4 §4.4); none has a say in the verdict.

Every function reads the executor's outputs (``PathResult`` tables, ``BatchResult`` arrays) or runs the executor on
PairFrames the caller loaded; nothing here loads data or writes files. Money is turned into fractions of equity by
dividing each day's amount by the previous day's equity (``equity0`` before the first day), so a row's
``funding − fees − slippage + basis`` equals the sum of its daily returns (to float rounding).

- ``annual_stats``: annualised return, CAGR, vol, the Sharpe and its CI (V1's), max drawdown, worst day and month.
- ``decomposition``: funding, basis P&L, fees, slippage, and of the costs the rebalance and liquidation part;
  days on and entries; in total, per calendar year and per pair.
- ``margin_events``: rebalances and liquidations, with their dates and costs.
- ``random_timing``: the primary's on/off series shifted in time (one circular offset per path, shared by the
  pairs; trend Phase 4 §4.1) through ``run_batch``; the primary's rank among the paths.
- ``reprice``: the row re-run at every cell of the ``reprice`` grid (``dataclasses.replace`` on its costs).
- ``filter_value``: the paired Sharpe difference primary − always_on.
- ``dsr_block``: the primary's deflated Sharpe with the seven pre-registered rows as trials.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

import numpy as np
import pandas as pd

from perpbt.carry.executor import ExecSpec, PathResult, run_batch, run_path
from perpbt.carry.frames import PairFrame
from perpbt.carry.prereg import CarryPrereg, RepriceConfig
from perpbt.carry.screen import DAYS_PER_YEAR, v2_stats
from perpbt.data.store import DAY_MS, date_ms
from perpbt.stats.bootstrap import paired_sharpe_diff, sharpe
from perpbt.stats.diagnostics import max_drawdown
from perpbt.stats.dsr import dsr_from_trials, sr_moments

COMPONENTS = ("funding", "basis", "fees", "slippage", "rebalance_cost", "liquidation_cost")
TIMING_COLUMNS = ("path", "offset", "ann_return", "sharpe", "entries", "rebalances", "liquidations")


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


# --- random timing ---------------------------------------------------------------------------------------------

def _return_days(cfg: ExecSpec) -> np.ndarray:
    return np.arange(date_ms(cfg.start), date_ms(cfg.end) + DAY_MS, DAY_MS, dtype=np.int64)


def live_window(pf: PairFrame, cfg: ExecSpec) -> tuple[int, int]:
    """``(j0, n_i)``: the PairFrame row of the pair's first live decision day in the period and how many follow.

    A decision day ``d`` (the day before a return day) is live when the frame has ``d`` and ``d + 1``, as in the
    executor; those rows are consecutive. ``(0, 0)`` when the pair is never live in the period.
    """
    j = (_return_days(cfg) - DAY_MS - int(pf.day_ms[0])) // DAY_MS
    j = j[(j >= 0) & (j + 1 < len(pf))]
    return (int(j[0]), len(j)) if len(j) else (0, 0)


def draw_offsets(n_days: int, margin: int, runs: int, rng: np.random.Generator) -> np.ndarray:
    """``runs`` shift offsets uniform on the integers ``margin .. n_days − margin`` (both ends included)."""
    if runs < 1:
        raise ValueError(f"random timing needs runs >= 1, got {runs}")
    if n_days - margin < margin:
        raise ValueError(f"random timing: {n_days} days leave no offset in [{margin}, {n_days - margin}]")
    return rng.integers(margin, n_days - margin, size=runs, endpoint=True).astype(np.int64)


def shifted_states(frames: Mapping[str, PairFrame], state: Mapping[str, np.ndarray], cfg: ExecSpec,
                   offsets: np.ndarray) -> dict[str, np.ndarray]:
    """The on/off series circularly shifted by each offset, every pair by the same ``k``, within its live days.

    Path ``m``, pair ``i`` with live decision rows ``j0 .. j0 + n_i − 1``: ``on'[j0 + t] = on[j0 + (t + k_m) mod
    n_i]`` (trend Phase 4 §4.1: a pair live on fewer days than the period takes ``k mod n_i``). The pair's time on,
    holding lengths and switches are kept up to the wrap; only the timing moves. Rows outside the window are off
    (the executor never reads them). ``(paths, rows)`` bool per pair, ``run_batch``'s input.
    """
    k = np.asarray(offsets, dtype=np.int64)
    if k.ndim != 1:
        raise ValueError(f"offsets must be 1-D, got shape {k.shape}")
    out = {}
    for pair, pf in frames.items():
        on = np.asarray(state[pair], dtype=bool)
        if on.shape != (len(pf),):
            raise ValueError(f"{pair}: state must have shape ({len(pf)},), got {on.shape}")
        j0, n_i = live_window(pf, cfg)
        arr = np.zeros((len(k), len(pf)), dtype=bool)
        if n_i:
            arr[:, j0:j0 + n_i] = on[j0 + (np.arange(n_i)[None, :] + k[:, None]) % n_i]
        out[pair] = arr
    return out


def path_returns(equity: np.ndarray, equity0: float) -> np.ndarray:
    """``(paths, days)`` daily returns of ``run_batch`` equity: ``E_t / E_{t−1} − 1``, the executor's own expression."""
    eq = np.asarray(equity, dtype=np.float64)
    prev = np.concatenate((np.full((eq.shape[0], 1), float(equity0)), eq[:, :-1]), axis=1)
    return eq / prev - 1.0


def rank_summary(values: np.ndarray, primary: float | None) -> dict:
    """The paths' distribution of a statistic and the primary's place in it (over the finite paths ``M'``).

    ``rank`` is ``1 + #{paths > primary}`` (1 = no path did better), ``share_at_least`` ``(1 + #{paths ≥
    primary}) / (M' + 1)``; both None when the primary's value is undefined.
    """
    v = np.asarray(values, dtype=np.float64)
    v = v[np.isfinite(v)]
    obs = None if primary is None or not math.isfinite(primary) else float(primary)
    out: dict = {"primary": obs, "n_finite": len(v)}
    if len(v):
        p05, p50, p95 = np.percentile(v, [5.0, 50.0, 95.0])
        out.update(mean=float(v.mean()), std=float(v.std(ddof=1)) if len(v) > 1 else None, p05=float(p05),
                   p50=float(p50), p95=float(p95))
    else:
        out.update(mean=None, std=None, p05=None, p50=None, p95=None)
    out["rank"] = None if obs is None else 1 + int(np.sum(v > obs))
    out["share_at_least"] = None if obs is None else (1 + int(np.sum(v >= obs))) / (len(v) + 1)
    return out


def random_timing(frames: Mapping[str, PairFrame], state: Mapping[str, np.ndarray], cfg: ExecSpec, *, runs: int,
                  margin: int, rng: np.random.Generator, primary_ret) -> tuple[dict, pd.DataFrame]:
    """``runs`` shifted paths of the primary through ``run_batch`` with the primary's ``cfg``.

    ``k ~ U{margin, …, n − margin}`` per path, ``n`` the period's return days (``stats.shift_margin_days``,
    ``stats.random_timing_runs``). Statistics per path: ``ann_return`` (``365 × mean``, V2's statistic) and the
    Sharpe. Returns the summary ``{"runs", "shift_margin_days", "n_days", "ann_return": rank_summary,
    "sharpe": rank_summary}`` against ``primary_ret`` (the primary's ``daily["ret"]``) and the per-path table
    (``TIMING_COLUMNS``). Shows whether *when* to be on matters beyond how often; descriptive.
    """
    n = len(_return_days(cfg))
    p_ret = np.asarray(primary_ret, dtype=np.float64)
    if p_ret.shape != (n,):
        raise ValueError(f"primary_ret must have the period's {n} days, got shape {p_ret.shape}")
    offsets = draw_offsets(n, margin, runs, rng)
    batch = run_batch(dict(frames), shifted_states(frames, state, cfg, offsets), cfg)
    ret = path_returns(batch.equity, cfg.equity0)
    ann = DAYS_PER_YEAR * ret.mean(axis=1)
    sh = np.array([sharpe(r) for r in ret])
    summary = {"runs": runs, "shift_margin_days": margin, "n_days": n,
               "ann_return": rank_summary(ann, DAYS_PER_YEAR * float(p_ret.mean())),
               "sharpe": rank_summary(sh, sharpe(p_ret))}
    table = pd.DataFrame({
        "path": np.arange(runs, dtype=np.int64), "offset": offsets, "ann_return": ann, "sharpe": sh,
        "entries": batch.counts["entries"], "rebalances": batch.counts["rebalances"],
        "liquidations": batch.counts["liquidations"],
    })
    return summary, table[list(TIMING_COLUMNS)]


# --- re-pricing ------------------------------------------------------------------------------------------------

def reprice_specs(cfg: ExecSpec, grid: RepriceConfig) -> list[tuple[float, float, ExecSpec]]:
    """``(slippage, spot_taker, ExecSpec)`` per grid cell, slippage outer: only the two costs change.

    A cell's slippage applies to every pair (``{"default": s}``); ``perp_taker`` and everything else is ``cfg``'s.
    """
    out = []
    for slip in grid.slippage:
        for taker in grid.spot_taker:
            costs = replace(cfg.costs, spot_taker=taker, slippage={"default": slip})
            out.append((slip, taker, replace(cfg, costs=costs)))
    return out


def reprice(frames: Mapping[str, PairFrame], state: Mapping[str, np.ndarray], cfg: ExecSpec, grid: RepriceConfig,
            *, hurdle: float, alpha: float, B: int, block_len: int,
            rng_v2: Callable[[], np.random.Generator]) -> list[dict]:
    """The row re-run at every re-pricing cell (``run_path``): per cell ``slippage``, ``spot_taker``,
    ``perp_taker``, ``ann_return`` (``365 × mean``), ``sharpe`` (None when undefined), ``v2`` (V2 at ``hurdle``,
    a fresh ``rng_v2()`` per cell: the same blocks in every cell), the cell's ``fees`` and ``slippage_cost``
    (fractions of equity summed over days) and ``entries``.
    """
    cells = []
    for slip, taker, c in reprice_specs(cfg, grid):
        r = run_path(dict(frames), dict(state), c)
        d = r.daily
        ret = d["ret"].to_numpy(dtype=np.float64)
        prev = _prev_equity(d, c.equity0).to_numpy()
        s = sharpe(ret)
        cells.append({
            "slippage": slip, "spot_taker": taker, "perp_taker": c.costs.perp_taker,
            "ann_return": DAYS_PER_YEAR * float(ret.mean()), "sharpe": s if math.isfinite(s) else None,
            "v2": v2_stats(ret, hurdle=hurdle, alpha=alpha, B=B, block_len=block_len, rng=rng_v2()),
            "fees": float(np.sum(d["fees"].to_numpy() / prev)),
            "slippage_cost": float(np.sum(d["slippage"].to_numpy() / prev)),
            "entries": r.counts["entries"],
        })
    return cells


# --- filter value, DSR -----------------------------------------------------------------------------------------

def filter_value(primary: pd.DataFrame, always_on: pd.DataFrame, *, B: int, block_len: int,
                 rng: np.random.Generator) -> dict:
    """``Sharpe(primary) − Sharpe(always_on)`` on the same days (``paired_sharpe_diff``: the same blocks for both),
    its 95% CI and one-sided ``p``; the two ``daily`` tables must cover the same dates."""
    if primary["date"].tolist() != always_on["date"].tolist():
        raise ValueError("filter_value: the primary and always_on rows cover different days")
    out = paired_sharpe_diff(primary["ret"].to_numpy(dtype=np.float64), always_on["ret"].to_numpy(dtype=np.float64),
                             B, rng, block_len)
    return {"rows": ["primary", "always_on"], "sharpe_primary": out["sharpe_a"], "sharpe_always_on": out["sharpe_b"],
            "diff": out["diff"], "diff_ci": [out["ci_lo"], out["ci_hi"]], "p": out["p"]}


def row_names(prereg: CarryPrereg) -> tuple[str, ...]:
    """The pre-registered rows: the primary and the exploratory rows, in the prereg's order."""
    return ("primary", *(e.name for e in prereg.exploratory))


def dsr_block(rets: Mapping[str, np.ndarray], rows: Sequence[str]) -> dict:
    """The primary's deflated Sharpe with every row of ``rows`` (the primary included) as a trial.

    ``rets`` must hold exactly ``rows``' daily returns (``row_names(prereg)``: N = 7). Daily Sharpe ratios
    (``stats.dsr``): ``trial_sr`` per row; ``N`` counts the defined ones, ``V`` their variance, ``sr_star`` the
    expected maximum under the null, ``dsr`` the primary's PSR against it.
    """
    rows = list(rows)
    if "primary" not in rows or len(set(rows)) != len(rows) or set(rets) != set(rows):
        raise ValueError(f"dsr_block: need the returns of exactly the rows {rows} (the primary among them), "
                         f"got {sorted(rets)}")
    m = sr_moments(rets["primary"])
    trial = {row: sr_moments(rets[row])["sr"] for row in rows}
    d = dsr_from_trials(m, [trial[row] for row in rows])
    return {"rows": rows, "trial_sr": trial, "sr": m["sr"], "T": m["T"], "skew": m["skew"], "kurt": m["kurt"], **d}
