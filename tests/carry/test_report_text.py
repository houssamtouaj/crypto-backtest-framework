"""Carry spec Phase 6 §6.2: the headline sentence and table for every reading, on fabricated stats.json blocks
(the synthetic world only produces "edge")."""
import pytest

from perpbt.report.carry.report import _headline, verdict_sentence
from perpbt.report.markdown import numbers_in, parse_tables


def stats(v1_passes: bool, v2_reading: str, reading: str, *, period: str = "insample", row: str = "primary") -> dict:
    lo, hi = {"above_hurdle": (0.011, 0.052), "not_proven": (-0.013, 0.041), "below_hurdle": (-0.061, -0.004)}[
        v2_reading]
    return {
        "schema": "carry-stats/1", "prereg_hash": "h", "period": {"name": period, "start": "a", "end": "b"},
        "row": row, "n_days": 1279,
        "v1": {"sharpe": 1.234 if v1_passes else -0.21, "sharpe_ci": [0.41, 2.05] if v1_passes else [-1.1, 0.7],
               "p": 0.0123 if v1_passes else 0.371, "passes": v1_passes},
        "v2": {"hurdle_annual": 0.05, "ann_excess": (lo + hi) / 2, "ann_excess_ci": [lo, hi],
               "p": 0.0217 if v2_reading == "above_hurdle" else 0.284 if v2_reading == "not_proven" else 0.973,
               "passes": v2_reading == "above_hurdle", "reading": v2_reading, "ann_return": 0.05 + (lo + hi) / 2,
               "ann_return_ci": [0.0499 + lo, 0.0499 + hi]},
        "edge": v1_passes and v2_reading == "above_hurdle", "reading": reading,
    }


CASES = [
    (True, "above_hurdle", "edge", "**Verdict (in-sample): edge.**", "proven above the hurdle"),
    (True, "not_proven", "positive_below_hurdle", "**Verdict (in-sample): positive below the hurdle.**",
     "not proven: the CI contains 0"),
    (True, "below_hurdle", "positive_below_hurdle", "**Verdict (in-sample): positive below the hurdle.**",
     "proven below the hurdle: the CI lies entirely below 0"),
    (False, "not_proven", "no_edge", "**Verdict (in-sample): no edge.**", "not proven: the CI contains 0"),
    (False, "above_hurdle", "no_edge", "**Verdict (in-sample): no edge.**", "proven above the hurdle"),
]


@pytest.mark.parametrize("v1, v2, reading, head, v2_text", CASES)
def test_verdict_sentence_for_every_reading(v1, v2, reading, head, v2_text):
    st = stats(v1, v2, reading)
    s = verdict_sentence(st, primary=True)
    assert s.startswith(head) and s.endswith(".") and f"): {v2_text}" in s
    assert f"V1 (net Sharpe above 0) {'passes' if v1 else 'fails'}, p = {st['v1']['p']:.3g}" in s
    assert f"{'passes' if v2 == 'above_hurdle' else 'fails'}, p = {st['v2']['p']:.3g}" in s
    lo, hi = st["v2"]["ann_excess_ci"]
    assert f"excess over the hurdle {100 * st['v2']['ann_excess']:.2f}% (95% CI {100 * lo:.2f}% to {100 * hi:.2f}%)" in s
    e = verdict_sentence({**st, "row": "entry_5"}, primary=False)
    assert e.startswith("**Descriptive (in-sample; exploratory row `entry_5`, no say in the verdict): it would read")


KEYS = {
    "V1: net Sharpe (annualised)": ("v1", "sharpe"), "V1: 95% CI": ("v1", "sharpe_ci"), "V1: p (one-sided)": ("v1", "p"),
    "V2: annualised net return": ("v2", "ann_return"), "V2: annualised net return, 95% CI": ("v2", "ann_return_ci"),
    "V2: hurdle": ("v2", "hurdle_annual"), "V2: excess over the hurdle": ("v2", "ann_excess"),
    "V2: excess, 95% CI": ("v2", "ann_excess_ci"), "V2: p (one-sided)": ("v2", "p"), "Return days": (None, "n_days"),
}


class _Run:
    def __init__(self, st):
        self.stats, self.period, self.is_primary = st, st["period"]["name"], st["row"] == "primary"


@pytest.mark.parametrize("v1, v2, reading, head, v2_text", CASES)
def test_headline_table_rows_are_their_stats_keys(v1, v2, reading, head, v2_text):
    st = stats(v1, v2, reading, period="holdout")
    lines = _headline(_Run(st))
    (tab,) = parse_tables("\n".join(lines))
    rows = {r[0]: r[1] for r in tab[1:]}
    for label, (block, key) in KEYS.items():
        want = st[key] if block is None else st[block][key]
        got = numbers_in(rows[label])
        want = want if isinstance(want, list) else [want]
        assert len(got) == len(want), label
        for (v, tol), w in zip(got, want):
            assert abs(v - w) <= tol * (1 + 1e-9), (label, v, w)
    assert rows["V2 reading"].startswith(v2_text) and rows["V1 passes"] == ("yes" if v1 else "no")
    assert "not fully unseen" in "\n".join(lines)
