"""Swing highs with their confirmation index (spec §2.2)."""
import numpy as np
import pytest

from perpbt.indicators.swings import Swings, swing_highs
from tests.synthetic import T0_MS, assert_causal, candles_from_rows, random_walk


def from_highs(highs):
    """Candles whose highs are ``highs`` (open = close = high - 0.5, low = high - 1)."""
    return candles_from_rows([(h - 0.5, h, h - 1.0, h - 0.5) for h in highs], start_ms=T0_MS)


HIGHS = [1, 3, 2, 5, 4, 4, 6, 2, 3, 1]


@pytest.mark.parametrize(
    "k, idx",
    [
        (1, [1, 3, 6, 8]),  # the 4s at s=4 and s=5 tie, so neither is a swing
        (2, [3, 6]),
        (3, [6]),           # 5 at s=3 fails: high[6] = 6 lies within 3 to its right
    ],
)
def test_known_swings(k, idx):
    sw = swing_highs(from_highs(HIGHS), k)
    assert sw.idx.tolist() == idx
    assert sw.level.tolist() == [float(HIGHS[s]) for s in idx]
    assert sw.confirmed_at.tolist() == [s + k for s in idx]
    assert sw.idx.dtype == np.int64 and sw.confirmed_at.dtype == np.int64 and sw.level.dtype == np.float64


@pytest.mark.parametrize(
    "highs, k",
    [
        ([1, 5, 5, 1], 1),               # plateau of two
        ([1, 2, 5, 3, 5, 2, 1], 2),      # equal highs two apart
        ([1, 2, 3, 7, 4, 5, 7, 1, 1, 1], 3),  # two 7s three apart: each sees the other
    ],
)
def test_equal_highs_are_excluded(highs, k):
    assert len(swing_highs(from_highs(highs), k)) == 0


@pytest.mark.parametrize("k", [1, 2, 3])
def test_confirmed_at_is_idx_plus_k_and_sorted(k):
    sw = swing_highs(random_walk(2000, seed=12, start_ms=T0_MS), k)
    assert len(sw) > 50
    np.testing.assert_array_equal(sw.confirmed_at, sw.idx + k)
    assert np.all(np.diff(sw.idx) > 0)


@pytest.mark.parametrize("k", [1, 2, 3])
def test_candles_within_k_of_either_end_are_never_swings(k):
    rising = from_highs(list(range(1, 12)))           # the last candle is the highest
    assert len(swing_highs(rising, k)) == 0
    falling = from_highs(list(range(12, 1, -1)))       # the first candle is the highest
    assert len(swing_highs(falling, k)) == 0
    cd = random_walk(1500, seed=13, start_ms=T0_MS)
    sw = swing_highs(cd, k)
    assert sw.idx.min() >= k and sw.idx.max() <= len(cd) - 1 - k


def test_every_swing_satisfies_the_definition_and_none_is_missed():
    cd = random_walk(800, seed=14, start_ms=T0_MS)
    h = cd.h
    for k in (1, 2, 3):
        expected = [
            s for s in range(k, len(h) - k)
            if all(h[s] > h[s - j] and h[s] > h[s + j] for j in range(1, k + 1))
        ]
        assert swing_highs(cd, k).idx.tolist() == expected


def test_short_and_flat_series_have_no_swings():
    for n in (0, 1, 2, 4):
        sw = swing_highs(random_walk(n, seed=1, start_ms=T0_MS), 2)
        assert len(sw) == 0 and sw.idx.dtype == np.int64 and sw.level.dtype == np.float64
    assert len(swing_highs(from_highs([5.0] * 20), 1)) == 0


@pytest.mark.parametrize("bad", [0, -2])
def test_k_must_be_positive(bad):
    with pytest.raises(ValueError):
        swing_highs(random_walk(20, seed=1, start_ms=T0_MS), bad)


def test_unchecked_constructor_keeps_the_arrays():
    idx = np.array([2, 5], dtype=np.int64)
    level = np.array([1.0, 2.0])
    conf = idx + 2
    sw = Swings._unchecked(idx, level, conf)
    assert sw.idx is idx and sw.level is level and sw.confirmed_at is conf and len(sw) == 2


def test_swings_validates_order_and_shapes():
    with pytest.raises(ValueError):
        Swings(np.array([3, 1]), np.array([1.0, 2.0]), np.array([5, 3]))
    with pytest.raises(ValueError):
        Swings(np.array([1]), np.array([1.0, 2.0]), np.array([3]))


@pytest.mark.parametrize("k", [1, 2, 3])
def test_swings_are_causal(k):
    def fn(cd):
        sw = swing_highs(cd, k)
        return sw.level, sw.confirmed_at

    for seed in range(20):
        cd = random_walk(500, seed=200 + seed, start_ms=T0_MS)
        assert_causal(fn, cd, cuts=[k, 150, 498], seeds=[seed], truncate=True)


def test_docstring_states_lag():
    assert "Lag k" in swing_highs.__doc__


def test_swings_rejects_float_indices():
    with pytest.raises(TypeError, match="idx"):
        Swings(np.array([1.5]), np.array([2.0]), np.array([3]))
    with pytest.raises(TypeError, match="confirmed_at"):
        Swings(np.array([1]), np.array([2.0]), np.array([3.0]))
    assert len(Swings(np.array([]), np.array([]), np.array([]))) == 0  # empty float arrays are fine


def test_k_must_be_an_integer():
    cd = random_walk(20, seed=1, start_ms=T0_MS)
    for bad in (2.0, True):
        with pytest.raises(TypeError):
            swing_highs(cd, bad)
