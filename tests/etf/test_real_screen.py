"""Real-data checks for the ETF prereg and screen (ETF spec Phase 2 §2.3, §2.5). Slow; skipped without data/etf.

In-sample only (no allow_holdout). Until ``runs/etf/screen.json`` exists no
strategy return is computed here: the screen runs once, after the freeze,
through ``perpbt etf screen``. Once it exists, the last test recomputes it
and pins the committed file.
"""
import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest

from perpbt.data.store import date_ms
from perpbt.etf.config import load_data_config
from perpbt.etf.frame import load_frames
from perpbt.etf.prereg import check_snapshot, load_prereg
from perpbt.etf.screen import screen_from_store, screen_inputs
from perpbt.etf.signals import compute_signals
from perpbt.etf.store import EtfStore, day_iso

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]
SCREEN = ROOT / "runs" / "etf" / "screen.json"
UNPINNED = ("computed_on", "git_commit")  # when and where the one-shot run happened


@pytest.fixture(scope="module")
def cfg():
    c = load_data_config(ROOT / "configs" / "etf" / "data.yaml")
    if not (ROOT / c.data_dir / "SPY" / "prices.parquet").is_file():
        pytest.skip("no ETF store under data/etf; run `perpbt etf fetch` first")
    return dataclasses.replace(c, data_dir=str(ROOT / c.data_dir))


@pytest.fixture(scope="module")
def prereg():
    return load_prereg(ROOT / "configs" / "etf" / "prereg.yaml")


def test_the_prereg_describes_the_stored_snapshot(cfg, prereg):
    check_snapshot(prereg, cfg, EtfStore(cfg.data_dir))


def test_the_screen_inputs_on_the_real_store(cfg, prereg):
    p = prereg.periods
    frames = load_frames(EtfStore(cfg.data_dir), cfg, end=p.insample_end)
    signals = {t: compute_signals(f, prereg.signal, prereg.sizing) for t, f in frames.items()}
    inp = screen_inputs(frames, signals, start=p.insample_start, end=p.insample_end)
    assert inp.tickers == ("SPY", "QQQ", "IWM", "GLD")
    assert inp.exposure.shape == (4, 3127)  # Phase 1 §1.8: 3,127 in-sample days
    assert day_iso(inp.day_ms[0]) == "2005-07-29" and day_iso(inp.day_ms[-1]) == "2017-12-28"
    assert np.isfinite(inp.weight).all() and np.isfinite(inp.ret_next).all() and np.isfinite(inp.cash_next).all()
    assert (inp.weight >= 0).all() and (inp.weight <= 1).all()
    assert (inp.cash_next < 0).sum() == 6  # the 2015 DTB3 prints, used as published (Phase 1 §1.8)
    assert int(frames["SPY"].day_ms[-1]) == date_ms("2017-12-29")


@pytest.mark.skipif(not SCREEN.is_file(), reason="the one-shot screen has not run (runs/etf/screen.json)")
def test_the_committed_screen_is_reproduced(cfg, prereg):
    committed = json.loads(SCREEN.read_text(encoding="utf-8"))
    again = screen_from_store(prereg, cfg)
    assert {k: v for k, v in committed.items() if k not in UNPINNED} == again
    assert committed["prereg_hash"] == json.loads((ROOT / "configs" / "etf" / "prereg.lock")
                                                  .read_text(encoding="utf-8"))["prereg_hash"]
