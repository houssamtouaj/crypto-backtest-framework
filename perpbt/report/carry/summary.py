"""The carry summary (carry spec Phase 6 §6.3): ``runs/carry/summary.md``, and ``perpbt carry report``.

One page: the question, the rule, the screen, the code lock, the verdict per period (in-sample, holdout, the forward
reviews when they exist) with links to the reports, the exploratory rows (descriptive), the holdout caveat and the
unmodelled risks. Every number in its tables is a ``stats.json`` value. When the screen stopped the project
(``screen.json`` decision ``stop``), ``summary.md`` is the screen's and is never overwritten.

``report_all`` writes every report (``report.write_reports``) and the summary from what exists under
``runs/carry/``; a rerun gives the same bytes.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from perpbt.carry.diagnostics import row_names
from perpbt.carry.prereg import CarryPrereg, check_frozen, lock_path, read_lock
from perpbt.carry.runner import carry_root
from perpbt.config import ConfigError
from perpbt.experiments.registry_log import Registry
from perpbt.report.carry.report import (
    HOLDOUT_CAVEAT, PERIOD_LABELS, REPORT_FILE, UNMODELLED, ReportRefused, RunFolder, check_prereg, fhurdle, fpci,
    latest_ok, read_done, reading_text, scan, v2_reading_text, verdict_sentence, write_reports,
)
from perpbt.report.markdown import NA, fp, fpct, table

SUMMARY_FILE = "summary.md"
SCREEN_FILE = "screen.json"
QUESTION = (
    "Does a simple, pre-registered, delta-neutral funding carry (long spot and short the USDT-M perpetual of the "
    "same coin, held only while recent funding is high) on a point-in-time basket earn a net return that is "
    "significantly positive and significantly above the yield of simply holding stablecoins, after fees, "
    "slippage, basis moves and margin? (overview §1)"
)
PERIOD_ROWS = (("run", "insample"), ("holdout", "holdout"), ("forward", "forward_12m"), ("forward", "forward_36m"))


def read_screen(root: Path) -> dict | None:
    f = root / SCREEN_FILE
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _link(f: RunFolder, root: Path) -> str:
    return f"[report]({f.rel(root)}/{REPORT_FILE})" if (f.path / REPORT_FILE).exists() else NA


def _rule(prereg: CarryPrereg) -> str:
    s, c, sig = prereg.stats, prereg.costs, prereg.signal
    slip = ", ".join(f"{k} {fpct(v, 2)}" for k, v in c.slippage.items())
    return (f"**Rule.** Per pair of the basket ({', '.join(prereg.universe['um']['members'])}), long spot and short "
            f"the perpetual in equal quantity while the {sig.lookback_days}-day trailing funding says on (on above "
            f"{fhurdle(sig.entry_annual)}/yr, off below {fhurdle(sig.exit_annual)}/yr), equal sleeves, perp leverage "
            f"{prereg.sleeve.perp_leverage:g}. Costs: perp taker {fpct(c.perp_taker, 2)}, spot taker "
            f"{fpct(c.spot_taker, 2)}, slippage per leg ({slip}). **Edge iff V1 (net Sharpe above 0) and V2 "
            f"(annualised net return above the {fhurdle(prereg.hurdle.annual)}/yr hurdle) both pass**, each "
            f"one-sided at α = {s.alpha:g}, unadjusted; moving-block bootstrap, B = {s.bootstrap_n}, blocks of "
            f"{s.block_len_days} days. V2 reads three ways: proven above the hurdle, not proven (the CI contains 0), "
            "proven below (the CI lies below 0).")


def _screen(screen: Mapping | None) -> str:
    if screen is None:
        return "**Screen (Phase 2).** No `screen.json` in this runs directory."
    return (f"**Screen (Phase 2).** The simplified net screen on the in-sample basket gave p = "
            f"{fp(screen['p_screen'])} (V2 against the {fhurdle(screen['hurdle_annual'])}/yr hurdle; stop at "
            f"{screen['stop_if_p_at_least']:g} or above): **{screen['decision']}**. It could stop the project, never "
            "tune it; its model has no fill delay, margin, rebalancing or liquidation.")


def _lock(lock: Mapping, prereg: CarryPrereg) -> str:
    if lock["code_version"] is None:
        return "**Code lock.** Not locked yet (`perpbt carry lock-code`, Phase 7)."
    return (f"**Code lock.** Carry code version `{lock['code_version']}` locked on {lock['code_locked_on']} (git "
            f"`{lock['code_git_commit']}`); the forward period starts {prereg.periods.forward_start}.")


def _period_status(kind: str, period: str, f: RunFolder | None, done: Mapping | None) -> str:
    if kind == "holdout" and done is not None:
        return f"DONE ({done['status']})"
    if f is None:
        return "not run yet"
    return "descriptive review" if f.is_review else "run"


V2_HEADERS = ("Excess over the hurdle", "95% CI (excess)", "V2 reading")


def _v2_cells(v2: Mapping) -> tuple[str, str, str]:
    """The excess over the hurdle, its CI and the three-way reading that CI decides (overview §3)."""
    return fpct(v2["ann_excess"], 2), fpci(*v2["ann_excess_ci"]), v2_reading_text(v2["reading"])


def _period_table(folders: Sequence[RunFolder], root: Path, prereg: CarryPrereg, done: Mapping | None) -> list[str]:
    p = prereg.periods
    dates = {"insample": f"{p.insample_start} to {p.insample_end}", "holdout": f"{p.holdout_start} to {p.holdout_end}"}
    rows = []
    for kind, period in PERIOD_ROWS:
        f = next((g for g in folders if g.kind == kind and g.period == period and g.is_primary), None)
        span = (f"{f.stats['period']['start']} to {f.stats['period']['end']}" if f is not None
                else dates.get(period, "from the forward start"))
        status = _period_status(kind, period, f, done)
        if f is None:
            rows.append((PERIOD_LABELS[period], span, status, NA, NA, NA, NA, NA, NA, NA, NA))
        elif f.is_review:
            rows.append((PERIOD_LABELS[period], span, status, "descriptive", NA, NA, NA, NA, NA,
                         fpct(f.stats["annual"]["ann_return"], 2), _link(f, root)))
        else:
            v1, v2 = f.stats["v1"], f.stats["v2"]
            rows.append((PERIOD_LABELS[period], span, status, reading_text(f.stats["reading"]), fp(v1["p"]),
                         fp(v2["p"]), *_v2_cells(v2), fpct(v2["ann_return"], 2), _link(f, root)))
    return [table(["Period", "Dates", "Status", "Verdict", "p (V1)", "p (V2)", *V2_HEADERS, "Annualised net return",
                   "Report"], rows), ""]


def _sentences(folders: Sequence[RunFolder]) -> list[str]:
    out = []
    for kind, period in PERIOD_ROWS:
        f = next((g for g in folders if g.kind == kind and g.period == period and g.is_primary), None)
        if f is None:
            continue
        if f.is_review:
            m = f.stats["metrics"]
            inside = sum(1 for x in m.values() if x["inside"])
            out += [f"- **{PERIOD_LABELS[period].capitalize()}** (descriptive, no test): {inside} of {len(m)} metrics lie inside "
                    "the in-sample primary's per-year range; see the report.", ""]
            continue
        out += [f"- {verdict_sentence(f.stats, primary=True)}", ""]
        if period == "holdout":
            out += [f"  {HOLDOUT_CAVEAT}", ""]
    return out


def _exploratory(folders: Sequence[RunFolder], root: Path) -> list[str]:
    lines = ["## Exploratory rows (descriptive)", "",
             "The six pre-registered exploratory rows on the portfolio: reported, no say in the verdict (the DSR "
             "trials of the primary's report). Each pair alone is a table in its row's report.", ""]
    any_rows = False
    for kind, period in PERIOD_ROWS[:2]:
        rows = [(f.row, reading_text(f.stats["reading"]), fp(f.stats["v1"]["p"]), fp(f.stats["v2"]["p"]),
                 *_v2_cells(f.stats["v2"]), fpct(f.stats["v2"]["ann_return"], 2), _link(f, root))
                for f in folders if f.kind == kind and f.period == period and f.is_portfolio and f.row != "primary"]
        if rows:
            any_rows = True
            lines += [f"**{PERIOD_LABELS[period]}**", "",
                      table(["Row", "Would read", "p (V1)", "p (V2)", *V2_HEADERS, "Annualised net return",
                             "Report"], rows), ""]
    if not any_rows:
        lines += ["None has run yet.", ""]
    return lines


def _notes(root: Path) -> list[str]:
    neutral = [r for r in Registry(root).rows() if r["status"] == "output_neutral"]
    reasons = sorted({r["reason"] for r in latest_ok(root).values() if r.get("reason")})
    notes = [f"Output-neutral code changes recorded in the registry: {len(neutral)}."]
    notes += [f"Run after a code change since the lock; reason: {r}" for r in reasons]
    notes.append("Numbers in the tables are `stats.json` values; each report says where its figures come from.")
    return ["## Notes", "", *[f"- {n}" for n in notes], ""]


def build_summary(root: str | Path, prereg: CarryPrereg, lock: Mapping,
                  folders: Sequence[RunFolder] | None = None) -> str | None:
    """The summary's Markdown, or None when the screen stopped the project (its ``summary.md`` stays)."""
    root = Path(root)
    screen = read_screen(root)
    if screen is not None and screen["decision"] == "stop":
        return None
    folders = list(folders) if folders is not None else scan(root, row_names(prereg))
    done = read_done(root)
    lines = ["# Funding carry — summary", "", f"**Question.** {QUESTION}", "", _rule(prereg), "", _screen(screen), "",
             _lock(lock, prereg), "", "## Verdict per period", "",
             *_period_table(folders, root, prereg, done), *_sentences(folders),
             "The 36-month forward review is the only fully unseen test; the holdout is not fully unseen (overview "
             "§4).", "", *_exploratory(folders, root), "## Unmodelled risks", "", UNMODELLED, "",
             *_notes(root)]
    return "\n".join(lines).rstrip() + "\n"


def write_summary(root: str | Path, prereg: CarryPrereg, lock: Mapping,
                  folders: Sequence[RunFolder] | None = None) -> Path | None:
    text = build_summary(root, prereg, lock, folders)
    if text is None:
        return None
    path = Path(root) / SUMMARY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def report_all(prereg_path: str | Path, runs_dir: str | Path) -> dict:
    """``perpbt carry report``: every portfolio report and the summary from what exists under ``runs/carry/``.

    Refuses (ReportRefused) unless the prereg is frozen and unchanged and every ``stats.json`` has its hash.
    Returns ``{"reports": [paths], "summary": path | None}`` (None: the screen stopped; its summary stays)."""
    try:
        prereg = check_frozen(prereg_path)
    except ConfigError as e:
        raise ReportRefused(str(e)) from e
    lock = read_lock(lock_path(prereg_path))
    root = carry_root(runs_dir)
    folders = scan(root, row_names(prereg))
    check_prereg(folders, prereg)
    reports = write_reports(root, prereg, folders)
    return {"reports": reports, "summary": write_summary(root, prereg, lock, folders)}
