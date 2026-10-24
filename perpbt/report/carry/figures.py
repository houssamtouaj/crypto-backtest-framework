"""The carry report figures (carry spec Phase 6 §6.1): one function per figure, one PNG each.

Each function takes tables of the run folder (``daily``, ``daily_pair``, ``signals``, ``random_timing``) and
blocks of ``stats.json``, writes the PNG and returns its path; nothing is simulated here. The V2 replicates are the
caller's (``report.v2_replicates``: redrawn with the verdict's seed and checked against ``stats.json``). A missing
input renders a placeholder note instead of failing.

The plumbing, sizes and colours are the first strategy's (``perpbt.report.figures``: matplotlib's object API, PNG,
fixed sizes, no interactive output). Series: the row blue, ``always_on`` orange, the hurdle a dashed grey; the
decomposition funding aqua, basis blue, fees orange, slippage violet. Hedged pair-days are shaded.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.dates import DateFormatter, MonthLocator
from matplotlib.ticker import FixedLocator, FuncFormatter, MaxNLocator, NullLocator

from perpbt.report import figures as fg
from perpbt.report.figures import BUYHOLD, FOURTH, INK, INK2, MUTED, STRATEGY, THIRD, placeholder

ON_SHADE = "#dfe9f6"
LOSS = "#e34948"
THRESHOLD_LIMIT = 10.0  # annualised; a threshold beyond it (always_on's -1e9) is not drawn
DAYS_PER_YEAR = 365
FIGURES = ("equity", "funding", "basis", "decomposition", "v2_bootstrap", "random_timing")
COMPONENTS = (("funding", 1.0, THIRD, "funding"), ("basis", 1.0, STRATEGY, "basis"),
              ("fees", -1.0, BUYHOLD, "fees"), ("slippage", -1.0, FOURTH, "slippage"))


def _empty(df) -> bool:
    return df is None or len(df) == 0


def equity0(daily: pd.DataFrame) -> float:
    """The starting equity: the first day's equity before its return."""
    return float(daily["equity"].iloc[0]) / (1.0 + float(daily["ret"].iloc[0]))


def _equity_ticks(ax, values: np.ndarray, n: int = 7) -> None:
    v = values[np.isfinite(values) & (values > 0)]
    if len(v) == 0:
        return
    ticks = sorted({float(f"{x:.3g}") for x in np.geomspace(v.min(), v.max(), n)})
    ax.yaxis.set_major_locator(FixedLocator(ticks))
    ax.yaxis.set_minor_locator(NullLocator())
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:,.0f}"))


def equity(daily: pd.DataFrame | None, path, *, hurdle_annual: float, always_on: pd.DataFrame | None = None,
           row: str = "primary") -> Path:
    """The row's equity, ``always_on``'s on the same dates (when given) and the hurdle compounding daily from the
    same start (``(1 + h)^(1/365)`` a day, V2's rate); log scale."""
    if _empty(daily):
        return placeholder(path, "Equity", "no daily marks in this period")
    d = fg._dates(daily["date"])
    e0 = equity0(daily)
    hurdle = e0 * (1.0 + hurdle_annual) ** (np.arange(1, len(daily) + 1) / DAYS_PER_YEAR)
    fig, (ax,) = fg._figure()
    ax.plot(d, daily["equity"].to_numpy(dtype=np.float64), color=STRATEGY, lw=1.6, label=row)
    series = [daily["equity"].to_numpy(dtype=np.float64), hurdle]
    if not _empty(always_on) and row != "always_on":
        ao = daily[["date"]].merge(always_on[["date", "equity"]], on="date", how="left")
        ax.plot(d, ao["equity"].to_numpy(dtype=np.float64), color=BUYHOLD, lw=1.4, label="always_on (no filter)")
        series.append(ao["equity"].to_numpy(dtype=np.float64))
    ax.plot(d, hurdle, color=MUTED, lw=1.2, ls="--", label=f"hurdle {100 * hurdle_annual:g}%/yr, compounding")
    ax.set_yscale("log")
    _equity_ticks(ax, np.concatenate(series))
    ax.set_ylabel("equity (USDT, log scale)")
    ax.set_title(f"Equity: {row} against the hurdle")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    return fg._save(fig, path)


# --- per pair --------------------------------------------------------------------------------------------------

def _pairs(signals: pd.DataFrame) -> list[str]:
    return list(dict.fromkeys(signals["pair"].tolist()))


def _hedged(daily_pair: pd.DataFrame | None, pair: str) -> pd.Series:
    """Per date, whether the pair was hedged at the close (short perp held)."""
    if _empty(daily_pair):
        return pd.Series(dtype=bool)
    rows = daily_pair[daily_pair["pair"] == pair]
    return pd.Series((rows["perp_qty"] < 0).to_numpy(), index=rows["date"].to_numpy())


def _panels(n: int, title: str):
    ncols = 1 if n == 1 else 2
    nrows = math.ceil(n / ncols)
    fig, axes = fg._figure(ncols, nrows, size=(10.0, 1.2 + 1.9 * nrows))
    for ax in axes[n:]:
        ax.set_visible(False)
    fig.suptitle(title, color=INK, fontsize=11)
    return fig, axes


def _date_ticks(ax, span: pd.Timedelta) -> None:
    """Month ticks by the span's length (narrow panels): every 6, 3 or 2 months, labelled ``YYYY-MM``."""
    step = 6 if span.days > 900 else 3 if span.days > 400 else 2
    ax.xaxis.set_major_locator(MonthLocator(bymonth=range(1, 13, step)))
    ax.xaxis.set_major_formatter(DateFormatter("%Y-%m"))


def _shade(ax, dates: pd.DatetimeIndex, on: np.ndarray) -> None:
    """Shade the ``on`` days over the full height, after the data is drawn (the y limits are kept)."""
    if len(dates) and on.any():
        lim = ax.get_ylim()
        ax.fill_between(dates, 0, 1, where=on, step="post", transform=ax.get_xaxis_transform(), color=ON_SHADE,
                        lw=0, zorder=0)
        ax.set_ylim(lim)


def _per_pair(signals, daily_pair, path, *, title: str, column: str, ylabel: str, lines=()) -> Path:
    pairs = _pairs(signals)
    fig, axes = _panels(len(pairs), title)
    span = fg._dates([signals["date"].min(), signals["date"].max()])
    for ax, pair in zip(axes, pairs, strict=False):
        rows = signals[signals["pair"] == pair]
        d = fg._dates(rows["date"])
        hedged = _hedged(daily_pair, pair).reindex(rows["date"].to_numpy(), fill_value=False).to_numpy(dtype=bool)
        for y, style in lines:
            ax.axhline(y, color=INK2, lw=0.9, ls=style)
        ax.plot(d, 100.0 * rows[column].to_numpy(dtype=np.float64), color=STRATEGY, lw=0.9)
        _shade(ax, d, hedged)
        ax.set_xlim(span[0], span[1])
        _date_ticks(ax, span[1] - span[0])
        ax.set_title(pair, fontsize=9)
        ax.set_ylabel(ylabel, fontsize=8)
    return fg._save(fig, path)


def funding(signals: pd.DataFrame | None, daily_pair: pd.DataFrame | None, path, *, entry: float,
            exit: float) -> Path:
    """Trailing annualised funding ``f̄`` per pair (one panel each) with the entry (dashed) and exit (dotted)
    thresholds and the days hedged at the close shaded. A threshold beyond ±``THRESHOLD_LIMIT`` (``always_on``)
    is not drawn."""
    title = "Trailing funding per pair (annualised; shaded: hedged at the close)"
    if _empty(signals):
        return placeholder(path, "Trailing funding per pair", "no signals table in this run folder")
    lines = [(100.0 * x, style) for x, style in ((entry, "--"), (exit, ":")) if abs(x) <= THRESHOLD_LIMIT]
    if not lines:
        title += "; always on: no thresholds"
    else:
        title += f"; entry {100 * entry:g}% dashed, exit {100 * exit:g}% dotted"
    return _per_pair(signals, daily_pair, path, title=title, column="fbar", ylabel="% / yr", lines=lines)


def basis(signals: pd.DataFrame | None, daily_pair: pd.DataFrame | None, path) -> Path:
    """Daily basis ``perp / spot − 1`` at the close per pair (one panel each), the days hedged shaded."""
    if _empty(signals):
        return placeholder(path, "Basis per pair", "no signals table in this run folder")
    return _per_pair(signals, daily_pair, path, title="Basis perp / spot − 1 at the close (shaded: hedged)",
                     column="basis", ylabel="%", lines=[(0.0, "-")])


# --- decomposition ---------------------------------------------------------------------------------------------

def decomposition(per_year: Sequence[Mapping] | None, path) -> Path:
    """Per calendar year, the return's parts as % of equity: funding and basis up or down, fees and slippage down
    (stacked; they sum to the net, marked), and in a second panel the rebalance and liquidation costs, which are a
    part of fees + slippage, not in addition to them."""
    if not per_year:
        return placeholder(path, "Return decomposition per year", "no return days")
    years = [str(r["year"]) for r in per_year]
    x = np.arange(len(years))
    fig, axes = fg._figure(2, 1, size=(10.0, 4.6))
    ax = axes[0]
    up, down = np.zeros(len(years)), np.zeros(len(years))
    for key, sign, color, label in COMPONENTS:
        v = np.array([100.0 * sign * float(r[key]) for r in per_year])
        bottom = np.where(v >= 0, up, down)
        ax.bar(x, v, bottom=bottom, color=color, width=0.62, label=label, edgecolor=fg.SURFACE, linewidth=1.0)
        up, down = up + np.maximum(v, 0), down + np.minimum(v, 0)
    net = np.array([100.0 * float(r["ret_sum"]) for r in per_year])
    ax.scatter(x, net, color=INK, marker="D", s=28, zorder=3, label="net")
    ax.axhline(0.0, color=MUTED, lw=0.8)
    ax.set_xticks(x, years)
    ax.set_ylabel("% of equity (sum of daily)")
    ax.set_title("Return by source", fontsize=10)
    ax.legend(frameon=False, fontsize=8, ncols=5, loc="upper center", bbox_to_anchor=(0.5, -0.08))
    ax = axes[1]
    w = 0.36
    for k, (key, color, label) in enumerate((("rebalance_cost", BUYHOLD, "rebalances"),
                                             ("liquidation_cost", LOSS, "liquidations"))):
        v = np.array([-100.0 * float(r[key]) for r in per_year])
        ax.bar(x + (k - 0.5) * w, v, width=w, color=color, label=label)
    ax.axhline(0.0, color=MUTED, lw=0.8)
    ax.set_xticks(x, years)
    ax.set_ylabel("% of equity (sum of daily)")
    ax.set_title("Of the costs: rebalances and liquidations", fontsize=10)
    ax.legend(frameon=False, fontsize=8)
    fig.suptitle("Return decomposition per year", color=INK, fontsize=11)
    return fg._save(fig, path)


# --- V2 and random timing --------------------------------------------------------------------------------------

def v2_bootstrap(replicates: np.ndarray | None, v2: Mapping | None, path) -> Path:
    """The moving-block bootstrap of V2's annualised excess over the hurdle, with 0, the 95% CI and the
    observed excess marked."""
    title = "V2: bootstrap of the annualised excess return over the hurdle"
    if replicates is None or v2 is None or len(fg._finite(replicates)) == 0:
        return placeholder(path, title, "the replicates could not be reproduced from the run folder")
    reps = 100.0 * fg._finite(replicates)
    fig, (ax,) = fg._figure()
    ax.hist(reps, bins=60, color=MUTED, zorder=2, label=f"replicates ({len(reps)})")
    ax.axvline(0.0, color=INK, lw=1.2, label="0 (the hurdle)")
    lo, hi = (100.0 * float(c) for c in v2["ann_excess_ci"])
    for c in (lo, hi):
        ax.axvline(c, color=BUYHOLD, lw=1.2, ls="--")
    ax.axvspan(lo, hi, color=BUYHOLD, alpha=0.08, lw=0, zorder=0, label=f"95% CI {lo:.2f} to {hi:.2f}")
    obs = 100.0 * float(v2["ann_excess"])
    ax.axvline(obs, color=STRATEGY, lw=2.0, label=f"observed {obs:.2f}")
    ax.set_xlabel(f"annualised excess over the {100 * float(v2['hurdle_annual']):g}%/yr hurdle (% / yr)")
    ax.set_ylabel("replicates")
    ax.set_title(f"{title} (p = {float(v2['p']):.3g})")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    return fg._save(fig, path)


def random_timing(table: pd.DataFrame | None, rt: Mapping | None, path) -> Path:
    """The shifted-timing paths' annualised net return and Sharpe, with the primary marked (descriptive)."""
    title = "Random timing: the primary's on/off series shifted in time"
    if _empty(table) or rt is None:
        return placeholder(path, title, "computed for the portfolio primary only")
    fig, axes = fg._figure(2, 1)
    for ax, key, scale, label in ((axes[0], "ann_return", 100.0, "annualised net return (% / yr)"),
                                  (axes[1], "sharpe", 1.0, "Sharpe (annualised)")):
        v = fg._finite(table[key]) * scale
        if len(v):
            ax.hist(v, bins=40, color=MUTED, alpha=0.8, label=f"paths ({len(v)})")
        p = rt[key]["primary"]
        if p is not None:
            ax.axvline(scale * float(p), color=STRATEGY, lw=2.0, label=f"primary (rank {rt[key]['rank']})")
        ax.set_xlabel(label)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.legend(frameon=False, fontsize=8, loc="upper left")
    axes[0].set_ylabel("paths")
    fig.suptitle(title, color=INK, fontsize=11)
    return fg._save(fig, path)
