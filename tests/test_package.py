"""Smoke tests: the package and every subpackage import."""
import importlib

import pytest

import perpbt

SUBPACKAGES = [
    "perpbt.data",
    "perpbt.indicators",
    "perpbt.strategy",
    "perpbt.execution",
    "perpbt.stats",
    "perpbt.experiments",
    "perpbt.report",
]


def test_version_is_a_non_empty_string():
    assert isinstance(perpbt.__version__, str)
    assert perpbt.__version__


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name):
    assert importlib.import_module(name).__name__ == name
