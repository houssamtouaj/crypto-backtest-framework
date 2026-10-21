"""Spec §7.2: the per-variant report — every section present, every table number taken from stats.json."""
import json
import re

import numpy as np
import pandas as pd
import pytest

from perpbt.data.store import CandleStore
from perpbt.experiments.registry import read_results
from perpbt.report.markdown import numbers_in, parse_tables
from perpbt.report.report import (
    SECTIONS,
    REPORT_FILE,
    ReportContext,
    VariantRun,
    gap_overlaps,
    param_label,
    render_report,
    write_reports,
)
from tests.report_harness import build_runs


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    w = build_runs(tmp_path_factory.mktemp("report"))
    data_cfg = w.prereg.data_config(w.data_dir)
    gap = {"start_ms": 0, "end_ms": 2**62, "missing": 1}  # one gap over every trade of BTCUSDT 15m
    (CandleStore(data_cfg).dir("BTCUSDT", "15m") / "manifest.json").write_text(json.dumps({"gaps": [gap]}))
    results = read_results(w.runs)
    paths = write_reports(w.runs, results, w.prereg, data_cfg, grid=True)
    return w, results, paths


def stats_numbers(obj) -> np.ndarray:
    out = []

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, (int, float)) and not isinstance(x, bool):
            out.append(float(x))

    walk(obj)
    return np.array(out)


def assert_tables_match_stats(text: str, stats: dict) -> int:
    """Every number in a value cell of every table is a stats.json value (to the printed precision)."""
    values = stats_numbers(stats)
    checked = 0
    for tab in parse_tables(text):
        for row in tab[1:]:
            for cell in row[1:]:
                for v, tol in numbers_in(cell):
                    assert np.any(np.abs(values - v) <= tol * (1 + 1e-9) + 1e-12), (cell, row)
                    checked += 1
    return checked


def headings(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^## (.+)$", text, flags=re.M)]


def test_reports_are_written_for_primaries_holdout_and_grid(world):
    w, results, paths = world
    assert len(paths) == len(results) == 12 + 4
    assert all(p.name == REPORT_FILE and p.exists() for p in paths)
    first = [p.parent.name for p in paths[:8]]
    assert set(first) == set(results.loc[results["is_primary"], "variant_id"])


def test_every_section_in_order_and_every_figure_exists(world):
    w, results, paths = world
    for p in paths:
        text = p.read_text(encoding="utf-8")
        assert headings(text) == list(SECTIONS), p
        assert text.startswith("# ")
        for fig in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text):
            assert (p.parent / fig).stat().st_size > 1_000, fig


def test_no_table_number_differs_from_stats_json(world):
    w, results, paths = world
    for p in paths:
        run = VariantRun.load(p.parent)
        assert assert_tables_match_stats(p.read_text(encoding="utf-8"), run.stats) > 100


def test_key_numbers_are_in_their_rows(world):
    w, results, paths = world
    run = VariantRun.load(paths[0].parent)
    tables = {t[0][0]: t for t in parse_tables(paths[0].read_text(encoding="utf-8"))}
    assert len(tables) == len(parse_tables(paths[0].read_text(encoding="utf-8")))  # first headers are distinct
    head = dict(r[:2] for r in tables["Headline"][1:])
    h = run.stats["headline"]
    assert int(head["trades (R subset)"]) == h["n"] == run.stats["summary"]["n_trades_r"]
    assert float(head["mean net R"]) == pytest.approx(h["mean_net_r"], abs=5e-4)
    lo, hi = (float(x) for x in head["mean net R, 95% CI (trade bootstrap)"].split(" to "))
    assert (lo, hi) == pytest.approx((h["mean_net_r_ci"]["lo"], h["mean_net_r_ci"]["hi"]), abs=5e-4)
    base = {r[0][:2]: r for r in tables["Baseline"][1:]}
    b, holm = run.stats["baselines"], run.stats["holm"]
    assert float(base["A:"][4]) == pytest.approx(b["p_a"], rel=5e-3)
    assert float(base["B:"][6]) == pytest.approx(holm["p_b_adj"], rel=5e-3)
    funnel = dict(tables["Stage"][1:])
    assert int(funnel["impulses"]) == run.stats["skips"]["impulses"]
    assert int(funnel["orders filled"]) == run.stats["summary"]["n_filled"]


def test_primary_holds_heatmaps_and_links_grid_reports(world):
    w, results, paths = world
    prim = results[results["is_primary"] & ~results["is_holdout"]].iloc[0]
    text = (w.runs / prim["variant_id"] / REPORT_FILE).read_text(encoding="utf-8")
    assert "### Heatmap 1: r_target" in text and "2 of 2 cells have run" in text
    assert (w.runs / prim["variant_id"] / "figures" / "heatmap_1.png").exists()
    links = re.findall(r"\(\.\./([0-9a-f]{64})/report\.md\)", text)
    cell = results[(results["pair"] == prim["pair"]) & (results["session_variant"] == prim["session_variant"])
                   & ~results["is_holdout"] & ~results["is_primary"]]
    assert sorted(links) == sorted(cell["variant_id"])
    assert "continue into the holdout run" in text
    grid_text = (w.runs / links[0] / REPORT_FILE).read_text(encoding="utf-8")
    assert f"(../{prim['variant_id']}/report.md)" in grid_text
    hold = results[results["is_holdout"]].iloc[0]
    assert "The grid is in-sample only" in (w.runs / hold["variant_id"] / REPORT_FILE).read_text(encoding="utf-8")


def test_heatmap_cells_match_the_axis_labels_of_the_registered_grid(world):
    """Every grid row of a heatmap lands on a labelled cell (a label mismatch would draw it as 'not run')."""
    from perpbt.experiments.grid import enumerate_grid
    from perpbt.experiments.prereg import load_prereg
    from perpbt.report.report import _cells_rows, heatmaps_for
    from tests.test_cli import ROOT

    w, results, paths = world
    for h in heatmaps_for(_cells_rows(results[~results["is_holdout"]]).query("pair == 'BTCUSDT'"), w.prereg.grid):
        assert set(h.cells["x"]) <= set(h.x_values) and set(h.cells["y"]) <= set(h.y_values)
    # the registered grid: build results-like rows from its 36 BTCUSDT/NY configs
    prereg = load_prereg(ROOT / "configs" / "prereg.yaml")
    cell = [v for v in enumerate_grid(prereg) if v.cell == ("BTCUSDT", "ny")]
    rows = pd.DataFrame({"params_json": [v.cfg.canonical_json() for v in cell],
                         "members": [json.dumps(list(v.members)) for v in cell],
                         "is_primary": [v.is_primary for v in cell], "mean_net_r": 0.0, "sharpe_ann": 0.0,
                         "n_trades_r": 1})
    maps = heatmaps_for(_cells_rows(rows), prereg.grid)
    assert [len(h.cells) for h in maps] == [h.expected for h in maps] == [8, 9, 16]
    for h in maps:
        assert set(zip(h.cells["x"], h.cells["y"])) == {(x, y) for x in h.x_values for y in h.y_values}
        assert h.cells["is_primary"].sum() == 1
    assert maps[2].x_values == ["0 ATR", "0.1 ATR", "0.25 ATR", "0.5 ATR"]
    assert maps[2].y_values == ["none", "session_end", "max_hold 24h", "max_hold 72h"]


def test_shared_fraction_and_gap_notes(world):
    w, results, paths = world
    for p in paths:
        run = VariantRun.load(p.parent)
        text = p.read_text(encoding="utf-8")
        assert "also traded by the other session variants" in text
        if run.cfg.pair == "BTCUSDT":
            n = len(run.table("trades"))
            assert f"Data gaps overlapping a trade (computed at report time from the candle manifests): 15m: {n} "                    f"trades over {1 if n else 0} gaps." in text
        else:
            assert "Data gaps: not checked" in text


def test_report_without_baselines_holm_or_dsr(tmp_path):
    w = build_runs(tmp_path, holdout=False, grid=False)
    res = read_results(w.runs)
    vid = res["variant_id"].iloc[0]
    run = VariantRun.load(w.runs / vid)
    run.stats["baselines"] = {k: None for k in run.stats["baselines"]}
    run.stats.pop("holm")
    run._tables["baseline_a_runs"] = run._tables["baseline_b"] = None
    text = render_report(run, ReportContext())
    assert headings(text) == list(SECTIONS)
    assert "Baselines have not run yet" in text and "The grid has not run yet" in text
    assert "Holm adjustment not applied yet" in text and "Not computed yet" in text
    assert "Data gaps: not checked" in text
    assert_tables_match_stats(text, run.stats)


def test_gap_overlaps():
    trades = pd.DataFrame({"entry_ms": [0, 100, 300], "exit_ms": [50, 200, 400]})
    gaps = {"15m": [{"start_ms": 60, "end_ms": 120, "missing": 1}, {"start_ms": 500, "end_ms": 600, "missing": 1}],
            "1m": []}
    assert gap_overlaps(trades, gaps) == {"15m": {"gaps": 1, "trades": 1}, "1m": {"gaps": 0, "trades": 0}}
    assert gap_overlaps(trades, None) is None
    assert gap_overlaps(trades.iloc[:0], {"15m": gaps["15m"]}) == {"15m": {"gaps": 0, "trades": 0}}


@pytest.mark.parametrize("axis,value,label", [
    ("r_target", 1.5, "1.5"), ("r_target", 2.0, "2"), ("entry_level", "mid", "mid"),
    ("stop_buffer", {"kind": "atr", "value": 0.25}, "0.25 ATR"), ("stop_buffer", {"kind": "pct", "value": 0.001}, "0.1%"),
    ("hold_rule", {"kind": "max_hold", "hours": 24.0}, "max_hold 24h"), ("hold_rule", {"kind": "none", "hours": None},
                                                                          "none"),
    ("trend_filter", True, "on"),
])
def test_param_label(axis, value, label):
    assert param_label(axis, value) == label


@pytest.mark.slow
def test_report_from_the_smoke_run(real_cfg, tmp_path, capsys):
    """Spec 7.2: the report renders from the Phase 4 smoke run (BTCUSDT 2024, UTC) and its tables match stats.json."""
    import time

    from perpbt.config import ExecConfig, StatsConfig, StrategyParams, VariantConfig
    from perpbt.experiments.runner import run_variant
    from perpbt.report.report import read_gaps
    from tests.strategy_harness import UTC

    cfg = VariantConfig(pair="BTCUSDT", session=UTC, params=StrategyParams(), exec=ExecConfig(),
                        stats=StatsConfig(baseline_runs=500), period_start="2024-01-01", period_end="2024-12-31",
                        is_holdout=False)
    out = run_variant(cfg, real_cfg, runs_dir=tmp_path, labels={"is_primary": True, "members": []}, baseline_runs=500)
    run = VariantRun.load(out)
    t0 = time.perf_counter()
    text = render_report(run, ReportContext(gap_overlaps=gap_overlaps(run.table("trades"), read_gaps(real_cfg, "BTCUSDT"))))
    secs = time.perf_counter() - t0
    assert headings(text) == list(SECTIONS)
    assert assert_tables_match_stats(text, run.stats) > 200
    assert "Data gaps overlapping a trade (computed at report time from the candle manifests): 15m: 0 trades" in text
    assert len(list((out / "figures").glob("*.png"))) == 15  # every figure but the three heatmaps
    with capsys.disabled():
        print(f"\nreport BTCUSDT 2024 UTC: {len(text)} chars, rendered in {secs:.1f} s")
