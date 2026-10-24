"""The executor on the real in-sample pairs (slow; carry spec Phase 3 §3.7).

Structure, runtime, causality, invariants and the batch/path equivalence only. No return, Sharpe, P&L or
funding total is asserted or printed (spec §3.7: no performance number in this phase).
"""
import time
from pathlib import Path

import numpy as np
import pytest

from perpbt.carry.executor import COUNTS, DAILY_COLUMNS, exec_spec, run_batch, run_path
from perpbt.carry.frames import load_pairframe
from perpbt.carry.prereg import load_prereg
from perpbt.carry.signals import compute_signals
from perpbt.config import DataConfig, load_yaml, to_dict
from tests.carry.test_executor_batch import assert_path_equal, stack

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def real():
    cfg = load_yaml(ROOT / "configs" / "carry" / "data.yaml", DataConfig)
    cfg = DataConfig(**{**to_dict(cfg), "data_dir": str(ROOT / cfg.data_dir)})
    if not (Path(cfg.data_dir) / "spot" / "candles").is_dir():
        pytest.skip("needs the downloaded data")
    prereg = load_prereg(ROOT / "configs" / "carry" / "prereg.yaml")
    pairs = prereg.universe["um"]["members"]
    frames = {pair: load_pairframe(cfg, pair, end=prereg.periods.insample_end) for pair in pairs}
    primary = {pair: np.asarray(compute_signals(pf, prereg.signal).on) for pair, pf in frames.items()}
    return frames, primary, exec_spec(prereg)


def random_paths(frames, n, seed):
    rng = np.random.default_rng(seed)
    out = {}
    for pair, pf in frames.items():
        flips = rng.random((n, len(pf))) < rng.uniform(0.005, 0.05, (n, 1))
        out[pair] = (np.cumsum(flips, axis=1) % 2).astype(bool)
    return out


def test_structure_and_invariants(real):
    frames, primary, cfg = real
    r = run_path(frames, primary, cfg)
    d = r.daily
    assert tuple(d.columns) == DAILY_COLUMNS and len(d) == 1279
    assert d["date"].iloc[0] == "2020-07-01" and d["date"].iloc[-1] == "2023-12-31"
    assert (d["n_live"] == 10).all()
    assert r.counts["delists"] == 0  # the in-sample end is not a delisting
    assert r.counts["postponed"] == 0 and r.counts["late_fills"] == 0 and r.counts["funding_px_fallbacks"] == 0
    assert (d["cash"] >= 0).all() and np.isfinite(d[["equity", "ret", "basis_pnl"]].to_numpy()).all()
    prev = np.concatenate(([cfg.equity0], d["equity"].to_numpy()[:-1]))
    np.testing.assert_allclose(d["equity"].to_numpy() - prev,
                               (d["funding"] - d["fees"] - d["slippage"] + d["basis_pnl"]).to_numpy(),
                               rtol=0, atol=1e-9 * cfg.equity0)
    assert len(r.daily_pair) == 12790 and r.counts["entries"] > 0


def test_one_path_under_a_second(real):
    frames, primary, cfg = real
    run_path(frames, primary, cfg)
    t0 = time.perf_counter()
    run_path(frames, primary, cfg)
    assert time.perf_counter() - t0 < 1.0


def test_thousand_paths_under_a_minute(real):
    frames, _, cfg = real
    paths = random_paths(frames, 1000, seed=1)
    t0 = time.perf_counter()
    b = run_batch(frames, paths, cfg)
    assert time.perf_counter() - t0 < 60.0
    assert b.equity.shape == (1000, 1279) and np.isfinite(b.equity).all() and (b.cash >= 0).all()


def test_batch_equals_path_on_real_paths(real):
    frames, primary, cfg = real
    rnd = random_paths(frames, 5, seed=2)
    per_path = [primary] + [{pair: rnd[pair][p] for pair in frames} for p in range(5)]
    b = run_batch(frames, stack(per_path), cfg)
    seen = dict.fromkeys(COUNTS, 0)
    for p, st in enumerate(per_path):
        path = run_path(frames, st, cfg)
        assert_path_equal(b, p, path)
        for c in COUNTS:
            seen[c] += path.counts[c]
    assert seen["entries"] > 0 and seen["exits"] > 0


def test_causal_in_the_decisions(real):
    frames, primary, cfg = real
    base = run_path(frames, primary, cfg)
    cut = 700
    t_ms = int(base.daily["day_ms"].iloc[cut])
    flipped = {pair: np.where(pf.day_ms >= t_ms, ~primary[pair], primary[pair]) for pair, pf in frames.items()}
    alt = run_path(frames, flipped, cfg)
    for col in DAILY_COLUMNS:
        assert base.daily[col].iloc[:cut + 1].tolist() == alt.daily[col].iloc[:cut + 1].tolist(), col
    assert base.daily["n_on"].iloc[cut + 1:].tolist() != alt.daily["n_on"].iloc[cut + 1:].tolist()


def test_always_off(real):
    frames, _, cfg = real
    r = run_path(frames, {pair: np.zeros(len(pf), dtype=bool) for pair, pf in frames.items()}, cfg)
    assert len(r.fills) == 0 and (r.daily["ret"] == 0).all() and all(v == 0 for v in r.counts.values())
