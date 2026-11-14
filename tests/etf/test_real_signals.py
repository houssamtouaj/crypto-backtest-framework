"""Real-data checks for the ETF frames and signals (ETF spec Phase 1 §1.7). Slow; skipped without data/etf.

In-sample only (no allow_holdout). The store is the Phase 0 snapshot whose
manifest the Phase 2 prereg will pin; it is read, never re-fetched. No
return of the strategy is computed here.
"""
import hashlib
from pathlib import Path

import numpy as np
import pytest

from perpbt.config import from_dict
from perpbt.data.store import date_ms
from perpbt.etf.config import EtfSizingConfig, load_data_config
from perpbt.etf.frame import load_frames
from perpbt.etf.signals import compute_signals, first_valid_index, summarise
from perpbt.etf.store import EtfStore, day_iso
from perpbt.trend.config import MEMBERS, SignalConfig

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SHA256 = "308f5012c5a274b62ee2d5f29e4b299cd225be3fa0cf2b02ff60a73f88c60658"  # Phase 0 §0.9
SIGNAL = from_dict(SignalConfig, {"members": list(MEMBERS), "lookbacks": [20, 40, 80, 160],
                                  "donchian_exit": "half", "direction": "long_flat"})
SIZING = from_dict(EtfSizingConfig, {"target_vol": 0.15, "vol_window": 60, "max_weight": 1.0, "annualise_days": 252})


@pytest.fixture(scope="module")
def cfg():
    c = load_data_config(ROOT / "configs" / "etf" / "data.yaml")
    if not (ROOT / c.data_dir / "manifest.json").is_file():
        pytest.skip("no ETF store under data/etf; run `perpbt etf fetch` first")
    return c


@pytest.fixture(scope="module")
def frames(cfg):
    return load_frames(EtfStore(ROOT / cfg.data_dir), cfg, end=cfg.insample_end)


@pytest.fixture(scope="module")
def signals(frames):
    return {t: compute_signals(f, SIGNAL, SIZING) for t, f in frames.items()}


def _at(frame, iso):
    return int(np.flatnonzero(frame.day_ms == date_ms(iso))[0])


def test_the_snapshot_is_the_phase_0_one(cfg):
    assert hashlib.sha256((ROOT / cfg.data_dir / "manifest.json").read_bytes()).hexdigest() == MANIFEST_SHA256


def test_frames_cover_warmup_to_insample_end(frames):
    assert list(frames) == ["SPY", "QQQ", "IWM", "GLD"]
    for f in frames.values():
        assert len(f) == 3302
        assert (day_iso(f.day_ms[0]), day_iso(f.day_ms[-1])) == ("2004-11-18", "2017-12-29")
        for a in (f.ret, f.r_on, f.r_id, f.cash):
            assert np.isnan(a[0]) and np.isfinite(a[1:]).all()
        assert np.nanmax(np.abs(f.ret)) < 0.15


def test_cash_uses_the_negative_2015_prints(frames):
    f = frames["SPY"]
    neg = [day_iso(m) for m in f.day_ms[f.cash < 0]]
    assert neg == ["2015-09-21", "2015-09-23", "2015-09-28", "2015-10-01", "2015-10-02", "2015-10-09"]
    annual = f.cash[1:] * 360 / (np.diff(f.day_ms) // 86_400_000)
    assert annual.min() > -0.001 and annual.max() < 0.06  # DTB3 stayed within -0.1%..6% over 2004-2017


def test_iwm_split_in_the_warmup_is_continuous(frames):
    f = frames["IWM"]
    assert abs(f.ret[_at(f, "2005-06-09")]) < 0.05


def test_qqq_corrections_are_in_the_frame(frames):
    f = frames["QQQ"]
    assert f.div[_at(f, "2010-06-18")] == 0.089 and f.div[_at(f, "2010-06-25")] == 0.0
    assert (frames["GLD"].div == 0).all()
    assert [int((f.div > 0).sum()) for f in frames.values()] == [53, 53, 53, 0]


def test_stale_opens_are_counted(frames):
    assert [int(f.stale_open.sum()) for f in frames.values()] == [42, 55, 42, 34]


def test_every_ticker_is_valid_before_the_insample_start(cfg, signals):
    for t, s in signals.items():
        k = first_valid_index(SIGNAL, SIZING)
        assert not s.valid[:k].any() and s.valid[k:].all(), t
        assert day_iso(s.first_valid_ms) == "2005-07-11"
        assert s.valid[s.day_ms == date_ms("2005-07-29")].all()  # the decision day before insample_start
        assert np.isfinite(s.members[:, k:]).all()
    summary = summarise(signals, cfg.insample_start, cfg.insample_end)  # raises on an invalid in-sample day
    for t, row in summary.items():
        assert row["days"] == 3127 and sum(row["exposure_hist"]) == 3127, t
        assert 0 < row["mean_weight"] <= row["mean_exposure"] < 1, t
