"""Cost re-pricing (spec §5.6, D13): net R is linear in the three cost rates.

Every trade (and every baseline run mean) is stored as components: gross R,
the fee and slippage coefficients in R (Phase 4 §4.7) and funding in R.
``reprice`` evaluates the one formula, in the same float order as
``execution.costs.trade_costs``, so the simulator's trades re-priced at
their own costs give its ``net_r`` bit for bit.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

import numpy as np
import pandas as pd

from perpbt.config import ExecConfig, StatsConfig

REQUIRED = ("gross_r", "c_maker_entry", "c_maker_exit", "c_taker_exit", "c_slip", "funding_r")


def _col(components, name: str) -> np.ndarray:
    return np.asarray(components[name], dtype=np.float64)


def _has(components, name: str) -> bool:
    if isinstance(components, Mapping):
        return name in components
    return name in getattr(components, "columns", ())


def reprice(components, fee_maker: float, fee_taker: float, slippage: float) -> np.ndarray:
    """Net R per row of ``components`` (a mapping or DataFrame of the component columns).

    ``gross_r − fm × (c_maker_entry + c_maker_exit) − ft × (c_taker_entry + c_taker_exit) − s × c_slip − funding_r``;
    without a ``c_taker_entry`` column (the simulator's trades, whose entries are all maker) the
    taker term is ``ft × c_taker_exit``.
    """
    missing = [k for k in REQUIRED if not _has(components, k)]
    if missing:
        raise KeyError(f"reprice: missing component columns {missing}")
    taker = _col(components, "c_taker_exit")
    if _has(components, "c_taker_entry"):
        taker = _col(components, "c_taker_entry") + taker
    return (
        _col(components, "gross_r")
        - fee_maker * (_col(components, "c_maker_entry") + _col(components, "c_maker_exit"))
        - fee_taker * taker
        - slippage * _col(components, "c_slip")
        - _col(components, "funding_r")
    )


GRID_COLUMNS = ("slippage", "fee_maker", "fee_taker", "is_primary", "n", "mean_net_r", "ci_lo", "ci_hi", "win_rate",
                "p_a", "z_a", "p_b", "z_b")


def reprice_grid(
    trades_r: pd.DataFrame,
    exec_cfg: ExecConfig,
    stats_cfg: StatsConfig,
    boot_rng: Callable[[], np.random.Generator],
    *,
    table_ids: np.ndarray | None = None,
    runs_a: pd.DataFrame | None = None,
    runs_b: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Spec §5.6: one row per (slippage, maker) cell of ``stats_cfg``, taker fixed at ``exec_cfg.fee_taker``.

    Per cell: mean net R of the R-subset trades, its trade-bootstrap CI
    (every cell draws from a fresh ``boot_rng()``, the same resamples), the
    win rate, and ``p_A``/``p_B`` with ``z`` from the baseline runs (None
    without runs). A's observed mean is over the trades of table A
    (``table_ids``). ``is_primary`` marks the cell of the variant's own costs.
    """
    from perpbt.stats.baselines import p_value, run_means
    from perpbt.stats.bootstrap import percentile_ci, trade_bootstrap

    ft = exec_cfg.fee_taker
    ids = trades_r["trade_id"].to_numpy()
    in_a = np.isin(ids, table_ids) if table_ids is not None else None
    rows = []
    for s in stats_cfg.reprice_slippage:
        for fm in stats_cfg.reprice_maker:
            net = reprice(trades_r, fm, ft, s) if len(trades_r) else np.zeros(0)
            mean = float(net.mean()) if len(net) else None
            lo, hi = percentile_ci(trade_bootstrap(net, stats_cfg.bootstrap_n, boot_rng()))
            pa = za = pb = zb = None
            if runs_a is not None and in_a is not None and in_a.any():
                pa, za = p_value(float(net[in_a].mean()), run_means(runs_a, fm, ft, s))
            if runs_b is not None and mean is not None:
                pb, zb = p_value(mean, run_means(runs_b, fm, ft, s))
            rows.append({
                "slippage": s, "fee_maker": fm, "fee_taker": ft,
                "is_primary": s == exec_cfg.slippage and fm == exec_cfg.fee_maker,
                "n": len(net), "mean_net_r": mean, "ci_lo": lo, "ci_hi": hi,
                "win_rate": float((net > 0).mean()) if len(net) else None,
                "p_a": pa, "z_a": za, "p_b": pb, "z_b": zb,
            })
    return pd.DataFrame(rows, columns=list(GRID_COLUMNS))


def cost_summary(trades_r: pd.DataFrame) -> dict:
    """Spec §5.6 extras at the primary costs: share of trades with ``cost_r > 1``; cost and stop-distance quantiles."""
    from perpbt.stats.diagnostics import quantiles

    n = len(trades_r)
    return {
        "share_cost_r_gt_1": float((trades_r["cost_r"] > 1.0).mean()) if n else None,
        "cost_r": quantiles(trades_r["cost_r"]),
        "stop_dist_pct": quantiles(100.0 * trades_r["stop_dist"] / trades_r["planned_entry"]),
        "stop_dist_atr": quantiles(trades_r["stop_dist_atr"]),
    }
