"""Candle and funding containers, and the Parquet stores with the holdout guard.

Layout (spec §1.2): ``<data_dir>/candles/<pair>/<tf>/<year>.parquet`` and
``<data_dir>/funding/<pair>/<year>.parquet``, each directory holding a
``manifest.json``. Rows from several sources merge by precedence
(``SOURCE_RANK``). ``load`` returns read-only numpy arrays and refuses to reach
past ``insample_end`` unless ``allow_holdout=True``, which only
``experiments/holdout.py`` may pass. ``write`` and ``read_frame`` are the
storage layer used by ``data fetch`` and ``data validate``; they carry no
guard and analysis code must not call them.
"""
from __future__ import annotations

import calendar
import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from perpbt.config import DataConfig

_STEP_MS: dict[str, int] = {"1m": 60_000, "15m": 900_000}
DAY_MS = 86_400_000

SOURCE_RANK: dict[str, int] = {"bulk_monthly": 0, "bulk_daily": 1, "ccxt": 2}

CANDLE_COLUMNS = (
    "pair", "tf", "open_ms", "open", "high", "low", "close", "volume",
    "quote_volume", "trades", "taker_buy_volume", "source",
)
FUNDING_COLUMNS = ("pair", "funding_ms", "rate", "interval_h", "source")

_CANDLE_DTYPES: dict[str, object] = {
    "pair": object, "tf": object, "open_ms": np.int64, "open": np.float64, "high": np.float64,
    "low": np.float64, "close": np.float64, "volume": np.float64, "quote_volume": np.float64,
    "trades": np.int64, "taker_buy_volume": np.float64, "source": object,
}
_FUNDING_DTYPES: dict[str, object] = {
    "pair": object, "funding_ms": np.int64, "rate": np.float64, "interval_h": np.int8, "source": object,
}


class HoldoutAccessError(RuntimeError):
    """A load reached past ``insample_end`` without ``allow_holdout=True``."""


# --- time helpers ---------------------------------------------------------------------


def date_ms(iso: str) -> int:
    """UTC midnight of the ISO date ``iso``, in ms."""
    d = date.fromisoformat(iso)
    return calendar.timegm((d.year, d.month, d.day, 0, 0, 0)) * 1000


def year_of_ms(ms: int) -> int:
    """Calendar year (UTC) of a millisecond timestamp."""
    return time.gmtime(int(ms) // 1000).tm_year


def insample_end_exclusive_ms(cfg: DataConfig) -> int:
    """First millisecond of the holdout: the day after ``insample_end`` at 00:00 UTC."""
    return date_ms(cfg.insample_end) + DAY_MS


# --- containers -----------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Candles:
    """One pair, one timeframe, UTC.

    ``ts`` is the candle open time in int64 milliseconds and is strictly
    increasing (sorted, unique). Price and volume arrays are float64 and
    aligned to ``ts``. Instances are immutable; arrays that come out of
    ``CandleStore.load`` are read-only, and ``slice`` returns views that
    inherit that flag (arrays built directly by tests stay writable).
    """

    pair: str
    tf: str
    ts: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741 - column name from the spec
    c: np.ndarray
    v: np.ndarray

    def __post_init__(self) -> None:
        ts = np.asarray(self.ts)
        if ts.ndim != 1:
            raise ValueError(f"Candles.ts: expected a 1-D array, got shape {ts.shape}")
        if not np.issubdtype(ts.dtype, np.integer):
            raise TypeError(f"Candles.ts: expected an integer dtype (UTC ms), got {ts.dtype}")
        ts = ts.astype(np.int64, copy=False)
        object.__setattr__(self, "ts", ts)
        n = len(ts)
        for name in ("o", "h", "l", "c", "v"):
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            if arr.ndim != 1 or len(arr) != n:
                raise ValueError(
                    f"Candles.{name}: expected a 1-D array of length {n}, got shape {arr.shape}"
                )
            object.__setattr__(self, name, arr)
        if n > 1 and not np.all(np.diff(ts) > 0):
            raise ValueError("Candles.ts must be strictly increasing (sorted, unique)")

    def __len__(self) -> int:
        return len(self.ts)

    def index_at(self, ts_ms: int, *, exact: bool = False) -> int:
        """Index of the first candle with open time >= ``ts_ms`` (searchsorted, left).

        With ``exact=True`` the candle must exist, else ``KeyError``.
        """
        i = int(np.searchsorted(self.ts, ts_ms, side="left"))
        if exact and (i == len(self.ts) or self.ts[i] != ts_ms):
            raise KeyError(f"no {self.pair} {self.tf} candle opens at {ts_ms} ms")
        return i

    def slice(self, start_ms: int, end_ms: int) -> Candles:
        """Candles with ``start_ms <= ts < end_ms``, as views on the same arrays."""
        a = self.index_at(start_ms)
        b = self.index_at(end_ms)
        return Candles(
            self.pair, self.tf,
            self.ts[a:b], self.o[a:b], self.h[a:b], self.l[a:b], self.c[a:b], self.v[a:b],
        )

    def step_ms(self) -> int:
        """Nominal candle spacing in ms for this timeframe (60_000 or 900_000)."""
        try:
            return _STEP_MS[self.tf]
        except KeyError:
            raise ValueError(
                f"unknown timeframe {self.tf!r}; expected one of {sorted(_STEP_MS)}"
            ) from None


@dataclass(frozen=True, eq=False)  # eq=False: the generated __eq__ raises on ndarray fields
class Funding:
    """Funding events of one pair. ``ts`` is minute-rounded UTC ms, strictly increasing.

    ``rate`` is signed (longs pay when positive); ``interval_h`` is the
    interval published with each event (int8), not assumed to be 8.
    """

    pair: str
    ts: np.ndarray
    rate: np.ndarray
    interval_h: np.ndarray

    def __post_init__(self) -> None:
        ts = np.asarray(self.ts)
        if ts.ndim != 1:
            raise ValueError(f"Funding.ts: expected a 1-D array, got shape {ts.shape}")
        if not np.issubdtype(ts.dtype, np.integer):
            raise TypeError(f"Funding.ts: expected an integer dtype (UTC ms), got {ts.dtype}")
        ts = ts.astype(np.int64, copy=False)
        object.__setattr__(self, "ts", ts)
        n = len(ts)
        rate = np.asarray(self.rate, dtype=np.float64)
        interval = np.asarray(self.interval_h)
        if not np.issubdtype(interval.dtype, np.integer):
            raise TypeError(f"Funding.interval_h: expected an integer dtype, got {interval.dtype}")
        interval = interval.astype(np.int8, copy=False)
        for name, arr in (("rate", rate), ("interval_h", interval)):
            if arr.ndim != 1 or len(arr) != n:
                raise ValueError(
                    f"Funding.{name}: expected a 1-D array of length {n}, got shape {arr.shape}"
                )
        object.__setattr__(self, "rate", rate)
        object.__setattr__(self, "interval_h", interval)
        if n > 1 and not np.all(np.diff(ts) > 0):
            raise ValueError("Funding.ts must be strictly increasing (sorted, unique)")

    def __len__(self) -> int:
        return len(self.ts)

    def events_between(self, a_ms: int, b_ms: int) -> slice:
        """Indices of the events with ``a_ms <= ts <= b_ms`` (inclusive on both ends)."""
        i = int(np.searchsorted(self.ts, a_ms, side="left"))
        j = int(np.searchsorted(self.ts, b_ms, side="right"))
        return slice(i, max(i, j))


# --- source merge ---------------------------------------------------------------------


@dataclass(frozen=True)
class MergeStats:
    """What a write did: keys added; on overlap, stored rows replaced or kept; disagreements."""

    added: int
    replaced: int
    kept: int
    mismatches: int


def merge_rows(
    existing: pd.DataFrame | None,
    new: pd.DataFrame,
    *,
    key: str,
    compare: Sequence[str],
    rtol: float = 1e-9,
) -> tuple[pd.DataFrame, MergeStats]:
    """Union of ``existing`` and ``new`` by ``key`` with source precedence.

    The row with the lower ``SOURCE_RANK`` wins; on equal rank the incoming
    row wins (so re-ingesting a file refreshes it). A losing row whose
    ``compare`` columns differ from the winner's by more than ``rtol``
    relative counts as one mismatch. Both inputs must have unique keys.
    """
    if existing is None or len(existing) == 0:
        merged = new.sort_values(key, kind="stable").reset_index(drop=True)
        return merged, MergeStats(len(new), 0, 0, 0)
    both = pd.concat([existing.assign(_new=0), new.assign(_new=1)], ignore_index=True)
    rank = both["source"].map(SOURCE_RANK)
    if rank.isna().any():
        raise ValueError(f"unknown source {sorted(set(both.loc[rank.isna(), 'source']))}; expected {sorted(SOURCE_RANK)}")
    both["_rank"] = rank.astype(np.int64)
    both = both.sort_values([key, "_rank", "_new"], ascending=[True, True, False], kind="stable")
    loser = both.duplicated(key, keep="first")
    winners = both[~loser]
    losers = both[loser]
    overlap = len(losers)
    replaced = int((losers["_new"] == 0).sum())
    mismatches = 0
    if overlap:
        matched = winners.set_index(key).loc[losers[key].to_numpy()]
        bad = np.zeros(overlap, dtype=bool)
        for col in compare:
            a = losers[col].to_numpy(dtype=np.float64)
            b = matched[col].to_numpy(dtype=np.float64)
            bad |= ~np.isclose(a, b, rtol=rtol, atol=0.0)
        mismatches = int(bad.sum())
    merged = winners.drop(columns=["_new", "_rank"]).sort_values(key, kind="stable").reset_index(drop=True)
    return merged, MergeStats(len(new) - overlap, replaced, overlap - replaced, mismatches)


# --- manifests ------------------------------------------------------------------------


def new_candle_manifest(pair: str, tf: str) -> dict:
    return {
        "pair": pair,
        "tf": tf,
        "download_date": None,
        "files": [],
        "missing": [],
        "ccxt_ranges": [],
        "rows": 0,
        "first_open_ms": None,
        "last_open_ms": None,
        "gaps": [],
        "overlap_mismatches": 0,
        "consistency_1m_15m": None,
    }


def new_funding_manifest(pair: str) -> dict:
    return {
        "pair": pair,
        "download_date": None,
        "files": [],
        "missing": [],
        "ccxt_ranges": [],
        "rows": 0,
        "first_funding_ms": None,
        "last_funding_ms": None,
        "last_interval_h": None,
        "intervals": {},
        "bad_intervals": [],
        "overlap_mismatches": 0,
    }


# --- stores ---------------------------------------------------------------------------


def _empty_frame(dtypes: dict[str, object]) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in dtypes.items()})


def _replace(tmp: Path, path: Path, *, attempts: int = 10) -> None:
    """``os.replace`` that retries a transient PermissionError.

    On Windows a file that an antivirus or indexer has just opened cannot be
    replaced for a few hundred milliseconds; the first full fetch died on it.
    """
    for attempt in range(1, attempts + 1):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == attempts:
                raise
            time.sleep(0.1 * attempt)


def _write_parquet(path: Path, frame: pd.DataFrame) -> None:
    tmp = path.with_name(path.name + ".tmp")
    frame.to_parquet(tmp, index=False)
    _replace(tmp, path)


def _frozen(arr: np.ndarray) -> np.ndarray:
    arr.setflags(write=False)
    return arr


class _ParquetStore:
    """Shared layout, holdout guard, manifest I/O and year-partitioned reads and writes."""

    kind: str = ""

    def __init__(self, data_cfg: DataConfig) -> None:
        self.cfg = data_cfg
        self.root = Path(data_cfg.data_dir) / self.kind
        self.holdout_ms = insample_end_exclusive_ms(data_cfg)

    def _guard(self, end_ms: int, allow_holdout: bool, what: str) -> None:
        if end_ms > self.holdout_ms and not allow_holdout:
            raise HoldoutAccessError(
                f"{what}: end_ms {end_ms} reaches past insample_end {self.cfg.insample_end} "
                f"(the holdout starts at {self.holdout_ms} ms); allow_holdout=True is reserved "
                "for experiments/holdout.py"
            )

    @staticmethod
    def _year_files(d: Path) -> dict[int, Path]:
        if not d.is_dir():
            return {}
        return {int(p.stem): p for p in d.glob("*.parquet") if p.stem.isdigit()}

    @staticmethod
    def _check_frame(
        frame: pd.DataFrame,
        *,
        key: str,
        columns: Sequence[str],
        dtypes: dict[str, object],
        fixed: dict[str, str],
        finite: Sequence[str],
    ) -> pd.DataFrame:
        missing = [c for c in columns if c not in frame.columns and c not in fixed]
        if missing:
            raise ValueError(f"missing columns {missing}")
        frame = frame.copy()
        for col, value in fixed.items():
            if col in frame.columns and not bool((frame[col] == value).all()):
                raise ValueError(f"column {col!r} must be {value!r} in every row")
            frame[col] = value
        frame = frame[list(columns)].astype(dtypes)
        ts = frame[key].to_numpy()
        if len(ts) > 1 and not np.all(np.diff(ts) > 0):
            raise ValueError(f"{key} must be strictly increasing (sorted, unique)")
        values = frame[list(finite)].to_numpy(dtype=np.float64)
        if not np.isfinite(values).all():
            raise ValueError(f"columns {list(finite)} must be finite")
        unknown = set(frame["source"]) - set(SOURCE_RANK)
        if unknown:
            raise ValueError(f"unknown source {sorted(unknown)}; expected {sorted(SOURCE_RANK)}")
        return frame.reset_index(drop=True)

    def _write(
        self,
        d: Path,
        frame: pd.DataFrame,
        *,
        key: str,
        compare: Sequence[str],
        columns: Sequence[str],
        dtypes: dict[str, object],
        fixed: dict[str, str],
        finite: Sequence[str],
    ) -> MergeStats:
        frame = self._check_frame(frame, key=key, columns=columns, dtypes=dtypes, fixed=fixed, finite=finite)
        d.mkdir(parents=True, exist_ok=True)
        files = self._year_files(d)
        years = pd.to_datetime(frame[key], unit="ms", utc=True).dt.year.to_numpy()
        totals = np.zeros(4, dtype=np.int64)
        for year in np.unique(years):
            year = int(year)
            existing = pd.read_parquet(files[year]) if year in files else None
            merged, stats = merge_rows(existing, frame[years == year], key=key, compare=compare)
            _write_parquet(d / f"{year}.parquet", merged)
            totals += (stats.added, stats.replaced, stats.kept, stats.mismatches)
        return MergeStats(*(int(x) for x in totals))

    def _read(
        self,
        d: Path,
        *,
        key: str,
        dtypes: dict[str, object],
        start_ms: int | None,
        end_ms: int | None,
    ) -> pd.DataFrame:
        files = self._year_files(d)
        years = sorted(files)
        if start_ms is not None:
            years = [y for y in years if y >= year_of_ms(start_ms)]
        if end_ms is not None:
            years = [y for y in years if y <= year_of_ms(end_ms - 1)]
        parts = [pd.read_parquet(files[y]) for y in years]
        frame = pd.concat(parts, ignore_index=True) if parts else _empty_frame(dtypes)
        if start_ms is not None:
            frame = frame[frame[key] >= start_ms]
        if end_ms is not None:
            frame = frame[frame[key] < end_ms]
        return frame.reset_index(drop=True)

    @staticmethod
    def _manifest_path(d: Path) -> Path:
        return d / "manifest.json"

    def _read_manifest(self, d: Path, skeleton: dict) -> dict:
        path = self._manifest_path(d)
        if not path.exists():
            return skeleton
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _write_manifest(self, d: Path, manifest: dict) -> None:
        d.mkdir(parents=True, exist_ok=True)
        path = self._manifest_path(d)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)
            f.write("\n")
        _replace(tmp, path)


class CandleStore(_ParquetStore):
    """Year-partitioned candles under ``<data_dir>/candles/<pair>/<tf>/``."""

    kind = "candles"

    def dir(self, pair: str, tf: str) -> Path:
        if tf not in _STEP_MS:
            raise ValueError(f"unknown timeframe {tf!r}; expected one of {sorted(_STEP_MS)}")
        return self.root / pair / tf

    def write(self, pair: str, tf: str, frame: pd.DataFrame) -> MergeStats:
        """Merge ``frame`` (KLINE_FRAME_COLUMNS plus ``source``) into the year files. Idempotent."""
        return self._write(
            self.dir(pair, tf), frame, key="open_ms", compare=("open", "high", "low", "close"),
            columns=CANDLE_COLUMNS, dtypes=_CANDLE_DTYPES, fixed={"pair": pair, "tf": tf},
            finite=("open", "high", "low", "close", "volume"),
        )

    def read_frame(self, pair: str, tf: str, start_ms: int | None = None, end_ms: int | None = None) -> pd.DataFrame:
        """Storage-layer read, no holdout guard: rows with ``start_ms <= open_ms < end_ms``."""
        return self._read(self.dir(pair, tf), key="open_ms", dtypes=_CANDLE_DTYPES, start_ms=start_ms, end_ms=end_ms)

    def load(self, pair: str, tf: str, start_ms: int, end_ms: int, *, allow_holdout: bool = False) -> Candles:
        """Candles with ``start_ms <= open_ms < end_ms`` as read-only arrays (spec §1.3).

        Raises HoldoutAccessError when ``end_ms`` reaches past ``insample_end``
        unless ``allow_holdout=True``; ``start_ms`` may precede ``insample_start``.
        """
        self._guard(end_ms, allow_holdout, f"CandleStore.load({pair}, {tf})")
        if start_ms >= end_ms:
            raise ValueError(f"CandleStore.load: start_ms {start_ms} must be < end_ms {end_ms}")
        d = self.dir(pair, tf)
        if not d.is_dir():
            raise FileNotFoundError(f"no stored candles for {pair} {tf} under {d}")
        frame = self.read_frame(pair, tf, start_ms, end_ms)

        def col(name: str, dtype: type) -> np.ndarray:
            return _frozen(frame[name].to_numpy(dtype=dtype, copy=True))

        return Candles(
            pair, tf, col("open_ms", np.int64), col("open", np.float64), col("high", np.float64),
            col("low", np.float64), col("close", np.float64), col("volume", np.float64),
        )

    def manifest(self, pair: str, tf: str) -> dict:
        """The stored manifest, or a fresh skeleton (``files == []``) when none exists."""
        return self._read_manifest(self.dir(pair, tf), new_candle_manifest(pair, tf))

    def write_manifest(self, pair: str, tf: str, manifest: dict) -> None:
        self._write_manifest(self.dir(pair, tf), manifest)

    def manifest_path(self, pair: str, tf: str) -> Path:
        return self._manifest_path(self.dir(pair, tf))


class FundingStore(_ParquetStore):
    """Year-partitioned funding events under ``<data_dir>/funding/<pair>/``."""

    kind = "funding"

    def dir(self, pair: str) -> Path:
        return self.root / pair

    def write(self, pair: str, frame: pd.DataFrame) -> MergeStats:
        """Merge ``frame`` (FUNDING_FRAME_COLUMNS plus ``source``) into the year files. Idempotent."""
        return self._write(
            self.dir(pair), frame, key="funding_ms", compare=("rate",), columns=FUNDING_COLUMNS,
            dtypes=_FUNDING_DTYPES, fixed={"pair": pair}, finite=("rate",),
        )

    def read_frame(self, pair: str, start_ms: int | None = None, end_ms: int | None = None) -> pd.DataFrame:
        """Storage-layer read, no holdout guard: rows with ``start_ms <= funding_ms < end_ms``."""
        return self._read(self.dir(pair), key="funding_ms", dtypes=_FUNDING_DTYPES, start_ms=start_ms, end_ms=end_ms)

    def load(self, pair: str, start_ms: int, end_ms: int, *, allow_holdout: bool = False) -> Funding:
        """Funding with ``start_ms <= funding_ms < end_ms`` as read-only arrays; same guard as candles."""
        self._guard(end_ms, allow_holdout, f"FundingStore.load({pair})")
        if start_ms >= end_ms:
            raise ValueError(f"FundingStore.load: start_ms {start_ms} must be < end_ms {end_ms}")
        d = self.dir(pair)
        if not d.is_dir():
            raise FileNotFoundError(f"no stored funding for {pair} under {d}")
        frame = self.read_frame(pair, start_ms, end_ms)
        return Funding(
            pair,
            _frozen(frame["funding_ms"].to_numpy(dtype=np.int64, copy=True)),
            _frozen(frame["rate"].to_numpy(dtype=np.float64, copy=True)),
            _frozen(frame["interval_h"].to_numpy(dtype=np.int8, copy=True)),
        )

    def manifest(self, pair: str) -> dict:
        return self._read_manifest(self.dir(pair), new_funding_manifest(pair))

    def write_manifest(self, pair: str, manifest: dict) -> None:
        self._write_manifest(self.dir(pair), manifest)

    def manifest_path(self, pair: str) -> Path:
        return self._manifest_path(self.dir(pair))
