"""Bulk archive (data.binance.vision): names, URLs, checksum download, CSV parsing.

Everything here is pure or takes an injectable ``http_get`` so tests never touch
the network. Zips are verified and parsed in memory; nothing is written to disk.
Parsed frames use the storage column names of phase-1-data.md §1.2 (without
``pair``, ``tf`` and ``source``, which the caller adds).
"""
from __future__ import annotations

import calendar
import hashlib
import io
import re
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from datetime import date

import numpy as np
import pandas as pd

BASE_URL = "https://data.binance.vision/data/futures/um"
ARCHIVE_START = date(2020, 1, 1)  # first month with USDT-M futures files (2019-12 is 404; verified 2026-09-26)
MICROSECOND_THRESHOLD = 100_000_000_000_000  # 1e14: a timestamp above this is microseconds (spec §1.1)

KLINE_CSV_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]
FUNDING_CSV_COLUMNS = ["calc_time", "funding_interval_hours", "last_funding_rate"]

KLINE_FRAME_COLUMNS = [
    "open_ms", "open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_buy_volume",
]
FUNDING_FRAME_COLUMNS = ["funding_ms", "rate", "interval_h"]

HttpGet = Callable[[str], "bytes | None"]

_PERIOD = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")
_NAME_PERIOD = re.compile(r"-(\d{4}-\d{2}(?:-\d{2})?)\.zip$")


class DownloadError(RuntimeError):
    """An HTTP failure other than 404, a network error, or a zip without a .CHECKSUM."""


class ChecksumError(RuntimeError):
    """The downloaded zip does not match its published sha256."""


# --- names, URLs, periods ------------------------------------------------------------


def _scope(period: str) -> str:
    m = _PERIOD.fullmatch(period)
    if not m:
        raise ValueError(f"period must be 'YYYY-MM' or 'YYYY-MM-DD', got {period!r}")
    return "daily" if m[3] else "monthly"


def kline_name(pair: str, tf: str, period: str) -> str:
    return f"{pair}-{tf}-{period}.zip"


def kline_url(pair: str, tf: str, period: str) -> str:
    return f"{BASE_URL}/{_scope(period)}/klines/{pair}/{tf}/{kline_name(pair, tf, period)}"


def funding_name(pair: str, period: str) -> str:
    return f"{pair}-fundingRate-{period}.zip"


def funding_url(pair: str, period: str) -> str:
    if _scope(period) != "monthly":
        raise ValueError(f"funding files are monthly only, got period {period!r}")
    return f"{BASE_URL}/monthly/fundingRate/{pair}/{funding_name(pair, period)}"


def period_of(name: str) -> str:
    """The ``YYYY-MM`` or ``YYYY-MM-DD`` period in an archive file name."""
    m = _NAME_PERIOD.search(name)
    if not m:
        raise ValueError(f"no period in archive file name {name!r}")
    return m[1]


def period_end(name: str) -> date:
    """Last calendar day covered by the archive file ``name``."""
    period = period_of(name)
    y, mo = int(period[:4]), int(period[5:7])
    if len(period) == 10:
        return date(y, mo, int(period[8:10]))
    return date(y, mo, calendar.monthrange(y, mo)[1])


def months_between(start: date, end: date) -> list[str]:
    """'YYYY-MM' for every month from ``start``'s month to ``end``'s month, inclusive; [] if start > end."""
    out: list[str] = []
    if start > end:
        return out
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def days_of_month(ym: str, *, first: date, until: date) -> list[str]:
    """'YYYY-MM-DD' for every day of month ``ym`` with ``first <= day < until``."""
    if _scope(ym) != "monthly":
        raise ValueError(f"expected 'YYYY-MM', got {ym!r}")
    y, m = int(ym[:4]), int(ym[5:7])
    n = calendar.monthrange(y, m)[1]
    days = (date(y, m, k + 1) for k in range(n))
    return [d.isoformat() for d in days if first <= d < until]


# --- HTTP and checksums --------------------------------------------------------------


def urllib_get(url: str, *, timeout: float = 120.0, attempts: int = 3) -> bytes | None:
    """GET ``url``; None on 404; DownloadError after ``attempts`` transient failures.

    Network errors, timeouts and 5xx responses are retried with a short
    backoff (a DNS hiccup must not abort a 20-minute run); 404 and other
    4xx responses are not.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "perpbt/0.0"})
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code < 500:
                raise DownloadError(f"GET {url}: HTTP {e.code}") from e
            last = DownloadError(f"GET {url}: HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = DownloadError(f"GET {url}: {e}")
        if attempt < attempts:
            time.sleep(2.0 * attempt)
    assert last is not None
    raise last


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_checksum(text: str, name: str) -> str:
    """The hex digest in a ``.CHECKSUM`` file (``<sha256>  <name>``); the name must match."""
    parts = text.split()
    if len(parts) < 2 or len(parts[0]) != 64 or not all(c in "0123456789abcdefABCDEF" for c in parts[0]):
        raise ChecksumError(f"{name}.CHECKSUM: unexpected content {text!r}")
    if parts[1].lstrip("*") != name:
        raise ChecksumError(f"{name}.CHECKSUM names a different file: {parts[1]!r}")
    return parts[0].lower()


def download_verified(url: str, *, http_get: HttpGet = urllib_get) -> bytes | None:
    """Fetch ``url`` and ``url + '.CHECKSUM'``; return the verified bytes, or None on 404.

    Raises ChecksumError on a digest mismatch and DownloadError when the zip
    exists but its checksum file does not. Nothing touches the disk.
    """
    name = url.rsplit("/", 1)[-1]
    data = http_get(url)
    if data is None:
        return None
    checksum = http_get(url + ".CHECKSUM")
    if checksum is None:
        raise DownloadError(f"{name}: the zip exists but its .CHECKSUM file is missing")
    expected = parse_checksum(checksum.decode("ascii", "replace"), name)
    actual = sha256_bytes(data)
    if actual != expected:
        raise ChecksumError(f"{name}: sha256 {actual} does not match the published {expected}")
    return data


# --- parsing --------------------------------------------------------------------------


def _single_csv(data: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"expected exactly one CSV in the zip, found {names}")
        return zf.read(names[0])


def _has_header(csv: bytes) -> bool:
    first = csv.split(b"\n", 1)[0].strip()
    return bool(first) and not first[:1].isdigit()


def _read_csv(csv: bytes, columns: list[str]) -> pd.DataFrame:
    """Read an archive CSV with or without its header row; empty input gives an empty frame."""
    body = csv.split(b"\n", 1)[1] if _has_header(csv) else csv
    if not body.strip():
        return pd.DataFrame({c: pd.Series(dtype=object) for c in columns})
    frame = pd.read_csv(io.BytesIO(body), header=None)
    if frame.shape[1] != len(columns):
        raise ValueError(f"expected {len(columns)} CSV columns, got {frame.shape[1]}")
    frame.columns = columns
    return frame


def _to_ms(values: pd.Series) -> np.ndarray:
    ts = values.to_numpy(dtype=np.int64)
    if len(ts) and ts.max() > MICROSECOND_THRESHOLD:
        ts = ts // 1000
    return ts


def parse_klines_csv(csv: bytes) -> pd.DataFrame:
    """Kline CSV bytes (header optional, ms or µs timestamps) → frame with KLINE_FRAME_COLUMNS."""
    raw = _read_csv(csv, KLINE_CSV_COLUMNS)
    out = pd.DataFrame(
        {
            "open_ms": _to_ms(raw["open_time"]),
            "open": raw["open"].to_numpy(dtype=np.float64),
            "high": raw["high"].to_numpy(dtype=np.float64),
            "low": raw["low"].to_numpy(dtype=np.float64),
            "close": raw["close"].to_numpy(dtype=np.float64),
            "volume": raw["volume"].to_numpy(dtype=np.float64),
            "quote_volume": raw["quote_volume"].to_numpy(dtype=np.float64),
            "trades": raw["count"].to_numpy(dtype=np.int64),
            "taker_buy_volume": raw["taker_buy_volume"].to_numpy(dtype=np.float64),
        }
    )
    return out[KLINE_FRAME_COLUMNS]


def parse_klines_zip(data: bytes) -> pd.DataFrame:
    return parse_klines_csv(_single_csv(data))


def round_to_minute(ms: np.ndarray) -> np.ndarray:
    """Nearest minute in ms (halves round up)."""
    ms = np.asarray(ms, dtype=np.int64)
    return (ms + 30_000) // 60_000 * 60_000


def parse_funding_csv(csv: bytes) -> pd.DataFrame:
    """Funding CSV bytes → frame with FUNDING_FRAME_COLUMNS; ``funding_ms`` is minute-rounded.

    Two events rounding to the same minute would later collapse into one stored
    row, so duplicates raise here.
    """
    raw = _read_csv(csv, FUNDING_CSV_COLUMNS)
    funding_ms = round_to_minute(_to_ms(raw["calc_time"]))
    interval = raw["funding_interval_hours"].to_numpy(dtype=np.int64)
    if len(interval) and (interval.min() < 1 or interval.max() > 127):
        raise ValueError(f"funding interval out of range: {sorted(set(interval.tolist()))}")
    if len(funding_ms) != len(np.unique(funding_ms)):
        raise ValueError("duplicate funding timestamps after rounding to the minute")
    out = pd.DataFrame(
        {
            "funding_ms": funding_ms,
            "rate": raw["last_funding_rate"].to_numpy(dtype=np.float64),
            "interval_h": interval.astype(np.int8),
        }
    )
    return out[FUNDING_FRAME_COLUMNS]


def parse_funding_zip(data: bytes) -> pd.DataFrame:
    return parse_funding_csv(_single_csv(data))
