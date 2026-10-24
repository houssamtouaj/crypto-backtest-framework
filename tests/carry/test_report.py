"""Carry spec Phase 6 §6.2/§6.4: the per-run reports render from a synthetic run; every headline and table number
is a stats.json value; byte-identical on rerun."""
import re
from dataclasses import replace

import numpy as np
import pytest

from perpbt.carry.prereg import load_prereg
from perpbt.report.carry.report import (
    HOLDOUT_CAVEAT, REPORT_FILE, REVIEW_SECTIONS, SECTIONS, ReportRefused, scan, v2_replicates, verdict_sentence,
    write_reports,
)
from perpbt.report.markdown import parse_tables
from tests.test_report import assert_tables_match_stats, stats_numbers

PNG = b"\x89PNG\r\n\x1a\n"
DECIMAL = re.compile(r"-?\d+\.\d+(?:e[-+]?\d+)?%?|\d+e-\d+")


@pytest.fixture(scope="module")
def reports(carry_world):
    prereg = load_prereg(carry_world.prereg_path)
    folders = scan(carry_world.root)
    paths = write_reports(carry_world.root, prereg)
    return carry_world, prereg, folders, paths


def headings(text: str) -> list[str]:
    return re.findall(r"^## (.+)$", text, flags=re.M)


def section(text: str, name: str) -> str:
    m = re.search(rf"^## {re.escape(name)}\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    assert m, name
    return m.group(1)


def portfolio(folders):
    return [f for f in folders if f.is_portfolio]


def sources(folders, f):
    """The stats.json documents a report may quote: its own, its period's always_on portfolio, its row's pairs."""
    same = [g for g in folders if g.kind == f.kind and g.period == f.period]
    return [f.stats] + [g.stats for g in same if (g.row == "always_on" and g.is_portfolio)
                        or (g.row == f.row and not g.is_portfolio)]


def test_one_report_per_portfolio_folder(reports):
    w, prereg, folders, paths = reports
    assert len(folders) == 28 + 21 + 2
    assert sorted(paths) == sorted(f.path / REPORT_FILE for f in portfolio(folders))
    assert len(paths) == 7 + 7 + 2
    assert not any((f.path / REPORT_FILE).exists() for f in folders if not f.is_portfolio)


def test_sections_in_order(reports):
    w, prereg, folders, paths = reports
    for f in portfolio(folders):
        text = (f.path / REPORT_FILE).read_text(encoding="utf-8")
        assert tuple(headings(text)) == (REVIEW_SECTIONS if f.is_review else SECTIONS), f.path


def test_every_table_number_is_a_stats_json_value(reports):
    w, prereg, folders, paths = reports
    checked = 0
    for f in portfolio(folders):
        checked += assert_tables_match_stats((f.path / REPORT_FILE).read_text(encoding="utf-8"), sources(folders, f))
    assert checked > 3_000


def test_headline_is_the_verdict_from_stats_json(reports):
    w, prereg, folders, paths = reports
    for f in portfolio(folders):
        if f.is_review:
            continue
        head = section((f.path / REPORT_FILE).read_text(encoding="utf-8"), "Headline")
        sentence = verdict_sentence(f.stats, primary=f.is_primary)
        assert sentence in head
        values = stats_numbers(f.stats)
        for tok in DECIMAL.findall(head.replace("95%", "")):
            v = float(tok.rstrip("%")) / (100 if tok.endswith("%") else 1)
            assert np.any(np.isclose(values, v, rtol=0, atol=1e-4 if tok.endswith("%") else 6e-3)), (tok, f.path)
        reading = {"edge": "edge", "positive_below_hurdle": "positive below the hurdle", "no_edge": "no edge"}
        if f.is_primary:
            assert sentence.startswith(f"**Verdict (") and f": {reading[f.stats['reading']]}.**" in sentence
        else:
            assert sentence.startswith("**Descriptive (") and "no say in the verdict" in sentence
        assert ("95% CI" in sentence) and ("p = " in sentence)
        assert parse_tables(head)[0][0] == ["Headline", "Value"]


def test_holdout_caveat_next_to_the_verdict(reports):
    w, prereg, folders, paths = reports
    for f in portfolio(folders):
        text = (f.path / REPORT_FILE).read_text(encoding="utf-8")
        if f.period == "holdout":
            assert HOLDOUT_CAVEAT in section(text, "Headline")
        else:
            assert "not fully unseen" not in text
    fwd = next(f for f in folders if f.period == "forward_36m")
    assert "only fully unseen test" in section((fwd.path / REPORT_FILE).read_text(encoding="utf-8"), "Headline")


def test_twelve_month_review_has_no_p_value(reports):
    w, prereg, folders, paths = reports
    (f,) = [f for f in folders if f.is_review]
    text = (f.path / REPORT_FILE).read_text(encoding="utf-8")
    assert not re.search(r"\bp\s*[=(]|\bp-value|\| p |Sharpe, 95% CI", text)
    assert "Descriptive only" in section(text, "Review")
    metrics = parse_tables(section(text, "Review"))[0]
    assert [r[0] for r in metrics[1:]] == ["annualised net return", "funding (annualised)", "basis (annualised)",
                                           "fees + slippage (annualised)", "entries a year", "share of pair-days on"]
    assert all(r[-1] in ("yes", "no", "n/a") for r in metrics[1:])


def test_exploratory_and_primary_only_blocks(reports):
    w, prereg, folders, paths = reports
    for f in portfolio(folders):
        if f.is_review:
            continue
        text = (f.path / REPORT_FILE).read_text(encoding="utf-8")
        primary_only = "primary only" in section(text, "Filter value and random timing")
        assert primary_only == (not f.is_primary)
        assert ("primary only" in section(text, "Deflated Sharpe ratio")) == (not f.is_primary)
        assert (f.path / "figures" / "random_timing.png").exists() == f.is_primary
        assert "No cell equals the verdict's costs" in section(text, "Cost re-pricing")
        assert "exchange and counterparty risk" in section(text, "Unmodelled risks")


def test_holdout_per_pair_table_marks_the_delisted_pair(reports):
    w, prereg, folders, paths = reports
    f = next(f for f in folders if f.kind == "holdout" and f.is_primary)
    rows = {r[0]: r for r in parse_tables(section((f.path / REPORT_FILE).read_text(encoding="utf-8"),
                                                  "Per-pair rows"))[0][1:]}
    assert set(rows) == {"AAAUSDT", "BBBUSDT", "CCCUSDT"}
    assert rows["CCCUSDT"][6].startswith("not run") and rows["AAAUSDT"][1].endswith("%")


def test_linked_figures_exist(reports):
    w, prereg, folders, paths = reports
    for f in portfolio(folders):
        text = (f.path / REPORT_FILE).read_text(encoding="utf-8")
        links = re.findall(r"!\[[^]]*\]\((figures/[^)]+)\)", text)
        expected = {"equity", "decomposition", "funding", "basis"} | (set() if f.is_review else {"v2_bootstrap"})
        expected |= {"random_timing"} if f.is_primary and not f.is_review else set()
        assert {link.split("/")[1][:-4] for link in links} == expected, f.path
        for link in links:
            data = (f.path / link).read_bytes()
            assert data.startswith(PNG) and len(data) > 5_000


def test_rerun_is_byte_identical(reports):
    w, prereg, folders, paths = reports

    def snap():
        return {p.relative_to(w.root).as_posix(): p.read_bytes() for p in sorted(w.root.rglob("*"))
                if p.is_file() and (p.name == REPORT_FILE or p.parent.name == "figures")}

    before = snap()
    assert len(before) > 16 * 5
    write_reports(w.root, prereg)
    assert snap() == before


def test_v2_replicates_reproduce_the_verdict(reports):
    w, prereg, folders, paths = reports
    f = next(f for f in folders if f.kind == "run" and f.is_primary)
    reps = v2_replicates(f, prereg)
    assert reps is not None and len(reps) == prereg.stats.bootstrap_n
    other = replace(prereg, stats=replace(prereg.stats, bootstrap_n=prereg.stats.bootstrap_n + 1))
    assert v2_replicates(f, other) is None
    text = (f.path / REPORT_FILE).read_text(encoding="utf-8")
    assert "their p and CI equal `stats.json`'s" in section(text, "Notes")


def test_refuses_another_prereg(reports):
    w, prereg, folders, paths = reports
    other = replace(prereg, hurdle=replace(prereg.hurdle, annual=0.06))
    with pytest.raises(ReportRefused, match="another prereg hash"):
        write_reports(w.root, other)
