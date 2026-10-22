"""Wilder ATR (spec §2.1)."""
import numpy as np
import pytest

from perpbt.indicators.atr import atr, true_range, wilder_rma
from tests.synthetic import T0_MS, STEP_15M_MS, assert_causal, candles_from_rows, random_walk

# (o, h, l, c). Row 3's TR is set by |h - c_prev|, row 4's by |l - c_prev|.
HAND_ROWS = [
    (10.0, 11.0, 9.0, 10.5),    # TR 2.0 (h - l, first row)
    (10.5, 12.0, 10.0, 11.5),   # TR max(2.0, 1.5, 0.5) = 2.0
    (11.5, 11.8, 11.0, 11.2),   # TR max(0.8, 0.3, 0.5) = 0.8
    (12.5, 13.0, 12.4, 12.9),   # TR max(0.6, 1.8, 1.2) = 1.8
    (10.5, 10.6, 10.0, 10.2),   # TR max(0.6, 2.3, 2.9) = 2.9
    (10.2, 10.5, 9.5, 10.0),    # TR max(1.0, 0.3, 0.7) = 1.0
]


def test_true_range_hand_values():
    cd = candles_from_rows(HAND_ROWS, start_ms=T0_MS)
    np.testing.assert_allclose(true_range(cd.h, cd.l, cd.c), [2.0, 2.0, 0.8, 1.8, 2.9, 1.0], rtol=0, atol=1e-12)


def test_atr_hand_computed_n3():
    cd = candles_from_rows(HAND_ROWS, start_ms=T0_MS)
    out = atr(cd, n=3)
    # ATR_2 = mean(2, 2, 0.8) = 8/5; ATR_3 = (2*8/5 + 1.8)/3 = 5/3;
    # ATR_4 = (2*5/3 + 2.9)/3 = 187/90; ATR_5 = (2*187/90 + 1)/3 = 232/135
    expected = [np.nan, np.nan, 8 / 5, 5 / 3, 187 / 90, 232 / 135]
    np.testing.assert_allclose(out, expected, rtol=0, atol=1e-12)


def _reference_atr(rows, n):
    """Plain-Python Wilder ATR, written from the spec formula without numpy."""
    trs = []
    for i, (_, h, l, _c) in enumerate(rows):
        if i == 0:
            trs.append(h - l)
        else:
            pc = rows[i - 1][3]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    out = [float("nan")] * len(rows)
    if len(rows) >= n:
        prev = sum(trs[:n]) / n
        out[n - 1] = prev
        for i in range(n, len(rows)):
            prev = (prev * (n - 1) + trs[i]) / n
            out[i] = prev
    return out


def test_atr_matches_a_20_candle_example():
    rng = np.random.default_rng(7)
    rows, price = [], 100.0
    for _ in range(20):
        o = price
        c = o * (1 + rng.normal(0, 0.01))
        h = max(o, c) * (1 + abs(rng.normal(0, 0.005)))
        l = min(o, c) * (1 - abs(rng.normal(0, 0.005)))
        rows.append((o, h, l, c))
        price = c * (1 + rng.normal(0, 0.003))  # gap between candles so |h - c_prev| matters
    cd = candles_from_rows(rows, start_ms=T0_MS)
    for n in (1, 5, 14, 20):
        np.testing.assert_allclose(atr(cd, n=n), _reference_atr(rows, n), rtol=0, atol=1e-12)


@pytest.mark.parametrize("n", [1, 5, 14])
def test_first_n_minus_1_are_nan_and_rest_finite(n):
    cd = random_walk(200, seed=3, start_ms=T0_MS)
    out = atr(cd, n=n)
    assert np.isnan(out[: n - 1]).all()
    assert np.isfinite(out[n - 1 :]).all()


def test_series_shorter_than_n_is_all_nan():
    cd = random_walk(10, seed=3, start_ms=T0_MS)
    out = atr(cd, n=14)
    assert out.shape == (10,) and np.isnan(out).all()
    assert atr(random_walk(0, seed=3, start_ms=T0_MS), n=14).shape == (0,)


def test_timestamp_gaps_are_ignored():
    cd = random_walk(100, seed=4, start_ms=T0_MS)
    ts = cd.ts.copy()
    ts[50:] += 7 * STEP_15M_MS  # an outage between rows 49 and 50
    gapped = type(cd)(cd.pair, cd.tf, ts, cd.o, cd.h, cd.l, cd.c, cd.v)
    np.testing.assert_array_equal(atr(gapped, n=14), atr(cd, n=14))


@pytest.mark.parametrize("bad", [0, -1])
def test_n_must_be_positive(bad):
    with pytest.raises(ValueError):
        atr(random_walk(20, seed=1, start_ms=T0_MS), n=bad)


def test_n_must_be_an_integer():
    with pytest.raises(TypeError):
        atr(random_walk(20, seed=1, start_ms=T0_MS), n=14.0)


def test_wilder_rma_start_offset():
    x = np.array([99.0, 1.0, 2.0, 3.0, 4.0])
    out = wilder_rma(x, 2, start=1)
    np.testing.assert_allclose(out, [np.nan, np.nan, 1.5, (1.5 + 3.0) / 2, (2.25 + 4.0) / 2], atol=1e-12)
    assert np.isnan(wilder_rma(x, 5, start=1)).all()


def test_atr_is_causal():
    for seed in range(20):
        cd = random_walk(600, seed=100 + seed, start_ms=T0_MS)
        assert_causal(lambda c: atr(c, 14), cd, cuts=[5, 13, 400], seeds=[seed], truncate=True)


def test_docstring_states_lag():
    assert "Lag 0" in atr.__doc__


def test_n_must_not_be_a_bool():
    with pytest.raises(TypeError):
        atr(random_walk(20, seed=1, start_ms=T0_MS), n=True)


def test_wilder_rma_validates_start():
    x = np.arange(5.0)
    with pytest.raises(ValueError, match="start"):
        wilder_rma(x, 2, start=-1)
    with pytest.raises(TypeError, match="start"):
        wilder_rma(x, 2, start=1.0)
