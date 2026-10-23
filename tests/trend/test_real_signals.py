"""Real-data checks for the trend signals (trend spec Phase 1 §1.7). Slow; skipped without data/.

Perps: every universe member's DayFrame and signals on the in-sample data,
warm and finite from the first trading day. Spot is holdout data: its
check reads the store's manifests (timestamps and gaps), never a price.
No performance number is computed here.
"""
import json
from pathlib import Path

import numpy as np
import pytest

from perpbt.config import DataConfig, from_dict, load_yaml, to_dict
from perpbt.data.store import DAY_MS, CandleStore, date_ms
from perpbt.trend.config import MEMBERS, SignalConfig, SizingConfig
from perpbt.trend.daily import MAX_FILLED_RUN, filled_runs, load_dayframe
from perpbt.trend.signals import compute_signals

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE = json.loads((ROOT / "configs" / "trend" / "universe.json").read_text(encoding="utf-8"))
SIGNAL = from_dict(SignalConfig, {"members": list(MEMBERS), "lookbacks": [20, 40, 80, 160],
                                  "donchian_exit": "half", "direction": "long_flat"})
SIZING = from_dict(SizingConfig, {"target_vol": 0.30, "vol_window": 60, "max_weight": 2.0, "max_gross": 2.0})
TRADING_START = "2020-07-01"
STEP_MS = 900_000


def _cfg(name: str) -> DataConfig:
    cfg = load_yaml(ROOT / "configs" / "trend" / name, DataConfig)
    cfg = DataConfig(**{**to_dict(cfg), "data_dir": str(ROOT / cfg.data_dir)})
    if not (Path(cfg.data_dir) / "candles").is_dir():
        pytest.skip("no downloaded data under data/; run `perpbt data fetch` first")
    return cfg


@pytest.fixture(scope="module")
def perp_cfg():
    return _cfg("data.yaml")


@pytest.mark.parametrize("pair", UNIVERSE["um"]["members"])
def test_perp_signals_warm_and_complete(perp_cfg, pair):
    f = load_dayframe(perp_cfg, pair, end=perp_cfg.insample_end)
    assert f.day_ms[0] == date_ms(perp_cfg.listing[pair])
    assert not f.filled.any()
    for sig in (SIGNAL, from_dict(SignalConfig, {**to_dict(SIGNAL), "direction": "long_short"})):
        s = compute_signals(f, sig, SIZING)
        live = f.day_ms >= date_ms(TRADING_START)
        assert live.any() and s.valid[live].all()
        assert np.isfinite(s.weight[live]).all() and np.isfinite(s.members[:, live]).all()
        assert (np.abs(s.weight[live]) <= SIZING.max_weight).all()


def test_eos_frame_ends_at_last_candle(perp_cfg):
    f = load_dayframe(perp_cfg, "EOSUSDT", end=perp_cfg.insample_end)
    assert f.day_ms[-1] == date_ms("2025-05-21")
    assert f.funding_ts.max() <= f.last_candle_ms
    assert f.funding_ptr[-1] == len(f.funding_ts)


@pytest.mark.parametrize("pair", ["BTCUSDT", "ETHUSDT"])
def test_store_head_not_used(perp_cfg, pair):
    assert CandleStore(perp_cfg).manifest(pair, "15m")["first_open_ms"] < date_ms("2020-01-01")
    f = load_dayframe(perp_cfg, pair, end="2020-01-31")
    assert f.day_ms[0] == date_ms("2020-01-01") and np.isnan(f.ret[0])


@pytest.mark.parametrize("pair", UNIVERSE["spot"]["members"])
def test_spot_filled_runs_from_manifests(pair):
    cfg = _cfg("data-spot.yaml")
    m = json.loads((Path(cfg.data_dir) / "spot" / "candles" / pair / "15m" / "manifest.json").read_text(encoding="utf-8"))
    ts = np.arange(m["first_open_ms"], m["last_open_ms"] + STEP_MS, STEP_MS, dtype=np.int64)
    present = np.ones(len(ts), dtype=bool)
    for g in m["gaps"]:
        present[(ts >= g["start_ms"]) & (ts < g["end_ms"])] = False
    first = date_ms(max(cfg.listing[pair], cfg.warmup_start))
    assert m["first_open_ms"] // DAY_MS * DAY_MS == first  # the listing day has the first candle
    day_ms = first + DAY_MS * np.arange((m["last_open_ms"] - first) // DAY_MS + 1, dtype=np.int64)
    has = np.zeros(len(day_ms), dtype=bool)
    has[(ts[present] - first) // DAY_MS] = True
    runs = filled_runs(day_ms, ~has)
    assert all(length <= MAX_FILLED_RUN for _, length in runs), runs
