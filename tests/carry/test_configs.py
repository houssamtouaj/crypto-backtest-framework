"""The carry data config (carry spec Phase 0 §0.2)."""
import json
from pathlib import Path

import pandas as pd
import pytest

from perpbt.config import DataConfig, load_yaml, to_dict
from perpbt.data.store import CandleStore, FundingStore, HoldoutAccessError, date_ms
from tests.fake_archive import kline_rows

ROOT = Path(__file__).resolve().parents[2]
CARRY = ROOT / "configs" / "carry" / "data.yaml"
STEP = 900_000


def test_carry_data_config_periods():
    cfg = load_yaml(CARRY, DataConfig)
    assert (cfg.data_dir, cfg.insample_start, cfg.insample_end, cfg.holdout_start, cfg.holdout_end,
            cfg.warmup_start) == ("data", "2020-01-01", "2023-12-31", "2024-01-01", "2026-10-20", "2020-01-01")


def test_carry_listing_is_trend_um():
    cfg = load_yaml(CARRY, DataConfig)
    trend = load_yaml(ROOT / "configs" / "trend" / "data.yaml", DataConfig)
    universe = json.loads((ROOT / "configs" / "trend" / "universe.json").read_text(encoding="utf-8"))
    assert cfg.listing == trend.listing
    assert sorted(cfg.listing) == sorted(universe["um"]["members"])


@pytest.mark.parametrize("market", ["um", "spot"])
def test_carry_stores_refuse_the_holdout(tmp_path, market):
    cfg = DataConfig(**{**to_dict(load_yaml(CARRY, DataConfig)), "data_dir": str(tmp_path)})
    store = CandleStore(cfg, market=market)
    t, h = date_ms("2023-12-31"), date_ms("2024-01-01")
    rows = kline_rows(h - STEP, 2, STEP)  # 2023-12-31 23:45 and 2024-01-01 00:00
    frame = pd.DataFrame(rows, columns=["open_ms", "open", "high", "low", "close", "volume", "quote_volume", "trades",
                                        "taker_buy_volume"]).assign(source="bulk_monthly")
    store.write("BTCUSDT", "15m", frame)
    assert len(store.load("BTCUSDT", "15m", t, h)) == 1
    with pytest.raises(HoldoutAccessError, match="insample_end"):
        store.load("BTCUSDT", "15m", t, h + STEP)
    if market == "um":
        fs = FundingStore(cfg)
        fs.write("BTCUSDT", pd.DataFrame({"funding_ms": [h - 8 * 3_600_000, h], "rate": 1e-4, "interval_h": 8,
                                          "source": "bulk_monthly"}))
        assert len(fs.load("BTCUSDT", t, h)) == 1
        with pytest.raises(HoldoutAccessError):
            fs.load("BTCUSDT", t, h + 1)
