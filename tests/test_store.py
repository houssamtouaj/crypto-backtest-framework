"""Candles, Funding, source merge, CandleStore and FundingStore (perpbt.data.store)."""
import numpy as np
import pandas as pd
import pytest

from perpbt.data.store import (
    Candles,
    CandleStore,
    Funding,
    FundingStore,
    HoldoutAccessError,
    MergeStats,
    date_ms,
    insample_end_exclusive_ms,
    merge_rows,
    new_candle_manifest,
    new_funding_manifest,
    year_of_ms,
)
from tests.fake_archive import kline_rows

T0 = 1_577_836_800_000  # 2020-01-01T00:00Z
STEP = 900_000
H = 3_600_000
HOLDOUT = 1_767_225_600_000  # 2026-01-01T00:00Z


def make(n=5, start_ms=1_000, step_ms=100, tf="15m"):
    ts = start_ms + step_ms * np.arange(n)
    o = np.full(n, 10.0)
    c = o + 1.0
    h = c + 1.0
    l = o - 1.0
    v = np.ones(n)
    return Candles("TEST", tf, ts, o, h, l, c, v)


# --- Candles (Phase 0) ------------------------------------------------------------------


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


def test_slice_shares_memory():
    cd = make(5)
    s = cd.slice(1100, 1300)
    assert np.shares_memory(s.o, cd.o) and np.shares_memory(s.ts, cd.ts)


def test_step_ms_from_timeframe():
    assert make(tf="1m").step_ms() == 60_000
    assert make(tf="15m").step_ms() == 900_000
    with pytest.raises(ValueError, match="timeframe"):
        make(tf="1h").step_ms()


# --- time helpers -------------------------------------------------------------------------


def test_date_ms_and_year_of_ms():
    assert date_ms("2020-01-01") == T0
    assert date_ms("2026-01-01") == HOLDOUT
    assert date_ms("2019-11-01") == 1_572_566_400_000
    assert year_of_ms(HOLDOUT - 1) == 2025 and year_of_ms(HOLDOUT) == 2026


def test_insample_end_exclusive_is_the_day_after(data_cfg):
    assert insample_end_exclusive_ms(data_cfg) == HOLDOUT


# --- Funding ------------------------------------------------------------------------------


def funding(n=4, start=T0, step=8 * H):
    ts = start + step * np.arange(n)
    return Funding("BTCUSDT", ts, np.full(n, 1e-4), np.full(n, 8, dtype=np.int8))


def test_funding_dtypes_and_len():
    f = funding()
    assert len(f) == 4
    assert f.ts.dtype == np.int64 and f.rate.dtype == np.float64 and f.interval_h.dtype == np.int8


def test_funding_rejects_unsorted_misaligned_and_float_intervals():
    with pytest.raises(ValueError, match="strictly increasing"):
        Funding("X", [T0, T0], [0.0, 0.0], [8, 8])
    with pytest.raises(ValueError, match="length"):
        Funding("X", [T0], [0.0, 0.0], [8])
    with pytest.raises(TypeError, match="integer"):
        Funding("X", [T0], [0.0], [8.0])


def test_events_between_is_inclusive_on_both_ends():
    f = funding(4)  # T0, T0+8h, T0+16h, T0+24h
    assert f.events_between(T0, T0 + 24 * H) == slice(0, 4)
    assert f.events_between(T0 + 8 * H, T0 + 16 * H) == slice(1, 3)
    assert f.events_between(T0 + 1, T0 + 8 * H - 1) == slice(1, 1)
    assert f.events_between(T0 + 8 * H, T0 + 8 * H) == slice(1, 2)
    assert f.events_between(T0 + 30 * H, T0 + 40 * H) == slice(4, 4)
    assert f.events_between(T0 - 10 * H, T0 - 1) == slice(0, 0)
    assert f.ts[f.events_between(T0 + 8 * H, T0 + 16 * H)].tolist() == [T0 + 8 * H, T0 + 16 * H]


# --- merge_rows ---------------------------------------------------------------------------


def cframe(rows, source="bulk_monthly"):
    f = pd.DataFrame(
        rows,
        columns=["open_ms", "open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_buy_volume"],
    )
    f["source"] = source
    return f


def test_merge_rows_without_existing_sorts():
    merged, stats = merge_rows(None, cframe(kline_rows(T0, 3, STEP)[::-1]), key="open_ms", compare=("open",))
    assert merged["open_ms"].tolist() == [T0, T0 + STEP, T0 + 2 * STEP]
    assert stats == MergeStats(3, 0, 0, 0)


def test_merge_rows_precedence_and_mismatch_count():
    old = cframe(kline_rows(T0, 3, STEP), "ccxt")
    new = cframe(kline_rows(T0 + STEP, 3, STEP, price=101.0), "bulk_daily")  # same prices as the stored rows
    new.loc[0, "close"] += 1.0  # disagrees with the ccxt row at T0 + STEP
    merged, stats = merge_rows(old, new, key="open_ms", compare=("open", "high", "low", "close"))
    assert merged["open_ms"].tolist() == [T0 + k * STEP for k in range(4)]
    assert merged["source"].tolist() == ["ccxt", "bulk_daily", "bulk_daily", "bulk_daily"]
    assert merged["close"].tolist()[1] == new["close"].tolist()[0]
    assert stats == MergeStats(added=1, replaced=2, kept=0, mismatches=1)


def test_merge_rows_keeps_higher_precedence_stored_rows():
    old = cframe(kline_rows(T0, 2, STEP), "bulk_monthly")
    new = cframe(kline_rows(T0, 2, STEP, price=200.0), "ccxt")
    merged, stats = merge_rows(old, new, key="open_ms", compare=("open",))
    assert merged["open"].tolist() == [100.0, 101.0]
    assert merged["source"].tolist() == ["bulk_monthly", "bulk_monthly"]
    assert stats == MergeStats(added=0, replaced=0, kept=2, mismatches=2)


def test_merge_rows_same_source_incoming_wins_and_equal_rows_do_not_mismatch():
    old = cframe(kline_rows(T0, 2, STEP))
    new = cframe(kline_rows(T0, 2, STEP))
    _, stats = merge_rows(old, new, key="open_ms", compare=("open", "close"))
    assert stats == MergeStats(0, 2, 0, 0)


def test_merge_rows_tolerance_is_relative_1e_9():
    old = cframe(kline_rows(T0, 1, STEP))
    new = cframe(kline_rows(T0, 1, STEP), "ccxt")
    new.loc[0, "open"] *= 1 + 5e-10
    assert merge_rows(old, new, key="open_ms", compare=("open",))[1].mismatches == 0
    new.loc[0, "open"] = 100.0 * (1 + 5e-9)
    assert merge_rows(old, new, key="open_ms", compare=("open",))[1].mismatches == 1


def test_merge_rows_unknown_source_raises():
    with pytest.raises(ValueError, match="source"):
        merge_rows(cframe(kline_rows(T0, 1, STEP)), cframe(kline_rows(T0, 1, STEP), "other"), key="open_ms", compare=("open",))


# --- CandleStore --------------------------------------------------------------------------


def test_write_rejects_unsorted_duplicate_and_bad_input(data_cfg, tmp_data_dir):
    store = CandleStore(data_cfg)
    with pytest.raises(ValueError, match="strictly increasing"):
        store.write("BTCUSDT", "15m", cframe(kline_rows(T0, 3, STEP)[::-1]))
    with pytest.raises(ValueError, match="strictly increasing"):
        store.write("BTCUSDT", "15m", cframe(kline_rows(T0, 2, STEP) + kline_rows(T0 + STEP, 1, STEP)))
    with pytest.raises(ValueError, match="columns"):
        store.write("BTCUSDT", "15m", cframe(kline_rows(T0, 2, STEP)).drop(columns=["volume"]))
    with pytest.raises(ValueError, match="timeframe"):
        store.write("BTCUSDT", "1h", cframe(kline_rows(T0, 2, STEP)))
    bad = cframe(kline_rows(T0, 2, STEP))
    bad.loc[1, "high"] = np.nan
    with pytest.raises(ValueError, match="finite"):
        store.write("BTCUSDT", "15m", bad)
    wrong_pair = cframe(kline_rows(T0, 2, STEP))
    wrong_pair["pair"] = "ETHUSDT"
    with pytest.raises(ValueError, match="pair"):
        store.write("BTCUSDT", "15m", wrong_pair)
    assert list(tmp_data_dir.rglob("*.parquet")) == []


def test_write_partitions_by_year_and_load_spans_years(data_cfg):
    store = CandleStore(data_cfg)
    new_year = date_ms("2021-01-01")
    assert store.write("BTCUSDT", "15m", cframe(kline_rows(new_year - 2 * STEP, 4, STEP))) == MergeStats(4, 0, 0, 0)
    d = store.dir("BTCUSDT", "15m")
    assert sorted(p.name for p in d.glob("*.parquet")) == ["2020.parquet", "2021.parquet"]
    assert pd.read_parquet(d / "2020.parquet")["open_ms"].tolist() == [new_year - 2 * STEP, new_year - STEP]
    assert pd.read_parquet(d / "2021.parquet")["open_ms"].tolist() == [new_year, new_year + STEP]
    cd = store.load("BTCUSDT", "15m", new_year - 2 * STEP, new_year + 2 * STEP)
    assert cd.pair == "BTCUSDT" and cd.tf == "15m"
    assert cd.ts.tolist() == [new_year + k * STEP for k in (-2, -1, 0, 1)]
    assert cd.o.tolist() == [100.0, 101.0, 102.0, 103.0]
    assert cd.v.tolist() == [10.0, 11.0, 12.0, 13.0]


def test_write_is_idempotent_and_merges_by_precedence(data_cfg):
    store = CandleStore(data_cfg)
    store.write("BTCUSDT", "15m", cframe(kline_rows(T0, 3, STEP), "ccxt"))
    assert store.write("BTCUSDT", "15m", cframe(kline_rows(T0, 3, STEP), "ccxt")) == MergeStats(0, 3, 0, 0)
    stats = store.write("BTCUSDT", "15m", cframe(kline_rows(T0 + STEP, 3, STEP, price=500.0), "bulk_monthly"))
    assert stats == MergeStats(added=1, replaced=2, kept=0, mismatches=2)
    f = store.read_frame("BTCUSDT", "15m")
    assert f["open_ms"].tolist() == [T0 + k * STEP for k in range(4)]
    assert f["source"].tolist() == ["ccxt", "bulk_monthly", "bulk_monthly", "bulk_monthly"]
    assert f["open"].tolist() == [100.0, 500.0, 501.0, 502.0]
    assert f["pair"].tolist() == ["BTCUSDT"] * 4 and f["tf"].tolist() == ["15m"] * 4
    assert f["open_ms"].dtype == np.int64 and f["trades"].dtype == np.int64
    assert len(pd.read_parquet(store.dir("BTCUSDT", "15m") / "2020.parquet")) == 4


def test_read_frame_is_half_open_and_empty_when_nothing_is_stored(data_cfg):
    store = CandleStore(data_cfg)
    empty = store.read_frame("BTCUSDT", "15m")
    assert len(empty) == 0 and "open_ms" in empty.columns and empty["open_ms"].dtype == np.int64
    store.write("BTCUSDT", "15m", cframe(kline_rows(T0, 5, STEP)))
    assert store.read_frame("BTCUSDT", "15m", T0 + STEP, T0 + 3 * STEP)["open_ms"].tolist() == [T0 + STEP, T0 + 2 * STEP]


def test_load_returns_read_only_arrays_and_half_open_range(data_cfg):
    store = CandleStore(data_cfg)
    store.write("BTCUSDT", "15m", cframe(kline_rows(T0, 5, STEP)))
    cd = store.load("BTCUSDT", "15m", T0 + STEP, T0 + 3 * STEP)
    assert cd.ts.tolist() == [T0 + STEP, T0 + 2 * STEP]
    for name in ("ts", "o", "h", "l", "c", "v"):
        assert not getattr(cd, name).flags.writeable, name
    with pytest.raises(ValueError):
        cd.c[0] = 1.0
    assert not cd.slice(T0 + STEP, T0 + 2 * STEP).c.flags.writeable


def test_holdout_guard(data_cfg):
    store = CandleStore(data_cfg)
    store.write("BTCUSDT", "15m", cframe(kline_rows(HOLDOUT - 2 * STEP, 4, STEP)))
    with pytest.raises(HoldoutAccessError, match="holdout"):
        store.load("BTCUSDT", "15m", HOLDOUT - 2 * STEP, HOLDOUT + 1)
    cd = store.load("BTCUSDT", "15m", HOLDOUT - 2 * STEP, HOLDOUT)
    assert cd.ts.tolist() == [HOLDOUT - 2 * STEP, HOLDOUT - STEP]
    cd = store.load("BTCUSDT", "15m", HOLDOUT - 2 * STEP, HOLDOUT + 2 * STEP, allow_holdout=True)
    assert len(cd) == 4


def test_load_before_insample_start_is_allowed(data_cfg):
    store = CandleStore(data_cfg)
    warm = date_ms("2019-11-01")
    store.write("BTCUSDT", "15m", cframe(kline_rows(warm, 3, STEP)))
    assert len(store.load("BTCUSDT", "15m", warm, warm + 3 * STEP)) == 3


def test_load_unknown_pair_raises(data_cfg):
    with pytest.raises(FileNotFoundError, match="XRPUSDT"):
        CandleStore(data_cfg).load("XRPUSDT", "15m", T0, T0 + STEP)


def test_load_empty_range_raises(data_cfg):
    store = CandleStore(data_cfg)
    store.write("BTCUSDT", "15m", cframe(kline_rows(T0, 2, STEP)))
    with pytest.raises(ValueError, match="start_ms"):
        store.load("BTCUSDT", "15m", T0 + STEP, T0 + STEP)
    with pytest.raises(ValueError, match="start_ms"):
        store.load("BTCUSDT", "15m", T0 + STEP, T0)


def test_load_range_with_no_rows_is_empty(data_cfg):
    store = CandleStore(data_cfg)
    store.write("BTCUSDT", "15m", cframe(kline_rows(T0, 2, STEP)))
    assert len(store.load("BTCUSDT", "15m", T0 + 10 * STEP, T0 + 20 * STEP)) == 0


def test_manifest_skeleton_and_round_trip(data_cfg):
    store = CandleStore(data_cfg)
    m = store.manifest("BTCUSDT", "15m")
    assert m == new_candle_manifest("BTCUSDT", "15m")
    assert m["files"] == [] and m["tf"] == "15m" and m["consistency_1m_15m"] is None
    m["files"].append({"name": "BTCUSDT-15m-2020-01.zip", "sha256": "ab" * 32, "rows": 2976, "source": "bulk_monthly"})
    store.write_manifest("BTCUSDT", "15m", m)
    text = store.manifest_path("BTCUSDT", "15m").read_text(encoding="utf-8")
    assert text.endswith("}\n") and '"pair": "BTCUSDT"' in text
    assert store.manifest("BTCUSDT", "15m") == m


# --- FundingStore -------------------------------------------------------------------------


def fframe(rows, source="bulk_monthly"):
    f = pd.DataFrame(rows, columns=["funding_ms", "rate", "interval_h"])
    f["interval_h"] = f["interval_h"].astype(np.int8)
    f["source"] = source
    return f


def test_funding_store_write_load_and_guard(data_cfg):
    store = FundingStore(data_cfg)
    rows = [(HOLDOUT - 16 * H, 1e-4, 8), (HOLDOUT - 8 * H, 2e-4, 8), (HOLDOUT, 3e-4, 4), (HOLDOUT + 4 * H, 4e-4, 4)]
    assert store.write("BTCUSDT", fframe(rows)) == MergeStats(4, 0, 0, 0)
    assert sorted(p.name for p in store.dir("BTCUSDT").glob("*.parquet")) == ["2025.parquet", "2026.parquet"]
    f = store.load("BTCUSDT", HOLDOUT - 16 * H, HOLDOUT)
    assert f.pair == "BTCUSDT"
    assert f.ts.tolist() == [HOLDOUT - 16 * H, HOLDOUT - 8 * H]
    assert f.interval_h.dtype == np.int8 and not f.rate.flags.writeable and not f.ts.flags.writeable
    with pytest.raises(HoldoutAccessError):
        store.load("BTCUSDT", HOLDOUT - 16 * H, HOLDOUT + 1)
    full = store.load("BTCUSDT", HOLDOUT - 16 * H, HOLDOUT + 5 * H, allow_holdout=True)
    assert len(full) == 4 and full.interval_h.tolist() == [8, 8, 4, 4]
    assert full.rate.tolist() == pytest.approx([1e-4, 2e-4, 3e-4, 4e-4])


def test_funding_store_precedence_and_mismatch(data_cfg):
    store = FundingStore(data_cfg)
    store.write("BTCUSDT", fframe([(T0, 1e-4, 8), (T0 + 8 * H, 2e-4, 8)], "bulk_monthly"))
    stats = store.write("BTCUSDT", fframe([(T0 + 8 * H, 9e-4, 8), (T0 + 16 * H, 3e-4, 8)], "ccxt"))
    assert stats == MergeStats(added=1, replaced=0, kept=1, mismatches=1)
    f = store.read_frame("BTCUSDT")
    assert f["rate"].tolist() == pytest.approx([1e-4, 2e-4, 3e-4])
    assert f["source"].tolist() == ["bulk_monthly", "bulk_monthly", "ccxt"]
    assert f["interval_h"].dtype == np.int8


def test_funding_store_rejects_unsorted_and_unknown_pair(data_cfg):
    store = FundingStore(data_cfg)
    with pytest.raises(ValueError, match="strictly increasing"):
        store.write("BTCUSDT", fframe([(T0 + 8 * H, 1e-4, 8), (T0, 1e-4, 8)]))
    with pytest.raises(FileNotFoundError, match="XRPUSDT"):
        store.load("XRPUSDT", T0, T0 + H)
    assert store.manifest("BTCUSDT") == new_funding_manifest("BTCUSDT")
