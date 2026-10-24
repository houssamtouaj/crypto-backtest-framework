"""V1, V2, the verdict and the stats.json document (carry spec Phase 4 §4.1-§4.3); constructed series only."""
import json
import math

import numpy as np
import pytest

from perpbt.carry import screen
from perpbt.carry.verdict import (
    PERIODS, READINGS, SCHEMA, Seeds, evaluate, reading, stats_document, stats_json,
)
from perpbt.stats.seeds import rng_for

N_DAYS = 1279  # the in-sample return days
B = 2000


def series(annual: float, noise: float, seed: int = 0, n: int = N_DAYS) -> np.ndarray:
    """Daily returns compounding to ``annual`` a year, plus normal noise of std ``noise``."""
    return screen.daily_hurdle(annual) + np.random.default_rng(seed).normal(0.0, noise, n)


def run(ret, *, seed=1, hurdle=0.05, descriptive=(0.0, 0.03, 0.05, 0.08)):
    seeds = Seeds(seed, "h" * 64, "insample")
    return evaluate(ret, alpha=0.05, B=B, block_len=30, hurdle=hurdle, descriptive=descriptive,
                    rng_v1=lambda: seeds.rng("primary", "v1"), rng_v2=lambda: seeds.rng("primary", "v2"))


def test_ten_percent_passes_v2_four_percent_does_not():
    good = run(series(0.10, 5e-4))
    assert good["v2"]["passes"] and good["v2"]["reading"] == "above_hurdle"
    assert good["v2"]["p"] < 0.01 and good["v2"]["ann_excess"] == pytest.approx(0.05, abs=0.015)
    assert good["v1"]["passes"] and good["edge"] and good["reading"] == "edge"
    low = run(series(0.04, 5e-4))
    assert not low["v2"]["passes"] and low["v2"]["p"] > 0.5
    assert low["v1"]["passes"] and not low["edge"] and low["reading"] == "positive_below_hurdle"


def test_proven_below_and_negative():
    below = run(series(0.02, 1e-5))
    assert below["v2"]["reading"] == "below_hurdle" and below["v2"]["ann_excess_ci"][1] < 0
    assert below["reading"] == "positive_below_hurdle"
    neg = run(series(-0.03, 5e-4))
    assert not neg["v1"]["passes"] and neg["v1"]["p"] > 0.5 and neg["reading"] == "no_edge"


def test_not_proven_when_the_ci_contains_zero():
    noisy = run(series(0.05, 0.01))
    lo, hi = noisy["v2"]["ann_excess_ci"]
    assert lo < 0 < hi and noisy["v2"]["reading"] == "not_proven" and not noisy["v2"]["passes"]


@pytest.mark.parametrize("v1, v2, want", [
    (True, True, "edge"), (True, False, "positive_below_hurdle"), (False, True, "no_edge"), (False, False, "no_edge"),
])
def test_reading_for_each_combination(v1, v2, want):
    assert reading(v1, v2) == want and want in READINGS


def test_v1_and_v2_are_the_screens_functions():
    ret = series(0.08, 1e-3, seed=4)
    out = run(ret)
    assert out["v1"] == screen.v1_stats(ret, alpha=0.05, B=B, block_len=30, rng=rng_for(1, "h" * 64, "insample/primary/v1"))
    assert out["v2"] == screen.v2_stats(ret, hurdle=0.05, alpha=0.05, B=B, block_len=30,
                                        rng=rng_for(1, "h" * 64, "insample/primary/v2"))


def test_hurdle_table_shares_the_v2_draws():
    out = run(series(0.07, 2e-3, seed=2))
    assert [row["hurdle_annual"] for row in out["hurdles"]] == [0.0, 0.03, 0.05, 0.08]
    assert out["hurdles"][2] == out["v2"]  # the 5% row is the verdict's
    excess = [row["ann_excess"] for row in out["hurdles"]]
    assert excess == sorted(excess, reverse=True)
    p = [row["p"] for row in out["hurdles"]]
    assert p == sorted(p)


def test_evaluate_refuses_bad_series():
    for bad in (np.zeros(1), np.array([0.01, np.nan, 0.0]), np.zeros((2, 3))):
        with pytest.raises(ValueError):
            run(bad)


def test_seeds_purposes():
    s = Seeds(7, "abc", "holdout")
    assert s.purpose("primary", "v1") == "holdout/primary/v1"
    a, b = s.rng("primary", "v1").random(3), Seeds(7, "abc", "holdout").rng("primary", "v1").random(3)
    np.testing.assert_array_equal(a, b)
    assert not np.array_equal(a, s.rng("primary", "v2").random(3))
    assert not np.array_equal(a, Seeds(7, "abc", "insample").rng("primary", "v1").random(3))
    assert PERIODS == ("insample", "holdout", "forward_12m", "forward_36m")
    with pytest.raises(ValueError):
        Seeds(7, "abc", "in-sample")
    with pytest.raises(ValueError):
        s.purpose("primary", "")


def document(seed: int) -> dict:
    out = run(series(0.09, 1e-3, seed=5), seed=seed)
    diag = {"annual": {"ann_return": np.float64(0.09), "worst_day": {"date": "2021-05-19", "ret": -np.inf}},
            "counts": {"entries": np.int64(3)}, "grid": (np.float32(0.5), float("nan"))}
    return stats_document(prereg_hash="h" * 64, period="insample", start="2020-07-01", end="2023-12-31",
                          row="primary", verdict=out, diagnostics=diag)


def test_stats_json_same_seed_same_bytes():
    a, b = stats_json(document(1)), stats_json(document(1))
    assert a == b and a.endswith("}\n")
    assert stats_json(document(2)) != a


def test_stats_json_schema_and_nulls():
    text = stats_json(document(1))
    doc = json.loads(text)
    assert list(doc) == ["schema", "prereg_hash", "period", "row", "n_days", "v1", "v2", "edge", "reading", "hurdles",
                         "diagnostics"]
    assert doc["schema"] == SCHEMA and doc["period"] == {"name": "insample", "start": "2020-07-01", "end": "2023-12-31"}
    assert doc["n_days"] == N_DAYS and isinstance(doc["edge"], bool)
    assert doc["diagnostics"]["annual"]["worst_day"]["ret"] is None
    assert doc["diagnostics"]["grid"] == [0.5, None] and doc["diagnostics"]["counts"]["entries"] == 3
    assert "NaN" not in text and "Infinity" not in text


def test_stats_document_refuses_an_unknown_period():
    with pytest.raises(ValueError):
        stats_document(prereg_hash="h", period="test", start="2020-07-01", end="2020-07-02", row="primary",
                       verdict=run(series(0.1, 1e-3)), diagnostics={})


def test_no_nan_for_a_zero_variance_series():
    # a constant series: Sharpe undefined (null), V2 still defined
    out = run(np.full(100, screen.daily_hurdle(0.06)))
    assert out["v1"]["sharpe"] is None and out["v1"]["p"] is None and not out["v1"]["passes"]
    assert out["v2"]["passes"] and math.isclose(out["v2"]["ann_excess_ci"][0], out["v2"]["ann_excess_ci"][1])
    json.loads(stats_json(stats_document(prereg_hash="h", period="insample", start="2020-07-01", end="2020-10-08",
                                         row="primary", verdict=out, diagnostics={})))
