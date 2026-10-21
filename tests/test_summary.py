"""Spec §7.3: the verdict, the sentence generator, holdout power, and the summary with and without the holdout."""
import itertools
import json
import re

import pytest

from perpbt.experiments.registry import read_results
from perpbt.report.markdown import parse_tables
from perpbt.report.summary import (
    BENCHMARKS,
    VERDICT_HEADERS,
    build_summary,
    cell_sentence,
    largest_caveat,
    min_detectable_effect,
    plain_answer,
    verdict,
)
from tests.report_harness import build_runs

ALPHA = 0.05
NAMES = {k: name for k, name, _ in BENCHMARKS}


def make_row(outcome: dict[str, bool], raw: bool | dict[str, bool], *, holdout: bool) -> dict:
    """A results row whose verdicts are ``outcome`` (benchmark -> passes) and whose raw p passes per ``raw``."""
    raw = raw if isinstance(raw, dict) else dict.fromkeys(outcome, raw)
    row = {"pair": "BTCUSDT", "session_variant": "ny", "is_holdout": holdout, "n_trades_r": 412, "mean_net_r": 0.06,
           "mean_net_r_ci_lo": -0.02, "mean_net_r_ci_hi": 0.14, "sharpe_diff": -0.3}
    for k, passes in outcome.items():
        if passes:
            p, adj = 0.004, 0.02
        elif raw[k]:
            p, adj = 0.03, 0.31
        else:
            p, adj = 0.31, 0.71
        row[f"p_{k}"], row[f"p_{k}_adj"] = p, adj
        if k == "bh" and (passes or raw[k]):
            row["sharpe_diff"] = 0.4
    return row


KEYS = ("a", "b", "bh")
# every outcome (2^3) x every raw pattern (2^3) x period; a benchmark that passes adjusted also passes raw
# (Holm never lowers a p), so the impossible patterns are left out (test_adjusted_below_raw_is_refused)
CASES = [(dict(zip(KEYS, bits, strict=True)), dict(zip(KEYS, raw, strict=True)), holdout)
         for bits in itertools.product((False, True), repeat=3) for raw in itertools.product((False, True), repeat=3)
         for holdout in (False, True) if all(r or not b for b, r in zip(bits, raw, strict=True))]


def test_case_table_covers_the_spec_list():
    # 27 consistent (outcome, raw) pairs x 2 periods; includes all 2^3 outcomes with raw passing and failing
    assert len(CASES) == 54
    for bits in itertools.product((False, True), repeat=3):
        o = dict(zip(KEYS, bits, strict=True))
        assert any(c[0] == o and all(c[1].values()) for c in CASES)
        assert any(c[0] == o and not any(c[1][k] for k in KEYS if not o[k]) for c in CASES)


def test_adjusted_below_raw_is_refused():
    with pytest.raises(ValueError):
        verdict(0.2, 0.02, ALPHA)


@pytest.mark.parametrize("outcome,raw,holdout", CASES)
def test_sentence_for_every_outcome(outcome, raw, holdout):
    s = cell_sentence(make_row(outcome, raw, holdout=holdout), ALPHA)
    period = "holdout" if holdout else "in-sample"
    assert s.startswith(f"BTCUSDT / NY, {period}: 412 trades, mean net R 0.06 (95% CI −0.02 to 0.14). ")
    assert s.endswith(".")
    clauses = s.split(". ", 1)[1].rstrip(".").split("), ")
    assert len(clauses) == 3 and clauses[0][0].isupper()
    for clause, k in zip(clauses, KEYS, strict=True):
        name, low = NAMES[k], clause.lower()
        assert low.startswith(f"beats {name} (" if outcome[k] else f"does not beat {name} ("), clause
        assert ("would pass without the correction" in clause) is (raw[k] and not outcome[k])
        assert "adjusted p = " in clause


def test_sentence_matches_the_spec_example():
    row = make_row({"a": False, "b": False, "bh": False}, False, holdout=False)
    row.update(p_a_adj=0.31, p_b_adj=0.44, p_bh_adj=0.71, p_a=0.2, p_b=0.3, p_bh=0.5)
    assert cell_sentence(row, ALPHA) == (
        "BTCUSDT / NY, in-sample: 412 trades, mean net R 0.06 (95% CI −0.02 to 0.14). Does not beat random timing "
        "(adjusted p = 0.31), does not beat random days (adjusted p = 0.44), does not beat buy-and-hold (Sharpe "
        "difference −0.30, adjusted p = 0.71).")


def test_sentence_without_holm_without_baselines_and_with_a_negative_sharpe_difference():
    row = make_row({"a": False, "b": False, "bh": False}, True, holdout=False)
    row.update(p_a_adj=None, p_b=None, p_b_adj=None)
    s = cell_sentence(row, ALPHA).lower()
    assert s.split(". ", 1)[1].startswith("not yet corrected for multiplicity against random timing (raw p = 0.03)")
    assert "not tested against random days (no p-value yet)" in s
    row.update(n_trades_r=0, p_a=None)
    assert "Not tested against random timing (no trades)" in cell_sentence(row, ALPHA)
    row = make_row({"a": False, "b": False, "bh": False}, False, holdout=False)
    row.update(p_bh=0.01, p_bh_adj=0.02, sharpe_diff=-0.1)
    assert "does not beat buy-and-hold (Sharpe difference −0.10 is not positive, adjusted p = 0.02)" in \
        cell_sentence(row, ALPHA)


def test_verdict_rule():
    assert verdict(0.01, 0.04, ALPHA).passes and verdict(0.01, 0.04, ALPHA).raw_passes
    assert verdict(0.01, 0.06, ALPHA) == verdict(0.01, 0.06, ALPHA).__class__(False, True)
    assert verdict(0.01, 0.02, ALPHA, sharpe_diff=-0.1, needs_positive=True).passes is False
    assert verdict(0.01, 0.02, ALPHA, sharpe_diff=float("nan"), needs_positive=True).passes is False
    assert verdict(None, None, ALPHA).passes is None and verdict(0.2, None, ALPHA).raw_passes is False


def test_power_matches_a_hand_example():
    # (z_0.95 + z_0.80) · 1.2 / sqrt(100) = (1.644854 + 0.841621) · 0.12
    assert min_detectable_effect(1.2, 100, 0.05) == pytest.approx(0.298377, abs=1e-6)
    assert min_detectable_effect(1.2, 100, 0.05 / 9) > min_detectable_effect(1.2, 100, 0.05)
    assert min_detectable_effect(None, 100, 0.05) is None and min_detectable_effect(1.0, 1, 0.05) is None


def test_plain_answer_and_caveat():
    def cell(a, b, bh, **kw):
        r = make_row({"a": a, "b": b, "bh": bh}, True, holdout=False)
        r.update(avg_cost_r=0.3, share_cost_r_gt_1=0.1, mean_gross_r=0.2, **kw)
        return r

    ins = {("BTCUSDT", "ny"): cell(True, True, True), ("ETHUSDT", "ny"): cell(False, False, False)}
    out = plain_answer(ins, {}, ALPHA, 9)
    assert 3 <= len(out) <= 6
    assert "1 beats all three: BTCUSDT / NY" in out[0] and "(2 of 9 primary cells)" in out[0]
    assert out[1].startswith("The holdout has not been run yet")
    assert out[2] == ("Without the correction, 3 more in-sample comparisons (cell × benchmark) would pass at raw "
                      "p < 0.05; about 0.3 of the 6 comparisons would pass by chance alone if there were no edge.")
    assert out[-1] == ("The largest caveat is costs: averaged over the primary cells (computed at report time), a "
                       "trade costs 0.30 R against a mean gross R of 0.20 before costs, and 10.0% of trades cost more "
                       "than 1 R.")
    ins[("ETHUSDT", "ny")] = cell(True, False, False)
    assert largest_caveat(ins, ALPHA).startswith("The largest caveat is beta: ETHUSDT / NY beats a random baseline")
    none = {c: {**r, "p_a_adj": None, "p_b_adj": None, "p_bh_adj": None} for c, r in ins.items()}
    assert "the Holm correction has not been applied, so there is no verdict (run `perpbt stats holm`)" in \
        plain_answer(none, {}, ALPHA, 9)[0]
    assert "the holdout runner applies it only when" in plain_answer(none, none, ALPHA, 9)[1]
    # one cell lost its holm block (rewritten after `stats holm`): no verdict, not a silent "does not beat"
    partial = {**ins, ("SOLUSDT", "ny"): {**cell(False, False, False), "p_a": 0.001, "p_a_adj": None}}
    out = plain_answer(partial, {}, ALPHA, 9)[0]
    assert out.startswith("In-sample, the Holm correction is incomplete: 1 of 3 primary cells have a raw p-value "
                          "without an adjusted one (SOLUSDT / NY;") and "no verdict" in out
    later = {c: r for c, r in ins.items() if c != ("BTCUSDT", "ny")}
    assert "no cell beats all three" in plain_answer(ins, later, ALPHA, 9)[1]
    nothing = {c: make_row({"a": False, "b": False, "bh": False}, False, holdout=False) for c in ins}
    assert plain_answer(nothing, {}, ALPHA, 2)[0] == ("In-sample, after the Holm correction, none of the 2 primary "
                                                       "cells beats random timing, random days or buy-and-hold.")
    assert plain_answer(ins, later, ALPHA, 9)[1].startswith("On the holdout, after the Holm correction (1 of 9 primary cells): 1 beats random timing (ETHUSDT / NY); none beats random days")


@pytest.fixture(scope="module")
def worlds(tmp_path_factory):
    out = {}
    for holdout in (False, True):
        w = build_runs(tmp_path_factory.mktemp(f"summary_{holdout}"), holdout=holdout)
        res = read_results(w.runs)
        out[holdout] = (w, res, build_summary(w.runs, w.prereg, w.prereg_path, res))
    return out


SECTIONS = ["Answer", "1. Verdict", "2. Per cell", "3. Holdout power", "4. Cross-session context",
            "5. Cost sensitivity", "6. Grid overview", "7. What was pre-registered and what changed"]


def verdict_table(text):
    return next(t for t in parse_tables(text) if t[0] == VERDICT_HEADERS)


@pytest.mark.parametrize("holdout", [False, True])
def test_summary_renders_with_holdout_absent_and_present(worlds, holdout):
    w, res, text = worlds[holdout]
    assert re.findall(r"^## (.+)$", text, flags=re.M) == SECTIONS
    rows = verdict_table(text)[1:]
    assert len(rows) == (8 if holdout else 4)
    assert [r[1] for r in rows].count("holdout") == (4 if holdout else 0)
    assert ("The holdout has not been run yet." in text) is (not holdout)
    assert ("Holdout attempts in the registry: 1." in text) is holdout
    assert ("Holdout: not run." in text) is (not holdout)
    answer = text.split("## Answer\n\n", 1)[1].split("\n", 1)[0]
    assert 3 <= answer.count(". ") + 1 <= 7
    if holdout:
        power = next(t for t in parse_tables(text) if t[0][0] == "Cell" and "Holdout n" in t[0])
        hold = res[res["is_holdout"]].set_index(["pair", "session_variant"])
        for r in power[1:]:
            pair, sess = r[0].split(" / ")
            assert int(r[2]) == hold.loc[(pair, sess.lower()), "n_trades_r"]


@pytest.mark.parametrize("holdout", [False, True])
def test_holm_columns_match_results_parquet(worlds, holdout):
    w, res, text = worlds[holdout]
    tab = verdict_table(text)
    col = {h: i for i, h in enumerate(tab[0])}
    prim = res[res["is_primary"]].set_index(["pair", "session_variant", "is_holdout"])
    for r in tab[1:]:
        pair, sess = r[0].split(" / ")
        src = prim.loc[(pair, sess.lower(), r[1] == "holdout")]
        for key, head in (("p_a_adj", "p_A (Holm)"), ("p_b_adj", "p_B (Holm)"), ("p_bh_adj", "p_BH (Holm)"),
                          ("p_a", "p_A"), ("p_b", "p_B"), ("p_bh", "p_BH")):
            assert float(r[col[head]]) == pytest.approx(src[key], rel=5e-3, abs=5e-4), (r[0], head)
        assert int(r[col["n"]]) == src["n_trades_r"]
        assert float(r[col["Sharpe diff"]]) == pytest.approx(src["sharpe_diff"], abs=5e-3)
        assert (r[col["Beats A"]] == "yes") is bool(src["p_a_adj"] < 0.05)
        assert (r[col["Beats B"]] == "yes") is bool(src["p_b_adj"] < 0.05)
        assert (r[col["Beats B&H"]] == "yes") is bool(src["p_bh_adj"] < 0.05 and src["sharpe_diff"] > 0)
        raw = [s for s, k in (("A", "p_a"), ("B", "p_b")) if src[k] < 0.05]
        raw += ["B&H"] if src["p_bh"] < 0.05 and src["sharpe_diff"] > 0 else []
        assert r[col["Raw p would pass"]] == (", ".join(raw) or "none")


def test_cost_sensitivity_and_grid_sections(worlds):
    w, res, text = worlds[True]
    tabs = parse_tables(text)
    means = next(t for t in tabs if t[0][0] == "Mean net R")
    assert len(means) == 1 + 4 and len(means[0]) == 1 + 4 + 1  # 2 × 2 re-pricing cells, then the registered one
    assert {r[-1] for r in means[1:]} == {"s 0.02% / m 0.02%"}
    prim = res[res["is_primary"] & ~res["is_holdout"]].iloc[0]
    rep = {(r["slippage"], r["fee_maker"]): r for r in json.loads(prim["reprice_json"])}
    row = next(r for r in means[1:] if r[0].startswith(prim["pair"]) and r[0].endswith(
        {"utc": "UTC", "ny": "NY"}[prim["session_variant"]]))
    assert float(row[1]) == pytest.approx(rep[(0.0, 0.0)]["mean_net_r"], abs=5e-4)
    adj = next(t for t in tabs if t[0][0] == "p_A (Holm)")
    assert all(r[-1] == "none" or r[-1].startswith("s ") for r in adj[1:])
    grid = next(t for t in tabs if t[0][0] == "Cell" and "Best variant (vs primary)" in t[0])
    assert len(grid) == 1 + 4 and all(r[2] == "3" for r in grid[1:])
    n = int(res.loc[~res["is_holdout"], "p_a"].notna().sum())
    assert f"of {n} grid variants with baselines have raw p_A < 0.05; about {n * 0.05:.1f} would by chance" in text


def test_caveat_without_a_gross_edge_and_for_small_samples():
    def cell(gross, cost, std=1.2, n=100):
        r = make_row({"a": False, "b": False, "bh": False}, False, holdout=False)
        r.update(avg_cost_r=cost, share_cost_r_gt_1=0.0, mean_gross_r=gross, std_net_r=std, n_trades_r=n)
        return r

    assert largest_caveat({("BTCUSDT", "ny"): cell(-0.1, 0.2)}, ALPHA, 9).startswith(
        "The largest caveat is not costs: even before costs the mean R is −0.10")
    # gross 0.5 R, cost 0.1 R: costs take a fifth; at n = 100 and sigma 1.2 the MDE at alpha/9 decides
    mde = min_detectable_effect(1.2, 100, ALPHA / 9)
    assert largest_caveat({("BTCUSDT", "ny"): cell(0.5, 0.1)}, ALPHA, 9) == (
        f"The largest caveat is sample size: the median primary cell has 100 trades; at that size only a mean net R "
        f"above about {mde:.2f} R would pass at α/9 with 80% power; averaged over the primary cells (computed at "
        "report time), costs take 0.10 R of a mean gross 0.50 R.")


def test_summary_with_a_missing_primary_cell_has_rectangular_tables(worlds):
    w, res, _ = worlds[True]
    drop = res[res["is_primary"] & ~res["is_holdout"]]["variant_id"].iloc[0]
    text = build_summary(w.runs, w.prereg, w.prereg_path, res[res["variant_id"] != drop])
    for tab in parse_tables(text):
        assert len({len(r) for r in tab}) == 1, tab[0]
    rows = verdict_table(text)[1:]
    assert sum(r[-1] == "not run" for r in rows) == 1
    assert "in-sample: not run yet." in text
