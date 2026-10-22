"""Spec tests 5.5 (buy-and-hold), 5.7 (deflated Sharpe) and 5.8 (Holm)."""
import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from perpbt.stats.bootstrap import sharpe
from perpbt.stats.buyhold import bh_daily, buy_and_hold
from perpbt.stats.dsr import dsr, dsr_from_trials, expected_max_sr, psr, sr_moments
from perpbt.stats.multiplicity import bonferroni, holm
from tests.sim_harness import funding_at
from tests.synthetic import T0_MS, candles_from_rows

DAY_MS = 86_400_000
SIX_H = 6 * 3_600_000


def phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# --- 5.5 buy-and-hold -------------------------------------------------------------------------

def four_days(closes):
    """Four candles per day (6-hourly), each day ending at its close; day 0 is warmup."""
    rows = []
    prev = 100.0
    for c in closes:
        rows += [(prev, max(prev, c), min(prev, c), prev)] * 3 + [(prev, max(prev, c), min(prev, c), c)]
        prev = c
    return candles_from_rows(rows, start_ms=T0_MS, step_ms=SIX_H)


def test_three_day_hand_example_with_funding():
    cd = four_days([100.0, 110.0, 99.0, 99.0])
    fund = funding_at([T0_MS + 8 * 3_600_000,  # warmup day: not in the period
                       T0_MS + DAY_MS + 8 * 3_600_000,
                       T0_MS + 2 * DAY_MS, T0_MS + 2 * DAY_MS + 16 * 3_600_000],
                      [0.5, 0.01, 0.001, -0.002])
    bh = bh_daily(cd, fund, 4, 15)
    assert bh["date"].tolist() == [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 4)]
    np.testing.assert_allclose(bh["ret"], [0.1 - 0.01, 99 / 110 - 1 + 0.001, 0.0], atol=1e-15)
    assert bh["funding"].tolist() == pytest.approx([0.01, -0.001, 0.0])


def test_first_day_without_a_previous_candle_uses_the_first_open():
    cd = four_days([104.0, 110.0])
    bh = bh_daily(cd, funding_at([], []), 0, 7)
    assert bh["ret"].tolist() == pytest.approx([104 / 100 - 1, 110 / 104 - 1])


def series(rng, n, mu, sd):
    dates = pd.date_range("2020-01-01", periods=n, freq="D").date
    return pd.DataFrame({"date": dates, "ret": rng.normal(mu, sd, n)})


def test_scaling_equalises_vol_and_reports_scaled_return_and_drawdown(rng):
    strat = series(rng, 400, 0.0005, 0.004)
    bh = series(rng, 400, 0.001, 0.03)
    out = buy_and_hold(strat, bh, 300, np.random.default_rng(1), np.random.default_rng(2), 10)
    scaled = bh["ret"] * out["scale"]
    assert scaled.std(ddof=1) == pytest.approx(strat["ret"].std(ddof=1), rel=1e-12)
    assert out["total_return_scaled"] == pytest.approx(float(np.prod(1 + scaled) - 1))
    eq = np.cumprod(1 + scaled.to_numpy())
    peak = np.maximum.accumulate(np.concatenate(([1.0], eq)))[1:]
    assert out["max_dd_scaled"] == pytest.approx(float(np.max(1 - eq / peak)))
    assert out["sharpe"] == pytest.approx(sharpe(bh["ret"]))
    assert out["n_days"] == 400


def test_in_sample_sigmas_are_used_when_given(rng):
    strat, bh = series(rng, 100, 0.0, 0.01), series(rng, 100, 0.0, 0.02)
    out = buy_and_hold(strat, bh, 100, np.random.default_rng(1), np.random.default_rng(2), 10,
                       sigma_strategy=0.5, sigma_bh=2.0)
    assert out["scale"] == 0.25 and out["sigma_strategy"] == 0.5


def test_paired_difference_sign_matches_a_hand_case(rng):
    bh = series(rng, 500, 0.0002, 0.02)
    better = bh.assign(ret=bh["ret"] + 0.002)
    worse = bh.assign(ret=bh["ret"] - 0.002)
    up = buy_and_hold(better, bh, 500, np.random.default_rng(1), np.random.default_rng(2), 10)
    down = buy_and_hold(worse, bh, 500, np.random.default_rng(1), np.random.default_rng(2), 10)
    assert up["sharpe_diff"] > 0 and up["p_bh"] < 0.05 and up["sharpe_diff_ci_lo"] > 0
    assert down["sharpe_diff"] < 0 and down["p_bh"] > 0.95


def test_days_are_aligned_by_date(rng):
    strat = series(rng, 50, 0.0, 0.01)
    bh = series(rng, 52, 0.0, 0.01).iloc[2:]  # two extra early days, two missing late days
    out = buy_and_hold(strat, bh, 50, np.random.default_rng(1), np.random.default_rng(2), 10)
    assert out["n_days"] == 48


# --- 5.7 deflated Sharpe ----------------------------------------------------------------------

def test_psr_matches_the_published_formula():
    sr, T, skew, kurt = 0.1, 101, -1.0, 5.0
    z = sr * math.sqrt(T - 1) / math.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr**2)
    assert psr(sr, 0.0, T, skew, kurt) == pytest.approx(phi(z), abs=1e-12)
    assert psr(sr, 0.0, T, 0.0, 3.0) > psr(sr, 0.0, T, -1.0, 5.0)  # negative skew, fat tails lower it


def test_worked_example_of_bailey_and_lopez_de_prado():
    sr = 2.5 / math.sqrt(250)
    sr_star = expected_max_sr(100, 0.5 / 250)
    assert sr_star == pytest.approx(0.1132, abs=2e-4)
    assert dsr(sr, 1250, -3.0, 10.0, 100, 0.5 / 250) == pytest.approx(0.90, abs=0.005)


def test_dsr_decreases_in_n_and_equals_psr_against_zero_at_n_one():
    sr, T, sk, ku, V = 0.08, 1000, -0.5, 6.0, 0.0004
    values = [dsr(sr, T, sk, ku, n, V) for n in (1, 2, 10, 36, 324)]
    assert all(a > b for a, b in zip(values, values[1:]))
    assert expected_max_sr(1, V) == 0.0
    assert values[0] == psr(sr, 0.0, T, sk, ku)


def test_sr_moments_and_dsr_from_trials(rng):
    r = rng.normal(0.001, 0.01, 800)
    m = sr_moments(r)
    assert m["sr"] == pytest.approx(r.mean() / r.std(ddof=1)) and m["T"] == 800
    assert m["kurt"] == pytest.approx(3.0, abs=0.5)
    trials = rng.normal(0.02, 0.03, 36)
    out = dsr_from_trials(m, trials)
    assert out["N"] == 36 and out["V"] == pytest.approx(np.var(trials, ddof=1))
    assert out["dsr"] == pytest.approx(dsr(m["sr"], 800, m["skew"], m["kurt"], 36, out["V"]))
    assert sr_moments(np.zeros(5))["sr"] is None


# --- 5.8 Holm -----------------------------------------------------------------------------------

P9 = [0.01, 0.04, 0.03, 0.005, 0.2, 0.5, 0.001, 0.02, 0.6]


def test_holm_matches_hand_values():
    want = [0.07, 0.16, 0.15, 0.04, 0.6, 1.0, 0.009, 0.12, 1.0]
    np.testing.assert_allclose(holm(P9), want, atol=1e-12)
    np.testing.assert_allclose(bonferroni(P9), np.minimum(1, 9 * np.array(P9)), atol=1e-15)


def test_holm_is_monotone_bounded_and_at_least_raw(rng):
    for _ in range(50):
        p = rng.uniform(0, 0.3, 9)
        adj = holm(p)
        order = np.argsort(p, kind="stable")
        assert np.all(np.diff(adj[order]) >= 0) and np.all(adj <= 1) and np.all(adj >= p)


def test_holm_skips_missing_values():
    adj = holm([0.01, None, 0.02])
    assert math.isnan(adj[1]) and adj[0] == pytest.approx(0.02) and adj[2] == pytest.approx(0.02)
