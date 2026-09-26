"""The ``data fetch`` pipeline: bulk sync, ccxt head and tail, manifest upkeep (spec §1.5).

Per pair and timeframe, every month from ``max(--from, listing, archive
start)`` to ``--to`` whose monthly zip the manifest does not list is
downloaded, verified, parsed and merged. The current month (which never has a
monthly zip) and any past month whose monthly zip is 404 fall back to daily
zips up to yesterday. 404s are recorded in ``missing`` and retried only while
the file's period ended recently. The manifest is rewritten after every
ingested file, so an interrupted run resumes without re-downloading. Delete a
manifest to force a series to be re-ingested.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from perpbt.config import DataConfig
from perpbt.data.bulk import (
    ARCHIVE_START,
    HttpGet,
    days_of_month,
    download_verified,
    funding_name,
    funding_url,
    kline_name,
    kline_url,
    months_between,
    parse_funding_zip,
    parse_klines_zip,
    period_end,
    period_of,
    sha256_bytes,
    urllib_get,
)
from perpbt.data.ccxt_fetch import Exchange, fetch_funding_range, fetch_ohlcv_range, head_range, make_exchange
from perpbt.data.store import _STEP_MS, CandleStore, FundingStore
from perpbt.data.validate import STEP_1M_MS, check_funding, gap_report

log = logging.getLogger(__name__)

RETRY_DAYS_MONTHLY = 35  # a monthly zip appears a few days after month end
RETRY_DAYS_DAILY = 3  # a daily zip appears the next morning (UTC)
TIMEFRAMES = ("1m", "15m")


def _retryable(name: str, today: date) -> bool:
    """A recorded 404 is retried while the file's period ended less than the retry window ago."""
    days = RETRY_DAYS_DAILY if len(period_of(name)) == 10 else RETRY_DAYS_MONTHLY
    return period_end(name) >= today - timedelta(days=days)


def _ingest(
    manifest: dict,
    *,
    url: str,
    name: str,
    source: str,
    parse: Callable[[bytes], pd.DataFrame],
    write: Callable[[pd.DataFrame], object],
    http_get: HttpGet,
    today: date,
) -> bool:
    """Download, verify, parse and merge one archive file; record it (or its absence) in ``manifest``."""
    data = download_verified(url, http_get=http_get)
    manifest["missing"] = [m for m in manifest["missing"] if m["name"] != name]
    if data is None:
        manifest["missing"].append({"name": name, "checked": today.isoformat()})
        log.info("%s: not published (404)", name)
        return False
    frame = parse(data)
    frame["source"] = source
    stats = write(frame)
    manifest["files"] = [f for f in manifest["files"] if f["name"] != name]
    manifest["files"].append({"name": name, "sha256": sha256_bytes(data), "rows": int(len(frame)), "source": source})
    manifest["overlap_mismatches"] += stats.mismatches
    log.info(
        "%s: %d rows (%d new, %d replaced, %d overlap mismatches)",
        name, len(frame), stats.added, stats.replaced, stats.mismatches,
    )
    return True


def _sort_manifest(manifest: dict) -> None:
    manifest["files"].sort(key=lambda f: f["name"])
    manifest["missing"].sort(key=lambda m: m["name"])


def _refresh_candles(store: CandleStore, pair: str, tf: str, manifest: dict) -> None:
    """Recompute rows, first/last and the gap report from what is stored."""
    ts = store.read_frame(pair, tf)["open_ms"].to_numpy()
    manifest["rows"] = int(len(ts))
    manifest["first_open_ms"] = int(ts[0]) if len(ts) else None
    manifest["last_open_ms"] = int(ts[-1]) if len(ts) else None
    manifest["gaps"] = [g.as_dict() for g in gap_report(ts, _STEP_MS[tf])]
    _sort_manifest(manifest)


def _refresh_funding(store: FundingStore, pair: str, manifest: dict) -> None:
    frame = store.read_frame(pair)
    ts = frame["funding_ms"].to_numpy()
    report = check_funding(ts, frame["interval_h"].to_numpy())
    manifest["rows"] = int(len(ts))
    manifest["first_funding_ms"] = int(ts[0]) if len(ts) else None
    manifest["last_funding_ms"] = int(ts[-1]) if len(ts) else None
    manifest["last_interval_h"] = int(frame["interval_h"].iloc[-1]) if len(ts) else None
    manifest["intervals"] = {str(k): v for k, v in report["intervals"].items()}  # JSON keys are strings
    manifest["bad_intervals"] = report["bad_intervals"]
    _sort_manifest(manifest)


def sync_candles(
    store: CandleStore,
    data_cfg: DataConfig,
    pair: str,
    tf: str,
    *,
    from_date: date,
    to_date: date,
    today: date,
    http_get: HttpGet = urllib_get,
) -> dict:
    """Bring ``pair``/``tf`` up to date from the bulk archive; returns the manifest."""
    manifest = store.manifest(pair, tf)
    listing = date.fromisoformat(data_cfg.listing[pair])
    start = max(from_date, listing, ARCHIVE_START)
    this_month = today.strftime("%Y-%m")

    def write(frame: pd.DataFrame):
        return store.write(pair, tf, frame)

    for ym in months_between(start, to_date):
        have = {f["name"] for f in manifest["files"]}
        missing = {m["name"] for m in manifest["missing"]}
        name = kline_name(pair, tf, ym)
        if name in have:
            continue
        if ym < this_month and (name not in missing or _retryable(name, today)):
            ok = _ingest(
                manifest, url=kline_url(pair, tf, ym), name=name, source="bulk_monthly",
                parse=parse_klines_zip, write=write, http_get=http_get, today=today,
            )
            store.write_manifest(pair, tf, manifest)
            if ok:
                continue
        for day in days_of_month(ym, first=start, until=min(today, to_date + timedelta(days=1))):
            dname = kline_name(pair, tf, day)
            if dname in have or (dname in missing and not _retryable(dname, today)):
                continue
            _ingest(
                manifest, url=kline_url(pair, tf, day), name=dname, source="bulk_daily",
                parse=parse_klines_zip, write=write, http_get=http_get, today=today,
            )
            store.write_manifest(pair, tf, manifest)
    _refresh_candles(store, pair, tf, manifest)
    manifest["download_date"] = today.isoformat()
    store.write_manifest(pair, tf, manifest)
    return manifest


def sync_funding(
    store: FundingStore,
    data_cfg: DataConfig,
    pair: str,
    *,
    from_date: date,
    to_date: date,
    today: date,
    http_get: HttpGet = urllib_get,
) -> dict:
    """Bring ``pair``'s funding up to date from the monthly archive files; returns the manifest."""
    manifest = store.manifest(pair)
    listing = date.fromisoformat(data_cfg.listing[pair])
    start = max(from_date, listing, ARCHIVE_START)
    this_month = today.strftime("%Y-%m")
    for ym in months_between(start, to_date):
        if ym >= this_month:
            break  # no monthly file yet; the ccxt tail covers the current month
        have = {f["name"] for f in manifest["files"]}
        missing = {m["name"] for m in manifest["missing"]}
        name = funding_name(pair, ym)
        if name in have or (name in missing and not _retryable(name, today)):
            continue
        _ingest(
            manifest, url=funding_url(pair, ym), name=name, source="bulk_monthly",
            parse=parse_funding_zip, write=lambda frame: store.write(pair, frame), http_get=http_get, today=today,
        )
        store.write_manifest(pair, manifest)
    _refresh_funding(store, pair, manifest)
    manifest["download_date"] = today.isoformat()
    store.write_manifest(pair, manifest)
    return manifest


def fetch_head(store: CandleStore, data_cfg: DataConfig, pair: str, *, from_date: date, exchange: Exchange) -> int:
    """15m head backfill (D14) for pairs listed before the archive; idempotent via ``ccxt_ranges``."""
    rng = head_range(data_cfg, pair, from_date=from_date)
    if rng is None:
        log.info("%s: no head backfill needed", pair)
        return 0
    start, end = rng
    manifest = store.manifest(pair, "15m")
    if any(r["start_ms"] <= start and r["end_ms"] >= end for r in manifest["ccxt_ranges"]):
        log.info("%s 15m: head %d..%d already fetched", pair, start, end)
        return 0
    frame = fetch_ohlcv_range(exchange, pair, "15m", start, end)
    stats = store.write(pair, "15m", frame)
    manifest["ccxt_ranges"].append({"start_ms": start, "end_ms": end, "rows": int(len(frame)), "purpose": "head"})
    manifest["overlap_mismatches"] += stats.mismatches
    _refresh_candles(store, pair, "15m", manifest)
    store.write_manifest(pair, "15m", manifest)
    log.info("%s 15m: ccxt head %d rows", pair, len(frame))
    return int(len(frame))


def fetch_tail(
    cstore: CandleStore,
    fstore: FundingStore,
    pair: str,
    tfs: Sequence[str],
    *,
    exchange: Exchange,
    now_ms: int,
) -> dict[str, int]:
    """Extend each stored series from its last row to the last closed candle (or event) before ``now_ms``."""
    fetched: dict[str, int] = {}
    for tf in tfs:
        manifest = cstore.manifest(pair, tf)
        last = manifest["last_open_ms"]
        step = _STEP_MS[tf]
        fetched[tf] = 0
        if last is None:
            log.info("%s %s: nothing stored, skipping the tail", pair, tf)
            continue
        start, end = last + step, (now_ms // step) * step
        if start >= end:
            continue
        frame = fetch_ohlcv_range(exchange, pair, tf, start, end)
        stats = cstore.write(pair, tf, frame)
        manifest["ccxt_ranges"].append({"start_ms": start, "end_ms": end, "rows": int(len(frame)), "purpose": "tail"})
        manifest["overlap_mismatches"] += stats.mismatches
        _refresh_candles(cstore, pair, tf, manifest)
        cstore.write_manifest(pair, tf, manifest)
        fetched[tf] = int(len(frame))
        log.info("%s %s: ccxt tail %d rows", pair, tf, len(frame))
    manifest = fstore.manifest(pair)
    last = manifest["last_funding_ms"]
    fetched["funding"] = 0
    if last is None:
        log.info("%s funding: nothing stored, skipping the tail", pair)
        return fetched
    start, end = last + STEP_1M_MS, now_ms
    if start >= end:
        return fetched
    interval_h = manifest["last_interval_h"] or 8
    frame = fetch_funding_range(exchange, pair, start, end, interval_h=interval_h)
    stats = fstore.write(pair, frame)
    manifest["ccxt_ranges"].append({"start_ms": start, "end_ms": end, "rows": int(len(frame)), "purpose": "tail"})
    manifest["overlap_mismatches"] += stats.mismatches
    _refresh_funding(fstore, pair, manifest)
    fstore.write_manifest(pair, manifest)
    fetched["funding"] = int(len(frame))
    log.info("%s funding: ccxt tail %d events (interval %dh)", pair, len(frame), interval_h)
    return fetched


def run_fetch(
    data_cfg: DataConfig,
    *,
    pairs: Sequence[str],
    tfs: Sequence[str] = TIMEFRAMES,
    from_date: date | None = None,
    to_date: date | None = None,
    ccxt_head: bool = False,
    ccxt_tail: bool = False,
    http_get: HttpGet = urllib_get,
    exchange_factory: Callable[[], Exchange] = make_exchange,
    today: date | None = None,
    now_ms: int | None = None,
) -> dict:
    """The ``perpbt data fetch`` pipeline. Returns ``{pair: {tf: {...}, "funding": {...}, ...}}``."""
    today = today or datetime.now(timezone.utc).date()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000) if now_ms is None else now_ms
    from_date = from_date or date.fromisoformat(data_cfg.warmup_start)
    to_date = to_date or today
    unknown = [p for p in pairs if p not in data_cfg.listing]
    if unknown:
        raise ValueError(f"pairs without a listing date in the data config: {unknown}")
    bad = [tf for tf in tfs if tf not in _STEP_MS]
    if bad:
        raise ValueError(f"unknown timeframe {bad}; expected one of {sorted(_STEP_MS)}")
    cstore, fstore = CandleStore(data_cfg), FundingStore(data_cfg)
    exchange = exchange_factory() if (ccxt_head or ccxt_tail) else None
    summary: dict = {}
    for pair in pairs:
        entry: dict = {}
        for tf in tfs:
            m = sync_candles(cstore, data_cfg, pair, tf, from_date=from_date, to_date=to_date, today=today, http_get=http_get)
            entry[tf] = {
                "rows": m["rows"], "files": len(m["files"]), "missing": len(m["missing"]),
                "gaps": len(m["gaps"]), "overlap_mismatches": m["overlap_mismatches"],
            }
        m = sync_funding(fstore, data_cfg, pair, from_date=from_date, to_date=to_date, today=today, http_get=http_get)
        entry["funding"] = {
            "rows": m["rows"], "files": len(m["files"]), "missing": len(m["missing"]),
            "bad_intervals": m["bad_intervals"], "overlap_mismatches": m["overlap_mismatches"],
        }
        if ccxt_head and "15m" in tfs:
            entry["ccxt_head_rows"] = fetch_head(cstore, data_cfg, pair, from_date=from_date, exchange=exchange)
        if ccxt_tail:
            entry["ccxt_tail"] = fetch_tail(cstore, fstore, pair, tfs, exchange=exchange, now_ms=now_ms)
        summary[pair] = entry
    return summary
