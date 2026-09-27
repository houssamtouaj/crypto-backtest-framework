"""Real-data checks (slow). Run after `perpbt data fetch` and `perpbt data validate`."""
import json
from pathlib import Path

import pytest

from perpbt.config import DataConfig, load_yaml, to_dict
from perpbt.data.store import CandleStore, FundingStore, date_ms
from perpbt.data.validate import consistency_1m_15m

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[1]
PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


@pytest.fixture(scope="module")
def cfg():
    cfg = load_yaml(ROOT / "configs" / "data.yaml", DataConfig)
    cfg = DataConfig(**{**to_dict(cfg), "data_dir": str(ROOT / cfg.data_dir)})
    if not (Path(cfg.data_dir) / "candles").is_dir():
        pytest.skip("no downloaded data under data/; run `perpbt data fetch` first")
    return cfg


@pytest.mark.parametrize("pair", PAIRS)
def test_one_real_month_1m_agrees_with_15m(cfg, pair):
    store = CandleStore(cfg)
    start, end = date_ms("2024-05-01"), date_ms("2024-06-01")
    c1m = store.load(pair, "1m", start, end)
    c15 = store.load(pair, "15m", start, end)
    assert len(c15) > 0.95 * 31 * 96 and len(c1m) > 0.95 * 31 * 1440
    report = consistency_1m_15m(c1m, c15)
    assert report["candles_compared"] > 0.95 * len(c15)
    assert report["mismatching_candles"] == 0, report


@pytest.mark.parametrize("pair", PAIRS)
def test_manifests_are_complete(cfg, pair):
    for tf in ("1m", "15m"):
        m = json.loads((Path(cfg.data_dir) / "candles" / pair / tf / "manifest.json").read_text(encoding="utf-8"))
        assert m["rows"] > 0 and m["download_date"] and m["files"]
        assert m["last_open_ms"] >= date_ms("2026-01-01")
        if tf == "15m":
            assert m["consistency_1m_15m"] is not None
    fm = json.loads((Path(cfg.data_dir) / "funding" / pair / "manifest.json").read_text(encoding="utf-8"))
    assert fm["rows"] > 0 and fm["bad_intervals"] == []


def test_head_backfill_present_for_btc_and_eth_only(cfg):
    store = CandleStore(cfg)
    btc = store.load("BTCUSDT", "15m", date_ms("2019-11-01"), date_ms("2020-01-01"))
    assert btc.ts[0] == date_ms("2019-11-01") and len(btc) == 61 * 96
    eth = store.load("ETHUSDT", "15m", date_ms("2019-11-01"), date_ms("2020-01-01"))
    assert 0 < len(eth) <= 61 * 96 and eth.ts[0] >= date_ms("2019-11-27")
    assert len(store.load("SOLUSDT", "15m", date_ms("2019-11-01"), date_ms("2020-09-14"))) == 0


def test_funding_loads_in_sample_and_is_guarded(cfg):
    store = FundingStore(cfg)
    f = store.load("BTCUSDT", date_ms("2020-01-01"), date_ms("2026-01-01"))
    assert f.ts[0] == date_ms("2020-01-01") and len(f) > 6 * 365 * 3 * 0.95
    assert set(f.interval_h.tolist()) <= {1, 2, 4, 8}
