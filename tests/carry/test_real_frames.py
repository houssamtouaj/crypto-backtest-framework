"""Real-data checks for the carry frames and signals (carry spec Phase 1 §1.4). Slow; skipped without data/.

In-sample loads only (through 2023-12-31). EOSUSDT's common last day lies
after the in-sample, so it is read from the manifests (timestamps, no
prices). No return, P&L or funding statistic is computed here, and the
share of days "on" is not looked at.
"""
from pathlib import Path

import numpy as np
import pytest

from perpbt.carry.config import CarrySignalConfig
from perpbt.carry.frames import CARRY_INSAMPLE_END, load_pairframe
from perpbt.carry.signals import compute_signals
from perpbt.config import DataConfig, from_dict, load_yaml, to_dict
from perpbt.data.store import DAY_MS, CandleStore, HoldoutAccessError, date_ms
from perpbt.trend.daily import MAX_FILLED_RUN, filled_runs

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]
STEP_MS = 900_000


def _cfg(name: str) -> DataConfig:
    cfg = load_yaml(ROOT / "configs" / name, DataConfig)
    cfg = DataConfig(**{**to_dict(cfg), "data_dir": str(ROOT / cfg.data_dir)})
    if not (Path(cfg.data_dir) / "spot" / "candles").is_dir():
        pytest.skip("no downloaded data under data/; run `perpbt data fetch` first")
    return cfg


@pytest.fixture(scope="module")
def cfg():
    return _cfg("carry/data.yaml")


PAIRS = sorted(load_yaml(ROOT / "configs" / "carry" / "data.yaml", DataConfig).listing)


@pytest.mark.parametrize("pair", PAIRS)
def test_pairframe_in_sample(cfg, pair):
    pf = load_pairframe(cfg, pair, end=CARRY_INSAMPLE_END)
    end = date_ms(CARRY_INSAMPLE_END)
    assert pf.day_ms[0] == date_ms(cfg.listing[pair])
    assert pf.day_ms[-1] == end and pf.last_common_ms == end + DAY_MS - STEP_MS
    assert all(length <= MAX_FILLED_RUN for _, length in filled_runs(pf.day_ms, pf.filled))
    assert np.isfinite(pf.basis).all()
    # no spot gap covers 00:00-01:00 (Phase 0 §0.7); seven perps' listing days start at 08:00, so day 0
    # has a late fill only
    assert pf.fill_ok[15][1:].all() and pf.fill_ok[60][1:].all()
    for leg in ("perp", "spot"):
        assert np.isfinite(pf.fill_px_late[leg][15]).all()
    assert set(np.unique(pf.funding_interval_h)) == {8}  # interval changes come after the in-sample
    assert pf.perp.funding_ts.max() <= pf.last_common_ms
    for w in (3, 7, 14):  # the primary and the exploratory lookbacks
        s = compute_signals(pf, from_dict(CarrySignalConfig, {"lookback_days": w, "entry_annual": 0.10,
                                                              "exit_annual": 0.0}))
        assert np.isnan(s.fbar[: w - 1]).all() and np.isfinite(s.fbar[w - 1:]).all()
        assert s.on.dtype == bool and len(s.on) == len(pf)


@pytest.mark.parametrize("config", ["carry/data.yaml", "trend/data.yaml"])
def test_real_load_refused_past_in_sample(config):
    with pytest.raises(HoldoutAccessError, match="carry in-sample end"):  # the carry guard, before any read
        load_pairframe(_cfg(config), "BTCUSDT", end="2024-01-01")


def test_eos_common_last_day_is_the_perps(cfg):
    perp = CandleStore(cfg, market="um").manifest("EOSUSDT", "15m")["last_open_ms"]
    spot = CandleStore(cfg, market="spot").manifest("EOSUSDT", "15m")["last_open_ms"]
    assert perp == date_ms("2025-05-21") + 9 * 3_600_000
    assert spot > perp and spot // DAY_MS * DAY_MS == date_ms("2025-05-26")
