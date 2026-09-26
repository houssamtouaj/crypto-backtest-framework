"""Candles container (Phase 0 part of perpbt.data.store)."""
import numpy as np
import pytest

from perpbt.data.store import Candles


def make(n=5, start_ms=1_000, step_ms=100, tf="15m"):
    ts = start_ms + step_ms * np.arange(n)
    o = np.full(n, 10.0)
    c = o + 1.0
    h = c + 1.0
    l = o - 1.0
    v = np.ones(n)
    return Candles("TEST", tf, ts, o, h, l, c, v)


def test_len_and_dtypes():
    cd = make(5)
    assert len(cd) == 5
    assert cd.ts.dtype == np.int64
    for name in ("o", "h", "l", "c", "v"):
        assert getattr(cd, name).dtype == np.float64


def test_lists_are_coerced_to_arrays():
    cd = Candles("TEST", "15m", [0, 900_000], [1, 2], [2, 3], [0, 1], [1.5, 2.5], [1, 1])
    assert cd.ts.dtype == np.int64
    assert cd.c.dtype == np.float64
    assert cd.c.tolist() == [1.5, 2.5]


def test_empty_is_allowed():
    z = np.zeros(0)
    cd = Candles("TEST", "15m", np.zeros(0, dtype=np.int64), z, z, z, z, z)
    assert len(cd) == 0


def test_arrays_must_have_equal_length():
    with pytest.raises(ValueError, match="length"):
        Candles("TEST", "15m", [0, 1], [1, 2], [2, 3], [0, 1], [1.5], [1, 1])


@pytest.mark.parametrize("ts", [[0, 0], [10, 5], [0, 5, 5, 9]])
def test_ts_must_be_strictly_increasing(ts):
    n = len(ts)
    a = np.ones(n)
    with pytest.raises(ValueError, match="strictly increasing"):
        Candles("TEST", "15m", ts, a, a, a, a, a)


def test_ts_must_be_integer():
    a = np.ones(2)
    with pytest.raises(TypeError, match="integer"):
        Candles("TEST", "15m", [0.0, 1.5], a, a, a, a, a)


def test_index_at_is_left_searchsorted():
    cd = make(5)  # ts = 1000, 1100, 1200, 1300, 1400
    assert cd.index_at(1000) == 0
    assert cd.index_at(1050) == 1
    assert cd.index_at(1100) == 1
    assert cd.index_at(999) == 0
    assert cd.index_at(1400) == 4
    assert cd.index_at(1401) == 5
    assert isinstance(cd.index_at(1100), int)


def test_index_at_exact():
    cd = make(5)
    assert cd.index_at(1100, exact=True) == 1
    with pytest.raises(KeyError):
        cd.index_at(1050, exact=True)
    with pytest.raises(KeyError):
        cd.index_at(99_999, exact=True)


def test_slice_is_half_open():
    cd = make(5)
    s = cd.slice(1100, 1300)
    assert s.ts.tolist() == [1100, 1200]
    assert s.pair == "TEST" and s.tf == "15m"
    assert cd.slice(1150, 1250).ts.tolist() == [1200]
    assert len(cd.slice(1300, 1300)) == 0
    assert cd.slice(0, 10_000).ts.tolist() == cd.ts.tolist()


def test_slice_keeps_columns_aligned():
    cd = make(5)
    s = cd.slice(1200, 1400)
    assert s.o.tolist() == cd.o[2:4].tolist()
    assert s.v.tolist() == cd.v[2:4].tolist()


def test_step_ms_from_timeframe():
    assert make(tf="1m").step_ms() == 60_000
    assert make(tf="15m").step_ms() == 900_000
    with pytest.raises(ValueError, match="timeframe"):
        make(tf="1h").step_ms()
