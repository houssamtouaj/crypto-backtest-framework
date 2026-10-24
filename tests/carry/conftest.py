import pytest

from tests.carry.reportenv import build_world


@pytest.fixture(scope="session")
def carry_world(tmp_path_factory):
    """The synthetic runs directory of the Phase 6 report tests (``reportenv.build_world``), built once."""
    return build_world(tmp_path_factory.mktemp("carry-world"))
