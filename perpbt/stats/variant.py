"""One variant's statistics and its ``stats.json`` (spec §5.10).

``compute_stats`` turns a ``SimResult`` and the market it ran on into one
plain dict holding every number the report and the summary print: counts
and skips, the headline with both mean-R CIs and the Sharpe CI, the
baseline p-values (when ``run_baselines`` has run), buy-and-hold, the DSR
inputs, the re-pricing grid, rolling and per-year tables, regime and
calendar breakdowns, distributions, and the in-sample references a holdout
run needs. Every random draw comes from ``rng_for(master_seed, variant_id,
purpose)``. ``write_stats`` writes it as sorted, indented JSON with
non-finite numbers as ``null`` (``sanitize`` applies the same conversion
in memory, so what is read back equals what was written). The Holm
adjustment and the DSR values need the other cells and are added by the
experiments layer.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from perpbt.config import VariantConfig
from perpbt.data.sessions import SessionCalendar
from perpbt.data.store import DAY_MS, Candles, Funding
from perpbt.execution.simulator import SimResult
from perpbt.execution.trades import utc_date
from perpbt.stats import bootstrap as bs
from perpbt.stats.baselines import (
    Setup,
    TableA,
    make_setup,
    p_value,
    precompute_table_a,
    r_subset,
    run_means,
    runs_a,
    runs_b,
)
from perpbt.stats.buyhold import bh_daily, buy_and_hold
from perpbt.stats.diagnostics import breakdown, exposure, headline, max_drawdown, quantiles
from perpbt.stats.dsr import sr_moments
from perpbt.stats.regimes import label_regimes, vol_median
from perpbt.stats.repricing import cost_summary, reprice_grid
from perpbt.stats.rolling import per_year, rolling_sharpe, rolling_windows
from perpbt.stats.seeds import rng_for

SCHEMA_VERSION = 1
PURPOSES = ("trade_bootstrap", "day_bootstrap", "sharpe_bootstrap", "bh_sharpe_bootstrap", "bh_diff_bootstrap",
            "baseline_a", "baseline_b")


@dataclass(frozen=True, eq=False)
class Market:
    """The data one variant ran on: the simulator's inputs and its period (``period_end_ms`` exclusive)."""

    candles15: Candles
    candles1m: Candles | None
    funding: Funding
    calendar: SessionCalendar
    period_start_ms: int
    period_end_ms: int
    listing_ms: int | None = None


@dataclass(eq=False)
class Baselines:
    """Baseline A's slot table and the per-run component means of A and B (spec §5.3, §5.4)."""

    table_a: TableA
    runs_a: pd.DataFrame
    runs_b: pd.DataFrame
    n_runs: int


def setup_of(market: Market, cfg: VariantConfig) -> Setup:
    return make_setup(
        market.candles15, market.candles1m if cfg.exec.use_1m else None, market.funding, market.calendar, cfg.exec,
        cfg.params, market.period_start_ms, market.period_end_ms, listing_ms=market.listing_ms,
    )


def run_baselines(result: SimResult, market: Market, cfg: VariantConfig, *, variant_id: str,
                  n_runs: int | None = None) -> Baselines:
    """Table A and ``n_runs`` (default ``cfg.stats.baseline_runs``) runs of A and of B."""
    m = n_runs or cfg.stats.baseline_runs
    seed = cfg.stats.master_seed
    s = setup_of(market, cfg)
    table = precompute_table_a(result.trades, s)
    ra = runs_a(table, m, rng_for(seed, variant_id, "baseline_a"))
    rb = runs_b(result.trades, s, m, rng_for(seed, variant_id, "baseline_b"))
    return Baselines(table, ra, rb, m)


# --- JSON ----------------------------------------------------------------------------------------

def sanitize(obj):
    """Plain JSON values: numpy scalars to Python, non-finite floats to None, dates to ISO, frames to records."""
    if isinstance(obj, pd.DataFrame):
        return [sanitize(r) for r in obj.to_dict(orient="records")]
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, np.ndarray)):
        return [sanitize(v) for v in obj]
    if obj is None or obj is pd.NA or isinstance(obj, (bool, np.bool_, str)):
        return bool(obj) if isinstance(obj, np.bool_) else (None if obj is pd.NA else obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        x = float(obj)
        return x if math.isfinite(x) else None
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"sanitize: cannot store {type(obj).__name__} in stats.json")


def write_stats(stats: dict, path: str | Path) -> None:
    """Write ``stats`` as ``stats.json`` (sorted keys, LF, non-finite as null): byte-identical across reruns."""
    text = json.dumps(sanitize(stats), sort_keys=True, indent=1, allow_nan=False, ensure_ascii=False)
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)  # atomic: a killed writer never leaves a truncated stats.json


def read_stats(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --- the statistics ------------------------------------------------------------------------------

def _ci(reps: np.ndarray) -> dict:
    lo, hi = bs.percentile_ci(reps)
    return {"lo": lo, "hi": hi}


def compute_stats(
    result: SimResult,
    market: Market,
    cfg: VariantConfig,
    *,
    variant_id: str,
    baselines: Baselines | None = None,
    insample_ref: dict | None = None,
) -> dict:
    """Every per-variant statistic of spec §5.10 as a sanitized dict (see the module docstring).

    ``insample_ref`` (``vol_median``, ``sigma_strategy``, ``sigma_bh``) comes
    from the in-sample run of the same cell when this is a holdout run;
    without it the variant's own period is the in-sample reference.
    """
    st = cfg.stats
    seed = st.master_seed

    def rng(purpose: str) -> np.random.Generator:
        return rng_for(seed, variant_id, purpose)

    trades, daily, s = result.trades, result.daily, result.summary
    first, last = int(s["first_idx"]), int(s["last_idx"])
    c15 = market.candles15
    ref = dict(insample_ref) if insample_ref else {}
    if "vol_median" not in ref:
        ref["vol_median"] = vol_median(c15, market.period_start_ms, market.period_end_ms)
    labelled = label_regimes(trades, c15, ref["vol_median"])
    tr = r_subset(labelled)
    net = tr["net_r"].to_numpy(dtype=np.float64)
    ret = daily["ret"].to_numpy(dtype=np.float64)
    equity = daily["equity"].to_numpy(dtype=np.float64)

    head = headline(tr)
    head.update({
        "mean_net_r_ci": _ci(bs.trade_bootstrap(net, st.bootstrap_n, rng("trade_bootstrap"))),
        "mean_net_r_ci_block": _ci(bs.day_block_bootstrap(net, tr["entry_ms"].to_numpy(dtype=np.int64) // DAY_MS,
                                                          st.bootstrap_n, rng("day_bootstrap"))),
        "sharpe_ann": bs.sharpe(ret),
        "sharpe_ci": _ci(bs.block_bootstrap_sharpe(ret, st.bootstrap_n, rng("sharpe_bootstrap"), st.block_len_days)),
        "max_dd": max_drawdown(equity, s["start_equity"]),
        **exposure(trades, daily, first, last),
        "max_concurrent": s["max_concurrent"],
        "total_return": s["final_equity"] / s["start_equity"] - 1.0,
    })

    costs = (cfg.exec.fee_maker, cfg.exec.fee_taker, cfg.exec.slippage)
    base = {"n_runs": None, "p_a": None, "z_a": None, "p_b": None, "z_b": None, "observed_a": None,
            "observed_b": None, "n_a": None, "n_excluded_a": None, "runs_a_mean": None, "runs_a_std": None,
            "runs_b_mean": None, "runs_b_std": None, "runs_b_mean_n": None}
    if baselines is not None:
        ta = baselines.table_a
        in_a = np.isin(tr["trade_id"].to_numpy(), ta.trade_ids)
        obs_a = float(net[in_a].mean()) if in_a.any() else None
        obs_b = float(net.mean()) if len(net) else None
        va, vb = run_means(baselines.runs_a, *costs), run_means(baselines.runs_b, *costs)
        pa, za = p_value(obs_a, va) if obs_a is not None else (None, None)
        pb, zb = p_value(obs_b, vb) if obs_b is not None else (None, None)
        base.update({
            "n_runs": baselines.n_runs, "p_a": pa, "z_a": za, "p_b": pb, "z_b": zb,
            "observed_a": obs_a, "observed_b": obs_b, "n_a": len(ta), "n_excluded_a": ta.n_excluded,
            "runs_a_mean": float(np.nanmean(va)) if np.isfinite(va).any() else None,
            "runs_a_std": float(np.nanstd(va, ddof=1)) if np.isfinite(va).sum() > 1 else None,
            "runs_b_mean": float(np.nanmean(vb)) if np.isfinite(vb).any() else None,
            "runs_b_std": float(np.nanstd(vb, ddof=1)) if np.isfinite(vb).sum() > 1 else None,
            "runs_b_mean_n": float(baselines.runs_b["n"].mean()) if len(baselines.runs_b) else None,
        })

    bh = bh_daily(c15, market.funding, first, last)
    bh_block = buy_and_hold(daily, bh, st.bootstrap_n, rng("bh_sharpe_bootstrap"), rng("bh_diff_bootstrap"),
                            st.block_len_days, sigma_strategy=ref.get("sigma_strategy"), sigma_bh=ref.get("sigma_bh"))
    ref.setdefault("sigma_strategy", bh_block["sigma_strategy"])
    ref.setdefault("sigma_bh", bh_block["sigma_bh"])

    grid = reprice_grid(
        tr, cfg.exec, st, lambda: rng("trade_bootstrap"),
        table_ids=baselines.table_a.trade_ids if baselines else None,
        runs_a=baselines.runs_a if baselines else None, runs_b=baselines.runs_b if baselines else None,
    )

    tr_year = tr.assign(year=np.array([utc_date(int(x)).year for x in tr["entry_ms"]], dtype=np.int64))
    stats = {
        "schema": SCHEMA_VERSION,
        "variant_id": variant_id, "pair": s["pair"], "session_variant": s["session_variant"],
        "is_holdout": cfg.is_holdout, "period_start_ms": market.period_start_ms,
        "period_end_ms": market.period_end_ms,
        "seeds": {"master_seed": seed, "purposes": list(PURPOSES)},
        "n_sessions": len(market.calendar.eligible_days(
            market.period_start_ms if market.listing_ms is None else max(market.period_start_ms, market.listing_ms),
            market.period_end_ms)),
        "summary": s, "skips": result.skips,
        "headline": head,
        "baselines": base,
        "buy_and_hold": bh_block,
        "dsr": {**sr_moments(ret), "local": None, "global": None},
        "repricing": grid,
        "costs": cost_summary(tr),
        "distributions": {k: quantiles(tr[k]) for k in ("cost_r", "implied_leverage", "hold_minutes", "mae_r",
                                                         "mfe_r", "funding_r")},
        "rolling": {"windows": rolling_windows(tr, market.period_start_ms, market.period_end_ms),
                    "sharpe": rolling_sharpe(daily)},
        "per_year": per_year(tr, daily, result.orders, s["start_equity"]),
        "regimes": {"trend": breakdown(tr, "regime_trend"), "vol": breakdown(tr, "regime_vol")},
        "breakdowns": {k: breakdown(tr_year, k) for k in ("dow", "entry_hour_utc", "year", "exit_reason")},
        "insample_ref": ref,
    }
    return sanitize(stats)
