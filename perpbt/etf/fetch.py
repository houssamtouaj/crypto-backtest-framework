"""``perpbt etf fetch``: download, validate, write the store, set the fetch dates (ETF spec Phase 0 §0.6).

Order: every source is downloaded and parsed in memory; the reviewed
corrections are applied (``etf/corrections.py``); the rules run on that;
only when every rule passes is the store written (manifest last) and
are ``holdout_end`` and ``download_date`` set in ``configs/etf/data.yaml``.
A failed validation writes nothing.

``holdout_end`` is the last day Yahoo returns for ``end = download_date``
(exclusive): the last full US trading day before the download day, since
the UTC date only turns after the New York close.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from perpbt.data.store import _replace
from perpbt.etf.config import EtfDataConfig, load_data_config
from perpbt.etf.corrections import apply, corrections_hash, load_corrections
from perpbt.etf.sources import (
    Fetched, fetch_fred, fetch_nasdaq_dividends, fetch_nasdaq_history, fetch_yahoo, parse_fred_csv,
    parse_nasdaq_dividends, parse_nasdaq_history, parse_yahoo_csv,
)
from perpbt.etf.store import (
    RATES_FILE, EtfData, EtfStore, FileSource, StoreRefused, actions_file, day_iso, prices_file, xdividends_file,
    xprices_file,
)
from perpbt.etf.validate import RuleResult, validate

__all__ = ["FetchResult", "rates_start", "run_fetch", "set_fetch_dates"]


@dataclass(frozen=True)
class FetchResult:
    ok: bool
    results: list[RuleResult]
    basis: dict[str, str]
    download_date: str
    holdout_end: str
    manifest: dict | None  # None when validation failed (nothing written)


def rates_start(cfg: EtfDataConfig) -> str:
    """The first day asked of FRED: the first of the warmup month (2004-11-01)."""
    return date.fromisoformat(cfg.warmup_start).replace(day=1).isoformat()


def set_fetch_dates(path: str | Path, *, download_date: str, holdout_end: str) -> None:
    """Set the two values in the YAML text (comments kept), then check the result loads."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    def put(m: re.Match, value: str) -> str:
        new = f'"{value}"'
        pad = m[3] if not m[3] else " " * max(1, len(m[2]) + len(m[3]) - len(new))  # keep a trailing comment's column
        return f"{m[1]}{new}{pad}"

    for key, value in (("holdout_end", holdout_end), ("download_date", download_date)):
        text, n = re.subn(rf'^({key}:[ \t]*)(null|"\d{{4}}-\d{{2}}-\d{{2}}")([ \t]*)',
                          lambda m, v=value: put(m, v), text, flags=re.MULTILINE)
        if n != 1:
            raise ValueError(f"{path}: expected one '{key}:' line with null or a quoted date, found {n}")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    try:
        load_data_config(tmp)
    except Exception:
        tmp.unlink()
        raise
    _replace(tmp, path)


def run_fetch(config_path: str | Path, *, refetch: bool = False, today: date | None = None,
              yahoo: Callable[..., Fetched] = fetch_yahoo, fred: Callable[..., Fetched] = fetch_fred,
              nasdaq_history: Callable[..., Fetched] = fetch_nasdaq_history,
              nasdaq_dividends: Callable[..., Fetched] = fetch_nasdaq_dividends) -> FetchResult:
    """Download every source, validate, and on a pass write the store and the fetch dates."""
    cfg = load_data_config(config_path)
    corrections = load_corrections(cfg.corrections)
    download_date = (today or datetime.now(timezone.utc).date()).isoformat()
    store = EtfStore(cfg.data_dir)
    old = store.manifest()
    if old is not None and old.get("download_date") != download_date and not refetch:
        raise StoreRefused(f"{store.root} holds the {old.get('download_date')} download; pass --refetch to replace it")

    provenance: dict[str, FileSource] = {}
    prices, actions = {}, {}
    for t in cfg.tickers:
        got = yahoo(t, cfg.warmup_start, download_date)
        prices[t], actions[t] = parse_yahoo_csv(got.raw, t)
        provenance[prices_file(t)] = provenance[actions_file(t)] = got.source
    holdout_end = day_iso(max(int(p["day_ms"].iloc[-1]) for p in prices.values()))

    got = fred(cfg.rates.series, rates_start(cfg))
    rates = parse_fred_csv(got.raw, cfg.rates.series)
    provenance[RATES_FILE] = got.source

    xprices, xdividends = {}, {}
    for t in cfg.tickers:
        got = nasdaq_history(t, cfg.warmup_start, holdout_end)
        xprices[t] = parse_nasdaq_history(got.raw, t)
        provenance[xprices_file(t)] = got.source
        got = nasdaq_dividends(t)
        xdividends[t] = parse_nasdaq_dividends(got.raw, t)
        provenance[xdividends_file(t)] = got.source

    data = EtfData(prices=prices, actions=actions, rates=rates, xprices=xprices, xdividends=xdividends)
    data = apply(data, corrections)
    results, basis = validate(data, cfg, accepted=corrections.accepted_keys())
    if not all(r.ok for r in results):
        return FetchResult(False, results, basis, download_date, holdout_end, None)
    extra = {"cross_check_source": cfg.cross_check.source, "cross_check_basis": basis,
             "corrections_file": Path(cfg.corrections).as_posix(), "corrections_hash": corrections_hash(corrections),
             "corrections": corrections.as_dict(), "validation": [r.as_dict() for r in results]}
    manifest = store.write(data, provenance, download_date=download_date, holdout_end=holdout_end, extra=extra,
                           refetch=refetch)
    set_fetch_dates(config_path, download_date=download_date, holdout_end=holdout_end)
    return FetchResult(True, results, basis, download_date, holdout_end, manifest)
