"""ccxt paging, symbol mapping and the head range (perpbt.data.ccxt_fetch)."""
from datetime import date

import numpy as np
import pytest

from perpbt.data.bulk import FUNDING_FRAME_COLUMNS, KLINE_FRAME_COLUMNS
from perpbt.data.ccxt_fetch import ccxt_symbol, fetch_funding_range, fetch_ohlcv_range, head_range, make_exchange
from perpbt.data.store import date_ms
from tests.fake_exchange import FakeExchange, StuckExchange, ccxt_candles, ccxt_funding

T0 = 1_577_836_800_000
STEP = 900_000
H = 3_600_000


def test_ccxt_symbol():
    assert ccxt_symbol("BTCUSDT") == "BTC/USDT:USDT"
    assert ccxt_symbol("SOLUSDT") == "SOL/USDT:USDT"
    with pytest.raises(ValueError):
        ccxt_symbol("BTCBUSD")
    with pytest.raises(ValueError):
        ccxt_symbol("USDT")


def test_pages_merge_without_duplicates_or_gaps():
    ex = FakeExchange(ccxt_candles(T0, 3500))
    frame = fetch_ohlcv_range(ex, "BTCUSDT", "15m", T0, T0 + 3500 * STEP, limit=1500)
    assert len(frame) == 3500
    assert frame["open_ms"].tolist() == [T0 + k * STEP for k in range(3500)]
    assert [c[3] for c in ex.calls] == [T0, T0 + 1500 * STEP, T0 + 3000 * STEP]
    assert all(c[1] == "BTC/USDT:USDT" and c[2] == "15m" and c[4] == 1500 for c in ex.calls)
    assert list(frame.columns) == KLINE_FRAME_COLUMNS + ["source"]
    assert frame["open_ms"].dtype == np.int64 and frame["trades"].dtype == np.int64
    assert (frame["source"] == "ccxt").all()
    assert frame["quote_volume"].isna().all() and frame["taker_buy_volume"].isna().all()
    assert (frame["trades"] == 0).all()
    assert frame["close"].tolist()[:2] == [100.5, 101.5]


def test_range_is_half_open_and_drops_rows_at_or_past_end():
    ex = FakeExchange(ccxt_candles(T0, 10))
    frame = fetch_ohlcv_range(ex, "BTCUSDT", "15m", T0 + STEP, T0 + 3 * STEP)
    assert frame["open_ms"].tolist() == [T0 + STEP, T0 + 2 * STEP]


def test_empty_exchange_gives_empty_frame_with_columns():
    frame = fetch_ohlcv_range(FakeExchange([]), "BTCUSDT", "15m", T0, T0 + 5 * STEP)
    assert len(frame) == 0 and list(frame.columns) == KLINE_FRAME_COLUMNS + ["source"]
    assert frame["open_ms"].dtype == np.int64


def test_exchange_that_ignores_since_does_not_loop_forever():
    ex = StuckExchange(ccxt_candles(T0, 5))
    frame = fetch_ohlcv_range(ex, "BTCUSDT", "15m", T0, T0 + 100 * STEP, limit=5)
    assert frame["open_ms"].tolist() == [T0 + k * STEP for k in range(5)]
    assert len(ex.calls) == 2


def test_1m_paging_uses_the_1m_step():
    ex = FakeExchange(ccxt_candles(T0, 20, "1m"), tf="1m")
    frame = fetch_ohlcv_range(ex, "BTCUSDT", "1m", T0, T0 + 20 * 60_000, limit=8)
    assert len(frame) == 20
    assert [c[3] for c in ex.calls] == [T0, T0 + 8 * 60_000, T0 + 16 * 60_000]


def test_funding_range_rounds_stamps_interval_and_pages():
    ex = FakeExchange(funding=ccxt_funding(T0, 7, interval_h=8, jitter_ms=3))
    frame = fetch_funding_range(ex, "BTCUSDT", T0 + 8 * H, T0 + 40 * H, interval_h=8, limit=3)
    assert list(frame.columns) == FUNDING_FRAME_COLUMNS + ["source"]
    assert frame["funding_ms"].tolist() == [T0 + 8 * H, T0 + 16 * H, T0 + 24 * H, T0 + 32 * H]
    assert frame["interval_h"].dtype == np.int8 and (frame["interval_h"] == 8).all()
    assert (frame["source"] == "ccxt").all()
    assert frame["rate"].tolist() == pytest.approx([2e-4, 3e-4, 4e-4, 5e-4])
    assert [c[2] for c in ex.calls][:2] == [T0 + 8 * H, T0 + 24 * H + 3 + 1]


def test_funding_range_empty():
    frame = fetch_funding_range(FakeExchange(), "BTCUSDT", T0, T0 + H, interval_h=8)
    assert len(frame) == 0 and frame["interval_h"].dtype == np.int8


def test_head_range_per_pair(data_cfg):
    assert head_range(data_cfg, "BTCUSDT") == (date_ms("2019-11-01"), date_ms("2020-01-01"))
    assert head_range(data_cfg, "ETHUSDT") == (date_ms("2019-11-01"), date_ms("2020-01-01"))
    assert head_range(data_cfg, "SOLUSDT") is None
    assert head_range(data_cfg, "BTCUSDT", from_date=date(2019, 12, 1)) == (date_ms("2019-12-01"), date_ms("2020-01-01"))
    assert head_range(data_cfg, "BTCUSDT", from_date=date(2020, 1, 1)) is None
    assert head_range(data_cfg, "BTCUSDT", from_date=date(2021, 6, 1)) is None


def test_make_exchange_builds_binanceusdm():
    pytest.importorskip("ccxt")
    ex = make_exchange()
    assert callable(ex.fetch_ohlcv) and callable(ex.fetch_funding_rate_history)
    assert ex.id == "binanceusdm"
