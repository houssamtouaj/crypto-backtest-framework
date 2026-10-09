"""Spec tests 5.1 and 5.2: trade and day-block bootstraps, block-bootstrap Sharpe, paired difference, seeds."""
import math

import numpy as np
import pytest

from perpbt.stats.bootstrap import (
    block_bootstrap_sharpe,
    block_indices,
    day_block_bootstrap,
    paired_sharpe_diff,
    percentile_ci,
    sharpe,
    trade_bootstrap,
)
from perpbt.stats.seeds import rng_for


def width(ci):
    return ci[1] - ci[0]


# --- seeds ------------------------------------------------------------------------------

def test_rng_for_is_deterministic_and_purpose_sensitive():
    a = rng_for(1, "v", "trade_bootstrap").random(5)
    assert np.array_equal(a, rng_for(1, "v", "trade_bootstrap").random(5))
    for other in (rng_for(2, "v", "trade_bootstrap"), rng_for(1, "w", "trade_bootstrap"), rng_for(1, "v", "x")):
        assert not np.array_equal(a, other.random(5))


# --- 5.1 trade and day-block bootstrap ---------------------------------------------------

def test_constant_trades_give_a_degenerate_ci(rng):
    reps = trade_bootstrap(np.full(30, 0.25), 1000, rng)
    assert percentile_ci(reps) == (0.25, 0.25)
    reps = day_block_bootstrap(np.full(30, 0.25), np.repeat(np.arange(10), 3), 1000, rng)
    assert percentile_ci(reps) == pytest.approx((0.25, 0.25))


def test_trade_bootstrap_coverage_on_normal_samples():
    hits = 0
    for seed in range(500):
        g = np.random.default_rng(seed)
        lo, hi = percentile_ci(trade_bootstrap(g.normal(0.0, 1.0, 200), 2000, g))
        hits += lo <= 0.0 <= hi
    assert 0.93 <= hits / 500 <= 0.97, hits


def test_trade_bootstrap_does_not_depend_on_the_chunk_size(rng):
    x = rng.normal(size=50)
    a = trade_bootstrap(x, 3000, np.random.default_rng(5), chunk=7)
    b = trade_bootstrap(x, 3000, np.random.default_rng(5))
    assert np.array_equal(a, b)


def test_day_block_ci_is_wider_than_trade_ci_under_a_common_daily_shock(rng):
    days = np.repeat(np.arange(100), 5)
    x = rng.normal(0.0, 1.0, 100)[days] * 2.0 + rng.normal(0.0, 0.3, len(days))
    trade = percentile_ci(trade_bootstrap(x, 4000, np.random.default_rng(1)))
    block = percentile_ci(day_block_bootstrap(x, days, 4000, np.random.default_rng(1)))
    assert width(block) > 1.8 * width(trade)


def test_day_block_bootstrap_resamples_whole_days():
    # two days, one trade each day with value 0 or two trades with value 1: a replicate mean is 0, 2/3 or 1
    x = np.array([0.0, 1.0, 1.0])
    reps = day_block_bootstrap(x, np.array([5, 9, 9]), 2000, np.random.default_rng(0))
    assert set(np.round(reps, 12)) == {0.0, round(2 / 3, 12), 1.0}


# --- 5.2 block bootstrap Sharpe and paired difference ------------------------------------

def test_sharpe_definition():
    r = np.array([0.01, -0.005, 0.0, 0.02])
    assert sharpe(r) == pytest.approx(math.sqrt(365) * r.mean() / r.std(ddof=1))
    assert math.isnan(sharpe(np.zeros(10))) and math.isnan(sharpe(np.array([0.1])))


def test_block_indices_are_contiguous_blocks_of_the_series(rng):
    rows = np.concatenate(list(block_indices(25, 40, rng, 10)))
    assert rows.shape == (40, 25)
    for row in rows:
        for k in (0, 10, 20):
            seg = row[k:k + 10]
            assert np.all(np.diff(seg) == 1) and seg[0] >= 0 and seg[-1] <= 24


def test_block_sharpe_coverage_on_iid_returns():
    true = math.sqrt(365) * 0.1 / 1.0
    hits = 0
    for seed in range(500):
        g = np.random.default_rng(1000 + seed)
        lo, hi = percentile_ci(block_bootstrap_sharpe(g.normal(0.1, 1.0, 400), 1000, g))
        hits += lo <= true <= hi
    assert 0.93 <= hits / 500 <= 0.97, hits


def test_block_ci_is_wider_than_iid_ci_on_ar1_returns(rng):
    n = 1500
    e = rng.normal(0.0, 1.0, n)
    r = np.empty(n)
    r[0] = e[0]
    for t in range(1, n):
        r[t] = 0.6 * r[t - 1] + e[t]
    r = 0.05 + r
    block = percentile_ci(block_bootstrap_sharpe(r, 3000, np.random.default_rng(2), block_len=10))
    iid = percentile_ci(block_bootstrap_sharpe(r, 3000, np.random.default_rng(2), block_len=1))
    assert width(block) > 1.3 * width(iid)


def test_paired_difference_of_a_series_with_itself_is_exactly_zero(rng):
    r = rng.normal(0.001, 0.01, 300)
    out = paired_sharpe_diff(r, r, 2000, rng)
    assert out["diff"] == 0.0 and out["ci_lo"] == 0.0 and out["ci_hi"] == 0.0
    assert out["p"] == 1.0  # every replicate is <= 0


def test_paired_difference_resamples_the_same_blocks(rng):
    a = rng.normal(0.002, 0.01, 300)
    b = a - 0.001  # same noise, lower mean: every replicate difference is positive
    out = paired_sharpe_diff(a, b, 2000, rng)
    assert out["diff"] > 0 and out["ci_lo"] > 0 and out["p"] == pytest.approx(1 / 2001)


def test_paired_difference_needs_aligned_series(rng):
    with pytest.raises(ValueError):
        paired_sharpe_diff(np.zeros(5), np.zeros(6), 10, rng)


# --- Review Focus 3: degenerate inputs ---------------------------------------------------

def test_degenerate_inputs(rng):
    assert percentile_ci(trade_bootstrap(np.zeros(0), 100, rng)) == (None, None)
    assert percentile_ci(trade_bootstrap(np.array([0.7]), 100, rng)) == (0.7, 0.7)
    assert percentile_ci(day_block_bootstrap(np.zeros(0), np.zeros(0, dtype=np.int64), 100, rng)) == (None, None)
    assert percentile_ci(block_bootstrap_sharpe(np.zeros(50), 100, rng)) == (None, None)  # std 0 everywhere
    two = np.array([0.01, 0.02])  # shorter than a block: every replicate is the series itself
    assert percentile_ci(block_bootstrap_sharpe(two, 100, rng)) == pytest.approx((sharpe(two), sharpe(two)))
    out = paired_sharpe_diff(np.zeros(30), np.zeros(30), 100, rng)
    assert math.isnan(out["diff"]) and out["ci_lo"] is None and out["p"] is None
    reps = block_bootstrap_sharpe(np.array([0.0, 0.0, 0.0, 0.01]), 200, rng, block_len=10)  # T < block length
    assert reps.shape == (200,)
