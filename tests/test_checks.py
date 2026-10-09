"""Shared argument checks (perpbt/checks.py)."""
import numpy as np
import pytest

from perpbt.checks import as_int


def test_as_int_accepts_python_and_numpy_integers():
    assert as_int(3, "x") == 3
    for v in (np.int64(3), np.int32(3), np.uint8(3)):
        out = as_int(v, "x")
        assert out == 3 and type(out) is int


@pytest.mark.parametrize("bad", [True, False, np.bool_(True), 3.0, np.float64(2.0), "3", None])
def test_as_int_rejects_bools_and_non_integers(bad):
    with pytest.raises(TypeError, match="x must be an integer"):
        as_int(bad, "x")
