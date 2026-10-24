"""run_batch equals run_path path by path (carry spec Phase 3 §3.5); synthetic data only."""
import numpy as np
import pytest

from perpbt.carry.executor import COUNTS, run_batch, run_path
from tests.carry.execframes import pairframe, spec, states
from tests.carry.test_executor import E0, messy_frames, random_states

MONEY = ("equity", "cash", "funding", "fees", "slippage", "basis_pnl")


def assert_path_equal(batch, p, path, *, tol=1e-12):
    d = path.daily
    np.testing.assert_array_equal(batch.day_ms, d["day_ms"].to_numpy())
    np.testing.assert_array_equal(batch.n_live, d["n_live"].to_numpy())
    np.testing.assert_array_equal(batch.n_on[p], d["n_on"].to_numpy())
    for c in MONEY:  # money in fractions of the starting equity
        np.testing.assert_allclose(getattr(batch, c)[p], d[c].to_numpy(), rtol=0, atol=tol * E0, err_msg=c)
    np.testing.assert_allclose(batch.ret[p], d["ret"].to_numpy(), rtol=0, atol=tol, err_msg="ret")
    assert {c: int(batch.counts[c][p]) for c in COUNTS} == path.counts


def stack(per_path: list[dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
    return {pair: np.stack([s[pair] for s in per_path]) for pair in per_path[0]}


def test_batch_equals_path_on_50_random_paths():
    frames = messy_frames()
    cfg = spec(end="2020-02-09")
    rng = np.random.default_rng(2026)
    paths = [random_states(frames, rng, p_switch=rng.uniform(0.05, 0.4)) for _ in range(50)]
    batch = run_batch(frames, stack(paths), cfg)
    assert batch.equity.shape == (50, 39)
    total = dict.fromkeys(COUNTS, 0)
    for p, st in enumerate(paths):
        path = run_path(frames, st, cfg)
        assert_path_equal(batch, p, path)
        for c in COUNTS:
            total[c] += path.counts[c]
    assert all(v > 0 for v in total.values()), total  # every rule is exercised


def test_batch_equals_path_bit_for_bit():
    # the same expressions in the same order: in practice not just close but identical
    frames = messy_frames(seed=8)
    cfg = spec(end="2020-02-09")
    rng = np.random.default_rng(4)
    paths = [random_states(frames, rng) for _ in range(10)]
    batch = run_batch(frames, stack(paths), cfg)
    for p, st in enumerate(paths):
        assert_path_equal(batch, p, run_path(frames, st, cfg), tol=0.0)


def test_one_path_batch_and_always_off():
    frames = messy_frames()
    cfg = spec(end="2020-02-09")
    off = states(frames, {})
    batch = run_batch(frames, {k: v[None] for k, v in off.items()}, cfg)
    assert (batch.equity == E0).all() and (batch.ret == 0).all()
    assert all(int(v[0]) == 0 for c, v in batch.counts.items() if c != "delists")
    assert_path_equal(batch, 0, run_path(frames, off, cfg))


def test_batch_rejects_bad_shapes():
    pf = pairframe("AUSDT", spot=[100] * 5, perp=[100] * 5)
    b = pairframe("BUSDT", spot=[100] * 5, perp=[100] * 5)
    frames = {"AUSDT": pf, "BUSDT": b}
    with pytest.raises(ValueError, match="bool"):
        run_batch(frames, {"AUSDT": np.zeros(5, bool), "BUSDT": np.zeros(5, bool)}, spec())
    with pytest.raises(ValueError, match="same number of paths"):
        run_batch(frames, {"AUSDT": np.zeros((2, 5), bool), "BUSDT": np.zeros((3, 5), bool)}, spec())
