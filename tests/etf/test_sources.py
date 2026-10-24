"""ETF sources (ETF spec Phase 0 §0.2): parsers on recorded fixtures, retries; no network."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from perpbt.data.store import date_ms
from perpbt.etf import sources
from perpbt.etf.sources import (
    FRED_HEADERS, NASDAQ_HEADERS, SourceError, fetch_fred, fetch_nasdaq_dividends, fetch_nasdaq_history,
    fetch_yahoo, parse_fred_csv, parse_nasdaq_dividends, parse_nasdaq_history, parse_yahoo_csv, with_retry,
    yahoo_csv,
)
from perpbt.etf.store import ACTION_COLUMNS, PRICE_COLUMNS

FIX = Path(__file__).parent / "fixtures"


def _bytes(name: str) -> bytes:
    return (FIX / name).read_bytes()


def test_parse_yahoo_iwm_split_and_dividend():
    prices, actions = parse_yahoo_csv(_bytes("yahoo_IWM.csv"), "IWM")
    assert tuple(prices.columns) == PRICE_COLUMNS and tuple(actions.columns) == ACTION_COLUMNS
    assert prices["day_ms"].iloc[0] == date_ms("2005-06-06") and len(prices) == 12
    assert prices["day_ms"].dtype == np.int64
    assert actions.to_dict("records") == [
        {"day_ms": date_ms("2005-06-09"), "dividend": 0.0, "split": 2.0, "capital_gain": 0.0},
        {"day_ms": date_ms("2005-06-20"), "dividend": 0.146, "split": 0.0, "capital_gain": 0.0},
    ]
    assert prices.loc[prices["day_ms"] == date_ms("2005-06-20"), "close"].item() == pytest.approx(63.68, abs=1e-4)


def test_parse_yahoo_rejects_other_columns():
    raw = _bytes("yahoo_IWM.csv").replace(b"Capital Gains", b"Other")
    with pytest.raises(SourceError, match="expected columns"):
        parse_yahoo_csv(raw, "IWM")


def test_yahoo_csv_takes_the_exchange_date_and_round_trips():
    idx = pd.DatetimeIndex(["2005-06-09", "2005-06-10"]).tz_localize("America/New_York")
    hist = pd.DataFrame({"Open": [1.1, 2.2], "High": [1.3, 2.4], "Low": [1.0, 2.0], "Close": [1.2, 2.3],
                         "Adj Close": [1.0, 2.0], "Volume": [10, 20], "Dividends": [0.0, 0.05],
                         "Stock Splits": [2.0, 0.0]}, index=idx)  # no Capital Gains column: added as 0
    prices, actions = parse_yahoo_csv(yahoo_csv(hist), "X")
    assert prices["day_ms"].tolist() == [date_ms("2005-06-09"), date_ms("2005-06-10")]
    assert prices["open"].tolist() == [1.1, 2.2]
    assert actions[["dividend", "split"]].to_numpy().tolist() == [[0.0, 2.0], [0.05, 0.0]]
    with pytest.raises(SourceError, match="empty"):
        yahoo_csv(hist.iloc[:0])


def test_fetch_yahoo_retries_an_empty_history():
    calls = []

    def history(ticker, start, end):
        calls.append((ticker, start, end))
        if len(calls) < 3:
            return pd.DataFrame()
        idx = pd.DatetimeIndex(["2005-06-09"]).tz_localize("America/New_York")
        return pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Adj Close": [1.0],
                             "Volume": [1], "Dividends": [0.0], "Stock Splits": [0.0], "Capital Gains": [0.0]},
                            index=idx)

    sleeps = []
    got = fetch_yahoo("SPY", "2004-11-18", "2026-10-24", history=history, sleep=sleeps.append)
    assert calls[0] == ("SPY", "2004-11-18", "2026-10-24") and len(calls) == 3
    assert sleeps == [2.0, 4.0]
    assert got.source.source == "yahoo" and got.source.params["end"] == "2026-10-24"
    assert len(got.source.raw_sha256) == 64


def test_with_retry_raises_after_three_retries():
    sleeps = []

    def boom():
        raise OSError("down")

    with pytest.raises(SourceError, match="failed after 4 attempts: down"):
        with_retry(boom, what="x", sleep=sleeps.append)
    assert sleeps == [2.0, 4.0, 8.0]


def test_parse_fred_drops_holidays():
    r = parse_fred_csv(_bytes("fred_DTB3.csv"), "DTB3")
    assert r["day_ms"].iloc[0] == date_ms("2004-11-01") and r["rate_pct"].iloc[0] == 1.96
    assert date_ms("2004-11-11") not in set(r["day_ms"]) and date_ms("2004-11-25") not in set(r["day_ms"])
    assert len(r) == 20
    assert len(parse_fred_csv(b"DATE,DTB3\n2004-11-01,1.96\n2004-11-02,.\n", "DTB3")) == 1  # the older format
    with pytest.raises(SourceError, match="unexpected header"):
        parse_fred_csv(b"observation_date,DGS10\n", "DTB3")
    with pytest.raises(SourceError, match="line 2"):
        parse_fred_csv(b"observation_date,DTB3\n2004-13-01,1.0\n", "DTB3")


def test_fetch_fred_and_nasdaq_send_their_headers():
    seen = []

    def get(url, headers):
        seen.append((url, headers))
        return b"{}"

    fetch_fred("DTB3", "2004-11-01", get=get)
    fetch_nasdaq_history("SPY", "2004-11-18", "2026-10-23", get=get)
    fetch_nasdaq_dividends("QQQ", get=get)
    assert seen[0] == ("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3&cosd=2004-11-01", FRED_HEADERS)
    assert "fromdate=2004-11-18&todate=2026-10-23" in seen[1][0] and seen[1][1] is NASDAQ_HEADERS
    assert seen[2][0].endswith("/QQQ/dividends?assetclass=etf")


def test_parse_nasdaq_history_oldest_first():
    x = parse_nasdaq_history(_bytes("nasdaq_history_SPY.json"), "SPY")
    assert list(x.columns) == ["day_ms", "open", "close"] and len(x) == 5
    assert x["day_ms"].is_monotonic_increasing and x["day_ms"].iloc[-1] == date_ms("2026-10-23")
    assert x["close"].iloc[-1] == 759.97
    with pytest.raises(SourceError, match="no rows"):
        parse_nasdaq_history(b'{"data": null}', "SPY")
    with pytest.raises(SourceError, match="not JSON"):
        parse_nasdaq_history(b"<html>", "SPY")


def test_nasdaq_numbers():
    assert sources._number("$1,234.50", "x") == 1234.5
    assert np.isnan(sources._number("N/A", "x"))
    with pytest.raises(SourceError, match="bad number"):
        sources._number("abc", "x")


def test_parse_nasdaq_dividends():
    d = parse_nasdaq_dividends(_bytes("nasdaq_dividends_QQQ.json"), "QQQ")
    assert len(d) == 3 and d["day_ms"].is_monotonic_increasing
    assert d["day_ms"].iloc[-1] == date_ms("2026-09-21") and d["dividend"].iloc[-1] == 0.75143
    assert parse_nasdaq_dividends(_bytes("nasdaq_dividends_SPY.json"), "SPY") is None  # not Nasdaq-listed


def test_http_get_is_the_default_and_no_test_touches_the_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network")

    monkeypatch.setattr(sources.urllib.request, "urlopen", refuse)
    with pytest.raises(SourceError, match="network"):
        fetch_fred("DTB3", "2004-11-01", sleep=lambda s: None)
