"""The carry per-run report (carry spec Phase 6 §6.2): ``runs/carry/[holdout/|forward/]<variant_id>/report.md``.

``scan`` reads every run folder under ``runs/carry/`` (``stats.json``, ``variant.json``; the Parquet tables on
demand). ``write_reports`` writes one report, with its ``figures/``, per **portfolio** folder: the primary and the six
exploratory rows of each period, and each forward review. A per-pair folder gets no report of its own *(my call)*:
its numbers are a table in its row's report ("Per-pair rows"), read from its ``stats.json``.

Every number in a report's headline and tables is a ``stats.json`` value: the folder's own, the ``always_on``
portfolio folder's of the same period (the equity comparison) or the row's per-pair folders'. The figures read the
run folder's tables (``signals.parquet`` for the funding and basis panels) and the sibling ``always_on``'s
``daily.parquet``; the V2 bootstrap figure redraws the verdict's replicates with its own seed
(``<period>/<row>/v2``) and is drawn only when they reproduce ``stats.json``'s p and CI.

Layouts: ``carry-stats/1`` (in-sample, holdout, the 36-month review) and ``carry-forward-review/1`` (the 12-month
review: descriptive, no p-value anywhere). Holdout reports state the "not fully unseen" caveat next to the verdict
(overview §4). Nothing here loads market data.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from perpbt.carry.diagnostics import COMPONENTS, row_names
from perpbt.carry.forward import REVIEW_SCHEMA
from perpbt.carry.holdout import DONE_FILE
from perpbt.carry.prereg import CarryPrereg
from perpbt.carry.runner import FORWARD_DIR, HOLDOUT_DIR, KINDS, PORTFOLIO, STATS_FILE, VARIANT_FILE
from perpbt.carry.verdict import PERIODS, SCHEMA, Seeds, daily_hurdle
from perpbt.experiments.registry_log import Registry
from perpbt.report.carry import figures as cf
from perpbt.report.markdown import NA, fbool, fci, fint, fnum, fp, fpct, table
from perpbt.stats.bootstrap import block_bootstrap_mean, percentile_ci

REPORT_FILE = "report.md"
FIGURES_DIR = "figures"
DAYS_PER_YEAR = 365
SECTIONS = ("Headline", "Rule", "Equity", "Hurdles", "Filter value and random timing", "Return decomposition",
            "Funding and basis per pair", "Margin events", "Cost re-pricing", "Per-pair rows", "Deflated Sharpe ratio",
            "Unmodelled risks", "Notes")
REVIEW_SECTIONS = ("Review", "Equity", "Return decomposition", "Funding and basis per pair", "Margin events",
                   "Unmodelled risks", "Notes")
PERIOD_LABELS = {"insample": "in-sample", "holdout": "holdout", "forward_12m": "forward, 12-month review",
                 "forward_36m": "forward, 36-month review"}
READING_TEXT = {"edge": "edge", "positive_below_hurdle": "positive below the hurdle", "no_edge": "no edge"}
V2_READING_TEXT = {
    "above_hurdle": "proven above the hurdle (V2 passes)",
    "not_proven": "not proven: the CI contains 0",
    "below_hurdle": "proven below the hurdle: the CI lies entirely below 0",
}
HOLDOUT_CAVEAT = (
    "**Caveat: this holdout is not fully unseen** (overview §4). No carry return, basis series or per-pair funding "
    "statistic had been computed on it in this repo, but the trend screen loaded these perp prices, the first "
    "strategy charged BTCUSDT and ETHUSDT funding over the whole window (its reports show mean funding per trade), "
    "and general market knowledge covers it. The 36-month forward test is the only fully unseen one."
)
PERIOD_NOTES = {
    "insample": "In-sample: the period the pre-registration and the simplified screen were set on; the rule was "
                "frozen before any executor return was computed.",
    "forward_36m": "Forward, 36-month review: the period started the day after the code lock, so nothing in it "
                   "was seen when the rule and the code were fixed. It is the only fully unseen test.",
}
UNMODELLED = (
    "A backtest cannot show the main risk of a carry trade: **exchange and counterparty risk** (an exchange "
    "failure, a frozen withdrawal), which is not modelled (overview §5 item 5). Also not modelled: auto-"
    "deleveraging, rule changes such as funding caps, and the yield idle USDT could earn (an off sleeve earns 0; the "
    "hurdle stands for it). Funding-interval changes are in the data. Costs are Binance VIP 0 without the BNB "
    "discount; the re-pricing table shows the sensitivity."
)
MAX_EVENTS = 40
COMPONENT_LABELS = {"funding": "funding", "basis": "basis", "fees": "fees", "slippage": "slippage",
                    "rebalance_cost": "of which rebalances", "liquidation_cost": "of which liquidations"}


class ReportRefused(RuntimeError):
    """The runs cannot be reported with this pre-registration (a ``stats.json`` of another prereg hash)."""


# --- run folders -----------------------------------------------------------------------------------------------

@dataclass
class RunFolder:
    """One run folder: ``stats.json``, ``variant.json`` and the Parquet tables (read on demand)."""

    path: Path
    kind: str  # run | holdout | forward
    stats: dict
    variant: dict
    _tables: dict = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, path: str | Path, kind: str) -> RunFolder:
        path = Path(path)
        return cls(path, kind, json.loads((path / STATS_FILE).read_text(encoding="utf-8")),
                   json.loads((path / VARIANT_FILE).read_text(encoding="utf-8")))

    def table(self, name: str) -> pd.DataFrame | None:
        if name not in self._tables:
            f = self.path / f"{name}.parquet"
            self._tables[name] = pq.read_table(f).to_pandas() if f.exists() else None
        return self._tables[name]

    @property
    def variant_id(self) -> str:
        return self.path.name

    @property
    def row(self) -> str:
        return self.variant["row"]

    @property
    def scope(self) -> str:
        return self.variant["scope"]

    @property
    def period(self) -> str:
        return self.stats["period"]["name"]

    @property
    def is_portfolio(self) -> bool:
        return self.scope == PORTFOLIO

    @property
    def is_primary(self) -> bool:
        return self.row == "primary" and self.is_portfolio

    @property
    def is_review(self) -> bool:
        return self.stats.get("schema") == REVIEW_SCHEMA

    @property
    def diagnostics(self) -> dict:
        return self.stats if self.is_review else self.stats["diagnostics"]

    def rel(self, root: Path) -> str:
        """The folder relative to ``runs/carry``, POSIX."""
        return self.path.relative_to(root).as_posix()


def scan(root: str | Path, row_order: Sequence[str] = ()) -> list[RunFolder]:
    """Every folder with a ``stats.json`` under ``root`` (in-sample), ``holdout/`` and ``forward/``, ordered by kind,
    period, row (``row_order`` first, then by name) and scope (the portfolio first, then the pairs by name)."""
    root = Path(root)
    out = []
    for kind, base in (("run", root), ("holdout", root / HOLDOUT_DIR), ("forward", root / FORWARD_DIR)):
        out += [RunFolder.load(p.parent, kind) for p in sorted(base.glob(f"*/{STATS_FILE}"))]
    order = {r: k for k, r in enumerate(row_order)}

    def key(f: RunFolder):
        return (KINDS.index(f.kind), PERIODS.index(f.period), order.get(f.row, len(order)), f.row,
                f.scope != PORTFOLIO, f.scope)

    return sorted(out, key=key)


def latest_ok(root: str | Path) -> dict[str, dict]:
    """The latest ``ok`` registry row per variant id (code version, commit, reason)."""
    out: dict[str, dict] = {}
    for r in Registry(root).rows():
        if r["status"] == "ok":
            out[r["variant_id"]] = r
    return out


def read_done(root: str | Path) -> dict | None:
    f = Path(root) / HOLDOUT_DIR / DONE_FILE
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


# --- formatting --------------------------------------------------------------------------------------------------

def fpci(lo, hi, digits: int = 2) -> str:
    """A CI of fractions shown as percent: ``1.25% to 11.20%``."""
    return f"{fpct(lo, digits)} to {fpct(hi, digits)}"


def fhurdle(h) -> str:
    return f"{100 * float(h):g}%"


def reading_text(reading: str | None) -> str:
    return READING_TEXT.get(reading, NA) if reading else NA


def v2_reading_text(reading: str | None) -> str:
    return V2_READING_TEXT.get(reading, NA) if reading else NA


def _pass(x) -> str:
    return "passes" if x else "fails"


def verdict_sentence(stats: Mapping, *, primary: bool) -> str:
    """The headline sentence: the verdict (primary) or the row's reading (exploratory, descriptive), with V1 and V2
    p-values, the annualised net return and its CI, and the three-way V2 reading."""
    v1, v2 = stats["v1"], stats["v2"]
    period = PERIOD_LABELS[stats["period"]["name"]]
    h = fhurdle(v2["hurdle_annual"])
    tests = (f"V1 (net Sharpe above 0) {_pass(v1['passes'])}, p = {fp(v1['p'])}; V2 (annualised net return above the "
             f"{h}/yr hurdle) {_pass(v2['passes'])}, p = {fp(v2['p'])}.")
    if primary:
        head = f"**Verdict ({period}): {reading_text(stats['reading'])}.**"
    else:
        head = (f"**Descriptive ({period}; exploratory row `{stats['row']}`, no say in the verdict): it would read "
                f"{reading_text(stats['reading'])}.**")
    money = (f"Annualised net return {fpct(v2['ann_return'], 2)} (95% CI {fpci(*v2['ann_return_ci'])}); excess over "
             f"the hurdle {fpct(v2['ann_excess'], 2)} (95% CI {fpci(*v2['ann_excess_ci'])}): "
             f"{v2_reading_text(v2['reading'])}.")
    return f"{head} {tests} {money}"


def _fig(lines: list[str], path: Path, alt: str) -> None:
    lines += [f"![{alt}]({FIGURES_DIR}/{path.name})", ""]


def _signal_text(variant: Mapping) -> str:
    s = variant["signal"]
    if abs(s["entry_annual"]) > cf.THRESHOLD_LIMIT:
        return f"always on (no filter); {s['lookback_days']}-day trailing funding reported"
    return (f"{s['lookback_days']}-day trailing funding (annualised); on above {fhurdle(s['entry_annual'])}/yr, off "
            f"below {fhurdle(s['exit_annual'])}/yr, otherwise the previous state")


# --- context -----------------------------------------------------------------------------------------------------

@dataclass
class ReportContext:
    """What a report needs beyond its folder."""

    prereg: CarryPrereg
    always_on: RunFolder | None = None
    pairs: list[tuple[str, RunFolder | None]] = field(default_factory=list)
    registry_row: dict | None = None
    done: dict | None = None  # holdout reports: the DONE content


def v2_replicates(run: RunFolder, prereg: CarryPrereg) -> np.ndarray | None:
    """V2's bootstrap replicates, redrawn with the verdict's seed (``<period>/<row>/v2``, the prereg's B and block
    length) on the folder's ``daily["ret"]``; None unless their p and CI equal ``stats.json``'s."""
    v2, daily = run.stats.get("v2"), run.table("daily")
    if v2 is None or daily is None or len(daily) < 2:
        return None
    s = prereg.stats
    seeds = Seeds(s.master_seed, run.stats["prereg_hash"], run.period)
    e = daily["ret"].to_numpy(dtype=np.float64) - daily_hurdle(v2["hurdle_annual"])
    reps = DAYS_PER_YEAR * block_bootstrap_mean(e, s.bootstrap_n, seeds.rng(run.stats["row"], "v2"), s.block_len_days)
    p = (1 + int(np.sum(reps <= 0.0))) / (s.bootstrap_n + 1)
    lo, hi = percentile_ci(reps)
    same = all(x is not None and math.isclose(a, x, rel_tol=1e-9, abs_tol=1e-15)
               for a, x in ((p, v2["p"]), (lo, v2["ann_excess_ci"][0]), (hi, v2["ann_excess_ci"][1])))
    return reps if same else None


# --- sections ----------------------------------------------------------------------------------------------------

def _title(run: RunFolder) -> str:
    what = "primary" if run.row == "primary" else f"exploratory row {run.row}"
    return f"# Funding carry — {what}, {PERIOD_LABELS[run.period]}"


def _header(run: RunFolder, ctx: ReportContext) -> list[str]:
    st, v = run.stats, run.variant
    reg = ctx.registry_row or {}
    role = ("pre-registered primary: its V1 and V2 are the verdict" if run.is_primary else
            "exploratory: reported, no say in the verdict (a DSR trial)")
    if run.is_review:
        role = "the primary's descriptive 12-month forward review"
    return [
        _title(run), "",
        f"- Row: `{run.row}` ({role})",
        f"- Signal: {_signal_text(v)}",
        f"- Sleeve: perp leverage {v['sleeve']['perp_leverage']:g} (equal sleeves, spot notional "
        f"L / (L + 1) of a sleeve)",
        f"- Pairs: {', '.join(v['pairs'])}",
        f"- Period: {st['period']['start']} to {st['period']['end']} ({fint(st['n_days'])} return days)",
        f"- Variant id: `{run.variant_id}`",
        f"- Pre-registration hash: `{st['prereg_hash']}`",
        f"- Code version: `{reg.get('code_version') or NA}` (git `{reg.get('git_commit') or NA}`)",
        "",
    ]


def _headline(run: RunFolder) -> list[str]:
    st = run.stats
    v1, v2 = st["v1"], st["v2"]
    lines = ["## Headline", "", verdict_sentence(st, primary=run.is_primary), ""]
    if run.period == "holdout":
        lines += [HOLDOUT_CAVEAT, ""]
    elif run.period in PERIOD_NOTES:
        lines += [PERIOD_NOTES[run.period], ""]
    rows = [
        ("V1: net Sharpe (annualised)", fnum(v1["sharpe"], 2)), ("V1: 95% CI", fci(*v1["sharpe_ci"], 2)),
        ("V1: p (one-sided)", fp(v1["p"])), ("V1 passes", fbool(v1["passes"])),
        ("V2: annualised net return", fpct(v2["ann_return"], 2)),
        ("V2: annualised net return, 95% CI", fpci(*v2["ann_return_ci"])),
        ("V2: hurdle", fpct(v2["hurdle_annual"], 1)), ("V2: excess over the hurdle", fpct(v2["ann_excess"], 2)),
        ("V2: excess, 95% CI", fpci(*v2["ann_excess_ci"])), ("V2: p (one-sided)", fp(v2["p"])),
        ("V2 passes", fbool(v2["passes"])), ("V2 reading", v2_reading_text(v2["reading"])),
        ("Reading", reading_text(st["reading"])), ("Return days", fint(st["n_days"])),
    ]
    return lines + [table(["Headline", "Value"], rows), ""]


def _slippage_text(prereg: CarryPrereg) -> str:
    return ", ".join(f"{k} {fpct(v, 2)}" for k, v in prereg.costs.slippage.items())


def _rule(ctx: ReportContext) -> list[str]:
    p = ctx.prereg
    s, c = p.stats, p.costs
    slip = _slippage_text(p)
    return ["## Rule", "",
            f"Edge iff V1 and V2 both pass, each one-sided at α = {s.alpha:g}, unadjusted (overview CD8). V1: the "
            f"portfolio's net daily Sharpe above 0; V2: the annualised net return (365 × mean daily return) above "
            f"the {fhurdle(p.hurdle.annual)}/yr hurdle (CD9), on the daily excess over its daily rate. Both by moving-"
            f"block bootstrap, B = {s.bootstrap_n}, blocks of {s.block_len_days} days; 95% percentile CIs. V2 reads "
            "three ways (overview §3): proven above the hurdle (passes), not proven (the CI contains 0), proven "
            "below (the CI lies below 0).", "",
            f"Costs on every fill: perp taker {fpct(c.perp_taker, 2)}, spot taker {fpct(c.spot_taker, 2)}, slippage "
            f"per leg ({slip}); funding from the data on the perp leg; liquidation fee "
            f"{fpct(p.margin.liquidation_fee, 1)}.", ""]


def _annual_rows(annual: Mapping, *, ci: bool = True) -> list[tuple[str, object]]:
    rows = [("annualised return (365 × mean)", fpct(annual["ann_return"], 2)), ("CAGR", fpct(annual["cagr"], 2)),
            ("annualised vol", fpct(annual["ann_vol"], 2)), ("Sharpe (annualised)", fnum(annual["sharpe"], 2))]
    if ci:
        rows.append(("Sharpe, 95% CI", fci(*annual["sharpe_ci"], 2)))
    return rows + [("max drawdown", fpct(annual["max_drawdown"], 2)), ("worst day", fpct(annual["worst_day"]["ret"], 2)),
                   ("worst day: date", annual["worst_day"]["date"]),
                   ("worst month", fpct(annual["worst_month"]["ret"], 2)),
                   ("worst month: month", annual["worst_month"]["month"])]


def _equity(run: RunFolder, ctx: ReportContext, out: Path) -> list[str]:
    ao = ctx.always_on if run.row != "always_on" else None
    ci = not run.is_review
    mine = _annual_rows(run.diagnostics["annual"], ci=ci)
    lines = ["## Equity", ""]
    if ao is not None:
        theirs = dict(_annual_rows(ao.diagnostics["annual"], ci=ci))
        lines += [f"The row against `always_on` (no filter: sleeves at perp leverage "
                  f"{ao.variant['sleeve']['perp_leverage']:g} held on every live day) over the same days, both from "
                  "their own `stats.json`.", "",
                  table(["Statistic", run.row, "always_on"], [(a, b, theirs.get(a, NA)) for a, b in mine]), ""]
    else:
        lines += [table(["Statistic", run.row], mine), ""]
    hurdle = run.stats["v2"]["hurdle_annual"] if "v2" in run.stats else ctx.prereg.hurdle.annual
    _fig(lines, cf.equity(run.table("daily"), out / "equity.png", hurdle_annual=hurdle,
                          always_on=None if ao is None else ao.table("daily"), row=run.row), "equity")
    if ao is None and run.row != "always_on":
        lines += ["`always_on` is not run in this period; the figure shows the row and the hurdle only.", ""]
    return lines


def _hurdles(run: RunFolder) -> list[str]:
    rows = [(fpct(h["hurdle_annual"], 1), fpct(h["ann_excess"], 2), fpci(*h["ann_excess_ci"]), fp(h["p"]),
             fbool(h["passes"]), v2_reading_text(h["reading"])) for h in run.stats["hurdles"]]
    return ["## Hurdles", "",
            "V2 against other hurdles, descriptive (overview §10.2): the same bootstrap draws as the verdict's V2, so "
            f"the {fpct(run.stats['v2']['hurdle_annual'], 1)} row is the verdict's.", "",
            table(["Hurdle", "Excess", "95% CI", "p (one-sided)", "Passes", "Reading"], rows), ""]


RT_KEYS = (("primary", "primary"), ("mean", "paths: mean"), ("std", "paths: std"), ("p05", "paths: 5th pct"),
           ("p50", "paths: median"), ("p95", "paths: 95th pct"))


def _filter_timing(run: RunFolder, out: Path) -> list[str]:
    d = run.diagnostics
    lines = ["## Filter value and random timing", ""]
    if "filter_value" not in d:
        return lines + ["Computed for the portfolio primary only (Phase 5 §5.9).", ""]
    fv, rt = d["filter_value"], d["random_timing"]
    lines += ["**Filter value:** the paired Sharpe difference primary − always_on (the same blocks for both), "
              "descriptive.", "",
              table(["Filter value", "Value"], [
                  ("Sharpe, primary", fnum(fv["sharpe_primary"], 2)), ("Sharpe, always_on", fnum(fv["sharpe_always_on"], 2)),
                  ("difference", fnum(fv["diff"], 2)), ("difference, 95% CI", fci(*fv["diff_ci"], 2)),
                  ("p (one-sided)", fp(fv["p"]))]), "",
              f"**Random timing:** the primary's on/off series shifted in time ({fint(rt['runs'])} paths, one offset "
              f"per path shared by the pairs, at least {fint(rt['shift_margin_days'])} days from either end of the "
              f"{fint(rt['n_days'])} days; Phase 4 §4.4). Rank 1 means no path did better; the share counts the "
              "paths at least as good, plus one. Descriptive.", ""]
    ann, sh = rt["ann_return"], rt["sharpe"]
    rows = [(label, fpct(ann[k], 2), fnum(sh[k], 2)) for k, label in RT_KEYS]
    rows += [("rank of the primary", fint(ann["rank"]), fint(sh["rank"])),
             ("share of paths at least as good", fnum(ann["share_at_least"], 3), fnum(sh["share_at_least"], 3)),
             ("finite paths", fint(ann["n_finite"]), fint(sh["n_finite"]))]
    lines += [table(["Random timing", "Annualised net return", "Sharpe"], rows), ""]
    _fig(lines, cf.random_timing(run.table("random_timing"), rt, out / "random_timing.png"), "random timing")
    return lines


def _decomposition(run: RunFolder, out: Path) -> list[str]:
    dec = run.diagnostics["decomposition"]
    t = dec["total"]
    total = [(COMPONENT_LABELS[name], fpct(t[name], 2)) for name in COMPONENTS]
    total += [("net (sum of daily returns)", fpct(t["ret_sum"], 2)), ("compounded", fpct(t["ret_compounded"], 2)),
              ("share of live pair-days on", fpct(t["share_on"], 1)), ("pair-days on", fint(t["pair_days_on"])),
              ("pair-days live", fint(t["days_live"])), ("entries", fint(t["entries"]))]
    years = [(y["year"], fint(y["n_days"]), fpct(y["funding"], 2), fpct(y["basis"], 2), fpct(y["fees"], 2),
              fpct(y["slippage"], 2), fpct(y["rebalance_cost"], 2), fpct(y["liquidation_cost"], 2),
              fpct(y["ret_sum"], 2), fpct(y["ret_compounded"], 2), fpct(y["share_on"], 1), fint(y["entries"]))
             for y in dec["per_year"]]
    pairs = [(p["pair"], fpct(p["funding"], 2), fpct(p["basis"], 2), fpct(p["fees"], 2), fpct(p["slippage"], 2),
              fpct(p["net"], 2), fint(p["days_on"]), fint(p["days_live"]), fint(p["entries"]))
             for p in dec["per_pair"]]
    lines = ["## Return decomposition", "",
             "Fractions of equity summed over days (each day's money over the previous day's equity), so funding + "
             "basis − fees − slippage is the net. Basis is the two legs' price P&L (a liquidation's loss to its "
             "price included); the rebalance and liquidation costs are a part of fees and slippage, not added to "
             "them (Phase 4 §4.7).", "",
             table(["Total", "Value"], total), "",
             table(["Year", "Days", "Funding", "Basis", "Fees", "Slippage", "of which rebalances",
                    "of which liquidations", "Net", "Compounded", "Share on", "Entries"], years), ""]
    _fig(lines, cf.decomposition(dec["per_year"], out / "decomposition.png"), "decomposition per year")
    lines += [table(["Pair", "Funding", "Basis", "Fees", "Slippage", "Net", "Days on", "Days live", "Entries"], pairs),
              ""]
    return lines


def _funding_basis(run: RunFolder, out: Path) -> list[str]:
    s = run.variant["signal"]
    lines = ["## Funding and basis per pair", "",
             "From the run folder's `signals.parquet`: the trailing funding the rule decides on and the basis at "
             "the close, per pair, with the days the pair was hedged at the close shaded.", ""]
    _fig(lines, cf.funding(run.table("signals"), run.table("daily_pair"), out / "funding.png",
                           entry=s["entry_annual"], exit=s["exit_annual"]), "trailing funding per pair")
    _fig(lines, cf.basis(run.table("signals"), run.table("daily_pair"), out / "basis.png"), "basis per pair")
    return lines


def _margin(run: RunFolder) -> list[str]:
    m = run.diagnostics["margin"]
    counts = [(label, fint(m[k]["count"]), fnum(m[k]["cost"], 2), fpct(m[k]["cost_frac"], 3))
              for k, label in (("rebalances", "rebalances"), ("liquidations", "liquidations"))]
    events = [("liquidation", e["date"], e["pair"], fnum(e["price"], 6), fnum(e["cost"], 2), fpct(e["cost_frac"], 3))
              for e in m["liquidations"]["events"]]
    events += [("rebalance", e["date"], e["pair"], fnum(e["qty"], 6), fnum(e["cost"], 2), fpct(e["cost_frac"], 3))
               for e in m["rebalances"]["events"]]
    lines = ["## Margin events", "",
             "Rebalance: at the close the perp wallet held less than half the margin at the target leverage; both "
             "legs were cut back at the next fill. Liquidation: the day high reached the liquidation price; the short "
             "was bought back there with the liquidation fee and the spot leg sold at the next fill (CD7).", "",
             table(["Event", "Count", "Cost (USDT)", "Cost (% of equity)"], counts), ""]
    if events:
        shown = events[:MAX_EVENTS]
        lines += [table(["Event", "Date", "Pair", "Liquidation price / qty cut", "Cost (USDT)", "Cost (% of equity)"],
                        shown), ""]
        if len(events) > len(shown):
            lines += [f"The first {len(shown)} of {len(events)} events (liquidations first); all are in "
                      "`stats.json` and `events.parquet`.", ""]
    return lines


def _reprice(run: RunFolder, ctx: ReportContext) -> list[str]:
    rows = [(fpct(c["slippage"], 2), fpct(c["spot_taker"], 3), fpct(c["perp_taker"], 3), fpct(c["ann_return"], 2),
             fnum(c["sharpe"], 2), fpct(c["v2"]["ann_excess"], 2), fpci(*c["v2"]["ann_excess_ci"]), fp(c["v2"]["p"]),
             v2_reading_text(c["v2"]["reading"]), fpct(c["fees"], 2), fpct(c["slippage_cost"], 2), fint(c["entries"]))
            for c in run.diagnostics["reprice"]]
    return ["## Cost re-pricing", "",
            "The row re-run at other costs, descriptive (overview §10.4); the verdict uses VIP 0. **No cell equals "
            f"the verdict's costs**: the pre-registered slippage is per pair ({_slippage_text(ctx.prereg)}), while a "
            "cell's slippage applies to every pair.", "",
            table(["Slippage", "Spot taker", "Perp taker", "Annualised net return", "Sharpe", "Excess over the hurdle",
                   "95% CI", "p (V2)", "V2 reading", "Fees", "Slippage cost", "Entries"], rows), ""]


def _per_pair(run: RunFolder, ctx: ReportContext) -> list[str]:
    lines = ["## Per-pair rows", ""]
    if run.kind == "forward":
        return lines + ["The forward reviews run the portfolio primary only (Phase 5 §5.5); no pair is run alone.", ""]
    failed = set((ctx.done or {}).get("failed") or ())
    rows = []
    for pair, f in ctx.pairs:
        if f is None:
            why = ("failed (see the registry)" if f"{run.row}/{pair}" in failed
                   else "not run alone in this period (no stats.json)")
            rows.append((pair, NA, NA, NA, NA, why, NA, NA, NA, NA))
            continue
        st = f.stats
        v1, v2 = st["v1"], st["v2"]
        rows.append((pair, fpct(v2["ann_return"], 2), fpct(v2["ann_excess"], 2), fpci(*v2["ann_excess_ci"]),
                     fp(v2["p"]), v2_reading_text(v2["reading"]), fnum(v1["sharpe"], 2), fp(v1["p"]),
                     reading_text(st["reading"]), fpct(st["diagnostics"]["decomposition"]["total"]["share_on"], 1)))
    return lines + [
        f"Each pair alone as a one-pair portfolio under the `{run.row}` row, from its own folder's `stats.json`; "
        "descriptive, no say in the verdict. A pair whose data ends before the period end (a delisting) is not run "
        "alone; the portfolio closes it by the delisting rule.", "",
        table(["Pair", "Annualised net return", "Excess over the hurdle", "95% CI (excess)", "p (V2)", "V2 reading",
               "Sharpe", "p (V1)", "Reading", "Share on"], rows), ""]


def _dsr(run: RunFolder) -> list[str]:
    lines = ["## Deflated Sharpe ratio", ""]
    d = run.diagnostics.get("dsr")
    if d is None:
        return lines + ["Computed for the portfolio primary only, with the seven pre-registered rows as trials.", ""]
    return lines + [
        "The primary's deflated Sharpe with the seven pre-registered rows (the primary among them) as trials; "
        "daily Sharpe ratios, not annualised. Descriptive.", "",
        table(["Trial", "Daily Sharpe"], [(row, fnum(d["trial_sr"][row], 4)) for row in d["rows"]]), "",
        table(["DSR", "Value"], [("daily Sharpe", fnum(d["sr"], 4)), ("days T", fint(d["T"])),
                                 ("skew", fnum(d["skew"], 3)), ("kurtosis (non-excess)", fnum(d["kurt"], 3)),
                                 ("trials N", fint(d["N"])), ("V (variance of the trials' Sharpe)", fnum(d["V"], 8)),
                                 ("SR* (expected maximum under the null)", fnum(d["sr_star"], 4)),
                                 ("DSR", fnum(d["dsr"], 3))]), ""]


def _risks() -> list[str]:
    return ["## Unmodelled risks", "", UNMODELLED, ""]


def _notes(run: RunFolder, ctx: ReportContext, replicated: bool | None) -> list[str]:
    notes = []
    if replicated is not None:
        notes.append(
            "The V2 bootstrap figure redraws the verdict's replicates at report time with its own seed "
            f"(`{run.period}/{run.stats['row']}/v2`); their p and CI equal `stats.json`'s." if replicated else
            "The V2 replicates could not be redrawn to match `stats.json`'s p and CI (another prereg or code); the "
            "figure is a placeholder.")
    reason = (ctx.registry_row or {}).get("reason")
    if reason:
        notes.append(f"Run after a code change since the lock; reason: {reason}")
    if ctx.done is not None:
        d = ctx.done
        notes.append(f"Holdout `DONE`: status {d['status']}; failed {len(d['failed'])}, skipped {len(d['skipped'])} "
                     f"variants; output-neutral code change {fbool(d['output_neutral'])}"
                     + (f"; earlier attempts {', '.join(d['prior_attempts'])}" if d.get("prior_attempts") else "")
                     + ".")
    notes.append("Numbers in the tables are `stats.json` values; the figures read the run folder's tables.")
    return ["## Notes", "", *[f"- {n}" for n in notes], ""]


# --- the 12-month review -----------------------------------------------------------------------------------------

METRIC_LABELS = {"ann_return": "annualised net return", "funding": "funding (annualised)",
                 "basis": "basis (annualised)", "cost_drag": "fees + slippage (annualised)",
                 "entries_per_year": "entries a year", "share_on": "share of pair-days on"}


def _metric(name: str, x) -> str:
    return fnum(x, 1) if name == "entries_per_year" else fpct(x, 2)


def _review(run: RunFolder) -> list[str]:
    st = run.stats
    years = ", ".join(st["insample_ref"]["years"])
    rows = [(METRIC_LABELS[k], _metric(k, m["forward"]), _metric(k, m["insample_min"]), _metric(k, m["insample_max"]),
             fbool(m["inside"])) for k, m in st["metrics"].items()]
    inside = sum(1 for m in st["metrics"].values() if m["inside"])
    return ["## Review", "",
            f"**Descriptive only (verdict_rule.forward_12m): no test is made at 12 months.** The first "
            f"{fint(st['n_days'])} days after the code lock, the primary run from flat, never pooled with earlier "
            f"periods. Each metric is set against the range of the in-sample primary's calendar years ({years}), "
            f"each annualised over its own days (the share on is a share, not annualised); {inside} of {len(rows)} lie inside. The confirmatory V1 and V2 come "
            "at 36 months.", "",
            table(["Metric", "Forward", "In-sample year min", "In-sample year max", "Inside"], rows), ""]


# --- render ------------------------------------------------------------------------------------------------------

def render_report(run: RunFolder, ctx: ReportContext) -> str:
    """Write the folder's figures into ``<folder>/figures`` and return the report's Markdown."""
    if run.stats.get("schema") not in (SCHEMA, REVIEW_SCHEMA):
        raise ReportRefused(f"{run.path}: unknown stats.json schema {run.stats.get('schema')!r}")
    out = run.path / FIGURES_DIR
    out.mkdir(parents=True, exist_ok=True)
    lines = _header(run, ctx)
    if run.is_review:
        lines += _review(run) + _equity(run, ctx, out) + _decomposition(run, out) + _funding_basis(run, out)
        lines += _margin(run) + _risks() + _notes(run, ctx, None)
        return "\n".join(lines).rstrip() + "\n"
    reps = v2_replicates(run, ctx.prereg)
    lines += _headline(run) + _rule(ctx) + _equity(run, ctx, out) + _hurdles(run)
    _fig(lines, cf.v2_bootstrap(reps, run.stats["v2"], out / "v2_bootstrap.png"), "V2 bootstrap")
    lines += _filter_timing(run, out) + _decomposition(run, out) + _funding_basis(run, out) + _margin(run)
    lines += _reprice(run, ctx) + _per_pair(run, ctx) + _dsr(run) + _risks() + _notes(run, ctx, reps is not None)
    return "\n".join(lines).rstrip() + "\n"


def write_report(run: RunFolder, ctx: ReportContext) -> Path:
    path = run.path / REPORT_FILE
    path.write_text(render_report(run, ctx), encoding="utf-8", newline="\n")
    return path


def check_prereg(folders: Sequence[RunFolder], prereg: CarryPrereg) -> None:
    """ReportRefused, before anything is written, when a folder's ``stats.json`` has an unknown schema or was made
    under another pre-registration."""
    unknown = [f.path.as_posix() for f in folders if f.stats.get("schema") not in (SCHEMA, REVIEW_SCHEMA)]
    if unknown:
        raise ReportRefused(f"{len(unknown)} run folder(s) have an unknown stats.json schema: {unknown[:3]}")
    h = prereg.prereg_hash()
    other = [f.path.as_posix() for f in folders if f.stats["prereg_hash"] != h]
    if other:
        raise ReportRefused(f"{len(other)} run folder(s) have another prereg hash than {h}: {other[:3]}")


def write_reports(root: str | Path, prereg: CarryPrereg, folders: Sequence[RunFolder] | None = None) -> list[Path]:
    """``report.md`` (and ``figures/``) for every portfolio folder under ``root`` (``runs/carry``), in ``scan``
    order; returns the paths."""
    root = Path(root)
    folders = list(folders) if folders is not None else scan(root, row_names(prereg))
    check_prereg(folders, prereg)
    ok = latest_ok(root)
    done = read_done(root)
    by_key = {(f.kind, f.period, f.row, f.scope): f for f in folders}
    written = []
    for f in folders:
        if not f.is_portfolio:
            continue
        ctx = ReportContext(
            prereg, always_on=by_key.get((f.kind, f.period, "always_on", PORTFOLIO)),
            pairs=[(pair, by_key.get((f.kind, f.period, f.row, pair))) for pair in f.variant["pairs"]],
            registry_row=ok.get(f.variant_id), done=done if f.kind == "holdout" else None)
        written.append(write_report(f, ctx))
    return written
