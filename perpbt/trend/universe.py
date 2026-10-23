"""The point-in-time trading universe (trend spec Phase 0 §0.3-§0.5).

Candidates come from the archive's S3 listing, delisted symbols included.
Coverage and volume come from the archive's monthly **1d** klines, read in
memory and never stored (the candle store only knows 1m and 15m); only the
quote volume, the first and the last archived day leave this module, never a
price. Every input lies in the ranking window, which ends before the first
trading day.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from perpbt.data import bulk
from perpbt.data.bulk import HttpGet, download_verified, kline_url, market_base, months_between, parse_klines_zip
from perpbt.data.store import DAY_MS, date_ms

log = logging.getLogger(__name__)

LISTING_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
ARCHIVE_HOST = "https://data.binance.vision/"
_S3 = "{http://s3.amazonaws.com/doc/2006-03-01/}"

STABLE_BASES = frozenset(
    {"USDC", "TUSD", "PAX", "USDP", "BUSD", "USDS", "USDSB", "DAI", "FDUSD", "UST", "USTC", "EUR", "GBP", "AUD"}
)
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


@dataclass(frozen=True)
class UniverseRule:
    market: str
    window_start: str  # ISO date, first day of the ranking window
    window_end: str  # ISO date, inclusive
    top_n: int
    text: str


RULES = {
    "um": UniverseRule(
        "um", "2020-01-01", "2020-06-30", 10,
        "USDT-M perpetuals (delisted included) with an archive 1d kline for every day of 2020-01-01..2020-06-30, "
        "ranked by that period's quote volume (ties by symbol), stablecoin bases and leveraged tokens excluded; "
        "top 10; trading from 2020-07-01",
    ),
    "spot": UniverseRule(
        "spot", "2017-12-01", "2017-12-31", 10,
        "spot *USDT pairs (delisted included) with an archive 1d kline for every day of 2017-12-01..2017-12-31, "
        "ranked by that period's quote volume (ties by symbol), stablecoin bases and leveraged tokens excluded; "
        "up to 10; each enters the holdout when warm, holdout ends 2019-12-31",
    ),
}


# --- the archive listing -------------------------------------------------------------


def list_archive(prefix: str, *, delimiter: str | None = "/", http_get: HttpGet | None = None) -> tuple[list[str], list[str]]:
    """``(common_prefixes, keys)`` under ``prefix`` in the archive bucket, over every page.

    S3 returns at most 1,000 entries per page; the next page starts after
    ``NextMarker`` (sent only with a delimiter), else after the last entry.
    """
    http_get = http_get or bulk.urllib_get
    prefixes: list[str] = []
    keys: list[str] = []
    marker = ""
    while True:
        query = {"prefix": prefix, "marker": marker}
        if delimiter:
            query["delimiter"] = delimiter
        data = http_get(f"{LISTING_URL}?{urlencode(query)}")
        if data is None:
            raise bulk.DownloadError(f"archive listing of {prefix!r} returned 404")
        root = ET.fromstring(data)
        page_prefixes = [c.findtext(f"{_S3}Prefix") or "" for c in root.iter(f"{_S3}CommonPrefixes")]
        page_keys = [e.text or "" for e in root.iter(f"{_S3}Key")]
        prefixes += page_prefixes
        keys += page_keys
        if root.findtext(f"{_S3}IsTruncated") != "true":
            return prefixes, keys
        nxt = root.findtext(f"{_S3}NextMarker") or max(page_prefixes + page_keys, default="")
        if not nxt or nxt <= marker:
            raise bulk.DownloadError(f"archive listing of {prefix!r} makes no progress after {marker!r}")
        marker = nxt


def _root(market: str) -> str:
    return market_base(market)[len(ARCHIVE_HOST):]


def archive_symbols(market: str, *, http_get: HttpGet | None = None) -> list[str]:
    """Every symbol with a monthly klines directory in ``market``'s archive, sorted."""
    prefixes, _ = list_archive(f"{_root(market)}/monthly/klines/", http_get=http_get)
    return sorted(p.rstrip("/").rsplit("/", 1)[-1] for p in prefixes)


def _kline_months(market: str, pair: str, *, http_get: HttpGet | None) -> list[str]:
    """``YYYY-MM`` of every monthly 1d kline zip of ``pair``, sorted."""
    _, keys = list_archive(f"{_root(market)}/monthly/klines/{pair}/1d/", http_get=http_get)
    return sorted(bulk.period_of(k) for k in keys if k.endswith(".zip"))


# --- candidates ----------------------------------------------------------------------


def exclusion(pair: str, market: str) -> str | None:
    """Why ``pair`` is not a candidate in ``market`` (``"dated future"``, ``"not USDT"``,
    ``"stablecoin"``, ``"leveraged token"``), or None."""
    market_base(market)  # validates the market
    if "_" in pair:
        return "dated future"
    if not pair.endswith("USDT") or len(pair) <= 4:
        return "not USDT"
    base = pair[:-4]
    if base in STABLE_BASES:
        return "stablecoin"
    if any(base.endswith(s) and len(base) - len(s) >= 3 for s in LEVERAGED_SUFFIXES):
        return "leveraged token"
    return None


def _days(frame: pd.DataFrame) -> np.ndarray:
    return frame["open_ms"].to_numpy(dtype=np.int64) // DAY_MS


def _iso(day: int) -> str:
    return date.fromordinal(date(1970, 1, 1).toordinal() + int(day)).isoformat()


def rank_universe(market: str, *, today: date, http_get: HttpGet | None = None) -> dict:
    """Rank ``market``'s candidates by window quote volume; the block written to ``universe.json``.

    ``dropped`` lists the symbols that had at least one window month but are
    not candidates (missing month, missing days, or an exclusion). Symbols
    that are not USDT pairs, dated futures, or without any window month are
    not listed.
    """
    rule = RULES.get(market)
    if rule is None:
        raise ValueError(f"unknown market {market!r}; expected one of {sorted(RULES)}")
    w0, w1 = date.fromisoformat(rule.window_start), date.fromisoformat(rule.window_end)
    window_months = months_between(w0, w1)
    first_day, last_day = date_ms(rule.window_start) // DAY_MS, date_ms(rule.window_end) // DAY_MS
    n_days = last_day - first_day + 1

    def read(pair: str, ym: str) -> pd.DataFrame:
        data = download_verified(kline_url(pair, "1d", ym, market=market), http_get=http_get or bulk.urllib_get)
        if data is None:
            raise bulk.DownloadError(f"{pair} 1d {ym}: listed in the archive but 404")
        return parse_klines_zip(data)

    ranked: list[dict] = []
    dropped: list[dict] = []
    for pair in archive_symbols(market, http_get=http_get):
        reason = exclusion(pair, market)
        if reason in ("dated future", "not USDT"):
            continue
        months = _kline_months(market, pair, http_get=http_get)
        have = set(months)
        if not have.intersection(window_months):
            continue
        if reason is None:
            gone = [ym for ym in window_months if ym not in have]
            reason = f"missing month {gone[0]}" if gone else None
        if reason is not None:
            dropped.append({"pair": pair, "reason": reason})
            continue
        frames = {ym: read(pair, ym) for ym in window_months}
        window = pd.concat(frames.values(), ignore_index=True)
        days = _days(window)
        inside = (days >= first_day) & (days <= last_day)
        missing = n_days - len(np.unique(days[inside]))
        if missing:
            dropped.append({"pair": pair, "reason": f"missing days: {missing}"})
            continue
        first = frames.get(months[0])
        first = read(pair, months[0]) if first is None else first
        last = frames.get(months[-1])
        last = read(pair, months[-1]) if last is None else last
        ranked.append({
            "pair": pair,
            "quote_volume": float(window["quote_volume"].to_numpy(dtype=np.float64)[inside].sum()),
            "first_day": _iso(_days(first).min()),
            "last_day": _iso(_days(last).max()),
        })
        log.info("%s %s: candidate, quote volume %.0f", market, pair, ranked[-1]["quote_volume"])
    ranked.sort(key=lambda r: (-r["quote_volume"], r["pair"]))
    return {
        "rule": rule.text,
        "window": [rule.window_start, rule.window_end],
        "ranked": ranked,
        "dropped": dropped,
        "members": [r["pair"] for r in ranked[: rule.top_n]],
        "computed_on": today.isoformat(),
    }

