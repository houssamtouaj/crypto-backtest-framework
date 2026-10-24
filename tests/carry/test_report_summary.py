"""Carry spec Phase 6 §6.3: runs/carry/summary.md and `perpbt carry report` on the synthetic world."""
import json
import re
import shutil

import pytest

from perpbt.carry.prereg import load_prereg, lock_path, read_lock
from perpbt.cli import main
from perpbt.report.carry.report import HOLDOUT_CAVEAT, REPORT_FILE, scan, verdict_sentence
from perpbt.report.carry.summary import SUMMARY_FILE, build_summary, report_all
from perpbt.report.markdown import parse_tables
from tests.test_report import assert_tables_match_stats

SCREEN = {"decision": "go", "p_screen": 0.0123, "hurdle_annual": 0.05, "stop_if_p_at_least": 0.1}


def report_cli(prereg, runs) -> int:
    return main(["carry", "report", "--prereg", str(prereg), "--runs-dir", str(runs)])


@pytest.fixture(scope="module")
def summary(carry_world):
    assert report_cli(carry_world.prereg_path, carry_world.runs) == 0
    return carry_world, (carry_world.root / SUMMARY_FILE).read_text(encoding="utf-8")


def frozen_copy(world, tmp_path):
    for name in ("prereg.yaml", "prereg.lock"):
        shutil.copyfile(world.env / name, tmp_path / name)
    return tmp_path / "prereg.yaml"


def test_cli_writes_reports_and_summary_byte_identically(summary, capsys):
    w, text = summary

    def snap():
        return {p.relative_to(w.root).as_posix(): p.read_bytes() for p in sorted(w.root.rglob("*"))
                if p.is_file() and (p.name in (REPORT_FILE, SUMMARY_FILE) or p.parent.name == "figures")}

    before = snap()
    assert sum(1 for k in before if k.endswith(REPORT_FILE)) == 16 and SUMMARY_FILE in before
    capsys.readouterr()
    assert report_cli(w.prereg_path, w.runs) == 0
    assert "16 reports; summary" in capsys.readouterr().out
    assert snap() == before


def test_summary_numbers_are_stats_json_values(summary):
    w, text = summary
    assert assert_tables_match_stats(text, [f.stats for f in scan(w.root)]) > 50


def test_summary_periods_links_and_caveat(summary):
    w, text = summary
    assert re.findall(r"^## (.+)$", text, flags=re.M) == ["Verdict per period", "Exploratory rows (descriptive)",
                                                          "Unmodelled risks", "Notes"]
    periods = parse_tables(text)[0]
    assert [r[0] for r in periods[1:]] == ["in-sample", "holdout", "forward, 12-month review",
                                           "forward, 36-month review"]
    assert periods[2][2] == "DONE (ok)" and periods[3][3] == "descriptive" and periods[3][4] == "n/a"
    for link in re.findall(r"\]\(([^)]+)\)", text):
        assert (w.root / link).exists(), link
    folders = scan(w.root)
    for f in folders:
        if f.is_primary and not f.is_review:
            assert verdict_sentence(f.stats, primary=True) in text
    assert HOLDOUT_CAVEAT in text
    explo = parse_tables(text)[1:]
    assert len(explo) == 2 and all(len(t) == 7 for t in explo)  # header + six rows, in-sample and holdout
    assert "No `screen.json`" in text and "**Code lock.** Carry code version" in text


def test_screen_line_when_the_screen_went_on(carry_world):
    prereg = load_prereg(carry_world.prereg_path)
    lock = read_lock(lock_path(carry_world.prereg_path))
    path = carry_world.root / "screen.json"
    path.write_text(json.dumps(SCREEN), encoding="utf-8")
    try:
        text = build_summary(carry_world.root, prereg, lock)
    finally:
        path.unlink()
    assert "p = 0.0123" in text and "**go**" in text


def test_screen_stop_leaves_the_screens_summary(carry_world, tmp_path, capsys):
    prereg = frozen_copy(carry_world, tmp_path)
    root = tmp_path / "runs" / "carry"
    root.mkdir(parents=True)
    (root / "screen.json").write_text(json.dumps({**SCREEN, "decision": "stop", "p_screen": 0.2}), encoding="utf-8")
    (root / SUMMARY_FILE).write_text("the screen's summary\n", encoding="utf-8")
    assert report_all(prereg, tmp_path / "runs") == {"reports": [], "summary": None}
    assert report_cli(prereg, tmp_path / "runs") == 0
    assert "left as it is" in capsys.readouterr().out
    assert (root / SUMMARY_FILE).read_text(encoding="utf-8") == "the screen's summary\n"


def test_nothing_run_yet(carry_world, tmp_path):
    prereg = frozen_copy(carry_world, tmp_path)
    out = report_all(prereg, tmp_path / "runs")
    text = out["summary"].read_text(encoding="utf-8")
    assert out["reports"] == []
    assert [r[2] for r in parse_tables(text)[0][1:]] == ["not run yet"] * 4
    assert "None has run yet." in text


def test_refuses_an_unfrozen_prereg(carry_world, tmp_path, capsys):
    shutil.copyfile(carry_world.prereg_path, tmp_path / "prereg.yaml")
    assert report_cli(tmp_path / "prereg.yaml", carry_world.runs) == 1
    assert "refused" in capsys.readouterr().err


def test_summary_ci_columns_are_the_excess_ci(summary):
    w, text = summary
    periods = parse_tables(text)[0]
    assert periods[0][6:9] == ["Excess over the hurdle", "95% CI (excess)", "V2 reading"]
    f = next(f for f in scan(w.root) if f.kind == "run" and f.is_primary)
    lo, hi = f.stats["v2"]["ann_excess_ci"]
    assert periods[1][7] == f"{100 * lo:.2f}% to {100 * hi:.2f}%"
    for explo in parse_tables(text)[1:]:
        assert explo[0][4:7] == ["Excess over the hurdle", "95% CI (excess)", "V2 reading"]
