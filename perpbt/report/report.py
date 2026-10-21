"""The per-variant report (spec §7.2): ``runs/<variant_id>/report.md`` and its ``figures/``.

``VariantRun`` reads one variant folder (``stats.json``, ``config.yaml``
and the Parquet tables). ``render_report`` writes the figures and returns
the Markdown; every number in its tables is a value of ``stats.json``
(formatted by ``report.markdown``), so the test can parse the tables back.
What one folder cannot know comes in a ``ReportContext``: the grid rows of
the heatmaps (from ``results.parquet``), links to the grid variants, the
trade tables of the other session variants (the shared-trade fraction),
the holdout run that continues the equity curve, the data gaps that
overlap trades, and the holdout code-change reason. Those few derived
numbers are stated in the text, marked as computed at report time.

``write_reports`` builds the contexts from ``results.parquet``, the
registry and the prereg and writes the primary reports (in-sample and
holdout) and, with ``grid=True``, every in-sample grid variant's report.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from perpbt.config import DataConfig, VariantConfig, load_yaml
from perpbt.data.store import CandleStore
from perpbt.experiments.registry import Registry
from perpbt.report import figures as fg
from perpbt.report.markdown import NA, fbool, fci, fint, fnum, fp, fpct, table
from perpbt.stats.baselines import r_subset
from perpbt.stats.diagnostics import QUANTILES, shared_fraction
from perpbt.stats.variant import read_stats
from perpbt.strategy.order_block import SKIP_REASONS

REPORT_FILE = "report.md"
FIGURES_DIR = "figures"
SESSION_NAMES = {"utc": "UTC", "ny": "NY", "london": "London"}
SECTIONS = ("Funnel", "Headline", "Baselines", "Buy-and-hold", "Costs", "Robustness", "Alpha decay",
            "Regimes and calendar", "Diagnostics", "Deflated Sharpe ratio", "Notes")
DOW = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def session_label(name: str) -> str:
    return SESSION_NAMES.get(name, name)


def cell_label(pair: str, session: str) -> str:
    return f"{pair} / {session_label(session)}"


def param_label(axis: str, value) -> str:
    """A grid axis value as a short label: ``0.1 ATR``, ``0.25%``, ``max_hold 24h``, ``1.5``."""
    if isinstance(value, Mapping) and "kind" in value:
        if axis == "stop_buffer" or "value" in value:
            v = float(value["value"])
            return f"{v:g} ATR" if value["kind"] == "atr" else f"{100 * v:g}%"
        return f"max_hold {float(value['hours']):g}h" if value["kind"] == "max_hold" else str(value["kind"])
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


# --- one folder ----------------------------------------------------------------------------------

@dataclass
class VariantRun:
    """One variant folder: ``stats.json``, ``config.yaml`` and the Parquet tables (read on demand)."""

    path: Path
    stats: dict
    cfg: VariantConfig
    _tables: dict = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, path: str | Path) -> VariantRun:
        path = Path(path)
        return cls(path, read_stats(path / "stats.json"), load_yaml(path / "config.yaml", VariantConfig))

    def table(self, name: str) -> pd.DataFrame | None:
        if name not in self._tables:
            f = self.path / f"{name}.parquet"
            self._tables[name] = pq.read_table(f).to_pandas() if f.exists() else None
        return self._tables[name]

    @property
    def variant_id(self) -> str:
        return self.stats["variant_id"]

    @property
    def experiment(self) -> dict:
        return self.stats.get("experiment") or {}

    @property
    def is_primary(self) -> bool:
        return bool(self.experiment.get("is_primary"))

    @property
    def params(self) -> dict:
        return json.loads(self.cfg.canonical_json())["params"]


@dataclass
class Heatmap:
    """One heatmap of a pair × session: its number, axes, value labels and grid rows (``figures.heatmap``)."""

    n: int
    axes: tuple[str, ...]
    x_values: list[str]
    y_values: list[str]
    cells: pd.DataFrame  # x, y, mean_net_r, sharpe_ann, n_trades_r, is_primary
    expected: int


@dataclass
class ReportContext:
    """What the report needs beyond the variant folder (all optional)."""

    alpha: float = 0.05
    heatmaps: list[Heatmap] | None = None  # in-sample primary only
    grid_links: list[tuple[str, str, str]] = field(default_factory=list)  # (members, differing params, link)
    primary_link: str | None = None  # a grid variant's link to its primary report
    shared: dict | None = None  # other session -> fraction of this variant's trades, and "any"
    holdout: VariantRun | None = None  # continues the equity curve
    gap_overlaps: dict | None = None  # tf -> {"gaps": n, "trades": k}; None when not checked
    holdout_reason: str | None = None


# --- sections ------------------------------------------------------------------------------------

def _fig(lines: list[str], path: Path, alt: str) -> None:
    lines.append(f"![{alt}]({FIGURES_DIR}/{path.name})")
    lines.append("")


def _header(run: VariantRun) -> list[str]:
    st, cfg, exp = run.stats, run.cfg, run.experiment
    period = "holdout" if cfg.is_holdout else "in-sample"
    role = "primary" if run.is_primary else "grid variant"
    s = cfg.session
    members = ", ".join(exp.get("members") or []) or "none"
    return [
        f"# {cell_label(st['pair'], st['session_variant'])}, {period} {role}",
        "",
        f"- Pair: {st['pair']}",
        f"- Session: {session_label(s.name)} ({s.open}–{s.close} {s.tz}, weekdays {list(s.days)}, Mon = 0)",
        f"- Period: {cfg.period_start} to {cfg.period_end} ({'holdout' if cfg.is_holdout else 'in-sample'})",
        f"- Variant id: `{run.variant_id}`",
        f"- Code version: `{exp.get('code_version', NA)}`",
        f"- Seed: {st['seeds']['master_seed']} (generators per purpose: {', '.join(st['seeds']['purposes'])})",
        f"- Role: {role}; member of {members}",
        f"- Costs: maker {fpct(cfg.exec.fee_maker, 2)}, taker {fpct(cfg.exec.fee_taker, 2)}, "
        f"slippage {fpct(cfg.exec.slippage, 2)}; risk {fpct(cfg.exec.risk_per_trade, 1)} per trade; "
        f"1m resolution {'on' if cfg.exec.use_1m else 'off'}",
        "",
    ]


def _funnel(run: VariantRun, out: Path) -> list[str]:
    sk, s = run.stats["skips"], run.stats["summary"]
    rows = [("impulses", fint(sk.get("impulses"))), ("blocks seen", fint(sk.get("blocks_seen")))]
    rows += [(f"skipped: {r}", fint(sk.get(r))) for r in SKIP_REASONS]
    rows += [("intents", fint(sk.get("intents"))), ("skipped: leverage cap", fint(sk.get("leverage"))),
             ("orders placed", fint(s["n_orders"])), ("orders filled", fint(s["n_filled"])),
             ("fill rate", fpct(s["fill_rate"])), ("trades", fint(s["n_trades"])),
             ("trades in the R subset", fint(s["n_trades_r"])), ("trades open at the data end", fint(s["n_data_end"]))]
    lines = ["## Funnel", "",
             "Every impulse that did not become a trade is counted by the first reason that stopped it "
             "(spec overview §6). Orders exclude intents skipped by the leverage cap.", "",
             table(["Stage", "Count"], rows), ""]
    _fig(lines, fg.funnel(sk, s, out / "funnel.png"), "funnel")
    return lines


def _headline(run: VariantRun) -> list[str]:
    h = run.stats["headline"]
    rows = [
        ("trades (R subset)", fint(h["n"])), ("win rate", fpct(h["win_rate"])),
        ("mean gross R", fnum(h["mean_gross_r"])), ("mean net R", fnum(h["mean_net_r"])),
        ("mean net R, 95% CI (trade bootstrap)", fci(h["mean_net_r_ci"]["lo"], h["mean_net_r_ci"]["hi"])),
        ("mean net R, 95% CI (day-block bootstrap)",
         fci(h["mean_net_r_ci_block"]["lo"], h["mean_net_r_ci_block"]["hi"])),
        ("median net R", fnum(h["median_net_r"])), ("std of net R", fnum(h["std_net_r"])),
        ("profit factor", fnum(h["profit_factor"], 2)), ("mean cost (R)", fnum(h["avg_cost_r"])),
        ("mean funding (R)", fnum(h["avg_funding_r"])), ("mean hold (minutes)", fnum(h["avg_hold_min"], 1)),
        ("Sharpe (annualised, daily)", fnum(h["sharpe_ann"], 2)),
        ("Sharpe, 95% CI (10-day block bootstrap)", fci(h["sharpe_ci"]["lo"], h["sharpe_ci"]["hi"], 2)),
        ("max drawdown", fpct(h["max_dd"])), ("total return", fpct(h["total_return"])),
        ("exposure (share of candles in a trade)", fpct(h["exposure_time"])),
        ("mean notional / equity at the daily marks", fnum(h["exposure_notional"], 3)),
        ("max concurrent positions", fint(h["max_concurrent"])),
    ]
    return ["## Headline", "",
            "Trade statistics use the R subset: trades closed by a stop, target or time exit "
            "(`exit_reason != data_end`); trades still open at the end of the data are left out.", "", table(["Headline", "Value"], rows), ""]


def _holm_note(run: VariantRun) -> str:
    holm = run.stats.get("holm")
    if holm:
        family = {"insample": "in-sample", "holdout": "holdout"}.get(holm["family"], holm["family"])
        return (f"Holm adjustment over the {family} family of {holm['n']} primary cells "
                f"(tested: A {holm['n_tested']['a']}, B {holm['n_tested']['b']}, buy-and-hold "
                f"{holm['n_tested']['bh']}) at α = {holm['alpha']}.")
    if run.is_primary:
        return "Holm adjustment not applied yet (`perpbt stats holm` after the baselines of all nine cells)."
    return "The Holm adjustment covers the nine primary cells only; grid variants report raw p-values."


def _baselines(run: VariantRun, out: Path) -> list[str]:
    b, holm, cfg = run.stats["baselines"], run.stats.get("holm") or {}, run.cfg
    costs = (cfg.exec.fee_maker, cfg.exec.fee_taker, cfg.exec.slippage)
    lines = ["## Baselines", ""]
    if b["n_runs"] is None:
        lines += ["Baselines have not run yet (`perpbt baselines`).", ""]
    else:
        rows = [
            ("A: random entry time in the strategy's own sessions", fnum(b["observed_a"]), fnum(b["runs_a_mean"]),
             fnum(b["runs_a_std"]), fp(b["p_a"]), fnum(b["z_a"], 2), fp(holm.get("p_a_adj")), fint(b["n_runs"])),
            ("B: random sessions and entry times", fnum(b["observed_b"]), fnum(b["runs_b_mean"]),
             fnum(b["runs_b_std"]), fp(b["p_b"]), fnum(b["z_b"], 2), fp(holm.get("p_b_adj")), fint(b["n_runs"])),
        ]
        lines += [table(["Baseline", "Observed mean net R", "Runs: mean", "Runs: std", "p (one-sided)", "z",
                         "p (Holm)", "Runs"], rows), "",
                  table(["Table A", "Value"], [("trades covered", fint(b["n_a"])),
                                               ("trades left out", fint(b["n_excluded_a"])),
                                               ("baseline B: mean trades per run", fnum(b["runs_b_mean_n"], 1))]),
                  ""]
    lines += [_holm_note(run), "",
              "Baseline design (judgment calls, overview risk 13): a baseline trade enters at the open of a "
              "uniformly drawn in-session candle with the maker fee, keeps the real trade's stop distance in "
              "ATR and the variant's target and hold rule, and pays actual funding; baseline trades that reach "
              "the data end are left out of their run's mean.", ""]
    _fig(lines, fg.baseline_a(run.table("baseline_a_runs"), out / "baseline_a.png", costs=costs,
                              observed=b["observed_a"], p=b["p_a"], z=b["z_a"], n_runs=b["n_runs"]), "baseline A")
    _fig(lines, fg.baseline_b(run.table("baseline_b"), out / "baseline_b.png", costs=costs,
                              observed=b["observed_b"], p=b["p_b"], z=b["z_b"], n_runs=b["n_runs"]), "baseline B")
    return lines


def _holdout_series(ctx: ReportContext) -> dict | None:
    h = ctx.holdout
    if h is None or h.table("daily") is None:
        return None
    return {"daily": h.table("daily"), "bh": h.table("buyhold"), "scale": h.stats["buy_and_hold"]["scale"],
            "start_equity": h.stats["summary"]["start_equity"]}


def _buy_and_hold(run: VariantRun, ctx: ReportContext, out: Path) -> list[str]:
    bh, holm = run.stats["buy_and_hold"], run.stats.get("holm") or {}
    rows = [
        ("days", fint(bh["n_days"])), ("buy-and-hold Sharpe", fnum(bh["sharpe"], 2)),
        ("buy-and-hold Sharpe, 95% CI", fci(bh["sharpe_ci_lo"], bh["sharpe_ci_hi"], 2)),
        ("strategy Sharpe", fnum(bh["sharpe_strategy"], 2)),
        ("Sharpe difference (strategy − buy-and-hold)", fnum(bh["sharpe_diff"], 2)),
        ("Sharpe difference, 95% CI (paired block bootstrap)", fci(bh["sharpe_diff_ci_lo"], bh["sharpe_diff_ci_hi"], 2)),
        ("p_BH (one-sided)", fp(bh["p_bh"])), ("p_BH (Holm)", fp(holm.get("p_bh_adj"))),
        ("buy-and-hold total return (1×)", fpct(bh["total_return"])),
        ("vol scale (σ strategy / σ buy-and-hold)", fnum(bh["scale"], 3)),
        ("σ strategy (daily, in-sample)", fnum(bh["sigma_strategy"], 5)),
        ("σ buy-and-hold (daily, in-sample)", fnum(bh["sigma_bh"], 5)),
        ("vol-scaled buy-and-hold total return", fpct(bh["total_return_scaled"])),
        ("vol-scaled buy-and-hold max drawdown", fpct(bh["max_dd_scaled"])),
    ]
    hold = _holdout_series(ctx)
    start = run.stats["summary"]["start_equity"]
    lines = ["## Buy-and-hold", "",
             "A constant 1× long in the perpetual with funding, scaled to the strategy's in-sample daily "
             "volatility; Sharpe is scale-invariant, the scaling matters for return and drawdown.", "",
             table(["Buy-and-hold", "Value"], rows), ""]
    if hold:
        lines += ["The figures continue into the holdout run (shaded), chained onto the in-sample end.", ""]
    _fig(lines, fg.equity(run.table("daily"), run.table("buyhold"), out / "equity.png", start_equity=start,
                          scale=bh["scale"], holdout=hold), "equity")
    _fig(lines, fg.drawdown(run.table("daily"), run.table("buyhold"), out / "drawdown.png", start_equity=start,
                            scale=bh["scale"], holdout=hold), "drawdown")
    return lines


def _qrow(label: str, q: Mapping | None, digits: int = 3) -> tuple:
    keys = (*QUANTILES, "mean")
    return (label, *([NA] * len(keys) if q is None else [fnum(q[k], digits) for k in keys]))


Q_HEADERS = ["min", "p05", "p25", "p50", "p75", "p95", "max", "mean"]


def _costs(run: VariantRun, out: Path) -> list[str]:
    st = run.stats
    c, d = st["costs"], st["distributions"]
    tr = _trades_r(run)
    rows = [_qrow("cost per trade (R)", c["cost_r"]), _qrow("stop distance (% of entry)", c["stop_dist_pct"]),
            _qrow("stop distance (ATR14)", c["stop_dist_atr"]),
            _qrow("implied leverage (×)", d["implied_leverage"], 2), _qrow("funding (R)", d["funding_r"], 4)]
    rep = [(fpct(r["slippage"], 2), fpct(r["fee_maker"], 2), fpct(r["fee_taker"], 2), fint(r["n"]),
            fnum(r["mean_net_r"]), fci(r["ci_lo"], r["ci_hi"]), fpct(r["win_rate"]), fp(r["p_a"]), fp(r["p_b"]),
            "registered" if r["is_primary"] else "")
           for r in st["repricing"]]
    lines = ["## Costs", "",
             "Cost in R is fees, slippage and funding divided by the stop distance; a trade whose cost exceeds "
             "1 R loses even when it reaches a 1 R target.", "",
             table(["Cost", "Value"], [("share of trades with cost above 1 R", fpct(c["share_cost_r_gt_1"]))]), "",
             table(["Cost distribution", *Q_HEADERS], rows), ""]
    _fig(lines, fg.cost_r(tr, out / "cost_r.png", share_above_1=c["share_cost_r_gt_1"]), "cost in R")
    _fig(lines, fg.stop_dist(tr, out / "stop_dist.png"), "stop distance and leverage")
    lines += ["### Re-pricing (D13)", "",
              "The same trades and baseline runs re-priced at other costs, without re-simulation; descriptive "
              "only, the verdict uses the registered costs.", "",
              table(["Slippage", "Maker fee", "Taker fee", "n", "Mean net R", "95% CI", "Win rate", "p_A", "p_B",
                     "Costs"], rep), ""]
    _fig(lines, fg.reprice(st["repricing"], out / "reprice.png"), "re-pricing grid")
    return lines


def _robustness(run: VariantRun, ctx: ReportContext, out: Path) -> list[str]:
    lines = ["## Robustness", ""]
    if run.cfg.is_holdout:
        return lines + ["The grid is in-sample only; the heatmaps are in the in-sample primary report.", ""]
    if not run.is_primary:
        link = f"[the primary report]({ctx.primary_link})" if ctx.primary_link else "the primary report"
        return lines + [f"The heatmaps of this pair × session are in {link}.", ""]
    if not ctx.heatmaps:
        return lines + ["The grid has not run yet (`perpbt grid --run`); the heatmaps appear once it has.", ""]
    for h in ctx.heatmaps:
        axes = " × ".join(h.axes)
        done = len(h.cells)
        lines += [f"### Heatmap {h.n}: {axes}", "", f"{done} of {h.expected} cells have run; the primary is outlined.",
                  ""]
        path = fg.heatmap(h.cells, out / f"heatmap_{h.n}.png", x_values=h.x_values, y_values=h.y_values,
                          x_axis=h.axes[0], y_axis=h.axes[1] if len(h.axes) > 1 else "", title=f"Heatmap {h.n}")
        _fig(lines, path, f"heatmap {h.n}")
    if ctx.grid_links:
        lines += ["Grid variants of this pair × session:", ""]
        lines += [f"- {m}: {d} ([report]({link}))" for m, d, link in ctx.grid_links]
        lines.append("")
    return lines


def _alpha_decay(run: VariantRun, out: Path) -> list[str]:
    rows = [(str(r["year"]), fint(r["n"]), fpct(r["win_rate"]), fnum(r["mean_net_r"]), fnum(r["sharpe"], 2),
             fpct(r["max_dd"]), fpct(r["fill_rate"])) for r in run.stats["per_year"]]
    lines = ["## Alpha decay", "",
             "Per calendar year (max drawdown starts from the previous year-end equity; fill rate is filled / "
             "placed entry orders by placement year).", "",
             table(["Year", "n", "Win rate", "Mean net R", "Sharpe", "Max DD", "Fill rate"], rows), ""]
    _fig(lines, fg.rolling(run.stats["rolling"], out / "rolling.png"), "rolling metrics")
    _fig(lines, fg.per_year(run.stats["per_year"], out / "per_year.png"), "per year")
    return lines


def _breakdown_rows(rows: Sequence[Mapping], label=lambda k: "unlabelled" if k is None else str(k)) -> list[tuple]:
    return [(label(r["key"]), fint(r["n"]), fpct(r["win_rate"]), fnum(r["mean_net_r"])) for r in rows]


def _regimes(run: VariantRun, out: Path) -> list[str]:
    st = run.stats
    reg, br = st["regimes"], st["breakdowns"]
    vm = st["insample_ref"].get("vol_median")
    rows = ([(f"trend: {a}", *b) for a, *b in _breakdown_rows(reg["trend"])]
            + [(f"vol: {a}", *b) for a, *b in _breakdown_rows(reg["vol"])])
    dow = _breakdown_rows(br["dow"], lambda k: DOW[int(k)] if k is not None else "unknown")
    lines = ["## Regimes and calendar", "",
             "Trend: daily ADX(14) above 25 as of the day before entry. Volatility: 30-day realised vol of daily "
             f"log returns as of the day before entry, above (`high`) or below (`low`) the in-sample median "
             f"{fnum(vm, 5)}; trades before the indicators are defined are unlabelled.", "",
             table(["Regime", "n", "Win rate", "Mean net R"], rows), "",
             table(["Day of week (UTC)", "n", "Win rate", "Mean net R"], dow), ""]
    _fig(lines, fg.regimes(reg, out / "regimes.png"), "regimes")
    _fig(lines, fg.calendar(br, out / "calendar.png"), "calendar")
    return lines


def _diagnostics(run: VariantRun, ctx: ReportContext, out: Path) -> list[str]:
    st = run.stats
    s, d = st["summary"], st["distributions"]
    counts = [("trades open at the data end (left out of the R subset)", fint(s["n_data_end"])),
              ("ambiguous candles resolved without full 1m data", fint(s["n_missing_1m"])),
              ("max concurrent positions", fint(s["max_concurrent"])),
              ("funding paid in total (USD)", fnum(s["funding_total"], 2))]
    res = [(k, fint(v)) for k, v in s["resolution_counts"].items()]
    lines = ["## Diagnostics", "",
             table(["Count", "Value"], counts), "",
             table(["Fill resolution", "Fills"], res or [("none", "0")]), "",
             table(["Exit reason", "n", "Win rate", "Mean net R"], _breakdown_rows(st["breakdowns"]["exit_reason"])),
             "",
             table(["Trade distribution", *Q_HEADERS], [_qrow("hold (minutes)", d["hold_minutes"], 1), _qrow("MAE (R)", d["mae_r"]),
                               _qrow("MFE (R)", d["mfe_r"])]), ""]
    if ctx.shared is None:
        lines += ["Shared trades with the other session variants: no other session variant of these parameters "
                  "has run.", ""]
    else:
        parts = [f"{session_label(k)} {fpct(v)}" for k, v in ctx.shared.items() if k != "any"]
        lines += ["Share of this variant's trades whose candidate candle is also traded by the other session "
                  f"variants (computed at report time from their trade tables): {', '.join(parts)}; any of them "
                  f"{fpct(ctx.shared.get('any'))}. The session variants are not independent tests.", ""]
    tr = _trades_r(run)
    _fig(lines, fg.mae_mfe(tr, out / "mae_mfe.png"), "MAE vs MFE")
    _fig(lines, fg.hold_time(tr, out / "hold_time.png"), "hold time")
    _fig(lines, fg.concurrency(run.table("daily"), out / "concurrency.png", max_concurrent=s["max_concurrent"]),
         "concurrency")
    return lines


def _dsr(run: VariantRun) -> list[str]:
    d = run.stats["dsr"]
    lines = ["## Deflated Sharpe ratio", "",
             table(["Daily returns", "Value"], [("daily Sharpe (not annualised)", fnum(d["sr"], 4)),
                                                ("days T", fint(d["T"])), ("skew", fnum(d["skew"], 3)),
                                                ("kurtosis (non-excess)", fnum(d["kurt"], 3))]), ""]
    rows = []
    for key, label in (("local", "this pair × session's grid"), ("global", "all grid variants")):
        x = d.get(key)
        rows.append((label, NA, NA, NA, NA) if not x else
                    (label, fint(x["N"]), fnum(x["V"], 6), fnum(x["sr_star"], 4), fnum(x["dsr"], 3)))
    lines += [table(["Trials", "N", "V (variance of daily Sharpe)", "SR*", "DSR"], rows), ""]
    if not d.get("local"):
        lines += ["Not computed yet: `perpbt stats dsr` runs after the whole grid.", ""]
    lines += ["Caveat: the grid cells share most of their trades, so the effective number of independent trials "
              "is far below N and the DSR is conservative.", ""]
    return lines


def _notes(run: VariantRun, ctx: ReportContext) -> list[str]:
    s, b = run.stats["summary"], run.stats["baselines"]
    miss = s["resolution_counts"].get("15m_pessimistic_missing_1m", 0)
    notes = [f"`15m_pessimistic_missing_1m`: {miss} fills and {s['n_missing_1m']} ambiguous (candle, order) pairs "
             "were resolved by the pessimistic 15m rule because 1m data was incomplete."]
    if ctx.gap_overlaps is None:
        notes.append("Data gaps: not checked (no candle manifest found).")
    else:
        notes.append("Data gaps overlapping a trade (computed at report time from the candle manifests): " + "; ".join(
            f"{tf}: {g['trades']} trades over {g['gaps']} gaps" for tf, g in ctx.gap_overlaps.items()) + ".")
    if b["n_runs"] is not None and b["n_excluded_a"]:
        notes.append(f"Baseline A left out {b['n_excluded_a']} trades (no ATR stop multiple or no slot).")
    if ctx.holdout_reason:
        notes.append(f"Holdout run after a code change since the freeze; reason: {ctx.holdout_reason}")
    return ["## Notes", "", *[f"- {n}" for n in notes], ""]


def _trades_r(run: VariantRun) -> pd.DataFrame | None:
    t = run.table("trades")
    return None if t is None else r_subset(t)


def render_report(run: VariantRun, ctx: ReportContext | None = None) -> str:
    """Write the variant's figures into ``<folder>/figures`` and return the report's Markdown."""
    ctx = ctx or ReportContext()
    out = run.path / FIGURES_DIR
    out.mkdir(parents=True, exist_ok=True)
    lines = _header(run)
    lines += _funnel(run, out) + _headline(run) + _baselines(run, out) + _buy_and_hold(run, ctx, out)
    lines += _costs(run, out) + _robustness(run, ctx, out) + _alpha_decay(run, out) + _regimes(run, out)
    lines += _diagnostics(run, ctx, out) + _dsr(run) + _notes(run, ctx)
    return "\n".join(lines).rstrip() + "\n"


def write_report(run: VariantRun, ctx: ReportContext | None = None) -> Path:
    path = run.path / REPORT_FILE
    path.write_text(render_report(run, ctx), encoding="utf-8", newline="\n")
    return path


# --- context from the runs directory -------------------------------------------------------------

def read_gaps(data_cfg: DataConfig, pair: str) -> dict[str, list[dict]] | None:
    """The ``gaps`` of the pair's 15m and 1m candle manifests; None when neither exists."""
    store = CandleStore(data_cfg)
    out = {}
    for tf in ("15m", "1m"):
        f = store.dir(pair, tf) / "manifest.json"
        if f.exists():
            out[tf] = json.loads(f.read_text(encoding="utf-8")).get("gaps") or []
    return out or None


def gap_overlaps(trades: pd.DataFrame | None, gaps: Mapping[str, Sequence[Mapping]] | None) -> dict | None:
    """Per timeframe: the gaps inside the trades' span and the trades whose ``[entry_ms, exit_ms]`` overlaps one."""
    if gaps is None:
        return None
    out = {}
    for tf, gs in gaps.items():
        if trades is None or len(trades) == 0:
            out[tf] = {"gaps": 0, "trades": 0}
            continue
        a, b = trades["entry_ms"].to_numpy(), trades["exit_ms"].to_numpy()
        hit = pd.Series(False, index=range(len(trades)))
        used = 0
        for g in gs:
            over = (a < g["end_ms"]) & (b >= g["start_ms"])
            used += bool(over.any())
            hit |= over
        out[tf] = {"gaps": used, "trades": int(hit.sum())}
    return out


def _cells_rows(results: pd.DataFrame) -> pd.DataFrame:
    params = results["params_json"].map(lambda s: json.loads(s)["params"])
    return results.assign(params=params, members_list=results["members"].map(json.loads))


def heatmaps_for(cell: pd.DataFrame, grid) -> list[Heatmap]:
    """The heatmaps of one pair × session from its in-sample ``results`` rows and the prereg's ``grid``."""
    out = []
    for n, h in enumerate(grid.heatmaps, start=1):
        rows = cell[cell["members_list"].map(lambda m, n=n: f"heatmap_{n}" in m)]
        xs = [param_label(h.axes[0], v) for v in h.values[0]]
        ys = [param_label(h.axes[1], v) for v in h.values[1]] if len(h.axes) > 1 else [""]
        cells = pd.DataFrame({
            "x": [param_label(h.axes[0], p[h.axes[0]]) for p in rows["params"]],
            "y": [param_label(h.axes[1], p[h.axes[1]]) if len(h.axes) > 1 else "" for p in rows["params"]],
            "mean_net_r": rows["mean_net_r"].to_numpy(), "sharpe_ann": rows["sharpe_ann"].to_numpy(),
            "n_trades_r": rows["n_trades_r"].to_numpy(), "is_primary": rows["is_primary"].to_numpy(dtype=bool),
        })
        out.append(Heatmap(n, tuple(h.axes), xs, ys, cells, len(xs) * len(ys)))
    return out


def _diff_text(params: dict, primary: dict) -> str:
    return ", ".join(f"{k}={param_label(k, v)}" for k, v in params.items() if primary.get(k) != v) or "primary"


def _same_params(a: dict, b: dict) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def write_reports(runs_dir: str | Path, results: pd.DataFrame, prereg, data_cfg: DataConfig | None = None, *,
                  grid: bool = False) -> list[Path]:
    """Write ``report.md`` for the primary variants of ``results`` (in-sample, then holdout) and, with ``grid``,
    for every in-sample grid variant; returns the paths in that order."""
    runs_dir = Path(runs_dir)
    if len(results) == 0:
        return []
    rows = _cells_rows(results)
    reasons = {vid: r.get("reason") for vid, r in Registry(runs_dir).latest_ok(kind="run").items()}
    loaded: dict[str, VariantRun] = {}

    def load(vid: str) -> VariantRun:
        if vid not in loaded:
            loaded[vid] = VariantRun.load(runs_dir / vid)
        return loaded[vid]

    gaps_cache: dict[str, dict | None] = {}

    def gaps(pair: str):
        if data_cfg is None:
            return None
        if pair not in gaps_cache:
            gaps_cache[pair] = read_gaps(data_cfg, pair)
        return gaps_cache[pair]

    def shared(r) -> dict | None:
        sib = rows[(rows["pair"] == r.pair) & (rows["is_holdout"] == r.is_holdout)
                   & rows["params"].map(lambda p: _same_params(p, r.params))]
        if len(sib) < 2:
            return None
        tables = {s.session_variant: load(s.variant_id).table("trades") for s in sib.itertuples()}
        tables = {k: v for k, v in tables.items() if v is not None}
        return shared_fraction(tables).get(r.session_variant) if len(tables) > 1 else None

    primary_vid = {(r.pair, r.session_variant): r.variant_id
                   for r in rows.itertuples() if r.is_primary and not r.is_holdout}
    order = sorted(rows.itertuples(), key=lambda r: (not r.is_primary, r.is_holdout, r.pair, r.session_variant))
    written = []
    for r in order:
        if not r.is_primary and (r.is_holdout or not grid):
            continue
        run = load(r.variant_id)
        ctx = ReportContext(alpha=prereg.stats.alpha, shared=shared(r), holdout_reason=reasons.get(r.variant_id),
                            gap_overlaps=gap_overlaps(run.table("trades"), gaps(r.pair)))
        key = (r.pair, r.session_variant)
        if r.is_primary and not r.is_holdout:
            cell = rows[(rows["pair"] == r.pair) & (rows["session_variant"] == r.session_variant)
                        & ~rows["is_holdout"]]
            ctx.heatmaps = heatmaps_for(cell, prereg.grid) if (~cell["is_primary"]).any() else None
            ctx.grid_links = [(", ".join(c.members_list), _diff_text(c.params, r.params), f"../{c.variant_id}/{REPORT_FILE}")
                              for c in cell.itertuples() if not c.is_primary]
            hold = rows[(rows["pair"] == r.pair) & (rows["session_variant"] == r.session_variant)
                        & rows["is_holdout"] & rows["is_primary"]]
            ctx.holdout = load(hold["variant_id"].iloc[0]) if len(hold) else None
        elif not r.is_primary and key in primary_vid:
            ctx.primary_link = f"../{primary_vid[key]}/{REPORT_FILE}"
        written.append(write_report(run, ctx))
    return written


__all__ = ["SECTIONS", "Heatmap", "ReportContext", "VariantRun", "cell_label", "fbool", "gap_overlaps",
           "heatmaps_for", "param_label", "read_gaps", "render_report", "session_label", "write_report",
           "write_reports"]
