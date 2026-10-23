"""In-memory stand-in for data.binance.vision, shared by the bulk and fetch tests.

``FakeArchive`` maps URLs to bytes; any URL it does not know is a 404 (``None``),
exactly like ``bulk.urllib_get``. It records every GET so tests can assert
what was (not) downloaded. A GET on the S3 listing endpoint
(``perpbt.trend.universe.LISTING_URL``) answers a ``ListBucketResult`` built
from the stored files, ``page_size`` entries per page, as S3 does.
"""
from __future__ import annotations

import hashlib
import io
import zipfile
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape

from perpbt.data.bulk import funding_name, funding_url, kline_name, kline_url
from perpbt.trend.universe import LISTING_URL

ARCHIVE_HOST = "https://data.binance.vision/"

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
    def __init__(self, *, page_size: int = 1000) -> None:
        self.files: dict[str, bytes] = {}
        self.calls: list[str] = []
        self.page_size = page_size

    def put(self, url: str, data: bytes, *, corrupt_checksum: bool = False) -> None:
        name = url.rsplit("/", 1)[1]
        self.files[url] = data
        digest = checksum_bytes(data, name)
        if corrupt_checksum:
            digest = ("0" * 64 + f"  {name}\n").encode()
        self.files[url + ".CHECKSUM"] = digest

    def add_klines(self, pair, tf, period, rows, *, header=True, unit="ms", corrupt_checksum=False, market="um") -> str:
        name = kline_name(pair, tf, period)
        data = zip_bytes(name[:-4] + ".csv", kline_csv(rows, header=header, unit=unit))
        self.put(kline_url(pair, tf, period, market=market), data, corrupt_checksum=corrupt_checksum)
        return name

    def add_funding(self, pair, period, rows, *, header=True, unit="ms", corrupt_checksum=False) -> str:
        name = funding_name(pair, period)
        data = zip_bytes(name[:-4] + ".csv", funding_csv(rows, header=header, unit=unit))
        self.put(funding_url(pair, period), data, corrupt_checksum=corrupt_checksum)
        return name

    def __call__(self, url: str) -> bytes | None:
        self.calls.append(url)
        if url.startswith(LISTING_URL + "?"):
            return self._listing(url)
        return self.files.get(url)

    def listing_gets(self) -> list[str]:
        return [u for u in self.calls if u.startswith(LISTING_URL + "?")]

    def _listing(self, url: str) -> bytes:
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}
        prefix, delimiter, marker = q.get("prefix", ""), q.get("delimiter") or None, q.get("marker", "")
        keys = sorted(u[len(ARCHIVE_HOST):] for u in self.files if u.startswith(ARCHIVE_HOST))
        entries: dict[str, bool] = {}  # name -> is a common prefix
        for key in keys:
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix):]
            if delimiter and delimiter in rest:
                entries[prefix + rest[: rest.index(delimiter) + 1]] = True
            else:
                entries[key] = False
        names = [n for n in sorted(entries) if n > marker]
        page, truncated = names[: self.page_size], len(names) > self.page_size
        parts = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">',
                 f"<Name>data.binance.vision</Name><Prefix>{escape(prefix)}</Prefix><Marker>{escape(marker)}</Marker>"]
        if truncated and delimiter:  # S3 sends NextMarker only with a delimiter
            parts.append(f"<NextMarker>{escape(page[-1])}</NextMarker>")
        parts.append(f"<IsTruncated>{'true' if truncated else 'false'}</IsTruncated>")
        for n in page:
            if entries[n]:
                parts.append(f"<CommonPrefixes><Prefix>{escape(n)}</Prefix></CommonPrefixes>")
            else:
                parts.append(f"<Contents><Key>{escape(n)}</Key><Size>1</Size></Contents>")
        parts.append("</ListBucketResult>")
        return "".join(parts).encode()

    def zip_gets(self) -> list[str]:
        """Names of the zips requested so far (checksum requests excluded)."""
        return [u.rsplit("/", 1)[1] for u in self.calls if u.endswith(".zip")]
