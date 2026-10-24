"""The random-timing and re-pricing engines on the real in-sample pairs (slow; carry spec Phase 4).

Structure, runtime and determinism only. No Sharpe, p-value, return, P&L or funding total is computed, asserted
or printed: the functions that compute statistics (``random_timing``, ``reprice``, ``evaluate``) are not called
here; their parts are (the offsets, the shifted states, ``run_batch`` / ``run_path`` and the ExecSpecs).
"""
import time
from pathlib import Path

import numpy as np
import pytest

from perpbt.carry.diagnostics import draw_offsets, live_window, reprice_specs, shifted_states
from perpbt.carry.executor import exec_spec, run_batch, run_path
from perpbt.carry.frames import load_pairframe
from perpbt.carry.prereg import load_prereg
from perpbt.carry.signals import compute_signals
from perpbt.carry.verdict import Seeds
from perpbt.config import DataConfig, load_yaml, to_dict
from perpbt.data.store import date_ms

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]
N_DAYS = 1279


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
    return prereg, frames, primary, exec_spec(prereg)


def test_shifted_states_of_the_primary(real):
    prereg, frames, primary, cfg = real
    s = prereg.stats
    seeds = Seeds.of(prereg, "insample")
    offsets = draw_offsets(N_DAYS, s.shift_margin_days, s.random_timing_runs, seeds.rng("random_timing"))
    again = draw_offsets(N_DAYS, s.shift_margin_days, s.random_timing_runs, seeds.rng("random_timing"))
    np.testing.assert_array_equal(offsets, again)
    assert offsets.min() >= 60 and offsets.max() <= N_DAYS - 60 and len(offsets) == 1000
    shifted = shifted_states(frames, primary, cfg, offsets)
    for pair, pf in frames.items():
        j0, n_i = live_window(pf, cfg)
        assert n_i == N_DAYS and pf.day_ms[j0] == date_ms("2020-06-30")  # the first decision day
        arr = shifted[pair]
        assert arr.shape == (1000, len(pf))
        np.testing.assert_array_equal(arr[:, j0:j0 + n_i].sum(axis=1), primary[pair][j0:j0 + n_i].sum())
        assert not arr[:, :j0].any() and not arr[:, j0 + n_i:].any()


def test_random_timing_engine_runtime(real):
    prereg, frames, primary, cfg = real
    s = prereg.stats
    offsets = draw_offsets(N_DAYS, s.shift_margin_days, s.random_timing_runs,
                           Seeds.of(prereg, "insample").rng("random_timing"))
    t0 = time.perf_counter()
    batch = run_batch(frames, shifted_states(frames, primary, cfg, offsets), cfg)
    assert time.perf_counter() - t0 < 60.0
    assert batch.equity.shape == (1000, N_DAYS) and np.isfinite(batch.equity).all() and (batch.cash >= 0).all()
    assert (batch.n_live == 10).all() and (batch.counts["delists"] == 0).all()


def test_reprice_cells_build_and_run(real):
    prereg, frames, primary, cfg = real
    cells = reprice_specs(cfg, prereg.reprice)
    assert len(cells) == 8
    t0 = time.perf_counter()
    for slip, taker, c in cells:
        assert c.costs.slippage == {"default": slip} and c.costs.spot_taker == taker
        assert (c.sleeve, c.margin, c.rebalance, c.start, c.end) == (cfg.sleeve, cfg.margin, cfg.rebalance,
                                                                     cfg.start, cfg.end)
        r = run_path(frames, primary, c)
        assert len(r.daily) == N_DAYS and (r.daily["cash"] >= 0).all()
    assert time.perf_counter() - t0 < 30.0
