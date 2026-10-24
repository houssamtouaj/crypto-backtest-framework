"""ETF downloads: Yahoo (yfinance), FRED and Nasdaq (ETF spec Phase 0 §0.2); the only ETF module on the network.

Each ``fetch_*`` returns a ``Fetched``: the raw bytes it received and their
provenance. Each ``parse_*`` is a pure function of those bytes, so tests run
on recorded fixtures and the manifest can hash what was parsed. yfinance
returns a frame, not bytes; ``yahoo_csv`` renders it as CSV and that CSV is
the raw input.

Network calls retry three times with backoff, then raise ``SourceError``;
nothing falls back to another source.

Sources:

- **Yahoo**: daily OHLC split-adjusted, ``Adj Close`` (for the consistency
  check only), dividends per share on the split-adjusted basis, splits and
  capital gains. ``end`` is exclusive.
- **FRED** ``DTB3``: the 3-month T-bill secondary market rate, percent,
  discount basis; an empty or ``.`` value is a holiday and is dropped. FRED
  times out on a browser user agent, so the request sends a plain one.
- **Nasdaq** (the cross-check; Stooq serves a JavaScript bot check since
  2026, Phase 0 §0.9): unadjusted daily open and close for about the last
  ten years, and the dividend history for Nasdaq-listed symbols only
  (``None`` for the others).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from perpbt.data.store import date_ms
from perpbt.etf.store import ACTION_COLUMNS, PRICE_COLUMNS, RATE_COLUMNS, XDIVIDEND_COLUMNS, XPRICE_COLUMNS, FileSource

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}"
NASDAQ_HISTORY_URL = (
    "https://api.nasdaq.com/api/quote/{ticker}/historical?assetclass=etf&fromdate={start}&todate={end}&limit=9999"
)
NASDAQ_DIVIDENDS_URL = "https://api.nasdaq.com/api/quote/{ticker}/dividends?assetclass=etf"
FRED_HEADERS = {"User-Agent": "python-urllib"}
NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
YAHOO_COLUMNS = ("Date", "Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends", "Stock Splits",
                 "Capital Gains")
RETRIES = 3
BACKOFF_S = 2.0
TIMEOUT_S = 60

__all__ = [
    "Fetched", "SourceError", "fetch_fred", "fetch_nasdaq_dividends", "fetch_nasdaq_history", "fetch_yahoo",
    "http_get", "parse_fred_csv", "parse_nasdaq_dividends", "parse_nasdaq_history", "parse_yahoo_csv",
    "with_retry", "yahoo_csv",
]


class SourceError(RuntimeError):
    """A download failed after its retries, or its bytes do not parse."""


@dataclass(frozen=True)
class Fetched:
    raw: bytes
    source: FileSource


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetched(raw: bytes, source: str, url: str, params: dict) -> Fetched:
    return Fetched(raw, FileSource(source=source, url=url, params=params, downloaded_at=_now_iso(),
                                   raw_sha256=hashlib.sha256(raw).hexdigest()))


def with_retry(fn: Callable[[], Any], *, what: str, retries: int = RETRIES, backoff_s: float = BACKOFF_S,
               sleep: Callable[[float], None] = time.sleep) -> Any:
    """``fn()``; on an exception retry ``retries`` times, sleeping ``backoff_s * 2**k``, then raise SourceError."""
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - every failure is retried, then reported
            if attempt == retries:
                raise SourceError(f"{what}: failed after {retries + 1} attempts: {e}") from e
            sleep(backoff_s * 2 ** attempt)
    raise AssertionError("unreachable")


def http_get(url: str, headers: dict[str, str], *, timeout: float = TIMEOUT_S) -> bytes:
    """One GET; an HTTP error status raises."""
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return r.read()


# --- Yahoo -----------------------------------------------------------------------------------------------------

def _yahoo_history(ticker: str, start: str, end: str) -> pd.DataFrame:
    import yfinance  # optional dependency (`pip install .[etf]`); imported here so the rest runs without it

    return yfinance.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=False, actions=True,
                                           raise_errors=True)


def yahoo_csv(history: pd.DataFrame) -> bytes:
    """A yfinance ``history`` frame as CSV bytes: exchange date, then ``YAHOO_COLUMNS``; floats round-trip."""
    if len(history) == 0:
        raise SourceError("yahoo: empty history")
    frame = history.copy()
    if "Capital Gains" not in frame:
        frame["Capital Gains"] = 0.0
    missing = [c for c in YAHOO_COLUMNS[1:] if c not in frame]
    if missing:
        raise SourceError(f"yahoo: history lacks columns {missing}")
    index = frame.index
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)  # the exchange's local date; yfinance indexes daily bars at local midnight
    frame.index = pd.Index([d.date().isoformat() for d in index], name="Date")
    return frame[list(YAHOO_COLUMNS[1:])].to_csv(lineterminator="\n").encode("utf-8")


def fetch_yahoo(ticker: str, start: str, end: str, *,
                history: Callable[[str, str, str], pd.DataFrame] = _yahoo_history,
                sleep: Callable[[float], None] = time.sleep) -> Fetched:
    """Daily bars and actions for ``start <= day < end`` (``end`` exclusive)."""
    raw = with_retry(lambda: yahoo_csv(history(ticker, start, end)), what=f"yahoo {ticker}", sleep=sleep)
    params = {"ticker": ticker, "start": start, "end": end, "interval": "1d", "auto_adjust": False, "actions": True}
    return _fetched(raw, "yahoo", "yfinance Ticker.history", params)


def parse_yahoo_csv(raw: bytes, ticker: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(prices, actions)``: prices on every row, actions on days with a dividend, split or capital gain."""
    try:
        frame = pd.read_csv(io.BytesIO(raw), dtype={"Date": str})
    except (ValueError, pd.errors.ParserError) as e:
        raise SourceError(f"yahoo {ticker}: unreadable CSV: {e}") from e
    if tuple(frame.columns) != YAHOO_COLUMNS:
        raise SourceError(f"yahoo {ticker}: expected columns {list(YAHOO_COLUMNS)}, got {list(frame.columns)}")
    try:
        day = np.array([date_ms(d) for d in frame["Date"]], dtype=np.int64)
    except (TypeError, ValueError) as e:
        raise SourceError(f"yahoo {ticker}: bad date: {e}") from e
    num = frame[list(YAHOO_COLUMNS[1:])].astype("float64")
    prices = pd.DataFrame({"day_ms": day, "open": num["Open"], "high": num["High"], "low": num["Low"],
                           "close": num["Close"], "volume": num["Volume"], "adj_close": num["Adj Close"]},
                          columns=list(PRICE_COLUMNS))
    acts = num[["Dividends", "Stock Splits", "Capital Gains"]].fillna(0.0)
    has = (acts != 0).any(axis=1).to_numpy()
    actions = pd.DataFrame({"day_ms": day[has], "dividend": acts["Dividends"].to_numpy()[has],
                            "split": acts["Stock Splits"].to_numpy()[has],
                            "capital_gain": acts["Capital Gains"].to_numpy()[has]}, columns=list(ACTION_COLUMNS))
    if prices["day_ms"].duplicated().any() or not prices["day_ms"].is_monotonic_increasing:
        raise SourceError(f"yahoo {ticker}: dates are not strictly increasing")
    return prices, actions


# --- FRED ------------------------------------------------------------------------------------------------------

def fetch_fred(series: str, start: str, *, get: Callable[[str, dict], bytes] = http_get,
               sleep: Callable[[float], None] = time.sleep) -> Fetched:
    url = FRED_URL.format(series=series, start=start)
    raw = with_retry(lambda: get(url, FRED_HEADERS), what=f"fred {series}", sleep=sleep)
    return _fetched(raw, "fred", url, {"series": series, "start": start})


def parse_fred_csv(raw: bytes, series: str) -> pd.DataFrame:
    """``day_ms, rate_pct`` for the published days; empty and ``.`` values (holidays) are dropped."""
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
    if not rows or len(rows[0]) != 2 or rows[0][0] not in ("observation_date", "DATE") or rows[0][1] != series:
        raise SourceError(f"fred {series}: unexpected header {rows[0] if rows else None}")
    days, rates = [], []
    for k, row in enumerate(rows[1:], start=2):
        if len(row) != 2:
            raise SourceError(f"fred {series}: line {k}: expected 2 fields, got {row}")
        if row[1].strip() in ("", "."):
            continue
        try:
            days.append(date_ms(row[0]))
            rates.append(float(row[1]))
        except ValueError as e:
            raise SourceError(f"fred {series}: line {k}: {e}") from e
    return pd.DataFrame({"day_ms": np.array(days, dtype=np.int64), "rate_pct": np.array(rates, dtype=np.float64)},
                        columns=list(RATE_COLUMNS))


# --- Nasdaq ----------------------------------------------------------------------------------------------------

def fetch_nasdaq_history(ticker: str, start: str, end: str, *, get: Callable[[str, dict], bytes] = http_get,
                         sleep: Callable[[float], None] = time.sleep) -> Fetched:
    url = NASDAQ_HISTORY_URL.format(ticker=ticker, start=start, end=end)
    raw = with_retry(lambda: get(url, NASDAQ_HEADERS), what=f"nasdaq history {ticker}", sleep=sleep)
    return _fetched(raw, "nasdaq", url, {"ticker": ticker, "start": start, "end": end})


def fetch_nasdaq_dividends(ticker: str, *, get: Callable[[str, dict], bytes] = http_get,
                           sleep: Callable[[float], None] = time.sleep) -> Fetched:
    url = NASDAQ_DIVIDENDS_URL.format(ticker=ticker)
    raw = with_retry(lambda: get(url, NASDAQ_HEADERS), what=f"nasdaq dividends {ticker}", sleep=sleep)
    return _fetched(raw, "nasdaq", url, {"ticker": ticker})


def _json(raw: bytes, what: str) -> dict:
    try:
        out = json.loads(raw)
    except ValueError as e:
        raise SourceError(f"{what}: not JSON: {e}") from e
    if not isinstance(out, dict):
        raise SourceError(f"{what}: expected a JSON object")
    return out


def _us_date(value: str, what: str) -> int:
    try:
        return date_ms(datetime.strptime(value, "%m/%d/%Y").date().isoformat())
    except (TypeError, ValueError) as e:
        raise SourceError(f"{what}: bad date {value!r}") from e


def _number(value: Any, what: str) -> float:
    """A Nasdaq number ("759.97", "$1.23", "1,234.5"); "N/A" or empty is NaN."""
    if value is None:
        return float("nan")
    text = str(value).replace("$", "").replace(",", "").strip()
    if text in ("", "N/A", "--"):
        return float("nan")
    try:
        return float(text)
    except ValueError as e:
        raise SourceError(f"{what}: bad number {value!r}") from e


def parse_nasdaq_history(raw: bytes, ticker: str) -> pd.DataFrame:
    """``day_ms, open, close`` (unadjusted), oldest first."""
    what = f"nasdaq history {ticker}"
    data = _json(raw, what).get("data") or {}
    rows = (data.get("tradesTable") or {}).get("rows")
    if not rows:
        raise SourceError(f"{what}: no rows")
    out = pd.DataFrame({
        "day_ms": np.array([_us_date(r.get("date"), what) for r in rows], dtype=np.int64),
        "open": [_number(r.get("open"), what) for r in rows],
        "close": [_number(r.get("close"), what) for r in rows],
    }, columns=list(XPRICE_COLUMNS)).sort_values("day_ms", kind="stable").reset_index(drop=True)
    if out["day_ms"].duplicated().any():
        raise SourceError(f"{what}: duplicate dates")
    return out


def parse_nasdaq_dividends(raw: bytes, ticker: str) -> pd.DataFrame | None:
    """``day_ms, dividend`` of the cash dividends by ex-date, oldest first; None when Nasdaq serves no history."""
    what = f"nasdaq dividends {ticker}"
    body = _json(raw, what)
    data = body.get("data")
    if not isinstance(data, dict):
        raise SourceError(f"{what}: no data (an error reply?): {body.get('status')}")
    rows = (data.get("dividends") or {}).get("rows")
    if rows is None:
        if "not available" in str(body.get("message") or "").lower():
            return None  # "Dividend History for Non-Nasdaq symbols is not available"
        raise SourceError(f"{what}: no rows and no 'not available' message: {body.get('message')!r}")
    cash = [r for r in rows if str(r.get("type", "")).lower() == "cash"]
    out = pd.DataFrame({
        "day_ms": np.array([_us_date(r.get("exOrEffDate"), what) for r in cash], dtype=np.int64),
        "dividend": np.array([_number(r.get("amount"), what) for r in cash], dtype=np.float64),
    }, columns=list(XDIVIDEND_COLUMNS)).sort_values("day_ms", kind="stable").reset_index(drop=True)
    if out["day_ms"].duplicated().any():
        raise SourceError(f"{what}: two cash dividends on one ex-date")
    return out
