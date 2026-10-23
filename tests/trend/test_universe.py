"""Point-in-time universe ranking from the archive's 1d klines (perpbt.trend.universe; trend Phase 0 §0.3-§0.5)."""
import json
from datetime import date, timedelta

import pytest

from perpbt.cli import main
from perpbt.data import bulk
from perpbt.data.store import date_ms
from perpbt.trend.universe import archive_symbols, exclusion, rank_universe, write_universe
from tests.fake_archive import FakeArchive

TODAY = date(2026, 10, 23)
WINDOW_MONTHS = ("2020-01", "2020-02", "2020-03", "2020-04", "2020-05", "2020-06")


def day_rows(first: date, last: date, qv: float = 1.0) -> list[tuple]:
    """One 1d row per day ``first..last`` with quote volume ``qv`` (prices are irrelevant here)."""
    rows, d = [], first
    while d <= last:
        rows.append((date_ms(d.isoformat()), 1.0, 1.0, 1.0, 1.0, 1.0, qv, 1, 0.5))
        d += timedelta(days=1)
    return rows


def month_rows(ym: str, qv: float = 1.0, *, first_day: int = 1) -> list[tuple]:
    y, m = int(ym[:4]), int(ym[5:7])
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return day_rows(date(y, m, first_day), nxt - timedelta(days=1), qv)


def add_symbol(a: FakeArchive, pair: str, months, qv: float = 1.0, *, market: str = "um") -> None:
    for ym in months:
        a.add_klines(pair, "1d", ym, month_rows(ym, qv), market=market)


def test_listing_follows_markers():
    a = FakeArchive(page_size=2)
    pairs = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT", "EEEUSDT"]
    for p in pairs:
        add_symbol(a, p, ["2020-01"])
    assert archive_symbols("um", http_get=a) == pairs
    assert len(a.listing_gets()) == 3


def test_symbol_filter():
    assert exclusion("BTCUSDT_200925", "um") == "dated future"
    assert exclusion("BTCBUSD", "um") == "not USDT"
    assert exclusion("USDCUSDT", "spot") == "stablecoin"
    assert exclusion("BTCUPUSDT", "spot") == "leveraged token"
    assert exclusion("ETHBEARUSDT", "spot") == "leveraged token"
    assert exclusion("JUPUSDT", "spot") is None
    assert exclusion("BTCUSDT", "um") is None


def test_ranking_and_ties():
    a = FakeArchive()
    add_symbol(a, "CCCUSDT", WINDOW_MONTHS, 7.0)
    add_symbol(a, "AAAUSDT", WINDOW_MONTHS, 5.0)
    add_symbol(a, "BBBUSDT", WINDOW_MONTHS, 7.0)
    a.add_klines("AAAUSDT", "1d", "2020-07", month_rows("2020-07", 1e9))  # after the window: ignored
    out = rank_universe("um", today=TODAY, http_get=a)
    assert [r["pair"] for r in out["ranked"]] == ["BBBUSDT", "CCCUSDT", "AAAUSDT"]
    assert [r["quote_volume"] for r in out["ranked"]] == [7.0 * 182, 7.0 * 182, 5.0 * 182]
    assert out["members"] == ["BBBUSDT", "CCCUSDT", "AAAUSDT"]
    assert out["window"] == ["2020-01-01", "2020-06-30"] and out["computed_on"] == "2026-10-23"
    assert "2020-07-01" in out["rule"]


def test_incomplete_coverage_dropped():
    a = FakeArchive()
    add_symbol(a, "AAAUSDT", WINDOW_MONTHS)
    a.add_klines("LATEUSDT", "1d", "2020-01", month_rows("2020-01", first_day=15))
    add_symbol(a, "LATEUSDT", WINDOW_MONTHS[1:])
    add_symbol(a, "HOLEUSDT", [m for m in WINDOW_MONTHS if m != "2020-03"])
    add_symbol(a, "NEWUSDT", ["2020-09"])  # no window month: not a candidate, not listed
    out = rank_universe("um", today=TODAY, http_get=a)
    assert [r["pair"] for r in out["ranked"]] == ["AAAUSDT"]
    assert out["dropped"] == [
        {"pair": "HOLEUSDT", "reason": "missing month 2020-03"},
        {"pair": "LATEUSDT", "reason": "missing days: 14"},
    ]


def test_delisted_symbol_kept():
    a = FakeArchive()
    add_symbol(a, "GONEUSDT", WINDOW_MONTHS + ("2020-07", "2021-01", "2021-06"))
    out = rank_universe("um", today=TODAY, http_get=a)
    assert out["ranked"] == [
        {"pair": "GONEUSDT", "quote_volume": 182.0, "first_day": "2020-01-01", "last_day": "2021-06-30"},
    ]


def test_first_day_before_window_spot():
    a = FakeArchive()
    a.add_klines("BTCUSDT", "1d", "2017-08", month_rows("2017-08", first_day=17), market="spot")
    add_symbol(a, "BTCUSDT", ["2017-09", "2017-10", "2017-11", "2017-12", "2018-01"], 2.0, market="spot")
    out = rank_universe("spot", today=TODAY, http_get=a)
    assert out["ranked"] == [
        {"pair": "BTCUSDT", "quote_volume": 62.0, "first_day": "2017-08-17", "last_day": "2018-01-31"},
    ]
    assert out["window"] == ["2017-12-01", "2017-12-31"]


def test_exclusions_applied_in_ranking():
    a = FakeArchive()
    add_symbol(a, "BTCUSDT", ["2017-12"], market="spot")
    add_symbol(a, "USDCUSDT", ["2017-12"], 1e6, market="spot")
    add_symbol(a, "BTCBUSD", ["2017-12"], 1e6, market="spot")  # not a candidate at all: not listed
    out = rank_universe("spot", today=TODAY, http_get=a)
    assert out["members"] == ["BTCUSDT"]
    assert out["dropped"] == [{"pair": "USDCUSDT", "reason": "stablecoin"}]


def test_top_n():
    a = FakeArchive()
    for k in range(12):
        add_symbol(a, f"P{k:02d}USDT", WINDOW_MONTHS, float(k + 1))
    out = rank_universe("um", today=TODAY, http_get=a)
    assert len(out["ranked"]) == 12
    assert out["members"] == [f"P{k:02d}USDT" for k in range(11, 1, -1)]


def test_no_prices_returned():
    a = FakeArchive()
    add_symbol(a, "AAAUSDT", WINDOW_MONTHS)
    out = rank_universe("um", today=TODAY, http_get=a)
    assert all(set(r) == {"pair", "quote_volume", "first_day", "last_day"} for r in out["ranked"])


def test_unknown_market_raises():
    with pytest.raises(ValueError, match="market"):
        rank_universe("coinm", today=TODAY, http_get=FakeArchive())


# --- universe.json and the CLI (§0.5) -------------------------------------------------------


def test_write_universe_keeps_other_market(tmp_path):
    path = tmp_path / "trend" / "universe.json"
    write_universe(path, "um", {"members": ["BTCUSDT"]})
    write_universe(path, "spot", {"members": ["ETHUSDT"]})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"um": {"members": ["BTCUSDT"]}, "spot": {"members": ["ETHUSDT"]}}
    assert path.read_bytes().endswith(b"}\n") and b"\r\n" not in path.read_bytes()
    write_universe(path, "um", {"members": ["XRPUSDT"]})
    assert json.loads(path.read_text(encoding="utf-8"))["spot"] == {"members": ["ETHUSDT"]}


def test_cli_trend_universe(monkeypatch, tmp_path, capsys):
    a = FakeArchive()
    add_symbol(a, "BBBUSDT", WINDOW_MONTHS, 2.0)
    add_symbol(a, "AAAUSDT", WINDOW_MONTHS, 1.0)
    monkeypatch.setattr(bulk, "urllib_get", a)
    out = tmp_path / "universe.json"
    assert main(["trend", "universe", "--market", "um", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["um"]["members"] == ["BBBUSDT", "AAAUSDT"]
    assert "BBBUSDT AAAUSDT" in capsys.readouterr().out


def test_cli_trend_universe_download_error_is_exit_1(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(bulk, "urllib_get", lambda url: None)  # the listing itself is a 404
    assert main(["trend", "universe", "--market", "spot", "--out", str(tmp_path / "u.json")]) == 1
    assert "404" in capsys.readouterr().err
    assert not (tmp_path / "u.json").exists()
