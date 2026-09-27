"""Bulk archive: names, URLs, period arithmetic, checksum download, CSV parsing."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from perpbt.data import bulk
from perpbt.data.bulk import (
    ChecksumError,
    DownloadError,
    days_of_month,
    download_verified,
    funding_name,
    funding_url,
    kline_name,
    kline_url,
    months_between,
    parse_checksum,
    parse_funding_csv,
    parse_funding_zip,
    parse_klines_csv,
    parse_klines_zip,
    period_end,
    period_of,
    round_to_minute,
)
from tests.fake_archive import FakeArchive, funding_csv, kline_csv, kline_rows, zip_bytes

T0 = 1_577_836_800_000  # 2020-01-01T00:00Z


# --- names and URLs ---------------------------------------------------------------


def test_kline_names_and_urls():
    assert kline_name("BTCUSDT", "15m", "2020-01") == "BTCUSDT-15m-2020-01.zip"
    assert kline_url("BTCUSDT", "15m", "2020-01") == (
        "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/15m/BTCUSDT-15m-2020-01.zip"
    )
    assert kline_url("ETHUSDT", "1m", "2026-09-24") == (
        "https://data.binance.vision/data/futures/um/daily/klines/ETHUSDT/1m/ETHUSDT-1m-2026-09-24.zip"
    )


def test_funding_names_and_urls():
    assert funding_name("BTCUSDT", "2020-01") == "BTCUSDT-fundingRate-2020-01.zip"
    assert funding_url("BTCUSDT", "2020-01") == (
        "https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2020-01.zip"
    )


@pytest.mark.parametrize("period", ["2020", "2020-1", "2020-01-1", "20200101", ""])
def test_bad_period_raises(period):
    with pytest.raises(ValueError, match="period"):
        kline_url("BTCUSDT", "15m", period)


def test_period_of_and_period_end():
    assert period_of("BTCUSDT-15m-2020-02.zip") == "2020-02"
    assert period_of("BTCUSDT-1m-2026-09-24.zip") == "2026-09-24"
    with pytest.raises(ValueError, match="period"):
        period_of("BTCUSDT-15m.zip")
    assert period_end("BTCUSDT-15m-2020-02.zip") == date(2020, 2, 29)
    assert period_end("BTCUSDT-15m-2026-09-24.zip") == date(2026, 9, 24)
    assert period_end("BTCUSDT-fundingRate-2021-12.zip") == date(2021, 12, 31)


def test_months_between_is_inclusive_of_both_months():
    assert months_between(date(2019, 11, 15), date(2020, 2, 1)) == ["2019-11", "2019-12", "2020-01", "2020-02"]
    assert months_between(date(2020, 1, 31), date(2020, 1, 1)) == []
    assert months_between(date(2020, 5, 5), date(2020, 5, 5)) == ["2020-05"]


def test_days_of_month_respects_first_and_until():
    days = days_of_month("2026-09", first=date(2020, 9, 14), until=date(2026, 9, 27))
    assert days[0] == "2026-09-01" and days[-1] == "2026-09-26" and len(days) == 26
    assert days_of_month("2020-09", first=date(2020, 9, 14), until=date(2020, 10, 1))[0] == "2020-09-14"
    assert days_of_month("2020-02", first=date(2019, 1, 1), until=date(2021, 1, 1))[-1] == "2020-02-29"
    assert days_of_month("2026-09", first=date(2027, 1, 1), until=date(2026, 9, 27)) == []


# --- checksum and download ----------------------------------------------------------


def test_parse_checksum_accepts_sha256sum_format():
    line = "7f81b2f3694d13779e7e896b69d60cd61e9444d7b9f9e90df761935e1c1b76e2  BTCUSDT-fundingRate-2020-01.zip\n"
    assert parse_checksum(line, "BTCUSDT-fundingRate-2020-01.zip") == (
        "7f81b2f3694d13779e7e896b69d60cd61e9444d7b9f9e90df761935e1c1b76e2"
    )
    assert parse_checksum("ab" * 32 + " *x.zip", "x.zip") == "ab" * 32


@pytest.mark.parametrize("text", ["", "notahash  x.zip", "ab" * 32, "ab" * 32 + "  other.zip"])
def test_parse_checksum_rejects_garbage_and_wrong_name(text):
    with pytest.raises(ChecksumError):
        parse_checksum(text, "x.zip")


def test_download_verified_returns_bytes_and_requests_both_files():
    archive = FakeArchive()
    archive.add_klines("BTCUSDT", "15m", "2020-01", kline_rows(T0, 4, 900_000))
    url = kline_url("BTCUSDT", "15m", "2020-01")
    data = download_verified(url, http_get=archive)
    assert data == archive.files[url]
    assert archive.calls == [url, url + ".CHECKSUM"]


def test_download_verified_404_is_none():
    archive = FakeArchive()
    assert download_verified(kline_url("BTCUSDT", "15m", "2019-12"), http_get=archive) is None


def test_download_verified_bad_checksum_raises():
    archive = FakeArchive()
    archive.add_klines("BTCUSDT", "15m", "2020-01", kline_rows(T0, 4, 900_000), corrupt_checksum=True)
    with pytest.raises(ChecksumError, match="BTCUSDT-15m-2020-01.zip"):
        download_verified(kline_url("BTCUSDT", "15m", "2020-01"), http_get=archive)


def test_download_verified_missing_checksum_file_is_an_error():
    archive = FakeArchive()
    url = kline_url("BTCUSDT", "15m", "2020-01")
    archive.files[url] = b"zip"
    with pytest.raises(DownloadError, match="CHECKSUM"):
        download_verified(url, http_get=archive)


def test_urllib_get_maps_404_to_none_and_other_errors_to_download_error(monkeypatch):
    import urllib.error

    def raise_http(code):
        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, code, "msg", {}, None)

        return opener

    monkeypatch.setattr(bulk.time, "sleep", lambda s: None)
    monkeypatch.setattr(bulk.urllib.request, "urlopen", raise_http(404))
    assert bulk.urllib_get("https://x/y.zip") is None
    monkeypatch.setattr(bulk.urllib.request, "urlopen", raise_http(500))
    with pytest.raises(DownloadError, match="500"):
        bulk.urllib_get("https://x/y.zip")

    def raise_url(req, timeout):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(bulk.urllib.request, "urlopen", raise_url)
    with pytest.raises(DownloadError, match="no network"):
        bulk.urllib_get("https://x/y.zip")


# --- kline parsing ------------------------------------------------------------------


ROWS = kline_rows(T0, 5, 900_000)


@pytest.mark.parametrize("header", [False, True])
@pytest.mark.parametrize("unit", ["ms", "us"])
def test_kline_header_styles_and_units_parse_identically(header, unit):
    frame = parse_klines_csv(kline_csv(ROWS, header=header, unit=unit))
    reference = parse_klines_csv(kline_csv(ROWS, header=True, unit="ms"))
    pd.testing.assert_frame_equal(frame, reference)
    assert list(frame.columns) == bulk.KLINE_FRAME_COLUMNS
    assert frame["open_ms"].dtype == np.int64 and frame["trades"].dtype == np.int64
    assert frame["open_ms"].tolist() == [r[0] for r in ROWS]
    assert frame["close"].tolist() == [r[4] for r in ROWS]
    assert frame["taker_buy_volume"].tolist() == [r[8] for r in ROWS]


def test_kline_zip_parses_like_csv():
    data = zip_bytes("BTCUSDT-15m-2020-01.csv", kline_csv(ROWS, header=False))
    pd.testing.assert_frame_equal(parse_klines_zip(data), parse_klines_csv(kline_csv(ROWS)))


def test_kline_zip_must_hold_exactly_one_csv():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.csv", kline_csv(ROWS))
        zf.writestr("b.csv", kline_csv(ROWS))
    with pytest.raises(ValueError, match="exactly one CSV"):
        parse_klines_zip(buf.getvalue())


def test_header_only_kline_csv_is_empty_not_an_error():
    frame = parse_klines_csv(kline_csv([], header=True))
    assert len(frame) == 0
    assert list(frame.columns) == bulk.KLINE_FRAME_COLUMNS
    assert frame["open_ms"].dtype == np.int64
    assert len(parse_klines_csv(b"")) == 0


def test_kline_rows_with_wrong_width_raise():
    with pytest.raises(ValueError):
        parse_klines_csv(b"1577836800000,1,2,3\n")


# --- funding parsing ----------------------------------------------------------------


def test_round_to_minute():
    m = 60_000
    ts = np.array([10 * m + 1, 11 * m - 2, 12 * m, 12 * m + 29_999, 12 * m + 30_000], dtype=np.int64)
    assert round_to_minute(ts).tolist() == [10 * m, 11 * m, 12 * m, 12 * m, 13 * m]


@pytest.mark.parametrize("unit", ["ms", "us"])
def test_funding_jitter_rounds_to_the_minute_and_interval_is_kept(unit):
    rows = [(T0 + 1, 8, -0.00012359), (T0 + 8 * 3_600_000 - 2, 8, 0.0001), (T0 + 16 * 3_600_000 + 4, 4, 0.0002)]
    frame = parse_funding_csv(funding_csv(rows, unit=unit))
    assert list(frame.columns) == bulk.FUNDING_FRAME_COLUMNS
    assert frame["funding_ms"].tolist() == [T0, T0 + 8 * 3_600_000, T0 + 16 * 3_600_000]
    assert frame["funding_ms"].dtype == np.int64
    assert frame["rate"].tolist() == pytest.approx([-0.00012359, 0.0001, 0.0002])
    assert frame["interval_h"].dtype == np.int8
    assert frame["interval_h"].tolist() == [8, 8, 4]


def test_funding_without_header_parses_too():
    rows = [(T0 + 1, 8, 0.0001)]
    pd.testing.assert_frame_equal(
        parse_funding_csv(funding_csv(rows, header=False)), parse_funding_csv(funding_csv(rows, header=True))
    )


def test_funding_zip_parses_like_csv():
    rows = [(T0 + 1, 8, 0.0001), (T0 + 8 * 3_600_000, 8, 0.0002)]
    data = zip_bytes("BTCUSDT-fundingRate-2020-01.csv", funding_csv(rows))
    pd.testing.assert_frame_equal(parse_funding_zip(data), parse_funding_csv(funding_csv(rows)))


def test_funding_duplicate_after_rounding_raises():
    rows = [(T0 + 1, 8, 0.0001), (T0 + 2, 8, 0.0002)]
    with pytest.raises(ValueError, match="duplicate"):
        parse_funding_csv(funding_csv(rows))


def test_funding_interval_out_of_int8_range_raises():
    with pytest.raises(ValueError, match="interval"):
        parse_funding_csv(funding_csv([(T0, 300, 0.0001)]))


def test_header_only_funding_csv_is_empty():
    frame = parse_funding_csv(funding_csv([], header=True))
    assert len(frame) == 0 and list(frame.columns) == bulk.FUNDING_FRAME_COLUMNS


def test_urllib_get_retries_transient_failures_but_not_404(monkeypatch):
    import urllib.error

    attempts = []

    def flaky(req, timeout):
        attempts.append(req.full_url)
        if len(attempts) < 3:
            raise urllib.error.URLError("getaddrinfo failed")

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"payload"

        return Resp()

    monkeypatch.setattr(bulk.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(bulk.time, "sleep", lambda s: None)
    assert bulk.urllib_get("https://x/y.zip") == b"payload"
    assert len(attempts) == 3

    attempts.clear()

    def always_404(req, timeout):
        attempts.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 404, "msg", {}, None)

    monkeypatch.setattr(bulk.urllib.request, "urlopen", always_404)
    assert bulk.urllib_get("https://x/y.zip") is None
    assert len(attempts) == 1

    attempts.clear()

    def always_down(req, timeout):
        attempts.append(req.full_url)
        raise urllib.error.URLError("still down")

    monkeypatch.setattr(bulk.urllib.request, "urlopen", always_down)
    with pytest.raises(DownloadError, match="still down"):
        bulk.urllib_get("https://x/y.zip", attempts=4)
    assert len(attempts) == 4
