"""Shared fixtures for the perpbt test suite."""
import numpy as np
import pytest

from perpbt.config import DataConfig


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
