"""Real-data checks (slow). Run after `perpbt data fetch` and `perpbt data validate`."""
import json
from pathlib import Path

import pytest

from perpbt.config import StrategyParams
from perpbt.data.sessions import SessionCalendar
from perpbt.data.store import CandleStore, FundingStore, date_ms
from perpbt.data.validate import consistency_1m_15m
from tests.strategy_harness import LONDON, NY, UTC, assert_counts_consistent, run_strategy

pytestmark = pytest.mark.slow

PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


@pytest.mark.parametrize("pair", PAIRS)
def test_one_real_month_1m_agrees_with_15m(real_cfg, pair):
    store = CandleStore(real_cfg)
    start, end = date_ms("2024-05-01"), date_ms("2024-06-01")
    c1m = store.load(pair, "1m", start, end)
    c15 = store.load(pair, "15m", start, end)
    assert len(c15) > 0.95 * 31 * 96 and len(c1m) > 0.95 * 31 * 1440
    report = consistency_1m_15m(c1m, c15)
    assert report["candles_compared"] > 0.95 * len(c15)
    assert report["mismatching_candles"] == 0, report


@pytest.mark.parametrize("pair", PAIRS)
def test_manifests_are_complete(real_cfg, pair):
    for tf in ("1m", "15m"):
        m = json.loads((Path(real_cfg.data_dir) / "candles" / pair / tf / "manifest.json").read_text(encoding="utf-8"))
        assert m["rows"] > 0 and m["download_date"] and m["files"]
        assert m["last_open_ms"] >= date_ms("2026-01-01")
        if tf == "15m":
            assert m["consistency_1m_15m"] is not None
    fm = json.loads((Path(real_cfg.data_dir) / "funding" / pair / "manifest.json").read_text(encoding="utf-8"))
    assert fm["rows"] > 0 and fm["bad_intervals"] == []


def test_head_backfill_present_for_btc_and_eth_only(real_cfg):
    store = CandleStore(real_cfg)
    btc = store.load("BTCUSDT", "15m", date_ms("2019-11-01"), date_ms("2020-01-01"))
    assert btc.ts[0] == date_ms("2019-11-01") and len(btc) == 61 * 96
    eth = store.load("ETHUSDT", "15m", date_ms("2019-11-01"), date_ms("2020-01-01"))
    assert 0 < len(eth) <= 61 * 96 and eth.ts[0] >= date_ms("2019-11-27")
    assert len(store.load("SOLUSDT", "15m", date_ms("2019-11-01"), date_ms("2020-09-14"))) == 0


def test_funding_loads_in_sample_and_is_guarded(real_cfg):
    store = FundingStore(real_cfg)
    f = store.load("BTCUSDT", date_ms("2020-01-01"), date_ms("2026-01-01"))
    assert f.ts[0] == date_ms("2020-01-01") and len(f) > 6 * 365 * 3 * 0.95
    assert set(f.interval_h.tolist()) <= {1, 2, 4, 8}


@pytest.mark.parametrize("pair", PAIRS)
def test_order_block_rule_on_real_candles(real_cfg, pair):
    cd = CandleStore(real_cfg).load(pair, "15m", date_ms(real_cfg.warmup_start), date_ms("2026-01-01"))
    runs = {}
    for spec in (UTC, NY, LONDON):
        run = runs[spec.name] = run_strategy(cd, StrategyParams(), spec)
        assert_counts_consistent(run.counts)
        assert run.counts["intents"] > 300, (spec.name, run.counts)
        cal = SessionCalendar(spec, cd.ts)
        for i, x in run.intents:
            assert x.stop < x.price < cd.c[i] and x.expires_ms == cal.end_ms[i], (spec.name, i)
    i0 = cd.index_at(date_ms("2024-01-01"))  # a later first call rebuilds the same live levels
    late = run_strategy(cd, StrategyParams(), UTC, start_i=i0)
    assert late.intents == [(i, x) for i, x in runs["utc"].intents if i >= i0]
