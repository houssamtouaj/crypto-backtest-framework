"""Validation: grid, gap report, 1m→15m consistency, funding checks (perpbt.data.validate)."""
import numpy as np
import pytest

from perpbt.data.store import Candles, date_ms
from perpbt.data.validate import Gap, check_funding, check_grid, consistency_1m_15m, gap_report
from tests.synthetic import random_walk

T0 = 1_577_836_800_000  # 2020-01-01T00:00Z
S15 = 900_000
S1 = 60_000
H = 3_600_000


def test_check_grid_accepts_grid_series_and_edge_sizes():
    check_grid(T0 + S15 * np.arange(10), S15)
    check_grid(np.array([T0]), S15)
    check_grid(np.zeros(0, dtype=np.int64), S15)
    check_grid(T0 + S1 * np.arange(3), S1)


def test_check_grid_rejects_off_grid_unsorted_and_duplicates():
    with pytest.raises(ValueError, match="grid"):
        check_grid(np.array([T0, T0 + S15 + 1]), S15)
    with pytest.raises(ValueError, match="strictly increasing"):
        check_grid(np.array([T0 + S15, T0]), S15)
    with pytest.raises(ValueError, match="strictly increasing"):
        check_grid(np.array([T0, T0]), S15)
    with pytest.raises(TypeError, match="integer"):
        check_grid(np.array([0.0, 1.0]), S15)


def test_gap_report_45_minute_hole_is_one_gap_of_three_slots():
    ts = np.concatenate([T0 + S15 * np.arange(4), T0 + S15 * np.arange(7, 10)])  # slots 4, 5, 6 missing
    gaps = gap_report(ts, S15)
    assert gaps == [Gap(start_ms=T0 + 4 * S15, end_ms=T0 + 7 * S15, missing=3)]
    assert gaps[0].as_dict() == {"start_ms": T0 + 4 * S15, "end_ms": T0 + 7 * S15, "missing": 3}


def test_gap_report_none_single_and_multiple():
    assert gap_report(T0 + S15 * np.arange(5), S15) == []
    assert gap_report(np.array([T0]), S15) == []
    assert gap_report(np.zeros(0, dtype=np.int64), S15) == []
    ts = np.array([T0, T0 + 2 * S15, T0 + 3 * S15, T0 + 10 * S15])
    gaps = gap_report(ts, S15)
    assert gaps == [Gap(T0 + S15, T0 + 2 * S15, 1), Gap(T0 + 4 * S15, T0 + 10 * S15, 6)]
    assert sum(g.missing for g in gaps) == 7


def aggregate(c1m: Candles) -> Candles:
    """The exact 15m series of a 1m series whose length is a multiple of 15."""
    n = len(c1m) // 15
    idx = np.arange(n * 15).reshape(n, 15)
    return Candles(
        c1m.pair, "15m", c1m.ts[idx[:, 0]], c1m.o[idx[:, 0]], c1m.h[idx].max(axis=1),
        c1m.l[idx].min(axis=1), c1m.c[idx[:, -1]], c1m.v[idx].sum(axis=1),
    )


def walk_1m(start_ms: int, days: int, seed: int = 1) -> Candles:
    return random_walk(days * 1440, seed=seed, start_ms=start_ms, step_ms=S1, tf="1m")


def test_synthetic_1m_aggregates_exactly_to_15m():
    c1m = walk_1m(T0, 3)
    report = consistency_1m_15m(c1m, aggregate(c1m))
    assert report == {
        "months_checked": 1,
        "candles_compared": 288,
        "mismatching_candles": 0,
        "mismatch_fields": {"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0},
    }


def test_altered_15m_high_is_one_mismatch():
    c1m = walk_1m(T0, 1)
    c15 = aggregate(c1m)
    c15.h[10] += 1.0
    report = consistency_1m_15m(c1m, c15)
    assert report["mismatching_candles"] == 1
    assert report["mismatch_fields"] == {"open": 0, "high": 1, "low": 0, "close": 0, "volume": 0}
    assert report["candles_compared"] == 96


def test_tiny_differences_within_1e_9_are_not_mismatches():
    c1m = walk_1m(T0, 1)
    c15 = aggregate(c1m)
    c15.c[5] *= 1 + 1e-12
    assert consistency_1m_15m(c1m, c15)["mismatching_candles"] == 0
    c15.c[5] *= 1 + 1e-8
    assert consistency_1m_15m(c1m, c15)["mismatching_candles"] == 1


def test_incomplete_windows_are_skipped_and_months_are_counted():
    start = date_ms("2020-01-31")
    c1m = walk_1m(start, 2)  # 2020-01-31 and 2020-02-01
    c15 = aggregate(c1m)
    keep = np.ones(len(c1m), dtype=bool)
    keep[7] = False  # one constituent of the first 15m candle is missing
    holey = Candles(c1m.pair, "1m", c1m.ts[keep], c1m.o[keep], c1m.h[keep], c1m.l[keep], c1m.c[keep], c1m.v[keep])
    report = consistency_1m_15m(holey, c15)
    assert report["candles_compared"] == 192 - 1
    assert report["mismatching_candles"] == 0
    assert report["months_checked"] == 2


def test_no_overlap_gives_zero_comparisons():
    c1m = walk_1m(T0, 1)
    c15 = aggregate(walk_1m(T0 + 10 * 86_400_000, 1))
    assert consistency_1m_15m(c1m, c15) == {
        "months_checked": 0,
        "candles_compared": 0,
        "mismatching_candles": 0,
        "mismatch_fields": {"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0},
    }


def test_consistency_requires_1m_and_15m_of_the_same_pair():
    c1m = walk_1m(T0, 1)
    with pytest.raises(ValueError, match="1m"):
        consistency_1m_15m(aggregate(c1m), c1m)
    other = Candles("OTHER", "15m", *[getattr(aggregate(c1m), k) for k in ("ts", "o", "h", "l", "c", "v")])
    with pytest.raises(ValueError, match="pair"):
        consistency_1m_15m(c1m, other)


def test_check_funding_reports_intervals_and_rejects_bad_timestamps():
    ts = T0 + 8 * H * np.arange(4)
    iv = np.array([8, 8, 4, 3], dtype=np.int8)
    assert check_funding(ts, iv) == {"intervals": {3: 1, 4: 1, 8: 2}, "bad_intervals": [3]}
    assert check_funding(np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int8)) == {"intervals": {}, "bad_intervals": []}
    with pytest.raises(ValueError, match="strictly increasing"):
        check_funding(np.array([T0, T0]), np.array([8, 8]))
    with pytest.raises(ValueError, match="minute"):
        check_funding(np.array([T0 + 1]), np.array([8]))


def test_check_funding_accepts_the_2h_interval():
    # SOLUSDT ran 2-hour funding 2022-11-10..18 (after two 4-hour events) during the FTX collapse
    ts = T0 + np.arange(4, dtype=np.int64) * 2 * 3_600_000
    assert check_funding(ts, np.array([2, 2, 2, 2], dtype=np.int8)) == {"intervals": {2: 4}, "bad_intervals": []}
