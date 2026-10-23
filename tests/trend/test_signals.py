"""The 12 ensemble members, exposure, vol forecast and target weights (trend spec Phase 1 §1.2-§1.6)."""
import numpy as np
import pytest

from perpbt.indicators.daily import daily_bars
from perpbt.trend.signals import donchian, donchian_states, ma_filter, return_sign
from tests.synthetic import T0_MS, random_walk

nan = np.nan


def arr(*xs):
    return np.asarray(xs, dtype=np.float64)


def same(a, b):
    np.testing.assert_array_equal(np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64))


def test_return_sign():
    c = arr(1, 2, 3, 3, 2)
    same(return_sign(c, 2), [nan, nan, 1, 1, 0])
    same(return_sign(c, 2, long_short=True), [nan, nan, 1, 1, -1])
    same(return_sign(arr(1, 2, 1), 2), [nan, nan, 0])
    same(return_sign(arr(1, 2, 1), 2, long_short=True), [nan, nan, 0])


def test_ma_filter():
    c = arr(1, 3, 2, 2)
    same(ma_filter(c, 2), [nan, 1, 0, 0])
    same(ma_filter(c, 2, long_short=True), [nan, 1, -1, 0])


def test_donchian_state_machine():
    c = arr(10, 10, 10, 10, 11, 12, 11.5, 10.5, 9, 9, 10.6)
    same(donchian(c, c, c, 4), [nan] * 4 + [1, 1, 1, 0, 0, 0, 0])
    same(donchian(c, c, c, 4, long_short=True), [nan] * 4 + [1, 1, 1, 0, -1, -1, 0])


def test_donchian_tie_does_not_enter():
    c = arr(10, 10, 10, 10, 10)
    same(donchian(c, c, c, 4), [nan] * 4 + [0])
    same(donchian(c, c, c, 4, long_short=True), [nan] * 4 + [0])


def test_donchian_uses_highs_and_lows():
    c = arr(10, 10, 10, 10, 10.5)
    h = arr(11, 10, 10, 10, 10.5)
    same(donchian(c, h, c, 4), [nan] * 4 + [0])
    # day 6's close 11.8 is above the prior two closes (11, 12) but below the prior two lows only with l3

    c2 = arr(10, 10, 10, 10, 11, 12, 11.8)
    l2 = arr(10, 10, 10, 10, 11, 11.9, 11.8)
    same(donchian(c2, c2, l2, 4), [nan] * 4 + [1, 1, 1])
    l3 = arr(10, 10, 10, 10, 11.85, 11.9, 11.8)
    same(donchian(c2, c2, l3, 4), [nan] * 4 + [1, 1, 0])


@pytest.mark.parametrize("seed", range(20))
def test_donchian_never_both_states(seed):
    bars = daily_bars(random_walk(400 * 96, seed=seed, start_ms=T0_MS, step_sigma=0.004))
    for n in (20, 40, 80, 160):
        long, short = donchian_states(bars.c, bars.h, bars.l, n)
        assert not (long & short).any()
        assert not long[:n].any() and not short[:n].any()
        ls = donchian(bars.c, bars.h, bars.l, n, long_short=True)
        assert set(np.unique(ls[n:])) <= {-1.0, 0.0, 1.0}
        assert np.isnan(ls[:n]).all()
