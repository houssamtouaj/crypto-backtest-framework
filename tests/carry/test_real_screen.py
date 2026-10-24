"""The committed screen reproduces (slow; carry spec Phase 2 §2.6).

Reruns the screen in memory on the stored in-sample data and compares it byte for byte with
``runs/carry/screen.json`` (without ``computed_on`` and ``git_commit``), so a later change to the frames, the
signals or the bootstrap that would move the frozen result fails here. Nothing is written.
"""
import json
from pathlib import Path

import pytest

from perpbt.carry.prereg import check_frozen
from perpbt.carry.screen import screen_from_store
from perpbt.config import DataConfig, load_yaml, to_dict

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]


def test_screen_json_reproduces():
    committed = ROOT / "runs" / "carry" / "screen.json"
    cfg = load_yaml(ROOT / "configs" / "carry" / "data.yaml", DataConfig)
    cfg = DataConfig(**{**to_dict(cfg), "data_dir": str(ROOT / cfg.data_dir)})
    if not committed.exists() or not (Path(cfg.data_dir) / "spot" / "candles").is_dir():
        pytest.skip("needs runs/carry/screen.json and the downloaded data")
    text = committed.read_text(encoding="utf-8")
    saved = json.loads(text)
    result = screen_from_store(check_frozen(ROOT / "configs" / "carry" / "prereg.yaml"), cfg)
    result = {**result, "computed_on": saved["computed_on"], "git_commit": saved["git_commit"]}
    assert json.dumps(result, indent=2, allow_nan=False) + "\n" == text
