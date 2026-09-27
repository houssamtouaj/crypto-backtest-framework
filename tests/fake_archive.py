"""In-memory stand-in for data.binance.vision, shared by the bulk and fetch tests.

``FakeArchive`` maps URLs to bytes; any URL it does not know is a 404 (``None``),
exactly like ``bulk.urllib_get``. It records every GET so tests can assert
what was (not) downloaded.
"""
from __future__ import annotations

import hashlib
import io
import zipfile

from perpbt.data.bulk import funding_name, funding_url, kline_name, kline_url

KLINE_HEADER = (
    "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
    "taker_buy_volume,taker_buy_quote_volume,ignore"
)
FUNDING_HEADER = "calc_time,funding_interval_hours,last_funding_rate"


def kline_rows(start_ms: int, n: int, step_ms: int, *, price: float = 100.0) -> list[tuple]:
    """``n`` synthetic rows on the grid: (open_ms, o, h, l, c, v, quote_v, trades, taker_buy_v)."""
    rows = []
    for k in range(n):
        o = price + k
        rows.append((start_ms + k * step_ms, o, o + 1.0, o - 1.0, o + 0.5, 10.0 + k, 1000.0 + k, 5 + k, 4.0 + k))
    return rows


def kline_csv(rows, *, header: bool = True, unit: str = "ms") -> bytes:
    """Archive-style kline CSV; ``unit="us"`` writes microsecond timestamps."""
    mult = 1000 if unit == "us" else 1
    lines = [KLINE_HEADER] if header else []
    for open_ms, o, h, l, c, v, qv, n, tbv in rows:
        close_t = (open_ms + 59_999) * mult
        lines.append(f"{open_ms * mult},{o},{h},{l},{c},{v},{close_t},{qv},{n},{tbv},{qv * 0.4},0")
    return ("\n".join(lines) + "\n").encode()


def funding_csv(rows, *, header: bool = True, unit: str = "ms") -> bytes:
    """Archive-style funding CSV from (calc_ms, interval_h, rate) rows."""
    mult = 1000 if unit == "us" else 1
    lines = [FUNDING_HEADER] if header else []
    for calc_ms, interval_h, rate in rows:
        lines.append(f"{calc_ms * mult},{interval_h},{rate:.8f}")
    return ("\n".join(lines) + "\n").encode()


def zip_bytes(csv_name: str, csv: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(csv_name, csv)
    return buf.getvalue()


def checksum_bytes(data: bytes, name: str) -> bytes:
    return f"{hashlib.sha256(data).hexdigest()}  {name}\n".encode()


class FakeArchive:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.calls: list[str] = []

    def put(self, url: str, data: bytes, *, corrupt_checksum: bool = False) -> None:
        name = url.rsplit("/", 1)[1]
        self.files[url] = data
        digest = checksum_bytes(data, name)
        if corrupt_checksum:
            digest = ("0" * 64 + f"  {name}\n").encode()
        self.files[url + ".CHECKSUM"] = digest

    def add_klines(self, pair, tf, period, rows, *, header=True, unit="ms", corrupt_checksum=False) -> str:
        name = kline_name(pair, tf, period)
        data = zip_bytes(name[:-4] + ".csv", kline_csv(rows, header=header, unit=unit))
        self.put(kline_url(pair, tf, period), data, corrupt_checksum=corrupt_checksum)
        return name

    def add_funding(self, pair, period, rows, *, header=True, unit="ms", corrupt_checksum=False) -> str:
        name = funding_name(pair, period)
        data = zip_bytes(name[:-4] + ".csv", funding_csv(rows, header=header, unit=unit))
        self.put(funding_url(pair, period), data, corrupt_checksum=corrupt_checksum)
        return name

    def __call__(self, url: str) -> bytes | None:
        self.calls.append(url)
        return self.files.get(url)

    def zip_gets(self) -> list[str]:
        """Names of the zips requested so far (checksum requests excluded)."""
        return [u.rsplit("/", 1)[1] for u in self.calls if u.endswith(".zip")]
