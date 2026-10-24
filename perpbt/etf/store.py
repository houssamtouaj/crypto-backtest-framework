"""The ETF store: prices, actions, T-bill rates and the cross-check source under ``data/etf/`` (ETF spec Phase 0 §0.3).

Layout, one parquet file per series, every day keyed by ``day_ms`` (the
exchange date at 00:00 UTC, int64 ms)::

    <TICKER>/prices.parquet       day_ms, open, high, low, close, volume, adj_close   (Yahoo, split-adjusted)
    <TICKER>/actions.parquet      day_ms, dividend, split, capital_gain              (days with an action only)
    rates/dtb3.parquet            day_ms, rate_pct                                   (published days only)
    nasdaq/<TICKER>.parquet       day_ms, open, close                                (the cross-check source)
    nasdaq/<TICKER>_dividends.parquet  day_ms, dividend                              (where Nasdaq serves one)
    manifest.json                 per file: source, url, params, download time, rows, first/last date, hashes

The manifest is the source of truth: it is written last, a file it does not
list is not loaded, and a store without one loads nothing. ``write`` never
replaces a store whose manifest has a different ``download_date`` unless
``refetch`` is given; the old store then moves to ``_old/<download_date>/``.

The holdout guard (Phase 0 §0.5): every load ending on or after
``ETF_HOLDOUT_START`` needs the ``allow_holdout`` flag. The date is this module's
constant, not the config's, so a wrong config cannot open the holdout.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from perpbt.data.store import HoldoutAccessError, _replace, _write_parquet, date_ms

ETF_HOLDOUT_START = "2018-01-01"
HOLDOUT_START_MS = date_ms(ETF_HOLDOUT_START)
MANIFEST = "manifest.json"
OLD_DIR = "_old"
RATES_FILE = "rates/dtb3.parquet"

PRICE_COLUMNS = ("day_ms", "open", "high", "low", "close", "volume", "adj_close")
ACTION_COLUMNS = ("day_ms", "dividend", "split", "capital_gain")
RATE_COLUMNS = ("day_ms", "rate_pct")
XPRICE_COLUMNS = ("day_ms", "open", "close")
XDIVIDEND_COLUMNS = ("day_ms", "dividend")

__all__ = [
    "ACTION_COLUMNS", "ETF_HOLDOUT_START", "EtfData", "EtfStore", "FileSource", "HoldoutAccessError",
    "PRICE_COLUMNS", "RATE_COLUMNS", "StoreRefused", "XDIVIDEND_COLUMNS", "XPRICE_COLUMNS", "day_iso",
    "prices_file", "actions_file", "xprices_file", "xdividends_file",
]


class StoreRefused(RuntimeError):
    """A write that would replace a store of another download date without ``refetch``, or a load without a manifest."""


def day_iso(ms: int) -> str:
    """The ISO date of a ``day_ms`` key."""
    return datetime.fromtimestamp(int(ms) // 1000, tz=timezone.utc).date().isoformat()


def prices_file(ticker: str) -> str:
    return f"{ticker}/prices.parquet"


def actions_file(ticker: str) -> str:
    return f"{ticker}/actions.parquet"


def xprices_file(ticker: str) -> str:
    return f"nasdaq/{ticker}.parquet"


def xdividends_file(ticker: str) -> str:
    return f"nasdaq/{ticker}_dividends.parquet"


def _typed(frame: pd.DataFrame, columns: tuple[str, ...], where: str) -> pd.DataFrame:
    """``frame`` with exactly ``columns`` (day_ms int64, the rest float64), sorted by day, unique days."""
    if tuple(frame.columns) != columns:
        raise ValueError(f"{where}: expected columns {list(columns)}, got {list(frame.columns)}")
    out = frame.astype({c: ("int64" if c == "day_ms" else "float64") for c in columns}).reset_index(drop=True)
    if not out["day_ms"].is_monotonic_increasing or out["day_ms"].duplicated().any():
        raise ValueError(f"{where}: day_ms must be strictly increasing")
    return out


@dataclass(frozen=True, eq=False)
class EtfData:
    """Everything one fetch produces, in memory; ``xdividends[t]`` is None where the cross-check source has none."""

    prices: Mapping[str, pd.DataFrame]
    actions: Mapping[str, pd.DataFrame]
    rates: pd.DataFrame
    xprices: Mapping[str, pd.DataFrame]
    xdividends: Mapping[str, pd.DataFrame | None]

    def __post_init__(self) -> None:
        tickers = list(self.prices)
        for name in ("actions", "xprices", "xdividends"):
            if list(getattr(self, name)) != tickers:
                raise ValueError(f"EtfData.{name}: tickers {list(getattr(self, name))} differ from {tickers}")
        object.__setattr__(self, "prices", {t: _typed(f, PRICE_COLUMNS, f"prices[{t}]") for t, f in self.prices.items()})
        object.__setattr__(self, "actions",
                           {t: _typed(f, ACTION_COLUMNS, f"actions[{t}]") for t, f in self.actions.items()})
        object.__setattr__(self, "rates", _typed(self.rates, RATE_COLUMNS, "rates"))
        object.__setattr__(self, "xprices",
                           {t: _typed(f, XPRICE_COLUMNS, f"xprices[{t}]") for t, f in self.xprices.items()})
        object.__setattr__(self, "xdividends", {
            t: None if f is None else _typed(f, XDIVIDEND_COLUMNS, f"xdividends[{t}]")
            for t, f in self.xdividends.items()
        })

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(self.prices)

    def files(self) -> dict[str, pd.DataFrame]:
        """Store path -> frame, for every file this data writes."""
        out: dict[str, pd.DataFrame] = {}
        for t in self.tickers:
            out[prices_file(t)] = self.prices[t]
            out[actions_file(t)] = self.actions[t]
        out[RATES_FILE] = self.rates
        for t in self.tickers:
            out[xprices_file(t)] = self.xprices[t]
            if self.xdividends[t] is not None:
                out[xdividends_file(t)] = self.xdividends[t]
        return out


@dataclass(frozen=True)
class FileSource:
    """Where one stored file came from (manifest provenance)."""

    source: str  # "yahoo", "fred", "nasdaq"
    url: str
    params: dict = field(default_factory=dict)
    downloaded_at: str = ""  # ISO UTC
    raw_sha256: str = ""  # of the raw bytes the parser read


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, obj: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    _replace(tmp, path)


class EtfStore:
    """The ETF data store rooted at ``root`` (``data_dir`` of ``configs/etf/data.yaml``)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # --- manifest -------------------------------------------------------------------

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST

    def manifest(self) -> dict | None:
        """The parsed manifest, or None when there is none."""
        if not self.manifest_path.is_file():
            return None
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def manifest_sha256(self) -> str:
        return _sha256_file(self.manifest_path)

    def _require_manifest(self) -> dict:
        m = self.manifest()
        if m is None:
            raise StoreRefused(f"no {MANIFEST} under {self.root}; run `perpbt etf fetch` first")
        return m

    # --- write ----------------------------------------------------------------------

    def write(self, data: EtfData, provenance: Mapping[str, FileSource], *, download_date: str, holdout_end: str,
              extra: Mapping | None = None, refetch: bool = False) -> dict:
        """Write every file of ``data`` and then the manifest; returns the manifest.

        ``provenance`` maps each store path to its ``FileSource``. ``extra`` is
        merged into the manifest (the cross-check basis, the validation table).
        """
        files = data.files()
        missing = sorted(set(files) - set(provenance))
        if missing:
            raise ValueError(f"EtfStore.write: no provenance for {missing}")
        old = self.manifest()
        if old is not None and old.get("download_date") != download_date:
            if not refetch:
                raise StoreRefused(
                    f"{self.root} holds the {old.get('download_date')} download; pass --refetch to replace it "
                    f"(it moves to {OLD_DIR}/{old.get('download_date')}/)"
                )
            self._retire(old["download_date"])
        self.root.mkdir(parents=True, exist_ok=True)
        if self.manifest_path.exists():
            self.manifest_path.unlink()  # a crash below leaves no manifest, so nothing loads half a store
        entries = {}
        for rel, frame in files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_parquet(path, frame)
            src = provenance[rel]
            entries[rel] = {
                "source": src.source, "url": src.url, "params": dict(src.params), "downloaded_at": src.downloaded_at,
                "raw_sha256": src.raw_sha256, "rows": int(len(frame)),
                "first": day_iso(frame["day_ms"].iloc[0]) if len(frame) else None,
                "last": day_iso(frame["day_ms"].iloc[-1]) if len(frame) else None,
                "sha256": _sha256_file(path),
            }
        manifest = {"download_date": download_date, "holdout_end": holdout_end, "tickers": list(data.tickers),
                    "files": entries, **dict(extra or {})}
        _write_json(self.manifest_path, manifest)
        return manifest

    def _retire(self, old_date: str) -> None:
        dest = self.root / OLD_DIR / old_date
        if dest.exists():
            raise StoreRefused(f"{dest} exists; move it away before re-fetching")
        dest.mkdir(parents=True)
        for entry in sorted(self.root.iterdir()):
            if entry.name != OLD_DIR:
                shutil.move(str(entry), str(dest / entry.name))

    # --- loads ----------------------------------------------------------------------

    def _read(self, rel: str, columns: tuple[str, ...], manifest: dict) -> pd.DataFrame:
        if rel not in manifest["files"]:
            raise StoreRefused(f"{rel} is not in {self.manifest_path}")
        return _typed(pd.read_parquet(self.root / rel), columns, rel)

    @staticmethod
    def _guard(end: str, allow_holdout: bool, what: str) -> None:
        if date_ms(end) >= HOLDOUT_START_MS and not allow_holdout:
            raise HoldoutAccessError(
                f"{what}: end {end} reaches the ETF holdout (from {ETF_HOLDOUT_START}); needs the allow_holdout flag"
            )

    @staticmethod
    def _window(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
        if end < start:
            raise ValueError(f"EtfStore: end {end} is before start {start}")
        lo, hi = date_ms(start), date_ms(end)
        return frame[(frame["day_ms"] >= lo) & (frame["day_ms"] <= hi)].reset_index(drop=True)

    def load(self, ticker: str, start: str, end: str, *, allow_holdout: bool = False) -> pd.DataFrame:
        """One ticker's prices with its actions (0 where none), ``start <= day <= end`` (ISO dates, inclusive)."""
        self._guard(end, allow_holdout, f"EtfStore.load({ticker})")
        manifest = self._require_manifest()
        prices = self._read(prices_file(ticker), PRICE_COLUMNS, manifest)
        actions = self._read(actions_file(ticker), ACTION_COLUMNS, manifest)
        if not actions["day_ms"].isin(prices["day_ms"]).all():
            raise StoreRefused(f"{actions_file(ticker)}: an action on a day without a price")
        out = prices.merge(actions, on="day_ms", how="left")
        out[list(ACTION_COLUMNS[1:])] = out[list(ACTION_COLUMNS[1:])].fillna(0.0)
        return self._window(out, start, end)

    def load_rates(self, start: str, end: str, *, allow_holdout: bool = False) -> pd.DataFrame:
        """The published T-bill rates (percent), ``start <= day <= end``."""
        self._guard(end, allow_holdout, "EtfStore.load_rates")
        return self._window(self._read(RATES_FILE, RATE_COLUMNS, self._require_manifest()), start, end)

    def load_all(self, *, allow_holdout: bool = False) -> EtfData:
        """The whole store as ``EtfData`` (validation only): it covers the holdout, so it needs ``allow_holdout``."""
        if not allow_holdout:
            raise HoldoutAccessError(f"EtfStore.load_all: the store reaches the ETF holdout (from {ETF_HOLDOUT_START})")
        manifest = self._require_manifest()
        tickers = manifest["tickers"]
        return EtfData(
            prices={t: self._read(prices_file(t), PRICE_COLUMNS, manifest) for t in tickers},
            actions={t: self._read(actions_file(t), ACTION_COLUMNS, manifest) for t in tickers},
            rates=self._read(RATES_FILE, RATE_COLUMNS, manifest),
            xprices={t: self._read(xprices_file(t), XPRICE_COLUMNS, manifest) for t in tickers},
            xdividends={t: self._read(xdividends_file(t), XDIVIDEND_COLUMNS, manifest)
                        if xdividends_file(t) in manifest["files"] else None for t in tickers},
        )
