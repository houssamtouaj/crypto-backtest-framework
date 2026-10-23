"""The data fetch pipeline and run_validate (perpbt.data.fetch, perpbt.data.validate)."""
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from perpbt.config import DataConfig, to_dict
from perpbt.data.bulk import ChecksumError, kline_name, period_of
from perpbt.data.fetch import fetch_gaps, fetch_head, fetch_tail, is_stale, run_fetch, sync_candles, sync_funding
from perpbt.data.store import CandleStore, FundingStore, date_ms
from perpbt.data.validate import run_validate
from tests.fake_archive import FakeArchive, kline_rows
from tests.fake_exchange import FakeExchange, ccxt_candles, ccxt_funding
from tests.synthetic import random_walk

STEP = 900_000
H = 3_600_000
TODAY = date(2026, 9, 27)
NOW_MS = date_ms("2026-09-27") + 10 * H + 7 * 60_000 + 33_000  # 2026-09-27T10:07:33Z


def bulk_frame(rows, source="bulk_monthly"):
    f = pd.DataFrame(
        rows, columns=["open_ms", "open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_buy_volume"]
    )
    f["source"] = source
    return f


def funding_frame(rows, source="bulk_monthly"):
    f = pd.DataFrame(rows, columns=["funding_ms", "rate", "interval_h"])
    f["interval_h"] = f["interval_h"].astype(np.int8)
    f["source"] = source
    return f


def day_rows(iso: str, n: int = 4, *, price: float = 100.0):
    return kline_rows(date_ms(iso), n, STEP, price=price)


@pytest.fixture
def archive():
    """Monthly 2026-07 and 2026-08, dailies 2026-09-20 .. 2026-09-25; 09-26 is not published yet."""
    a = FakeArchive()
    a.add_klines("BTCUSDT", "15m", "2026-07", day_rows("2026-07-01"), header=False)
    a.add_klines("BTCUSDT", "15m", "2026-08", day_rows("2026-08-01"), unit="us")
    for d in range(20, 26):
        a.add_klines("BTCUSDT", "15m", f"2026-09-{d:02d}", day_rows(f"2026-09-{d:02d}"))
    return a


def sync(store, data_cfg, archive, *, from_date, to_date=None, today=TODAY, tf="15m", pair="BTCUSDT"):
    return sync_candles(store, data_cfg, pair, tf, from_date=from_date, to_date=to_date or today, today=today, http_get=archive)


# --- bulk sync ---------------------------------------------------------------------------


def test_fresh_fetch_writes_parquet_and_manifest(data_cfg, tmp_data_dir, archive):
    store = CandleStore(data_cfg)
    manifest = sync(store, data_cfg, archive, from_date=date(2026, 7, 1))
    frame = store.read_frame("BTCUSDT", "15m")
    assert len(frame) == 4 * 8
    assert frame["open_ms"].is_monotonic_increasing and frame["open_ms"].is_unique
    assert set(frame["source"]) == {"bulk_monthly", "bulk_daily"}
    assert [f["name"] for f in manifest["files"]] == [
        "BTCUSDT-15m-2026-07.zip", "BTCUSDT-15m-2026-08.zip",
    ] + [f"BTCUSDT-15m-2026-09-{d:02d}.zip" for d in range(20, 26)]
    assert all(len(f["sha256"]) == 64 and f["rows"] == 4 for f in manifest["files"])
    assert [m["name"] for m in manifest["missing"]] == [
        f"BTCUSDT-15m-2026-09-{d:02d}.zip" for d in list(range(1, 20)) + [26]
    ]
    assert all(m["checked"] == "2026-09-27" for m in manifest["missing"])
    assert manifest["rows"] == 32
    assert manifest["first_open_ms"] == date_ms("2026-07-01") and manifest["last_open_ms"] == date_ms("2026-09-25") + 3 * STEP
    assert manifest["download_date"] == "2026-09-27"
    assert manifest["overlap_mismatches"] == 0 and len(manifest["gaps"]) == 7
    assert "BTCUSDT-15m-2026-09.zip" not in archive.zip_gets()  # the current month has no monthly zip
    assert store.manifest("BTCUSDT", "15m") == manifest
    assert list(tmp_data_dir.rglob("*.zip")) == []


def test_rerun_is_a_noop(data_cfg, archive):
    store = CandleStore(data_cfg)
    first = sync(store, data_cfg, archive, from_date=date(2026, 7, 1))
    archive.calls.clear()
    second = sync(store, data_cfg, archive, from_date=date(2026, 7, 1))
    assert archive.zip_gets() == ["BTCUSDT-15m-2026-09-26.zip"]  # only the recent 404 is retried
    assert second["files"] == first["files"] and second["rows"] == 32
    assert len(store.read_frame("BTCUSDT", "15m")) == 32


def test_new_daily_file_is_picked_up_on_rerun(data_cfg, archive):
    store = CandleStore(data_cfg)
    sync(store, data_cfg, archive, from_date=date(2026, 9, 20))
    archive.add_klines("BTCUSDT", "15m", "2026-09-26", day_rows("2026-09-26"))
    archive.calls.clear()
    manifest = sync(store, data_cfg, archive, from_date=date(2026, 9, 20))
    assert archive.zip_gets() == ["BTCUSDT-15m-2026-09-26.zip"]
    assert manifest["missing"] == [] and manifest["rows"] == 28
    assert manifest["last_open_ms"] == date_ms("2026-09-26") + 3 * STEP


def test_old_missing_files_are_not_retried(data_cfg):
    store = CandleStore(data_cfg)
    archive = FakeArchive()
    manifest = sync(store, data_cfg, archive, from_date=date(2026, 6, 1), to_date=date(2026, 6, 30))
    assert len(manifest["missing"]) == 31 and manifest["rows"] == 0
    assert len(archive.zip_gets()) == 31
    archive.calls.clear()
    sync(store, data_cfg, archive, from_date=date(2026, 6, 1), to_date=date(2026, 6, 30))
    assert archive.zip_gets() == []
    archive.calls.clear()
    sync(store, data_cfg, archive, from_date=date(2026, 6, 1), to_date=date(2026, 6, 30), today=date(2026, 7, 5))
    assert archive.zip_gets() == ["BTCUSDT-15m-2026-06.zip"]  # monthly retried within 35 days; dailies past 3 days are not


def test_monthly_replaces_dailies_when_it_appears(data_cfg):
    store = CandleStore(data_cfg)
    archive = FakeArchive()
    archive.add_klines("BTCUSDT", "15m", "2026-08-30", day_rows("2026-08-30"))
    archive.add_klines("BTCUSDT", "15m", "2026-08-31", day_rows("2026-08-31"))
    manifest = sync(store, data_cfg, archive, from_date=date(2026, 8, 1), to_date=date(2026, 8, 31), today=date(2026, 9, 2))
    assert "BTCUSDT-15m-2026-08.zip" in [m["name"] for m in manifest["missing"]]
    assert manifest["rows"] == 8 and set(store.read_frame("BTCUSDT", "15m")["source"]) == {"bulk_daily"}
    archive.add_klines("BTCUSDT", "15m", "2026-08", day_rows("2026-08-30", price=200.0) + day_rows("2026-08-31", price=200.0))
    archive.calls.clear()
    manifest = sync(store, data_cfg, archive, from_date=date(2026, 8, 1), to_date=date(2026, 8, 31), today=date(2026, 9, 10))
    assert archive.zip_gets() == ["BTCUSDT-15m-2026-08.zip"]
    frame = store.read_frame("BTCUSDT", "15m")
    assert len(frame) == 8 and set(frame["source"]) == {"bulk_monthly"} and frame["open"].tolist()[0] == 200.0
    assert manifest["overlap_mismatches"] == 8
    assert "BTCUSDT-15m-2026-08.zip" not in [m["name"] for m in manifest["missing"]]
    assert "BTCUSDT-15m-2026-08.zip" in [f["name"] for f in manifest["files"]]
    assert "BTCUSDT-15m-2026-08-30.zip" in [f["name"] for f in manifest["files"]]


def test_bad_checksum_raises_and_leaves_no_parquet(data_cfg, tmp_data_dir):
    store = CandleStore(data_cfg)
    archive = FakeArchive()
    archive.add_klines("BTCUSDT", "15m", "2026-07", day_rows("2026-07-01"), corrupt_checksum=True)
    with pytest.raises(ChecksumError, match="2026-07"):
        sync(store, data_cfg, archive, from_date=date(2026, 7, 1), to_date=date(2026, 7, 31))
    assert list(tmp_data_dir.rglob("*.parquet")) == [] and list(tmp_data_dir.rglob("*.zip")) == []
    assert not store.manifest_path("BTCUSDT", "15m").exists()


def test_bad_checksum_midway_keeps_the_manifest_in_step_with_parquet(data_cfg, archive):
    store = CandleStore(data_cfg)
    archive.add_klines("BTCUSDT", "15m", "2026-08", day_rows("2026-08-01"), corrupt_checksum=True)
    with pytest.raises(ChecksumError, match="2026-08"):
        sync(store, data_cfg, archive, from_date=date(2026, 7, 1), to_date=date(2026, 8, 31))
    manifest = store.manifest("BTCUSDT", "15m")
    assert [f["name"] for f in manifest["files"]] == ["BTCUSDT-15m-2026-07.zip"]
    assert len(store.read_frame("BTCUSDT", "15m")) == 4
    archive.add_klines("BTCUSDT", "15m", "2026-08", day_rows("2026-08-01"))
    archive.calls.clear()
    sync(store, data_cfg, archive, from_date=date(2026, 7, 1), to_date=date(2026, 8, 31))
    assert archive.zip_gets() == ["BTCUSDT-15m-2026-08.zip"]
    assert len(store.read_frame("BTCUSDT", "15m")) == 8


def test_listing_date_bounds_months_and_days(data_cfg):
    store = CandleStore(data_cfg)
    archive = FakeArchive()
    manifest = sync_candles(
        store, data_cfg, "SOLUSDT", "15m", from_date=date(2020, 8, 1), to_date=date(2020, 9, 30), today=date(2020, 10, 5), http_get=archive
    )
    gets = archive.zip_gets()
    assert gets[0] == "SOLUSDT-15m-2020-09.zip"
    assert gets[1:] == [f"SOLUSDT-15m-2020-09-{d:02d}.zip" for d in range(14, 31)]
    assert not any("2020-08" in g for g in gets)
    assert manifest["rows"] == 0


def test_1m_files_go_to_their_own_directory(data_cfg):
    store = CandleStore(data_cfg)
    archive = FakeArchive()
    archive.add_klines("BTCUSDT", "1m", "2026-07", kline_rows(date_ms("2026-07-01"), 30, 60_000))
    manifest = sync(store, data_cfg, archive, from_date=date(2026, 7, 1), to_date=date(2026, 7, 31), tf="1m")
    assert manifest["tf"] == "1m" and manifest["rows"] == 30 and manifest["gaps"] == []
    assert store.manifest_path("BTCUSDT", "1m").exists() and not store.manifest_path("BTCUSDT", "15m").exists()


# --- funding sync ------------------------------------------------------------------------


def test_funding_sync_is_monthly_only_and_skips_the_current_month(data_cfg):
    store = FundingStore(data_cfg)
    archive = FakeArchive()
    july = date_ms("2026-07-01")
    archive.add_funding("BTCUSDT", "2026-07", [(july + k * 8 * H + 2, 8, 1e-4 * k) for k in range(93)])
    archive.add_funding("BTCUSDT", "2026-08", [(date_ms("2026-08-01") + k * 4 * H - 1, 4, 1e-4) for k in range(186)])
    manifest = sync_funding(store, data_cfg, "BTCUSDT", from_date=date(2026, 7, 1), to_date=TODAY, today=TODAY, http_get=archive)
    assert archive.zip_gets() == ["BTCUSDT-fundingRate-2026-07.zip", "BTCUSDT-fundingRate-2026-08.zip"]
    assert [f["name"] for f in manifest["files"]] == archive.zip_gets()
    assert manifest["rows"] == 93 + 186
    assert manifest["first_funding_ms"] == july and manifest["last_funding_ms"] == date_ms("2026-08-01") + 185 * 4 * H
    assert manifest["last_interval_h"] == 4 and manifest["intervals"] == {"4": 186, "8": 93} and manifest["bad_intervals"] == []
    assert manifest["download_date"] == "2026-09-27" and manifest["missing"] == []
    f = store.read_frame("BTCUSDT")
    assert (f["funding_ms"] % 60_000 == 0).all() and f["interval_h"].dtype == np.int8


def test_funding_404_is_recorded(data_cfg):
    store = FundingStore(data_cfg)
    manifest = sync_funding(store, data_cfg, "BTCUSDT", from_date=date(2026, 8, 1), to_date=TODAY, today=TODAY, http_get=FakeArchive())
    assert [m["name"] for m in manifest["missing"]] == ["BTCUSDT-fundingRate-2026-08.zip"]
    assert manifest["rows"] == 0 and manifest["last_interval_h"] is None


# --- ccxt head ---------------------------------------------------------------------------


def test_head_backfill_covers_exactly_the_range_and_records_it(data_cfg):
    store = CandleStore(data_cfg)
    start, end = date_ms("2019-11-01"), date_ms("2020-01-01")
    exchange = FakeExchange(ccxt_candles(date_ms("2019-10-01"), 110 * 96))  # 2019-10-01 .. 2020-01-19
    rows = fetch_head(store, data_cfg, "BTCUSDT", from_date=date(2019, 11, 1), exchange=exchange)
    assert rows == 61 * 96 == 5856
    frame = store.read_frame("BTCUSDT", "15m")
    assert frame["open_ms"].iloc[0] == start and frame["open_ms"].iloc[-1] == end - STEP
    assert (frame["source"] == "ccxt").all()
    manifest = store.manifest("BTCUSDT", "15m")
    assert manifest["ccxt_ranges"] == [{"start_ms": start, "end_ms": end, "rows": 5856, "purpose": "head"}]
    assert manifest["first_open_ms"] == start and manifest["rows"] == 5856 and manifest["gaps"] == []
    assert exchange.calls[0][3] == start
    exchange.calls.clear()
    assert fetch_head(store, data_cfg, "BTCUSDT", from_date=date(2019, 11, 1), exchange=exchange) == 0
    assert exchange.calls == [] and len(store.manifest("BTCUSDT", "15m")["ccxt_ranges"]) == 1


def test_head_backfill_is_nothing_for_sol(data_cfg):
    store = CandleStore(data_cfg)
    exchange = FakeExchange(ccxt_candles(date_ms("2019-10-01"), 100))
    assert fetch_head(store, data_cfg, "SOLUSDT", from_date=date(2019, 11, 1), exchange=exchange) == 0
    assert exchange.calls == [] and not store.manifest_path("SOLUSDT", "15m").exists()


# --- ccxt tail ---------------------------------------------------------------------------


def test_tail_drops_the_open_candle(data_cfg):
    cstore, fstore = CandleStore(data_cfg), FundingStore(data_cfg)
    cstore.write("BTCUSDT", "15m", bulk_frame(day_rows("2026-09-26", 96), "bulk_daily"))
    cstore.write_manifest("BTCUSDT", "15m", {**cstore.manifest("BTCUSDT", "15m"), "last_open_ms": date_ms("2026-09-26") + 95 * STEP})
    exchange = FakeExchange(ccxt_candles(date_ms("2026-09-26"), 96 + 41, price=200.0))  # up to the open 10:00 candle
    fetched = fetch_tail(cstore, fstore, "BTCUSDT", ("15m",), exchange=exchange, now_ms=NOW_MS)
    assert fetched == {"15m": 40, "funding": 0}
    manifest = cstore.manifest("BTCUSDT", "15m")
    day = date_ms("2026-09-27")
    assert manifest["last_open_ms"] == day + 39 * STEP
    assert manifest["ccxt_ranges"] == [{"start_ms": day, "end_ms": day + 40 * STEP, "rows": 40, "purpose": "tail"}]
    assert manifest["rows"] == 96 + 40 and manifest["gaps"] == [] and manifest["overlap_mismatches"] == 0
    assert exchange.calls[0][3] == day
    exchange.calls.clear()
    assert fetch_tail(cstore, fstore, "BTCUSDT", ("15m",), exchange=exchange, now_ms=NOW_MS) == {"15m": 0, "funding": 0}
    assert exchange.calls == []


def test_bulk_rows_win_over_earlier_ccxt_rows(data_cfg):
    cstore, fstore = CandleStore(data_cfg), FundingStore(data_cfg)
    cstore.write("BTCUSDT", "15m", bulk_frame(day_rows("2026-09-25", 96), "bulk_daily"))
    cstore.write_manifest("BTCUSDT", "15m", {**cstore.manifest("BTCUSDT", "15m"), "last_open_ms": date_ms("2026-09-25") + 95 * STEP})
    exchange = FakeExchange(ccxt_candles(date_ms("2026-09-26"), 96 + 41, price=200.0))
    assert fetch_tail(cstore, fstore, "BTCUSDT", ("15m",), exchange=exchange, now_ms=NOW_MS)["15m"] == 96 + 40
    archive = FakeArchive()
    archive.add_klines("BTCUSDT", "15m", "2026-09-26", day_rows("2026-09-26", 96, price=300.0))
    manifest = sync(cstore, data_cfg, archive, from_date=date(2026, 9, 26))
    frame = cstore.read_frame("BTCUSDT", "15m", date_ms("2026-09-26"), date_ms("2026-09-27"))
    assert len(frame) == 96 and set(frame["source"]) == {"bulk_daily"} and (frame["open"] >= 300.0).all()
    assert manifest["overlap_mismatches"] == 96
    assert manifest["rows"] == 96 * 2 + 40
    assert len(cstore.read_frame("BTCUSDT", "15m")) == 96 * 2 + 40


def test_tail_funding_uses_the_last_stored_interval(data_cfg):
    cstore, fstore = CandleStore(data_cfg), FundingStore(data_cfg)
    last = date_ms("2026-08-31") + 16 * H
    fstore.write("BTCUSDT", funding_frame([(last - 8 * H, 1e-4, 8), (last, 2e-4, 4)]))
    fstore.write_manifest("BTCUSDT", {**fstore.manifest("BTCUSDT"), "last_funding_ms": last, "last_interval_h": 4})
    events = ccxt_funding(last, 200, interval_h=4, jitter_ms=2)  # from the last stored event onward
    exchange = FakeExchange(funding=events)
    fetched = fetch_tail(cstore, fstore, "BTCUSDT", (), exchange=exchange, now_ms=NOW_MS)
    expected = [e for e in events if last < e["timestamp"] - 2 < NOW_MS]
    assert fetched == {"funding": len(expected)}
    frame = fstore.read_frame("BTCUSDT")
    assert len(frame) == 2 + len(expected)
    assert (frame["interval_h"] == np.int8(4)).sum() == 1 + len(expected)
    manifest = fstore.manifest("BTCUSDT")
    assert manifest["ccxt_ranges"] == [{"start_ms": last + 60_000, "end_ms": NOW_MS, "rows": len(expected), "purpose": "tail"}]
    assert manifest["last_interval_h"] == 4 and manifest["rows"] == len(frame)
    assert exchange.calls[0][2] == last + 60_000


def test_tail_skips_series_without_stored_rows(data_cfg):
    cstore, fstore = CandleStore(data_cfg), FundingStore(data_cfg)
    exchange = FakeExchange(ccxt_candles(date_ms("2026-09-26"), 10), funding=ccxt_funding(date_ms("2026-09-26"), 3))
    assert fetch_tail(cstore, fstore, "BTCUSDT", ("15m",), exchange=exchange, now_ms=NOW_MS) == {"15m": 0, "funding": 0}
    assert exchange.calls == []


# --- ccxt gap fill -----------------------------------------------------------------------


def holed_archive():
    """One daily file for 2026-09-20 whose rows 10..19 are absent (an archive hole, not an outage)."""
    rows = day_rows("2026-09-20", 96)
    a = FakeArchive()
    a.add_klines("BTCUSDT", "15m", "2026-09-20", rows[:10] + rows[20:])
    return a


def test_gap_fill_fetches_each_gap_once_and_records_it(data_cfg):
    cstore = CandleStore(data_cfg)
    manifest = sync(cstore, data_cfg, holed_archive(), from_date=date(2026, 9, 20), to_date=date(2026, 9, 20))
    day = date_ms("2026-09-20")
    assert manifest["gaps"] == [{"start_ms": day + 10 * STEP, "end_ms": day + 20 * STEP, "missing": 10}]
    exchange = FakeExchange(ccxt_candles(day, 96, price=100.0))
    assert fetch_gaps(cstore, "BTCUSDT", ("15m",), exchange=exchange) == {"15m": 10}
    manifest = cstore.manifest("BTCUSDT", "15m")
    assert manifest["gaps"] == [] and manifest["rows"] == 96
    assert manifest["ccxt_ranges"] == [{"start_ms": day + 10 * STEP, "end_ms": day + 20 * STEP, "rows": 10, "purpose": "gap"}]
    frame = cstore.read_frame("BTCUSDT", "15m")
    ccxt_rows = frame[frame["source"] == "ccxt"]["open_ms"].to_numpy()
    np.testing.assert_array_equal(ccxt_rows, day + STEP * np.arange(10, 20))
    assert exchange.calls[0][3] == day + 10 * STEP
    exchange.calls.clear()
    assert fetch_gaps(cstore, "BTCUSDT", ("15m",), exchange=exchange) == {"15m": 0}
    assert exchange.calls == []


def test_gap_the_exchange_cannot_fill_stays_a_gap_and_is_not_requeried(data_cfg):
    cstore = CandleStore(data_cfg)
    sync(cstore, data_cfg, holed_archive(), from_date=date(2026, 9, 20), to_date=date(2026, 9, 20))
    exchange = FakeExchange([])  # a genuine outage: the exchange has nothing either
    assert fetch_gaps(cstore, "BTCUSDT", ("15m",), exchange=exchange) == {"15m": 0}
    manifest = cstore.manifest("BTCUSDT", "15m")
    assert len(manifest["gaps"]) == 1 and manifest["ccxt_ranges"][0]["rows"] == 0
    assert manifest["ccxt_ranges"][0]["purpose"] == "gap"
    exchange.calls.clear()
    assert fetch_gaps(cstore, "BTCUSDT", ("15m",), exchange=exchange) == {"15m": 0}
    assert exchange.calls == []


def test_gap_fill_skips_series_without_a_manifest(data_cfg):
    exchange = FakeExchange(ccxt_candles(date_ms("2026-09-20"), 10))
    assert fetch_gaps(CandleStore(data_cfg), "BTCUSDT", ("15m",), exchange=exchange) == {"15m": 0}
    assert exchange.calls == []


def test_run_fetch_gap_flag_fills_archive_holes(data_cfg):
    day = date_ms("2026-09-20")
    exchange = FakeExchange(ccxt_candles(day, 96, price=100.0))
    summary = run_fetch(
        data_cfg, pairs=["BTCUSDT"], tfs=("15m",), from_date=date(2026, 9, 20), to_date=date(2026, 9, 20),
        ccxt_gaps=True, http_get=holed_archive(), exchange_factory=lambda: exchange, today=TODAY, now_ms=NOW_MS,
    )
    assert summary["BTCUSDT"]["ccxt_gaps"] == {"15m": 10}
    assert CandleStore(data_cfg).manifest("BTCUSDT", "15m")["gaps"] == []


# --- run_fetch ---------------------------------------------------------------------------


def test_run_fetch_end_to_end(data_cfg, archive):
    july = date_ms("2026-07-01")
    archive.add_funding("BTCUSDT", "2026-07", [(july + k * 8 * H, 8, 1e-4) for k in range(93)])
    archive.add_funding("BTCUSDT", "2026-08", [(date_ms("2026-08-01") + k * 8 * H, 8, 1e-4) for k in range(93)])
    exchange = FakeExchange(
        ccxt_candles(date_ms("2019-10-01"), 110 * 96) + ccxt_candles(date_ms("2026-09-25"), 2 * 96 + 41, price=200.0),
        funding=ccxt_funding(date_ms("2026-09-01"), 100),
    )
    factories = []

    def factory():
        factories.append(exchange)
        return exchange

    summary = run_fetch(
        data_cfg, pairs=["BTCUSDT"], tfs=("15m",), from_date=date(2026, 7, 1), ccxt_head=True, ccxt_tail=True,
        http_get=archive, exchange_factory=factory, today=TODAY, now_ms=NOW_MS,
    )
    assert factories == [exchange]
    entry = summary["BTCUSDT"]
    assert entry["15m"]["rows"] == 32 and entry["15m"]["files"] == 8 and entry["15m"]["missing"] == 20
    assert entry["funding"]["rows"] == 186 and entry["funding"]["files"] == 2
    assert entry["ccxt_head_rows"] == 0  # from_date 2026-07-01 is past the archive start
    assert entry["ccxt_tail"]["15m"] == 2 * 96 + 40 - 4  # 2026-09-25 01:00 .. 2026-09-27 09:45 inclusive
    store = CandleStore(data_cfg)
    manifest = store.manifest("BTCUSDT", "15m")
    assert manifest["rows"] == entry["15m"]["rows"] + entry["ccxt_tail"]["15m"]
    assert len(manifest["ccxt_ranges"]) == 1 and manifest["ccxt_ranges"][0]["purpose"] == "tail"
    assert FundingStore(data_cfg).manifest("BTCUSDT")["ccxt_ranges"][0]["purpose"] == "tail"
    assert entry["ccxt_tail"]["funding"] > 0


def test_run_fetch_rejects_unknown_pair_and_timeframe(data_cfg):
    with pytest.raises(ValueError, match="XRPUSDT"):
        run_fetch(data_cfg, pairs=["XRPUSDT"], http_get=FakeArchive(), today=TODAY, now_ms=NOW_MS)
    with pytest.raises(ValueError, match="timeframe"):
        run_fetch(data_cfg, pairs=["BTCUSDT"], tfs=("1h",), http_get=FakeArchive(), today=TODAY, now_ms=NOW_MS)


def test_run_fetch_without_ccxt_flags_never_builds_an_exchange(data_cfg, archive):
    def factory():
        raise AssertionError("exchange must not be built")

    summary = run_fetch(
        data_cfg, pairs=["BTCUSDT"], tfs=("15m",), from_date=date(2026, 9, 20), http_get=archive,
        exchange_factory=factory, today=TODAY, now_ms=NOW_MS,
    )
    assert summary["BTCUSDT"]["15m"]["rows"] == 24 and "ccxt_tail" not in summary["BTCUSDT"]


# --- markets and delisted pairs (trend Phase 0 §0.1) ----------------------------------------


@pytest.fixture
def spot_cfg(data_cfg):
    return DataConfig(**{**to_dict(data_cfg), "insample_start": "2017-08-01", "insample_end": "2019-12-31",
                         "holdout_start": "2020-01-01", "warmup_start": "2017-08-01",
                         "listing": {"BTCUSDT": "2017-08-17"}})


def spot_archive():
    a = FakeArchive()
    a.add_klines("BTCUSDT", "15m", "2017-12", day_rows("2017-12-01"), market="spot")
    a.add_klines("BTCUSDT", "15m", "2018-01", day_rows("2018-01-01"), market="spot")
    return a


def spot_fetch(cfg, archive, **kw):
    kw = {"from_date": date(2017, 12, 1), "to_date": date(2018, 1, 31), **kw}
    return run_fetch(cfg, pairs=["BTCUSDT"], tfs=("15m",), market="spot", today=TODAY, now_ms=NOW_MS,
                     http_get=archive, **kw)


def test_spot_fetch_writes_spot_store_and_no_funding(spot_cfg, tmp_data_dir):
    archive = spot_archive()
    summary = spot_fetch(spot_cfg, archive)
    assert summary["BTCUSDT"]["15m"]["rows"] == 8 and summary["BTCUSDT"]["15m"]["files"] == 2
    assert "funding" not in summary["BTCUSDT"]
    assert sorted(p.name for p in (tmp_data_dir / "spot" / "candles" / "BTCUSDT" / "15m").glob("*.parquet")) == [
        "2017.parquet", "2018.parquet",
    ]
    assert not (tmp_data_dir / "candles").exists() and not (tmp_data_dir / "funding").exists()
    assert not any("fundingRate" in u for u in archive.calls)
    assert all("/data/spot/" in u for u in archive.calls)
    assert CandleStore(spot_cfg, market="spot").manifest("BTCUSDT", "15m")["rows"] == 8


def test_spot_months_before_archive_start_not_requested(spot_cfg):
    cfg = DataConfig(**{**to_dict(spot_cfg), "listing": {"BTCUSDT": "2017-01-01"}})
    archive = spot_archive()
    spot_fetch(cfg, archive, from_date=date(2017, 1, 1))
    assert min(period_of(n) for n in archive.zip_gets()) == "2017-08"  # the spot archive start, not 2017-01


def test_spot_refuses_ccxt_flags(spot_cfg):
    for flag in ("ccxt_head", "ccxt_tail", "ccxt_gaps"):
        with pytest.raises(ValueError, match="spot"):
            spot_fetch(spot_cfg, spot_archive(), **{flag: True})


def test_stale_pair_skips_ccxt_tail_and_gaps(data_cfg):
    old = day_rows("2026-08-20", 96)
    a = FakeArchive()
    a.add_klines("ETHUSDT", "15m", "2026-08-20", old[:10] + old[20:])  # ends 38 days before TODAY, one gap
    a.add_klines("BTCUSDT", "15m", "2026-09-25", day_rows("2026-09-25", 96))
    exchange = FakeExchange(ccxt_candles(date_ms("2026-08-20"), 40 * 96))
    summary = run_fetch(
        data_cfg, pairs=["ETHUSDT", "BTCUSDT"], tfs=("15m",), from_date=date(2026, 8, 20), ccxt_tail=True,
        ccxt_gaps=True, http_get=a, exchange_factory=lambda: exchange, today=TODAY, now_ms=NOW_MS,
    )
    assert summary["ETHUSDT"]["ccxt_skipped"] == "delisted"
    assert "ccxt_tail" not in summary["ETHUSDT"] and "ccxt_gaps" not in summary["ETHUSDT"]
    assert all(call[1] != "ETH/USDT:USDT" for call in exchange.calls)
    assert summary["BTCUSDT"]["ccxt_tail"]["15m"] > 0 and "ccxt_skipped" not in summary["BTCUSDT"]


def test_off_grid_archive_rows_are_snapped_and_recorded(spot_cfg):
    t = date_ms("2018-02-09")
    rows = kline_rows(t, 2, STEP) + kline_rows(t + 3 * STEP - 105_211, 3, STEP, price=200.0)  # shifted restart grid
    a = FakeArchive()
    a.add_klines("BTCUSDT", "15m", "2018-02", rows, market="spot")
    summary = spot_fetch(spot_cfg, a, from_date=date(2018, 2, 1), to_date=date(2018, 2, 28))
    assert summary["BTCUSDT"]["15m"]["rows"] == 5 and summary["BTCUSDT"]["15m"]["gaps"] == 1
    store = CandleStore(spot_cfg, market="spot")
    assert store.read_frame("BTCUSDT", "15m")["open_ms"].tolist() == [t, t + STEP] + [t + k * STEP for k in (3, 4, 5)]
    entry = store.manifest("BTCUSDT", "15m")["files"][0]
    assert entry["name"] == "BTCUSDT-15m-2018-02.zip" and entry["snapped"] == 3


def test_staleness_is_measured_against_the_requested_end(data_cfg):
    old = day_rows("2026-08-20", 96)
    a = FakeArchive()
    a.add_klines("BTCUSDT", "15m", "2026-08-20", old[:10] + old[20:])  # an active pair fetched over a past window
    exchange = FakeExchange(ccxt_candles(date_ms("2026-08-20"), 96))
    summary = run_fetch(
        data_cfg, pairs=["BTCUSDT"], tfs=("15m",), from_date=date(2026, 8, 20), to_date=date(2026, 8, 20),
        ccxt_gaps=True, http_get=a, exchange_factory=lambda: exchange, today=TODAY, now_ms=NOW_MS,
    )
    assert "ccxt_skipped" not in summary["BTCUSDT"] and summary["BTCUSDT"]["ccxt_gaps"] == {"15m": 10}


def test_is_stale():
    assert not is_stale({"last_open_ms": None}, TODAY)
    assert not is_stale({"last_open_ms": date_ms("2026-09-20")}, TODAY)  # exactly 7 days
    assert is_stale({"last_open_ms": date_ms("2026-09-20") - STEP}, TODAY)


def test_validate_spot(spot_cfg):
    spot_fetch(spot_cfg, spot_archive())
    report = run_validate(spot_cfg, ["BTCUSDT"], market="spot")
    assert report["BTCUSDT"]["15m"]["rows"] == 8 and "funding" not in report["BTCUSDT"]
    assert report["BTCUSDT"]["15m"]["gaps"] == 1
    assert len(CandleStore(spot_cfg, market="spot").manifest("BTCUSDT", "15m")["gaps"]) == 1


# --- run_validate ------------------------------------------------------------------------


def test_run_validate_writes_reports_into_manifests(data_cfg):
    cstore, fstore = CandleStore(data_cfg), FundingStore(data_cfg)
    t0 = date_ms("2024-05-01")
    c1m = random_walk(2 * 1440, seed=3, start_ms=t0, step_ms=60_000, tf="1m")
    idx = np.arange(2 * 96 * 15).reshape(-1, 15)
    rows15 = [
        (int(c1m.ts[i[0]]), c1m.o[i[0]], c1m.h[i].max(), c1m.l[i].min(), c1m.c[i[-1]], c1m.v[i].sum(), 0.0, 1, 0.0)
        for i in idx
    ]
    rows15[10] = rows15[10][:2] + (rows15[10][2] + 1.0,) + rows15[10][3:]  # one altered high
    rows1 = [(int(t), o, h, l, c, v, 0.0, 1, 0.0) for t, o, h, l, c, v in zip(c1m.ts, c1m.o, c1m.h, c1m.l, c1m.c, c1m.v)]
    del rows1[100:103]  # a 3-minute hole in the 1m series
    cstore.write("BTCUSDT", "1m", bulk_frame(rows1))
    cstore.write("BTCUSDT", "15m", bulk_frame(rows15))
    fstore.write("BTCUSDT", funding_frame([(t0, 1e-4, 8), (t0 + 8 * H, 1e-4, 8), (t0 + 16 * H, 1e-4, 3)]))
    report = run_validate(data_cfg, ["BTCUSDT", "ETHUSDT"])
    entry = report["BTCUSDT"]
    assert entry["1m"] == {"rows": 2 * 1440 - 3, "gaps": 1, "missing_slots": 3}
    assert entry["15m"]["rows"] == 192 and entry["15m"]["gaps"] == 0
    cons = entry["15m"]["consistency_1m_15m"]
    assert cons["candles_compared"] == 191 and cons["mismatching_candles"] == 1 and cons["months_checked"] == 1
    assert entry["funding"] == {"rows": 3, "intervals": {3: 1, 8: 2}, "bad_intervals": [3]}
    assert report["ETHUSDT"] == {}
    m15 = cstore.manifest("BTCUSDT", "15m")
    assert m15["consistency_1m_15m"] == cons and m15["rows"] == 192 and m15["gaps"] == []
    m1 = cstore.manifest("BTCUSDT", "1m")
    assert m1["gaps"] == [{"start_ms": t0 + 100 * 60_000, "end_ms": t0 + 103 * 60_000, "missing": 3}]
    assert m1["consistency_1m_15m"] is None
    fm = fstore.manifest("BTCUSDT")
    assert fm["intervals"] == {"3": 1, "8": 2} and fm["bad_intervals"] == [3] and fm["last_interval_h"] == 3


def test_run_validate_hard_failure_raises(data_cfg):
    cstore = CandleStore(data_cfg)
    cstore.write("BTCUSDT", "15m", bulk_frame([(date_ms("2024-05-01") + 1, 1.0, 2.0, 0.5, 1.5, 1.0, 0.0, 1, 0.0)]))
    with pytest.raises(ValueError, match="grid"):
        run_validate(data_cfg, ["BTCUSDT"])


def test_manifest_refresh_reads_only_the_columns_it_needs(data_cfg, archive, monkeypatch):
    seen = []
    real = CandleStore.read_frame

    def spy(self, pair, tf, start_ms=None, end_ms=None, columns=None):
        seen.append(columns)
        return real(self, pair, tf, start_ms, end_ms, columns=columns)

    monkeypatch.setattr(CandleStore, "read_frame", spy)
    sync(CandleStore(data_cfg), data_cfg, archive, from_date=date(2026, 9, 20))
    assert seen and all(c == ["open_ms"] for c in seen)
