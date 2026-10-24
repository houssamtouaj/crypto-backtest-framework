"""ETF configuration types (ETF spec overview §6; Phase 0 §0.1 the data config).

Frozen dataclasses on the first strategy's ``_Config`` base: fields are
coerced to their annotation, validated, and hash through
``config.canonical_json``. ``load_data_config`` reads
``configs/etf/data.yaml`` with the duplicate-key loader.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from perpbt.config import ConfigError, _check_iso_date, _Config, from_dict, read_yaml_unique
from perpbt.data.store import DAY_MS, date_ms
from perpbt.etf.store import ETF_HOLDOUT_START

RATE_SOURCES = ("fred",)
CROSS_CHECK_SOURCES = ("nasdaq",)
_TICKER = re.compile(r"^[A-Z]{1,5}$")

__all__ = ["CrossCheckSource", "EtfDataConfig", "RatesSource", "load_data_config"]


@dataclass(frozen=True)
class RatesSource(_Config):
    source: str  # "fred"
    series: str  # the FRED series id, "DTB3"

    def _validate(self) -> None:
        if self.source not in RATE_SOURCES:
            raise ConfigError(f"RatesSource.source: expected one of {list(RATE_SOURCES)}, got {self.source!r}")
        if not re.fullmatch(r"[A-Z0-9]+", self.series):
            raise ConfigError(f"RatesSource.series: expected a FRED series id, got {self.series!r}")


@dataclass(frozen=True)
class CrossCheckSource(_Config):
    source: str  # "nasdaq" (Stooq serves a bot check since 2026; Phase 0 §0.9)
    close_tol: float  # every common day within this, on levels or on returns

    def _validate(self) -> None:
        if self.source not in CROSS_CHECK_SOURCES:
            raise ConfigError(
                f"CrossCheckSource.source: expected one of {list(CROSS_CHECK_SOURCES)}, got {self.source!r}"
            )
        if not math.isfinite(self.close_tol) or not 0 < self.close_tol < 1:
            raise ConfigError(f"CrossCheckSource.close_tol: must be in (0, 1), got {self.close_tol!r}")


@dataclass(frozen=True)
class EtfDataConfig(_Config):
    data_dir: str
    tickers: tuple[str, ...]
    warmup_start: str  # the common first trading day (GLD's)
    insample_start: str
    insample_end: str
    holdout_start: str  # must be the store's constant ETF_HOLDOUT_START
    holdout_end: str | None  # set by the fetch: the last full US trading day before the download day
    download_date: str | None  # set by the fetch (UTC date)
    rates: RatesSource
    cross_check: CrossCheckSource
    corrections: str  # path of the reviewed corrections file (perpbt/etf/corrections.py)

    def _validate(self) -> None:
        if not self.tickers:
            raise ConfigError("EtfDataConfig.tickers: must not be empty")
        for t in self.tickers:
            if not _TICKER.fullmatch(t):
                raise ConfigError(f"EtfDataConfig.tickers: expected upper-case tickers, got {t!r}")
        if len(set(self.tickers)) != len(self.tickers):
            raise ConfigError(f"EtfDataConfig.tickers: duplicates in {list(self.tickers)}")
        names = ("warmup_start", "insample_start", "insample_end", "holdout_start")
        for name in names:
            _check_iso_date(getattr(self, name), f"EtfDataConfig.{name}")
        w0, i0, i1, h0 = (getattr(self, n) for n in names)
        if not w0 <= i0 <= i1 < h0:
            raise ConfigError(
                "EtfDataConfig: need warmup_start <= insample_start <= insample_end < holdout_start, "
                f"got {w0}, {i0}, {i1}, {h0}"
            )
        if h0 != ETF_HOLDOUT_START:
            raise ConfigError(f"EtfDataConfig.holdout_start: must be {ETF_HOLDOUT_START} (the store's guard), got {h0}")
        if date_ms(h0) != date_ms(i1) + DAY_MS:
            raise ConfigError(f"EtfDataConfig.holdout_start: must be the day after insample_end ({i1}), got {h0}")
        if (self.holdout_end is None) != (self.download_date is None):
            raise ConfigError("EtfDataConfig: holdout_end and download_date are set together (by the fetch)")
        if self.holdout_end is not None:
            _check_iso_date(self.holdout_end, "EtfDataConfig.holdout_end")
            _check_iso_date(self.download_date, "EtfDataConfig.download_date")
            if self.holdout_end < h0:
                raise ConfigError(f"EtfDataConfig.holdout_end: must be >= holdout_start ({h0}), got {self.holdout_end}")
            if self.holdout_end >= self.download_date:
                raise ConfigError(
                    f"EtfDataConfig.holdout_end: must be before download_date ({self.download_date}), "
                    f"got {self.holdout_end}"
                )


def load_data_config(path: str | Path) -> EtfDataConfig:
    """``configs/etf/data.yaml`` as an ``EtfDataConfig``; duplicate, unknown or missing keys raise ConfigError."""
    raw = read_yaml_unique(path)
    if raw is None:
        raw = {}
    return from_dict(EtfDataConfig, raw)
