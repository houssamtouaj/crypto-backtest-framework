"""The runner's data checks on the stored data (slow; structure only: no variant is run, nothing is computed)."""
import json
from pathlib import Path

import pytest

from perpbt.carry.prereg import load_prereg
from perpbt.carry.runner import KNOWN_DELISTINGS, check_caught_up, load_frames
from perpbt.config import DataConfig, load_yaml
from perpbt.data.store import DAY_MS, date_ms

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]


def test_insample_frames_are_caught_up():
    prereg = load_prereg(ROOT / "configs" / "carry" / "prereg.yaml")
    cfg = load_yaml(ROOT / "configs" / "carry" / "data.yaml", DataConfig)
    frames = load_frames(cfg, prereg.universe["um"]["members"], end=prereg.periods.insample_end)
    check_caught_up(frames, prereg.periods.insample_end)  # every pair through 2023-12-31, funding included


def test_known_delistings_match_the_stored_perp_end():
    # metadata only: the perp manifest's last candle day is the PairFrame's last day (the perp ends first)
    for pair, day in KNOWN_DELISTINGS.items():
        manifest = json.loads((ROOT / "data" / "candles" / pair / "15m" / "manifest.json").read_text(encoding="utf-8"))
        last_day = manifest["last_open_ms"] // DAY_MS * DAY_MS
        assert last_day == date_ms(day), pair
