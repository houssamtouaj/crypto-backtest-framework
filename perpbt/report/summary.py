"""The summary and the pre-registered verdict (spec §7.3): ``runs/summary.md``.

The verdict functions are pure functions of the numbers: ``verdict``
applies the rule of the pre-registration (Holm-adjusted one-sided p below
α, and for buy-and-hold also a positive Sharpe difference) and
``cell_sentence`` turns one ``results.parquet`` row into the sentence the
summary prints. ``min_detectable_effect`` is the holdout power
calculation. ``build_summary`` reads ``results.parquet`` (the verdict,
cross-session and grid tables), the primaries' trade tables (shared-trade
fractions), the prereg, its lock, the registry and ``holdout/DONE``.
The numbers it derives itself (holdout power, Holm over the nine at each
re-pricing cell, shared-trade fractions) are labelled as computed at
report time.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml
from scipy.stats import norm

from perpbt.experiments.holdout import done_path, prior_attempts
from perpbt.experiments.prereg import Prereg, lock_path, read_lock
from perpbt.report.markdown import NA, fbool, fci, fint, fnum, fp, fpct, table
from perpbt.report.report import VariantRun, _diff_text, cell_label, session_label
from perpbt.stats.diagnostics import shared_fraction
from perpbt.stats.multiplicity import holm

SUMMARY_FILE = "summary.md"
POWER = 0.80
BENCHMARKS = (("a", "random timing", "A"), ("b", "random days", "B"), ("bh", "buy-and-hold", "B&H"))
RULE_KEYS = ("beats_baseline_a", "beats_baseline_b", "beats_buy_and_hold")  # the rule `verdict` applies (spec §6.8)


def _v(x):
    """A results value as a plain Python value: NaN / NA to None."""
    if x is None or x is pd.NA:
        return None
    if isinstance(x, float) and math.isnan(x):
        return None
    return x.item() if hasattr(x, "item") else x


# --- the verdict, one benchmark ------------------------------------------------------------------

@dataclass(frozen=True)
class Verdict:
    passes: bool | None  # the verdict rule on the adjusted p; None when there is no adjusted p
    raw_passes: bool | None  # the same rule on the raw p; None when there is no raw p


def verdict(p_raw, p_adj, alpha: float, *, sharpe_diff=None, needs_positive: bool = False) -> Verdict:
    """The pre-registered rule for one benchmark: ``p_adj < alpha`` (and ``sharpe_diff > 0`` for buy-and-hold).

    Raises ValueError when the adjusted p is below the raw p (Holm never lowers a p-value).
    """
    p_raw, p_adj, sharpe_diff = _v(p_raw), _v(p_adj), _v(sharpe_diff)
    if p_raw is not None and p_adj is not None and p_adj < p_raw - 1e-12:
        raise ValueError(f"verdict: adjusted p {p_adj} is below the raw p {p_raw}")
    sign_ok = not needs_positive or (sharpe_diff is not None and sharpe_diff > 0)
    return Verdict(None if p_adj is None else bool(p_adj < alpha and sign_ok),
                   None if p_raw is None else bool(p_raw < alpha and sign_ok))


def verdicts(row: Mapping, alpha: float) -> dict[str, Verdict]:
    """The three verdicts of one ``results`` row (``p_a``, ``p_a_adj``, …, ``sharpe_diff``)."""
    return {k: verdict(row.get(f"p_{k}"), row.get(f"p_{k}_adj"), alpha, sharpe_diff=row.get("sharpe_diff"),
                       needs_positive=k == "bh") for k, _, _ in BENCHMARKS}


# --- the sentence --------------------------------------------------------------------------------

def _s(text: str) -> str:
    """Typographic minus in prose."""
    return text.replace("-", "−") if text.startswith("-") else text


def _sn(x, digits: int = 2) -> str:
    return _s(fnum(x, digits))


def _clause(key: str, name: str, row: Mapping, v: Verdict, alpha: float) -> str:
    p, p_adj = row.get(f"p_{key}"), row.get(f"p_{key}_adj")
    pre = ""
    if key == "bh":
        d = row.get("sharpe_diff")
        sign_fails = p_adj is not None and p_adj < alpha and not (d is not None and d > 0)
        pre = f"Sharpe difference {_sn(d)}" + (" is not positive, " if sign_fails else ", ")
    if v.passes is None:
        if v.raw_passes is None:
            why = "no trades" if row.get("n_trades_r") == 0 else "no p-value yet"
            return f"not tested against {name} ({why})"
        return f"not yet corrected for multiplicity against {name} ({pre}raw p = {fp(p)})"
    verb = f"beats {name}" if v.passes else f"does not beat {name}"
    extra = f"; raw p = {fp(p)} would pass without the correction" if v.raw_passes and not v.passes else ""
    return f"{verb} ({pre}adjusted p = {fp(p_adj)}{extra})"


def cell_sentence(row: Mapping, alpha: float) -> str:
    """One cell's verdict as a sentence (a pure function of the ``results`` row; spec §7.3 item 2)."""
    row = {k: _v(x) for k, x in row.items()}
    period = "holdout" if row.get("is_holdout") else "in-sample"
    n = row.get("n_trades_r")
    head = (f"{cell_label(row['pair'], row['session_variant'])}, {period}: {fint(n)} trades, mean net R "
            f"{_sn(row.get('mean_net_r'))} (95% CI {_sn(row.get('mean_net_r_ci_lo'))} to "
            f"{_sn(row.get('mean_net_r_ci_hi'))}).")
    vs = verdicts(row, alpha)
    clauses = [_clause(k, name, row, vs[k], alpha) for k, name, _ in BENCHMARKS]
    body = ", ".join(clauses)
    return f"{head} {body[0].upper()}{body[1:]}."


# --- holdout power -------------------------------------------------------------------------------

def min_detectable_effect(sigma, n, alpha: float, power: float = POWER) -> float | None:
    """Smallest mean net R a one-sided test of mean > 0 detects with ``power`` (normal approximation).

    ``(z_{1−α} + z_power) · σ / √n``; None without a σ or with fewer than 2 trades.
    """
    sigma, n = _v(sigma), _v(n)
    if sigma is None or n is None or n < 2:
        return None
    return float((norm.ppf(1.0 - alpha) + norm.ppf(power)) * sigma / math.sqrt(n))


# --- the summary ---------------------------------------------------------------------------------

@dataclass
class Inputs:
    runs_dir: Path
    prereg: Prereg
    prereg_path: Path
    results: pd.DataFrame

    @property
    def alpha(self) -> float:
        return self.prereg.stats.alpha

    @property
    def cells(self) -> list[tuple[str, str]]:
        return [(p, s) for p in self.prereg.pairs for s in self.prereg.sessions]

    def primary(self, holdout: bool) -> dict[tuple[str, str], dict]:
        r = self.results
        sel = r[r["is_primary"].fillna(False).astype(bool) & (r["is_holdout"] == holdout)]
        return {(x["pair"], x["session_variant"]): {k: _v(v) for k, v in x.items()} for x in sel.to_dict("records")}

    def run(self, vid: str) -> VariantRun:
        return VariantRun.load(self.runs_dir / vid)


def _verdict_rows(inp: Inputs) -> list[tuple]:
    rows = []
    for holdout in (False, True):
        found = inp.primary(holdout)
        if holdout and not found:
            continue
        for cell in inp.cells:
            r = found.get(cell)
            label, period = cell_label(*cell), "holdout" if holdout else "in-sample"
            if r is None:
                rows.append((label, period, *[NA] * 13, "not run"))
                continue
            vs = verdicts(r, inp.alpha)
            raw = ", ".join(short for k, _, short in BENCHMARKS if vs[k].raw_passes) or "none"
            rows.append((label, period, fint(r["n_trades_r"]), fnum(r["mean_net_r"]),
                         fci(r["mean_net_r_ci_lo"], r["mean_net_r_ci_hi"]),
                         fp(r["p_a"]), fp(r["p_a_adj"]), fp(r["p_b"]), fp(r["p_b_adj"]), fp(r["p_bh"]),
                         fp(r["p_bh_adj"]), fnum(r["sharpe_diff"], 2),
                         fbool(vs["a"].passes), fbool(vs["b"].passes), fbool(vs["bh"].passes), raw))
    return rows


VERDICT_HEADERS = ["Cell", "Period", "n", "Mean net R", "95% CI", "p_A", "p_A (Holm)", "p_B", "p_B (Holm)", "p_BH",
                   "p_BH (Holm)", "Sharpe diff", "Beats A", "Beats B", "Beats B&H", "Raw p would pass"]


def _section_verdict(inp: Inputs) -> list[str]:
    vr = inp.prereg.verdict_rule
    rule = "; ".join(f"{k}: {vr.get(k, 'not stated in the prereg')}" for k in RULE_KEYS)
    other = sorted(k for k in vr if k.startswith("beats_") and k not in RULE_KEYS)
    note = (f" The prereg also names {', '.join(other)}, which this summary does not apply." if other else "")
    return ["## 1. Verdict", "",
            f"The pre-registered rule ({rule}; α = {inp.alpha}).{note} p-values are one-sided; Holm adjusts each "
            "benchmark over the primary family of one period. \"Raw p would pass\" lists the benchmarks whose "
            "unadjusted p is below α, so the effect of the correction is visible. A: random entry time in the "
            "strategy's own sessions; B: random sessions and entry times; B&H: vol-scaled buy-and-hold.", "",
            table(VERDICT_HEADERS, _verdict_rows(inp)), ""]


def _section_sentences(inp: Inputs) -> list[str]:
    lines = ["## 2. Per cell", ""]
    for holdout in (False, True):
        found = inp.primary(holdout)
        for cell in inp.cells:
            if cell in found:
                lines.append(f"- {cell_sentence(found[cell], inp.alpha)}")
            elif not holdout:
                lines.append(f"- {cell_label(*cell)}, in-sample: not run yet.")
    if not inp.primary(True):
        lines.append("- Holdout: not run yet (`perpbt holdout`).")
    return lines + [""]


def _section_power(inp: Inputs) -> list[str]:
    n_cells = len(inp.cells)
    a, a1 = inp.alpha, inp.alpha / n_cells
    lines = ["## 3. Holdout power", "",
             f"The smallest mean net R the holdout could detect with {POWER:.0%} power in a one-sided test of mean "
             f"net R > 0 (normal approximation), given the in-sample standard deviation of net R: "
             f"(z(1−α) + z(0.8)) · σ / √n, at α = {a} and at α/{n_cells} = {a1:.4g}, the bar the best cell must "
             "clear under Holm. Computed at report time. It is an approximation: the verdict compares the cell with "
             "the random baselines, whose mean is not 0, rather than with 0.", ""]
    hold = inp.primary(True)
    if not hold:
        return lines + ["The holdout has not been run yet.", ""]
    ins = inp.primary(False)
    rows = []
    for cell in inp.cells:
        h, i = hold.get(cell), ins.get(cell)
        sigma = inp.run(i["variant_id"]).stats["headline"]["std_net_r"] if i else None
        n = h["n_trades_r"] if h else None
        rows.append((cell_label(*cell), fnum(sigma), fint(n), fnum(min_detectable_effect(sigma, n, a)),
                     fnum(min_detectable_effect(sigma, n, a1)), fnum(h["mean_net_r"]) if h else NA))
    return lines + [table(["Cell", "In-sample std of net R", "Holdout n", f"MDE at α = {a}",
                           f"MDE at α/{n_cells}", "Holdout mean net R"], rows), "",
                    "A holdout that does not pass rules out little below the MDE: edges smaller than the MDE would "
                    "often go undetected.", ""]


def _shared(inp: Inputs, found: Mapping[tuple[str, str], dict]) -> dict[tuple[str, str], dict]:
    out = {}
    for pair in inp.prereg.pairs:
        tables = {s: inp.run(found[(pair, s)]["variant_id"]).table("trades")
                  for s in inp.prereg.sessions if (pair, s) in found}
        tables = {k: v for k, v in tables.items() if v is not None}
        if len(tables) > 1:
            for s, fr in shared_fraction(tables).items():
                out[(pair, s)] = fr
    return out


def _section_sessions(inp: Inputs) -> list[str]:
    found = inp.primary(False)
    shared = _shared(inp, found)
    rows = []
    for cell in inp.cells:
        r = found.get(cell)
        if r is None:
            rows.append((cell_label(*cell), *[NA] * 7))
            continue
        fr = shared.get(cell) or {}
        others = ", ".join(f"{session_label(k)} {fpct(v)}" for k, v in fr.items() if k != "any") or NA
        rows.append((cell_label(*cell), fint(r["n_sessions"]), fint(r["n_blocks_seen"]), fint(r["n_orders"]),
                     fint(r["n_filled"]), fint(r["n_trades_r"]), fpct(fr.get("any")), others))
    return ["## 4. Cross-session context", "",
            "The three session windows differ in length (UTC 24 h, NY 6.5 h, London 8.5 h, D1), so the number of "
            "opportunities differs as much as any edge. The shared-trade fraction is the share of a variant's "
            "trades whose candidate candle another session variant of the same pair also traded (computed at "
            "report time from the trade tables): the session variants are not independent tests.", "",
            table(["Cell", "Sessions", "Blocks seen", "Orders", "Fills", "Trades (R subset)",
                   "Shared with any other session", "Shared, by session"], rows), ""]


def _cost_key(r: Mapping) -> tuple[float, float]:
    return float(r["slippage"]), float(r["fee_maker"])


def _cost_label(k: tuple[float, float]) -> str:
    return f"s {fpct(k[0], 2)} / m {fpct(k[1], 2)}"


def _section_costs(inp: Inputs) -> list[str]:
    found = inp.primary(False)
    tables = {cell: json.loads(found[cell]["reprice_json"]) for cell in inp.cells if cell in found}
    lines = ["## 5. Cost sensitivity", "",
             "Mean net R of each primary re-priced at other costs (s: slippage on stop and time exits, m: maker "
             "fee; taker fixed; D13). Descriptive only: the verdict uses each pair's registered costs (last column).",
             ""]
    if not tables:
        return lines + ["No primary cell has run yet.", ""]
    keys = sorted({_cost_key(r) for t in tables.values() for r in t})
    head = [_cost_label(k) for k in keys]
    by = {cell: {_cost_key(r): r for r in t} for cell, t in tables.items()}
    registered = {cell: next((_cost_label(_cost_key(r)) for r in t if r["is_primary"]), NA) for cell, t in tables.items()}
    mean_rows = [(cell_label(*cell), *[fnum(by[cell].get(k, {}).get("mean_net_r")) for k in keys], registered[cell])
                 for cell in tables]
    adj = {}
    for k in keys:
        cells = list(tables)
        ps = holm([by[c].get(k, {}).get("p_a") for c in cells])
        adj.update({(c, k): p for c, p in zip(cells, ps, strict=True)})
    order = sorted(keys, key=lambda k: (-k[0], -k[1]))  # most expensive first
    p_rows = []
    for cell in tables:
        first = next((k for k in order if adj[(cell, k)] is not None and adj[(cell, k)] < inp.alpha), None)
        first_s = "none" if first is None else _cost_label(first)
        p_rows.append((cell_label(*cell), *[fp(adj[(cell, k)]) for k in keys], first_s))
    lines += [table(["Mean net R", *head, "Registered costs"], mean_rows), "",
              f"Holm-adjusted p_A over the {len(tables)} primaries at each cost cell (computed at report time), and "
              f"the first cost cell, from the most expensive (slippage, then maker fee, descending), at which "
              f"the adjusted p_A falls below α = {inp.alpha}:", "",
              table(["p_A (Holm)", *head, "First passing cell"], p_rows), ""]
    return lines


def _section_grid(inp: Inputs) -> list[str]:
    r = inp.results
    grid = r[~r["is_holdout"]]
    lines = ["## 6. Grid overview", ""]
    if (~grid["is_primary"].fillna(False).astype(bool)).sum() == 0:
        return lines + ["The grid has not run yet (`perpbt grid --run`).", ""]
    rows = []
    for cell in inp.cells:
        g = grid[(grid["pair"] == cell[0]) & (grid["session_variant"] == cell[1]) & grid["mean_net_r"].notna()]
        if len(g) == 0:
            rows.append((cell_label(*cell), *[NA] * 9))
            continue
        best = {k: _v(v) for k, v in g.loc[g["mean_net_r"].idxmax()].items()}
        prim = _primary_params_json(inp, cell)
        diff = NA if prim is None else _diff_text(json.loads(best["params_json"])["params"],
                                                  json.loads(prim)["params"])
        dsr = inp.run(best["variant_id"]).stats["dsr"]
        n_loc, n_glob = ((dsr.get(k) or {}).get("N") for k in ("local", "global"))
        rows.append((cell_label(*cell), diff, fint(len(g)), fint(best["n_trades_r"]), fnum(best["mean_net_r"]),
                     fp(best["p_a"]), fnum(best["dsr_local"], 3), fint(n_loc), fnum(best["dsr_global"], 3),
                     fint(n_glob)))
    tested = grid["p_a"].notna()
    k, n = int((grid.loc[tested, "p_a"] < inp.alpha).sum()), int(tested.sum())
    lines += [table(["Cell", "Best variant (vs primary)", "Variants", "n", "Mean net R", "p_A", "DSR (cell)",
                     "N (cell)", "DSR (all)", "N (all)"], rows), "",
              "DSR (cell) deflates against the pair × session's grid, DSR (all) against every grid variant; N is the "
              "number of trials.", "",
              f"{k} of {n} grid variants with baselines have raw p_A < {inp.alpha}; about {n * inp.alpha:.1f} would "
              "by chance if none had an edge. The best cell is selected after the fact, so its raw p overstates; "
              "the DSR deflates its Sharpe for the number of trials (conservatively, since the variants share most "
              "of their trades).", ""]
    return lines


def _primary_params_json(inp: Inputs, cell) -> str | None:
    r = inp.primary(False).get(cell)
    return r["params_json"] if r else None


def _section_prereg(inp: Inputs) -> list[str]:
    p = inp.prereg
    lines = ["## 7. What was pre-registered and what changed", "",
             f"- Registered on {p.registered_on}; data download date {p.data_download_date or 'not set'}; "
             f"in-sample {p.insample.start} to {p.insample.end}; holdout {p.holdout.start} to "
             f"{p.holdout.end or 'not set'}.",
             f"- Current prereg hash `{p.prereg_hash()}`."]
    lock = lock_path(inp.prereg_path)
    codes = sorted({c for c in inp.results["code_version"].dropna()})
    if lock.exists():
        lk = read_lock(lock)
        same = lk["prereg_hash"] == p.prereg_hash()
        lines += [f"- Frozen on {lk['frozen_on']} (lock: prereg hash `{lk['prereg_hash']}`, "
                  f"{'matches' if same else 'DIFFERS from'} the current file; code version `{lk['code_version']}`; "
                  f"git commit `{lk['git_commit']}`; data download date {lk['data_download_date']}).",
                  f"- Results code version: {', '.join(f'`{c}`' for c in codes) or NA} "
                  f"({'same as' if codes == [lk['code_version']] else 'differs from'} the freeze)."]
    else:
        lines += ["- Not frozen yet (`perpbt prereg freeze`).",
                  f"- Results code version: {', '.join(f'`{c}`' for c in codes) or NA}."]
    attempts = prior_attempts(inp.runs_dir)
    done = done_path(inp.runs_dir)
    if done.exists():
        d = yaml.safe_load(done.read_text(encoding="utf-8")) or {}
        lines.append(f"- Holdout: {d.get('status')} at {d.get('finished_utc')} (batch {d.get('batch')}); "
                     f"failed cells: {len(d.get('failed') or [])}.")
        if d.get("reason"):
            lines.append(f"- Holdout code-change reason: {d['reason']}")
    else:
        lines.append("- Holdout: not run.")
    lines.append(f"- Holdout attempts in the registry: {len(attempts)}"
                 + (" (more than one: the holdout was touched again after `DONE` was deleted)." if len(attempts) > 1
                    else "."))
    return lines + [""]


def _period_answer(found: Mapping, alpha: float, period: str, n_cells: int) -> str:
    lead = period[0].upper() + period[1:]
    how = ("the holdout runner applies it only when every holdout cell succeeds" if "holdout" in period
           else "run `perpbt stats holm`")
    if not found:
        return f"{lead}: no primary cell has run yet."
    keys = [k for k, _, _ in BENCHMARKS]
    if all(r.get(f"p_{k}_adj") is None for r in found.values() for k in keys):
        return f"{lead}, the Holm correction has not been applied, so there is no verdict ({how})."
    partial = [c for c, r in found.items()
               if any(r.get(f"p_{k}") is not None and r.get(f"p_{k}_adj") is None for k in keys)]
    if partial:
        return (f"{lead}, the Holm correction is incomplete: {len(partial)} of {len(found)} primary cells have a raw "
                f"p-value without an adjusted one ({', '.join(cell_label(*c) for c in partial)}; a rerun or a "
                f"baselines pass after the correction drops it), so there is no verdict ({how}).")
    ran = f"{len(found)} of {n_cells}" if len(found) < n_cells else f"{n_cells}"
    passing = {key: [c for c, r in found.items() if verdicts(r, alpha)[key].passes] for key in keys}
    if not any(passing.values()):
        return (f"{lead}, after the Holm correction, none of the {ran} primary cells beats random timing, random "
                "days or buy-and-hold.")
    parts = [f"{len(c)} beat{'s' if len(c) == 1 else ''} {name} ({', '.join(cell_label(*x) for x in c)})" if c
             else f"none beats {name}" for (key, name, _), c in zip(BENCHMARKS, passing.values(), strict=True)]
    all_three = [c for c in found if all(c in v for v in passing.values())]
    end = (f"{len(all_three)} beat{'s' if len(all_three) == 1 else ''} all three: "
           f"{', '.join(cell_label(*c) for c in all_three)}." if all_three else "no cell beats all three.")
    return f"{lead}, after the Holm correction ({ran} primary cells): {'; '.join(parts)}; {end}"


def largest_caveat(found: Mapping, alpha: float, n_cells: int | None = None) -> str:
    """The single largest caveat, from the in-sample primary rows (averages computed at report time).

    Beta when a cell beats a random baseline but not buy-and-hold. Otherwise,
    from the mean gross R and the mean cost in R over the primaries: no gross
    edge when the gross R is not positive; costs when they take at least half
    of a positive gross R; else sample size, with the median in-sample MDE at
    the first Holm step (the rows carry ``std_net_r``).
    """
    vs = {c: verdicts(r, alpha) for c, r in found.items()}
    beta = [cell_label(*c) for c, v in vs.items() if (v["a"].passes or v["b"].passes) and not v["bh"].passes]
    if beta:
        return (f"The largest caveat is beta: {', '.join(beta)} beat{'s' if len(beta) == 1 else ''} a random "
                "baseline but not vol-scaled buy-and-hold, so a long-only strategy's gains there are not "
                "distinguishable from holding the market.")
    vals = [r for r in found.values() if r.get("avg_cost_r") is not None and r.get("mean_gross_r") is not None]
    if not vals:
        return "The largest caveat is that no primary cell has trades to judge."
    cost = sum(r["avg_cost_r"] for r in vals) / len(vals)
    gross = sum(r["mean_gross_r"] for r in vals) / len(vals)
    share = [r["share_cost_r_gt_1"] for r in vals if r.get("share_cost_r_gt_1") is not None]
    avg = "averaged over the primary cells (computed at report time)"
    if gross <= 0:
        return (f"The largest caveat is not costs: even before costs the mean R is {_sn(gross)}, {avg}, so the rule "
                "does not select profitable entries in the first place.")
    if cost >= 0.5 * gross:
        return (f"The largest caveat is costs: {avg}, a trade costs {_sn(cost)} R against a mean gross R of "
                f"{_sn(gross)} before costs, and {fpct(sum(share) / len(share)) if share else NA} of trades cost "
                "more than 1 R.")
    k = n_cells or len(found)
    mdes = sorted(m for m in (min_detectable_effect(r.get("std_net_r"), r.get("n_trades_r"), alpha / k)
                              for r in found.values()) if m is not None)
    ns = sorted(r["n_trades_r"] for r in found.values() if r.get("n_trades_r") is not None)
    mde = (f"; at that size only a mean net R above about {_sn(mdes[len(mdes) // 2])} R would pass at α/{k} with "
           "80% power" if mdes else "")
    return (f"The largest caveat is sample size: the median primary cell has {ns[len(ns) // 2]} trades{mde}"
            f"; averaged over the primary cells (computed at report time), costs take {_sn(cost)} R of a mean "
            f"gross {_sn(gross)} R.")


def plain_answer(insample: Mapping, holdout: Mapping, alpha: float, n_cells: int) -> list[str]:
    """Three to six sentences from the verdict rows (``results`` rows keyed by cell; spec §7.3 item 8)."""
    out = [_period_answer(insample, alpha, "in-sample", n_cells)]
    out.append(_period_answer(holdout, alpha, "on the holdout", n_cells) if holdout else
               "The holdout has not been run yet, so nothing is confirmed out of sample.")
    vs = [verdicts(r, alpha)[k] for r in insample.values() for k, _, _ in BENCHMARKS]
    raw = sum(bool(v.raw_passes) and not v.passes for v in vs)
    tested = sum(v.raw_passes is not None for v in vs)
    if raw:
        out.append(f"Without the correction, {raw} more in-sample comparison{'s' if raw > 1 else ''} (cell × "
                   f"benchmark) would pass at raw p < {alpha}; about {tested * alpha:.1f} of the {tested} "
                   "comparisons would pass by chance alone if there were no edge.")
    out.append(largest_caveat(insample, alpha, n_cells))
    return out


def build_summary(runs_dir: str | Path, prereg: Prereg, prereg_path: str | Path, results: pd.DataFrame) -> str:
    inp = Inputs(Path(runs_dir), prereg, Path(prereg_path), results)
    lines = ["# Summary: does the first 15-minute bullish order block have an edge?", "",
             f"Pairs {', '.join(prereg.pairs)}; sessions {', '.join(prereg.sessions)}; in-sample "
             f"{prereg.insample.start} to {prereg.insample.end}. Every number below comes from `results.parquet` "
             "or the variants' `stats.json` unless marked as computed at report time. Per-cell detail is in each "
             "variant's `report.md`. R is a trade's planned risk (entry minus stop); net R is its result after "
             "fees, slippage and funding, in units of R.", ""]
    ins = inp.primary(False)
    for r in ins.values():  # the in-sample σ of net R, for the sample-size caveat
        r["std_net_r"] = inp.run(r["variant_id"]).stats["headline"]["std_net_r"]
    answer = plain_answer(ins, inp.primary(True), inp.alpha, len(inp.cells))
    lines += ["## Answer", "", " ".join(answer), ""]
    lines += (_section_verdict(inp) + _section_sentences(inp) + _section_power(inp) + _section_sessions(inp)
              + _section_costs(inp) + _section_grid(inp) + _section_prereg(inp))
    return "\n".join(lines).rstrip() + "\n"


def write_summary(runs_dir: str | Path, prereg: Prereg, prereg_path: str | Path, results: pd.DataFrame) -> Path:
    path = Path(runs_dir) / SUMMARY_FILE
    path.write_text(build_summary(runs_dir, prereg, prereg_path, results), encoding="utf-8", newline="\n")
    return path


__all__ = ["BENCHMARKS", "Verdict", "build_summary", "cell_sentence", "largest_caveat", "min_detectable_effect",
           "plain_answer", "verdict", "verdicts", "write_summary"]
