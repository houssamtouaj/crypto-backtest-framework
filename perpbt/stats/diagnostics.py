"""Trade and equity diagnostics (spec §5.9): headline numbers, exposure, drawdown, distributions, breakdowns."""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

QUANTILES = {"min": 0.0, "p05": 5.0, "p25": 25.0, "p50": 50.0, "p75": 75.0, "p95": 95.0, "max": 100.0}


def quantiles(x) -> dict | None:
    """``min, p05, p25, p50, p75, p95, max`` and ``mean`` of the finite values; None when there are none."""
    a = np.asarray(x, dtype=np.float64)
    a = a[np.isfinite(a)]
    if len(a) == 0:
        return None
    q = np.percentile(a, list(QUANTILES.values()))
    return {**{k: float(v) for k, v in zip(QUANTILES, q, strict=True)}, "mean": float(a.mean())}


def headline(trades_r: pd.DataFrame) -> dict:
    """``n``, win rate (``net_r > 0``), mean gross/net R, median net R, profit factor, mean cost, funding, hold."""
    n = len(trades_r)
    net = trades_r["net_r"].to_numpy(dtype=np.float64)
    loss = -net[net < 0].sum()

    def mean(col):
        return float(trades_r[col].mean()) if n else None

    return {
        "n": n, "win_rate": float((net > 0).mean()) if n else None,
        "mean_gross_r": mean("gross_r"), "mean_net_r": mean("net_r"),
        "median_net_r": float(np.median(net)) if n else None,
        "profit_factor": float(net[net > 0].sum() / loss) if loss > 0 else None,
        "avg_cost_r": mean("cost_r"), "avg_funding_r": mean("funding_r"), "avg_hold_min": mean("hold_minutes"),
    }


def exposure(trades: pd.DataFrame, daily: pd.DataFrame, first_idx: int, last_idx: int) -> dict:
    """Fraction of period candles inside some trade's ``[entry_idx, exit_idx]``; mean daily notional / equity."""
    n = last_idx - first_idx + 1
    marks = np.zeros(n + 1, dtype=np.int64)
    a = np.clip(trades["entry_idx"].to_numpy(dtype=np.int64), first_idx, last_idx + 1) - first_idx
    b = np.clip(trades["exit_idx"].to_numpy(dtype=np.int64), first_idx - 1, last_idx) - first_idx + 1
    ok = b > a
    np.add.at(marks, a[ok], 1)
    np.add.at(marks, b[ok], -1)
    covered = np.cumsum(marks[:-1]) > 0
    notional = daily["exposure_notional"].to_numpy(dtype=np.float64) / daily["equity"].to_numpy(dtype=np.float64)
    return {"exposure_time": float(covered.mean()) if n > 0 else None,
            "exposure_notional": float(notional.mean()) if len(notional) else None}


def breakdown(trades_r: pd.DataFrame, key: str) -> list[dict]:
    """``key``, ``n``, win rate and mean net R per value of column ``key`` (sorted; a missing value last)."""
    rows = []
    col = trades_r[key].astype(object).where(trades_r[key].notna(), None)
    values = sorted({v for v in col if v is not None}, key=lambda v: (str(type(v)), v))
    if col.isna().any():
        values.append(None)
    for v in values:
        net = trades_r["net_r"].to_numpy(dtype=np.float64)[(col.isna() if v is None else col == v).to_numpy()]
        rows.append({"key": v.item() if hasattr(v, "item") else v, "n": len(net),
                     "win_rate": float((net > 0).mean()), "mean_net_r": float(net.mean())})
    return rows


def shared_fraction(tables: Mapping[str, pd.DataFrame]) -> dict:
    """For each variant, the fraction of its trades whose ``candidate_ms`` appears in each other variant, and in any.

    Trades without a candidate candle are left out; None when a variant has none.
    """
    keys = {name: set(int(x) for x in t["candidate_ms"].dropna()) for name, t in tables.items()}
    out = {}
    for a, t in tables.items():
        mine = [int(x) for x in t["candidate_ms"].dropna()]
        row = {}
        for b in tables:
            if b != a:
                row[b] = float(np.mean([x in keys[b] for x in mine])) if mine else None
        others = set().union(*(keys[b] for b in tables if b != a)) if len(tables) > 1 else set()
        row["any"] = float(np.mean([x in others for x in mine])) if mine else None
        out[a] = row
    return out


def max_drawdown(equity, start: float) -> float | None:
    """Largest fall from a running peak, as a fraction: ``max(1 − equity / peak)`` with ``start`` as the first peak."""
    eq = np.asarray(equity, dtype=np.float64)
    if len(eq) == 0:
        return None
    peak = np.maximum.accumulate(np.concatenate(([start], eq)))[1:]
    return float(np.max(1.0 - eq / peak))
