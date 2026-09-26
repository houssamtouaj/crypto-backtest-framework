"""Shared fixtures for the perpbt test suite."""
import numpy as np
import pytest


@pytest.fixture
def tmp_data_dir(tmp_path):
    """An empty data directory, deleted after the test."""
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def rng():
    """A fixed-seed numpy Generator; tests that need randomness take this."""
    return np.random.default_rng(20260926)
