"""The committed trend configs agree with the resolved universe (trend Phase 0 §0.2, §0.5)."""
import json
import subprocess
from pathlib import Path

import pytest

from perpbt.config import DataConfig, load_yaml
from perpbt.trend.universe import RULES

ROOT = Path(__file__).resolve().parents[2]
TREND = ROOT / "configs" / "trend"


@pytest.fixture(scope="module")
def universe():
    return json.loads((TREND / "universe.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("market, name", [("um", "data.yaml"), ("spot", "data-spot.yaml")])
def test_listing_is_the_universe(universe, market, name):
    cfg = load_yaml(TREND / name, DataConfig)
    block = universe[market]
    first = {r["pair"]: r["first_day"] for r in block["ranked"]}
    assert sorted(cfg.listing) == sorted(block["members"])
    assert cfg.listing == {p: first[p] for p in block["members"]}
    assert block["members"] == [r["pair"] for r in block["ranked"][: RULES[market].top_n]]
    assert block["window"] == [RULES[market].window_start, RULES[market].window_end]
    assert all(cfg.listing[p] <= RULES[market].latest_first_day for p in block["members"])


def test_perp_config_periods(universe):
    cfg = load_yaml(TREND / "data.yaml", DataConfig)
    assert (cfg.data_dir, cfg.insample_start, cfg.insample_end) == ("data", "2020-01-01", "2026-10-20")
    assert (cfg.holdout_start, cfg.holdout_end, cfg.warmup_start) == ("2026-10-21", None, "2020-01-01")
    assert "SOLUSDT" not in universe["um"]["members"]
    assert {"BTCUSDT", "ETHUSDT"} <= set(universe["um"]["members"])


def test_spot_config_periods():
    cfg = load_yaml(TREND / "data-spot.yaml", DataConfig)
    assert (cfg.data_dir, cfg.insample_start, cfg.insample_end) == ("data", "2017-08-01", "2019-12-31")
    assert (cfg.holdout_start, cfg.holdout_end, cfg.warmup_start) == ("2020-01-01", None, "2017-08-01")


def test_first_strategy_data_config_untouched():
    try:
        dev = subprocess.run(["git", "show", "dev:configs/data.yaml"], cwd=ROOT, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git or the dev branch is unavailable")
    here = (ROOT / "configs" / "data.yaml").read_bytes()
    assert here.replace(b"\r\n", b"\n") == dev.replace(b"\r\n", b"\n")
