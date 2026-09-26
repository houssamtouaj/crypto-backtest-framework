"""Synthetic builders: random_walk, candles_from_rows, perturb_after, assert_causal."""
import numpy as np
import pytest

from perpbt.data.store import Candles
from tests.synthetic import (
    STEP_15M_MS,
    T0_MS,
    assert_causal,
    candles_from_rows,
    perturb_after,
    random_walk,
)


def arrays_equal(a: Candles, b: Candles, sl: slice) -> bool:
    return all(
        np.array_equal(getattr(a, name)[sl], getattr(b, name)[sl])
        for name in ("ts", "o", "h", "l", "c", "v")
    )


# --- fixtures from conftest -------------------------------------------------


def test_conftest_fixtures(tmp_data_dir, rng):
    assert tmp_data_dir.is_dir() and tmp_data_dir.name == "data"
    assert isinstance(rng, np.random.Generator)
    assert rng.integers(0, 1_000_000) == np.random.default_rng(20260926).integers(0, 1_000_000)


# --- candles_from_rows ------------------------------------------------------


def test_candles_from_rows_four_columns():
    cd = candles_from_rows([(1, 2, 0.5, 1.5), (1.5, 1.6, 1.4, 1.45)], start_ms=T0_MS)
    assert len(cd) == 2
    assert cd.ts.tolist() == [T0_MS, T0_MS + STEP_15M_MS]
    assert cd.o.tolist() == [1.0, 1.5]
    assert cd.h.tolist() == [2.0, 1.6]
    assert cd.l.tolist() == [0.5, 1.4]
    assert cd.c.tolist() == [1.5, 1.45]
    assert cd.v.tolist() == [1.0, 1.0]
    assert (cd.pair, cd.tf) == ("TEST", "15m")


def test_candles_from_rows_five_columns_and_step():
    cd = candles_from_rows([(1, 1, 1, 1, 7), (1, 1, 1, 1, 8)], start_ms=10, step_ms=60_000, pair="P", tf="1m")
    assert cd.v.tolist() == [7.0, 8.0]
    assert cd.ts.tolist() == [10, 60_010]
    assert (cd.pair, cd.tf) == ("P", "1m")
    assert cd.step_ms() == 60_000


def test_candles_from_rows_empty():
    assert len(candles_from_rows([], start_ms=T0_MS)) == 0


@pytest.mark.parametrize("row", [(1, 0.9, 0.5, 1.0), (1, 2, 1.2, 1.5), (1, 2, 0.5)])
def test_candles_from_rows_rejects_invalid_rows(row):
    with pytest.raises(ValueError):
        candles_from_rows([(1, 2, 0.5, 1.5), row], start_ms=T0_MS)


# --- random_walk ------------------------------------------------------------


def test_random_walk_is_valid_ohlc():
    cd = random_walk(500, seed=1, start_ms=T0_MS)
    assert len(cd) == 500
    assert cd.ts.tolist() == (T0_MS + STEP_15M_MS * np.arange(500)).tolist()
    assert np.all(cd.h >= np.maximum(cd.o, cd.c))
    assert np.all(cd.l <= np.minimum(cd.o, cd.c))
    assert np.all(cd.l > 0)
    assert np.all(cd.v > 0)
    assert cd.o[0] == 100.0
    assert np.array_equal(cd.o[1:], cd.c[:-1])


def test_random_walk_is_reproducible_per_seed():
    a = random_walk(200, seed=7, start_ms=T0_MS)
    b = random_walk(200, seed=7, start_ms=T0_MS)
    c = random_walk(200, seed=8, start_ms=T0_MS)
    assert arrays_equal(a, b, slice(None))
    assert not np.array_equal(a.c, c.c)


def test_random_walk_parameters():
    cd = random_walk(50, seed=3, start_price=2_000.0, step_sigma=0.0, start_ms=5, step_ms=60_000, pair="X", tf="1m")
    assert cd.o[0] == 2_000.0
    assert np.allclose(cd.c, 2_000.0)  # zero volatility: flat closes
    assert cd.ts[1] - cd.ts[0] == 60_000
    assert (cd.pair, cd.tf) == ("X", "1m")


def test_random_walk_zero_length():
    assert len(random_walk(0, seed=1, start_ms=T0_MS)) == 0


# --- perturb_after ----------------------------------------------------------


def test_perturb_after_keeps_prefix_and_timestamps():
    base = random_walk(300, seed=11, start_ms=T0_MS)
    cut = 120
    pert = perturb_after(base, cut, seed=99)
    assert len(pert) == len(base)
    assert np.array_equal(pert.ts, base.ts)
    assert arrays_equal(base, pert, slice(0, cut + 1))
    assert (pert.pair, pert.tf) == (base.pair, base.tf)


def test_perturb_after_changes_suffix_and_keeps_continuity():
    base = random_walk(300, seed=11, start_ms=T0_MS)
    cut = 120
    pert = perturb_after(base, cut, seed=99)
    assert pert.o[cut + 1] == base.c[cut]
    assert not np.array_equal(pert.c[cut + 1 :], base.c[cut + 1 :])
    assert np.all(pert.h >= np.maximum(pert.o, pert.c))
    assert np.all(pert.l <= np.minimum(pert.o, pert.c))


def test_perturb_after_is_reproducible_per_seed():
    base = random_walk(100, seed=1, start_ms=T0_MS)
    a = perturb_after(base, 10, seed=5)
    b = perturb_after(base, 10, seed=5)
    c = perturb_after(base, 10, seed=6)
    assert arrays_equal(a, b, slice(None))
    assert not np.array_equal(a.c[11:], c.c[11:])


def test_perturb_after_last_index_is_identity():
    base = random_walk(50, seed=1, start_ms=T0_MS)
    pert = perturb_after(base, 49, seed=5)
    assert arrays_equal(base, pert, slice(None))


@pytest.mark.parametrize("cut", [-1, 50, 51])
def test_perturb_after_rejects_out_of_range_cut(cut):
    base = random_walk(50, seed=1, start_ms=T0_MS)
    with pytest.raises(IndexError):
        perturb_after(base, cut, seed=5)


# --- assert_causal ----------------------------------------------------------


def cumsum(cd: Candles) -> np.ndarray:
    return np.cumsum(cd.c)


def reversed_cumsum(cd: Candles) -> np.ndarray:
    return np.cumsum(cd.c[::-1])[::-1]


def test_assert_causal_passes_on_causal_function():
    cd = random_walk(400, seed=2, start_ms=T0_MS)
    assert_causal(cumsum, cd, cuts=[0, 10, 200, 398], seeds=[1, 2, 3])


def test_assert_causal_fails_on_non_causal_function():
    cd = random_walk(400, seed=2, start_ms=T0_MS)
    with pytest.raises(AssertionError, match="not causal"):
        assert_causal(reversed_cumsum, cd, cuts=[200], seeds=[1])


def test_assert_causal_handles_nan_warmup():
    def lagged_mean(cd: Candles) -> np.ndarray:
        out = np.full(len(cd), np.nan)
        for i in range(4, len(cd)):
            out[i] = cd.c[i - 4 : i + 1].mean()
        return out

    cd = random_walk(200, seed=4, start_ms=T0_MS)
    assert_causal(lagged_mean, cd, cuts=[0, 3, 4, 100], seeds=[1, 2])


def test_assert_causal_pair_form_respects_confirmed_at():
    k = 2

    def forward_max_confirmed(cd: Candles):
        # value at i looks k candles ahead, but is only confirmed at i + k
        n = len(cd)
        idx = np.arange(n - k)
        values = np.array([cd.h[i : i + k + 1].max() for i in idx])
        return values, idx + k

    def forward_max_unconfirmed(cd: Candles):
        values, _ = forward_max_confirmed(cd)
        return values, np.arange(len(values))

    cd = random_walk(300, seed=5, start_ms=T0_MS)
    assert_causal(forward_max_confirmed, cd, cuts=[0, 1, 50, 150], seeds=[1, 2])
    # Several seeds: a single perturbation can leave a 3-candle max unchanged by chance.
    with pytest.raises(AssertionError, match="not causal"):
        assert_causal(forward_max_unconfirmed, cd, cuts=[150], seeds=[1, 2, 3, 4, 5])


def test_assert_causal_pair_form_compares_values_of_confirmed_rows():
    def leaks_last_close(cd: Candles):
        idx = np.arange(len(cd))
        return cd.c + cd.c[-1], idx  # confirmed at idx, but every value uses the last close

    cd = random_walk(300, seed=6, start_ms=T0_MS)
    with pytest.raises(AssertionError, match="not causal"):
        assert_causal(leaks_last_close, cd, cuts=[100], seeds=[1])


def test_assert_causal_pair_form_detects_changed_row_set():
    def rows_depend_on_future(cd: Candles):
        # the number of "confirmed" rows depends on the whole series: non-causal
        count = int(np.sum(cd.c > cd.c.mean()))
        return cd.c[:count], np.arange(count)

    cd = random_walk(300, seed=6, start_ms=T0_MS)
    # Several seeds: one perturbation could leave the count unchanged by chance.
    with pytest.raises(AssertionError, match="not causal"):
        assert_causal(rows_depend_on_future, cd, cuts=[100], seeds=[1, 2, 3, 4, 5, 6])


def test_assert_causal_rejects_bad_return_shape():
    cd = random_walk(20, seed=6, start_ms=T0_MS)
    with pytest.raises(TypeError):
        assert_causal(lambda c: (c.c, c.c, c.c), cd, cuts=[5], seeds=[1])
