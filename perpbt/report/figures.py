"""The report figures (spec §7.1): one function per figure, one PNG each.

Each function takes the tables it needs and a path, writes the PNG and
returns the path. The inputs are the variant folder's tables (``daily``,
``trades`` (the R subset), ``buyhold``, the baseline run tables) and blocks
of ``stats.json``; nothing is simulated or re-estimated here. A missing
optional input (no baselines yet, no holdout, no trades, a grid that has
not run) renders a placeholder note instead of failing.

Figures use matplotlib's object API (``Figure``, no pyplot), so importing
this module never changes the global backend. Colours: strategy blue,
buy-and-hold orange, a third series aqua; diverging blue (positive) to red
(negative) through a neutral grey; sequential blue for counts.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator

from perpbt.stats.baselines import run_means

STRATEGY, BUYHOLD, THIRD, FOURTH = "#2a78d6", "#eb6834", "#1baf7a", "#6250d6"
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8985", "#e4e3df", "#fcfcfb"
HOLDOUT_SHADE = "#f0efec"
DIVERGING = LinearSegmentedColormap.from_list("perpbt_div", ["#e34948", "#f0efec", "#2a78d6"])
SEQUENTIAL = LinearSegmentedColormap.from_list("perpbt_seq", ["#cde2fb", "#104281"])
EXIT_STYLE = {"stop": (BUYHOLD, "o"), "target": (STRATEGY, "s"), "time": (THIRD, "^"), "data_end": (MUTED, "x")}
DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DPI = 110


# --- plumbing ------------------------------------------------------------------------------------

def _figure(ncols: int = 1, nrows: int = 1, *, size=(9.0, 4.6)) -> tuple[Figure, np.ndarray]:
    fig = Figure(figsize=size, dpi=DPI, facecolor=SURFACE, layout="constrained")
    axes = np.atleast_1d(fig.subplots(nrows, ncols, squeeze=False).ravel())
    for ax in axes:
        _style(ax)
    return fig, axes


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.title.set_color(INK)
    ax.xaxis.label.set_color(INK2)
    ax.yaxis.label.set_color(INK2)


def _save(fig: Figure, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="png", facecolor=SURFACE)
    return path


def placeholder(path: str | Path, title: str, note: str) -> Path:
    """A PNG that states why the figure is empty."""
    fig = Figure(figsize=(9.0, 2.2), dpi=DPI, facecolor=SURFACE)
    fig.text(0.5, 0.62, title, ha="center", va="center", fontsize=12, color=INK)
    fig.text(0.5, 0.35, note, ha="center", va="center", fontsize=10, color=INK2, style="italic")
    return _save(fig, path)


def _finite(x) -> np.ndarray:
    a = np.asarray(x, dtype=np.float64)
    return a[np.isfinite(a)]


def _dates(col) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(pd.Series(list(col)).astype(str)))


def _num(x) -> float:
    return float("nan") if x is None else float(x)


# --- equity and drawdown -------------------------------------------------------------------------

def _curves(daily: pd.DataFrame, bh: pd.DataFrame, scale, start_equity: float) -> pd.DataFrame:
    """``date``, strategy equity and vol-scaled buy-and-hold equity on the strategy's days."""
    both = daily[["date", "equity"]].merge(bh[["date", "ret"]], on="date", how="left")
    k = _num(scale)
    k = k if np.isfinite(k) else 1.0
    bh_eq = start_equity * np.cumprod(1.0 + k * both["ret"].fillna(0.0).to_numpy(dtype=np.float64))
    return pd.DataFrame({"date": _dates(both["date"]), "strategy": both["equity"].to_numpy(dtype=np.float64),
                         "buyhold": bh_eq})


def _chained(daily, bh, scale, start_equity, holdout) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    cur = _curves(daily, bh, scale, start_equity)
    if not holdout:
        return cur, None
    h = _curves(holdout["daily"], holdout["bh"], holdout.get("scale"), holdout["start_equity"])
    if len(h) == 0 or len(cur) == 0:
        return cur, None
    h["strategy"] *= cur["strategy"].iloc[-1] / holdout["start_equity"]
    h["buyhold"] *= cur["buyhold"].iloc[-1] / holdout["start_equity"]
    return pd.concat([cur, h], ignore_index=True), h["date"].iloc[0]


def _log_ticks(ax, values: np.ndarray, n: int = 7) -> None:
    """About ``n`` log-spaced ticks rounded to two significant digits, labelled in plain USD."""
    v = values[np.isfinite(values) & (values > 0)]
    if len(v) == 0:
        return
    ticks = sorted({float(f"{x:.2g}") for x in np.geomspace(v.min(), v.max(), n)})
    ax.yaxis.set_major_locator(FixedLocator(ticks))
    ax.yaxis.set_minor_locator(NullLocator())
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:,.0f}"))


def _shade(ax, start, end) -> None:
    if start is not None:
        ax.axvspan(start, end, color=HOLDOUT_SHADE, zorder=0, lw=0)
        ax.text(start, 1.0, " holdout", transform=ax.get_xaxis_transform(), va="top", fontsize=8, color=INK2)


def _scale_note(scale) -> str:
    k = _num(scale)
    return f"buy-and-hold, vol-scaled ×{k:.2f}" if np.isfinite(k) else "buy-and-hold (not vol-scaled: σ undefined)"


def equity(daily: pd.DataFrame, bh: pd.DataFrame | None, path, *, start_equity: float, scale=None,
           holdout: Mapping | None = None) -> Path:
    """Strategy equity vs vol-scaled buy-and-hold, log scale; the holdout shaded when ``holdout`` is given.

    ``holdout`` = ``{"daily", "bh", "scale", "start_equity"}`` of the
    holdout run, chained onto the end of this one.
    """
    if daily is None or len(daily) == 0:
        return placeholder(path, "Equity", "no daily marks in this period")
    if bh is None:
        bh = pd.DataFrame({"date": daily["date"], "ret": np.zeros(len(daily))})
    cur, h0 = _chained(daily, bh, scale, start_equity, holdout)
    fig, (ax,) = _figure()
    ax.plot(cur["date"], cur["strategy"], color=STRATEGY, lw=1.6, label="strategy")
    ax.plot(cur["date"], cur["buyhold"], color=BUYHOLD, lw=1.6, label=_scale_note(scale))
    ax.set_yscale("log")
    _log_ticks(ax, np.concatenate([cur["strategy"].to_numpy(), cur["buyhold"].to_numpy()]))
    ax.set_ylabel("equity (USD, log scale)")
    ax.set_title("Equity: strategy vs buy-and-hold")
    _shade(ax, h0, cur["date"].iloc[-1])
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    return _save(fig, path)


def drawdown(daily: pd.DataFrame, bh: pd.DataFrame | None, path, *, start_equity: float, scale=None,
             holdout: Mapping | None = None) -> Path:
    """Daily drawdown of the strategy and of vol-scaled buy-and-hold (fraction below the running peak)."""
    if daily is None or len(daily) == 0:
        return placeholder(path, "Drawdown", "no daily marks in this period")
    if bh is None:
        bh = pd.DataFrame({"date": daily["date"], "ret": np.zeros(len(daily))})
    cur, h0 = _chained(daily, bh, scale, start_equity, holdout)
    fig, (ax,) = _figure()
    for col, color, label in (("strategy", STRATEGY, "strategy"), ("buyhold", BUYHOLD, _scale_note(scale))):
        eq = cur[col].to_numpy()
        peak = np.maximum.accumulate(np.concatenate(([start_equity], eq)))[1:]
        ax.plot(cur["date"], 100.0 * (eq / peak - 1.0), color=color, lw=1.4, label=label)
    ax.set_ylabel("drawdown (%)")
    ax.set_title("Daily drawdown")
    _shade(ax, h0, cur["date"].iloc[-1])
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    return _save(fig, path)


# --- alpha decay ---------------------------------------------------------------------------------

def rolling(rolling_block: Mapping, path) -> Path:
    """Rolling 6-month mean net R (by window end, stepped monthly) and rolling 182-day Sharpe."""
    win = [w for w in rolling_block.get("windows", []) if w.get("mean_net_r") is not None]
    sh = [s for s in rolling_block.get("sharpe", []) if s.get("sharpe") is not None]
    if not win and not sh:
        return placeholder(path, "Rolling metrics", "the period is shorter than one 6-month window")
    fig, axes = _figure(1, 2, size=(9.0, 6.0))
    ax = axes[0]
    if win:
        ax.step(_dates([w["end"] for w in win]), [w["mean_net_r"] for w in win], where="post", color=STRATEGY, lw=1.6)
        ax.axhline(0.0, color=MUTED, lw=0.8)
    else:
        ax.text(0.5, 0.5, "no 6-month window with trades", ha="center", transform=ax.transAxes, color=INK2)
    ax.set_ylabel("mean net R")
    ax.set_title("Rolling 6-month mean net R (window end, monthly steps)")
    ax = axes[1]
    if sh:
        ax.step(_dates([s["end"] for s in sh]), [s["sharpe"] for s in sh], where="post", color=THIRD, lw=1.6)
        ax.axhline(0.0, color=MUTED, lw=0.8)
    else:
        ax.text(0.5, 0.5, "no 182-day window yet", ha="center", transform=ax.transAxes, color=INK2)
    ax.set_ylabel("Sharpe (annualised)")
    ax.set_title("Rolling 182-day daily Sharpe (month ends)")
    return _save(fig, path)


PER_YEAR_PANELS = (("n", "trades"), ("win_rate", "win rate"), ("mean_net_r", "mean net R"),
                   ("sharpe", "Sharpe"), ("max_dd", "max drawdown"), ("fill_rate", "fill rate"))


def per_year(rows: Sequence[Mapping], path) -> Path:
    """Bar table by calendar year: n, win rate, mean net R, Sharpe, max DD, fill rate."""
    if not rows:
        return placeholder(path, "Per year", "no daily marks in this period")
    years = [str(r["year"]) for r in rows]
    fig, axes = _figure(3, 2, size=(10.0, 5.6))
    for ax, (key, title) in zip(axes, PER_YEAR_PANELS, strict=True):
        vals = np.array([_num(r.get(key)) for r in rows])
        ok = np.isfinite(vals)
        ax.bar(np.arange(len(years))[ok], vals[ok], color=STRATEGY, width=0.7)
        ax.axhline(0.0, color=MUTED, lw=0.8)
        ax.set_xticks(np.arange(len(years)), years, rotation=45 if len(years) > 4 else 0)
        ax.set_title(title, fontsize=10)
    return _save(fig, path)


# --- baselines -----------------------------------------------------------------------------------

def baseline(runs: pd.DataFrame | None, path, *, costs: tuple[float, float, float], observed, p, z=None,
             name: str = "A", n_runs=None) -> Path:
    """Histogram of the baseline's run means (net R at ``costs``) with the observed value and ``p`` marked."""
    title = {"A": "Baseline A: random entry time in the same sessions",
             "B": "Baseline B: random sessions and entry times"}.get(name, f"Baseline {name}")
    if runs is None or len(runs) == 0:
        return placeholder(path, title, "baselines not run yet (perpbt baselines)")
    means = _finite(run_means(runs, *costs))
    if len(means) == 0:
        return placeholder(path, title, "no baseline run has a trade")
    fig, (ax,) = _figure()
    ax.hist(means, bins=min(60, max(10, len(means) // 20)), color=MUTED, alpha=0.8, label="run means")
    if observed is not None:
        ax.axvline(observed, color=STRATEGY, lw=2.0, label="observed")
        zs = "" if z is None else f", z = {z:.2f}"
        ps = "n/a" if p is None else f"{p:.3g}"
        ax.text(observed, 0.95, f" observed {observed:.3f}\n p = {ps}{zs}", transform=ax.get_xaxis_transform(),
                va="top", fontsize=9, color=INK)
    ax.set_xlabel("mean net R per run")
    ax.set_ylabel("runs")
    ax.set_title(f"{title} ({len(means) if n_runs is None else n_runs} runs)")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    return _save(fig, path)


def baseline_a(runs, path, **kw) -> Path:
    return baseline(runs, path, name="A", **kw)


def baseline_b(runs, path, **kw) -> Path:
    return baseline(runs, path, name="B", **kw)


# --- heatmaps ------------------------------------------------------------------------------------

def _annotated(ax, mat: np.ndarray, cmap, norm, fmt, x_labels, y_labels, primary) -> None:
    masked = np.ma.masked_invalid(mat)
    ax.imshow(masked, cmap=cmap, norm=norm, aspect="auto")
    ax.grid(False)
    for (r, c), v in np.ndenumerate(mat):
        text = "not run" if not np.isfinite(v) else fmt(v)
        ax.text(c, r, text, ha="center", va="center", fontsize=8, color=INK)
    if primary is not None:
        r, c = primary
        ax.add_patch(Rectangle((c - 0.5, r - 0.5), 1, 1, fill=False, ec=INK, lw=2.2))
    ax.set_xticks(range(len(x_labels)), x_labels, fontsize=8)
    ax.set_yticks(range(len(y_labels)), y_labels, fontsize=8)


def _div_norm(mat: np.ndarray) -> TwoSlopeNorm:
    v = np.nanmax(np.abs(mat)) if np.isfinite(mat).any() else 1.0
    v = v if v > 0 else 1.0
    return TwoSlopeNorm(vcenter=0.0, vmin=-v, vmax=v)


def heatmap(cells: pd.DataFrame, path, *, x_values: Sequence[str], y_values: Sequence[str], x_axis: str,
            y_axis: str = "", title: str = "Heatmap") -> Path:
    """Mean net R, Sharpe and n per grid cell (columns ``x``, ``y``, ``mean_net_r``, ``sharpe_ann``,
    ``n_trades_r``, ``is_primary``); the primary cell outlined; cells without a run marked."""
    if cells is None or len(cells) == 0:
        return placeholder(path, title, "the grid has not run yet (perpbt grid --run)")
    ys = list(y_values) or [""]
    shape = (len(ys), len(x_values))
    mats = {k: np.full(shape, np.nan) for k in ("mean_net_r", "sharpe_ann", "n_trades_r")}
    primary = None
    for row in cells.itertuples(index=False):
        if row.x not in x_values or row.y not in ys:
            continue
        r, c = ys.index(row.y), list(x_values).index(row.x)
        for k in mats:
            mats[k][r, c] = _num(getattr(row, k))
        if row.is_primary:
            primary = (r, c)
    fig, axes = _figure(3, 1, size=(12.0, 1.6 + 0.55 * len(ys) + 1.2))
    n_max = np.nanmax(mats["n_trades_r"]) if np.isfinite(mats["n_trades_r"]).any() else 1.0
    panels = (("mean_net_r", "mean net R", DIVERGING, _div_norm(mats["mean_net_r"]), lambda v: f"{v:.3f}"),
              ("sharpe_ann", "Sharpe", DIVERGING, _div_norm(mats["sharpe_ann"]), lambda v: f"{v:.2f}"),
              ("n_trades_r", "n (R subset)", SEQUENTIAL, None, lambda v: f"{int(v)}"))
    for ax, (key, label, cmap, norm, fmt) in zip(axes, panels, strict=True):
        if norm is None:
            from matplotlib.colors import Normalize

            norm = Normalize(vmin=0.0, vmax=max(n_max, 1.0))
        _annotated(ax, mats[key], cmap, norm, fmt, list(x_values), ys, primary)
        ax.set_title(label, fontsize=10)
        ax.set_xlabel(x_axis)
        if y_axis:
            ax.set_ylabel(y_axis)
    fig.suptitle(f"{title} (primary outlined)", color=INK, fontsize=11)
    return _save(fig, path)


def heatmap_1(cells, path, **kw) -> Path:
    return heatmap(cells, path, **{"title": "Heatmap 1", **kw})


def heatmap_2(cells, path, **kw) -> Path:
    return heatmap(cells, path, **{"title": "Heatmap 2", **kw})


def heatmap_3(cells, path, **kw) -> Path:
    return heatmap(cells, path, **{"title": "Heatmap 3", **kw})


def reprice(rows: Sequence[Mapping], path) -> Path:
    """The re-pricing grid (slippage × maker fee) of mean net R with ``p_A`` annotated (D13)."""
    if not rows:
        return placeholder(path, "Cost re-pricing", "no re-pricing table")
    slips = sorted({r["slippage"] for r in rows})
    makers = sorted({r["fee_maker"] for r in rows})
    mat = np.full((len(slips), len(makers)), np.nan)
    pa = np.full_like(mat, np.nan)
    primary = None
    for r in rows:
        i, j = slips.index(r["slippage"]), makers.index(r["fee_maker"])
        mat[i, j], pa[i, j] = _num(r["mean_net_r"]), _num(r["p_a"])
        if r["is_primary"]:
            primary = (i, j)
    fig, (ax,) = _figure(size=(6.5, 1.6 + 0.8 * len(slips)))
    masked = np.ma.masked_invalid(mat)
    ax.imshow(masked, cmap=DIVERGING, norm=_div_norm(mat), aspect="auto")
    ax.grid(False)
    for (i, j), v in np.ndenumerate(mat):
        p = "p_A n/a" if not np.isfinite(pa[i, j]) else f"p_A {pa[i, j]:.3g}"
        ax.text(j, i, f"{v:.3f}\n{p}" if np.isfinite(v) else "n/a", ha="center", va="center", fontsize=8, color=INK)
    if primary is not None:
        ax.add_patch(Rectangle((primary[1] - 0.5, primary[0] - 0.5), 1, 1, fill=False, ec=INK, lw=2.2))
    ax.set_xticks(range(len(makers)), [f"{100 * m:.2f}%" for m in makers])
    ax.set_yticks(range(len(slips)), [f"{100 * s:.2f}%" for s in slips])
    ax.set_xlabel("maker fee")
    ax.set_ylabel("slippage")
    ax.set_title("Mean net R by cost (taker fixed; registered costs outlined)")
    return _save(fig, path)


# --- costs, stops, trade diagnostics -------------------------------------------------------------

def _no_trades(trades: pd.DataFrame | None) -> bool:
    return trades is None or len(trades) == 0


def cost_r(trades: pd.DataFrame, path, *, share_above_1=None) -> Path:
    """Distribution of cost in R with the share of trades above 1 R stated."""
    if _no_trades(trades):
        return placeholder(path, "Cost in R", "no trades")
    x = _finite(trades["cost_r"])
    fig, (ax,) = _figure()
    ax.hist(x, bins=60, color=STRATEGY, alpha=0.85)
    ax.axvline(1.0, color=BUYHOLD, lw=1.6, ls="--")
    share = "n/a" if share_above_1 is None else f"{100 * share_above_1:.1f}%"
    ax.text(1.0, 0.95, f" cost > 1 R: {share} of trades", transform=ax.get_xaxis_transform(), va="top", fontsize=9,
            color=INK)
    ax.set_xlabel("cost per trade (R): fees, slippage, funding")
    ax.set_ylabel("trades")
    ax.set_title("Cost in R")
    return _save(fig, path)


def stop_dist(trades: pd.DataFrame, path) -> Path:
    """Stop distance in percent of the planned entry and in ATR, and the implied leverage."""
    if _no_trades(trades):
        return placeholder(path, "Stop distance and leverage", "no trades")
    pct = 100.0 * trades["stop_dist"].to_numpy(dtype=np.float64) / trades["planned_entry"].to_numpy(dtype=np.float64)
    fig, axes = _figure(3, 1, size=(11.0, 3.8))
    for ax, x, label, color in ((axes[0], pct, "stop distance (% of entry)", STRATEGY),
                                (axes[1], trades["stop_dist_atr"], "stop distance (ATR14)", THIRD),
                                (axes[2], trades["implied_leverage"], "implied leverage (×)", BUYHOLD)):
        v = _finite(x)
        if len(v):
            ax.hist(v, bins=50, color=color, alpha=0.85)
        ax.set_xlabel(label)
    axes[0].set_ylabel("trades")
    fig.suptitle("Stop distance and implied leverage at 1% risk", color=INK, fontsize=11)
    return _save(fig, path)


def _reasons(trades: pd.DataFrame) -> list[str]:
    present = [str(x) for x in trades["exit_reason"].dropna().unique()]
    return [r for r in EXIT_STYLE if r in present] + sorted(r for r in present if r not in EXIT_STYLE)


def mae_mfe(trades: pd.DataFrame, path) -> Path:
    """MAE vs MFE in R, one colour and marker per exit reason."""
    if _no_trades(trades):
        return placeholder(path, "MAE vs MFE", "no trades")
    fig, (ax,) = _figure(size=(7.0, 5.5))
    for reason in _reasons(trades):
        sel = trades["exit_reason"] == reason
        color, marker = EXIT_STYLE.get(reason, (FOURTH, "D"))
        ax.scatter(trades.loc[sel, "mae_r"], trades.loc[sel, "mfe_r"], s=14, color=color, marker=marker, alpha=0.6,
                   edgecolors="none" if marker != "x" else None, label=f"{reason} ({int(sel.sum())})")
    ax.set_xlabel("MAE (R)")
    ax.set_ylabel("MFE (R)")
    ax.set_title("Maximum adverse vs favourable excursion")
    ax.legend(frameon=False, fontsize=8, title="exit reason", title_fontsize=8)
    return _save(fig, path)


def hold_time(trades: pd.DataFrame, path) -> Path:
    """Hold-time distribution (hours), one panel per exit reason."""
    if _no_trades(trades):
        return placeholder(path, "Hold time", "no trades")
    reasons = _reasons(trades)
    fig, axes = _figure(len(reasons), 1, size=(max(5.0, 3.2 * len(reasons)), 3.6))
    hours = trades["hold_minutes"].to_numpy(dtype=np.float64) / 60.0
    hi = float(np.nanmax(hours)) if len(hours) else 1.0
    bins = np.linspace(0.0, max(hi, 0.25), 40)
    for ax, reason in zip(axes, reasons, strict=True):
        sel = (trades["exit_reason"] == reason).to_numpy()
        ax.hist(hours[sel], bins=bins, color=EXIT_STYLE.get(reason, (FOURTH, ""))[0], alpha=0.85)
        ax.set_title(f"{reason} ({int(sel.sum())})", fontsize=10)
        ax.set_xlabel("hours held")
    axes[0].set_ylabel("trades")
    return _save(fig, path)


# --- breakdowns ----------------------------------------------------------------------------------

def _bars(ax, rows: Sequence[Mapping], labels: Sequence[str], title: str, *, n_font: int = 8) -> None:
    x = np.arange(len(rows))
    vals = np.array([_num(r["mean_net_r"]) for r in rows])
    colors = [STRATEGY if v >= 0 else "#e34948" for v in np.nan_to_num(vals)]
    ax.bar(x, np.nan_to_num(vals), color=colors, width=0.7)
    ax.axhline(0.0, color=MUTED, lw=0.8)
    for xi, v, r in zip(x, np.nan_to_num(vals), rows, strict=True):
        ax.text(xi, v, f"n={r['n']}", ha="center", va="bottom" if v >= 0 else "top", fontsize=n_font, color=INK2)
    ax.set_xticks(x, labels, fontsize=8)
    ax.set_ylabel("mean net R")
    ax.set_title(title, fontsize=10)


def _label(v) -> str:
    return "unlabelled" if v is None else str(v)


def regimes(regime_block: Mapping, path) -> Path:
    """Mean net R and n by trend regime (ADX(14) > 25) and by vol regime (30-day vol vs in-sample median)."""
    trend, vol = regime_block.get("trend") or [], regime_block.get("vol") or []
    if not trend and not vol:
        return placeholder(path, "Regimes", "no trades")
    fig, axes = _figure(2, 1)
    _bars(axes[0], trend, [_label(r["key"]) for r in trend], "trend regime")
    _bars(axes[1], vol, [_label(r["key"]) for r in vol], "volatility regime")
    return _save(fig, path)


def calendar(breakdowns: Mapping, path) -> Path:
    """Mean net R and n by day of week and by entry hour (UTC)."""
    dow, hour = breakdowns.get("dow") or [], breakdowns.get("entry_hour_utc") or []
    if not dow and not hour:
        return placeholder(path, "Calendar", "no trades")
    fig, axes = _figure(1, 2, size=(10.0, 6.4))
    _bars(axes[0], dow, [DOW[int(r["key"])] if r["key"] is not None else "?" for r in dow], "day of week (UTC)")
    _bars(axes[1], hour, [_label(r["key"]) for r in hour], "entry hour (UTC)", n_font=6)
    return _save(fig, path)


FUNNEL_STAGES = (("impulses", "impulses"), ("blocks_seen", "blocks seen"), ("intents", "intents"))


def funnel(skips: Mapping, summary: Mapping, path) -> Path:
    """Impulses → blocks seen → intents → orders → fills → trades, and every skip reason's count."""
    stages = [(label, skips.get(key)) for key, label in FUNNEL_STAGES]
    stages += [("orders", summary.get("n_orders")), ("fills", summary.get("n_filled")),
               ("trades", summary.get("n_trades")), ("trades (R subset)", summary.get("n_trades_r"))]
    reasons = [(k, v) for k, v in skips.items() if k not in {"impulses", "blocks_seen", "intents"}]
    fig, axes = _figure(2, 1, size=(11.0, 4.2))
    ax = axes[0]
    labels = [s for s, _ in stages][::-1]
    vals = [0 if v is None else v for _, v in stages][::-1]
    ax.barh(labels, vals, color=STRATEGY, height=0.65)
    for y, v in enumerate(vals):
        ax.text(v, y, f" {v}", va="center", fontsize=8, color=INK)
    ax.set_title("Funnel", fontsize=10)
    ax = axes[1]
    labels = [k for k, _ in reasons][::-1]
    vals = [0 if v is None else v for _, v in reasons][::-1]
    ax.barh(labels, vals, color=MUTED, height=0.65)
    for y, v in enumerate(vals):
        ax.text(v, y, f" {v}", va="center", fontsize=8, color=INK)
    ax.set_title("Skip reasons", fontsize=10)
    return _save(fig, path)


def concurrency(daily: pd.DataFrame, path, *, max_concurrent=None) -> Path:
    """Open positions at each daily mark; the intraday maximum marked."""
    if daily is None or len(daily) == 0:
        return placeholder(path, "Concurrency", "no daily marks in this period")
    d = _dates(daily["date"])
    n = daily["n_open"].to_numpy(dtype=np.float64)
    fig, (ax,) = _figure()
    ax.step(d, n, where="post", color=STRATEGY, lw=1.2, label="open at the daily mark")
    if max_concurrent is not None:
        ax.axhline(max_concurrent, color=BUYHOLD, lw=1.2, ls="--", label=f"max concurrent (any candle) = {max_concurrent}")
    k = int(np.argmax(n))
    ax.scatter([d[k]], [n[k]], color=INK, s=24, zorder=3, label=f"max at a daily mark = {int(n[k])}")
    ax.set_ylabel("open positions")
    ax.set_title("Concurrent positions")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    return _save(fig, path)


FIGURES = ("equity", "drawdown", "rolling", "per_year", "baseline_a", "baseline_b", "heatmap_1", "heatmap_2",
           "heatmap_3", "reprice", "cost_r", "stop_dist", "mae_mfe", "hold_time", "regimes", "calendar", "funnel",
           "concurrency")
