"""Shared fixtures for the perpbt test suite."""
from pathlib import Path

import numpy as np
import pytest

from perpbt.config import DataConfig, load_yaml, to_dict


@pytest.fixture
def tmp_data_dir(tmp_path):
    """An empty data directory under pytest's per-test temporary path."""
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def data_cfg(tmp_data_dir):
    """A DataConfig rooted at the temporary data directory, with the real listing dates."""
    return DataConfig(
        data_dir=str(tmp_data_dir),
        insample_start="2020-01-01",
        insample_end="2025-12-31",
        holdout_start="2026-01-01",
        holdout_end=None,
        warmup_start="2019-11-01",
        listing={"BTCUSDT": "2019-09-08", "ETHUSDT": "2019-11-27", "SOLUSDT": "2020-09-14"},
    )


@pytest.fixture
def rng():
    """A fixed-seed numpy Generator; tests that need randomness take this."""
    return np.random.default_rng(20260926)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def real_cfg():
    """Real data config from configs/data.yaml with data_dir rebased to repo root; skips if data/ is absent."""
    cfg = load_yaml(ROOT / "configs" / "data.yaml", DataConfig)
    cfg = DataConfig(**{**to_dict(cfg), "data_dir": str(ROOT / cfg.data_dir)})
    if not (Path(cfg.data_dir) / "candles").is_dir():
        pytest.skip("no downloaded data under data/; run `perpbt data fetch` first")
    return cfg
