"""ETF configuration types (ETF spec overview §6; Phase 0 §0.1 the data config; Phase 1 §1.5 the sizing;
Phase 2 §2.1 the remaining prereg blocks: rebalance, costs, cash, reprice).

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

from perpbt.config import (
    ConfigError, _check_iso_date, _check_literal, _check_non_negative, _check_positive, _Config, from_dict,
    read_yaml_unique,
)
from perpbt.data.store import DAY_MS, date_ms
from perpbt.etf.store import ETF_HOLDOUT_START

RATE_SOURCES = ("fred",)
CROSS_CHECK_SOURCES = ("nasdaq",)
ANNUALISE_DAYS = 252  # US trading days a year (overview §7)
CASH_DAY_COUNT = 360  # T-bill money-market convention (Phase 1 §1.3)
FREQUENCIES = ("daily", "weekly")
WEEKDAYS = ("friday",)  # the week's last trading day (Phase 2 §2.1); Phase 3 handles a Friday holiday
FILLS = ("next_open", "next_close")
MAX_COST = 0.1  # a cost per side at or above 10% is a typo, not a scenario
_TICKER = re.compile(r"^[A-Z]{1,5}$")

__all__ = ["ANNUALISE_DAYS", "CASH_DAY_COUNT", "CrossCheckSource", "EtfCashConfig", "EtfCostConfig",
           "EtfDataConfig", "EtfRebalanceConfig", "EtfRepriceConfig", "EtfSizingConfig", "FILLS", "FREQUENCIES",
           "RatesSource", "WEEKDAYS", "load_data_config"]


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
    dividend_tickers: tuple[str, ...]  # must have an independent dividend history (rule 6 fails without one)
    min_span_days: int  # the cross-check prices must span at least this many calendar days, ending with Yahoo's

    def _validate(self) -> None:
        if self.min_span_days < 1:
            raise ConfigError(f"CrossCheckSource.min_span_days: must be >= 1, got {self.min_span_days}")
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
        if not set(self.cross_check.dividend_tickers) <= set(self.tickers):
            raise ConfigError(f"CrossCheckSource.dividend_tickers: {list(self.cross_check.dividend_tickers)} are not "
                              f"all in tickers {list(self.tickers)}")
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


@dataclass(frozen=True)
class EtfSizingConfig(_Config):
    """The prereg ``sizing`` block (Phase 1 §1.5, Phase 2 §2.1): ``w* = e × min(target_vol / σ̂, max_weight)``.

    ``target_vol`` None is the ``no_vol_target`` row (``w* = e × max_weight``).
    ``trend.signals.target_weight`` reads only ``target_vol`` and ``max_weight``
    (a test checks), so it takes this config as it is.
    """

    target_vol: float | None  # annualised, 0.15; None: no vol targeting
    vol_window: int  # daily total returns in σ̂, 60
    max_weight: float  # cap on w* per ETF, in (0, 1]: a cash account does not borrow
    annualise_days: int  # 252 (trading days); fixed, σ̂ uses √252

    def _validate(self) -> None:
        if self.target_vol is not None:
            _check_positive(self.target_vol, "EtfSizingConfig.target_vol")
        if self.vol_window < 2:
            raise ConfigError(f"EtfSizingConfig.vol_window: must be >= 2, got {self.vol_window}")
        if not (math.isfinite(self.max_weight) and 0 < self.max_weight <= 1):
            raise ConfigError(f"EtfSizingConfig.max_weight: must be in (0, 1], got {self.max_weight!r}")
        if self.annualise_days != ANNUALISE_DAYS:
            raise ConfigError(f"EtfSizingConfig.annualise_days: must be {ANNUALISE_DAYS}, got {self.annualise_days}")


@dataclass(frozen=True)
class EtfRebalanceConfig(_Config):
    """The prereg ``rebalance`` block (Phase 2 §2.1): when positions trade and at which price (Phase 3)."""

    frequency: str  # "daily" | "weekly"
    weekday: str | None  # weekly only: "friday", the week's last trading day decides
    drift_band: float  # relative: trade when |w_held − w*| > drift_band × w*
    fill: str  # "next_open" | "next_close": the trading day after the decision

    def _validate(self) -> None:
        _check_literal(self.frequency, FREQUENCIES, "EtfRebalanceConfig.frequency")
        if self.frequency == "daily" and self.weekday is not None:
            raise ConfigError(f"EtfRebalanceConfig.weekday: must be null for daily rebalancing, got {self.weekday!r}")
        if self.frequency == "weekly":
            if self.weekday is None:
                raise ConfigError("EtfRebalanceConfig.weekday: weekly rebalancing needs a decision weekday")
            _check_literal(self.weekday, WEEKDAYS, "EtfRebalanceConfig.weekday")
        if not math.isfinite(self.drift_band):
            raise ConfigError(f"EtfRebalanceConfig.drift_band: must be finite, got {self.drift_band!r}")
        _check_non_negative(self.drift_band, "EtfRebalanceConfig.drift_band")
        _check_literal(self.fill, FILLS, "EtfRebalanceConfig.fill")


def _check_cost(x: float, where: str) -> None:
    if not (math.isfinite(x) and 0 <= x < MAX_COST):
        raise ConfigError(f"{where}: must be in [0, {MAX_COST}), got {x!r}")


@dataclass(frozen=True)
class EtfCostConfig(_Config):
    """The prereg ``costs`` block: commission and slippage, fractions of traded value per side."""

    commission: float
    slippage: dict[str, float]  # ticker -> fraction; "default" for the others

    def _validate(self) -> None:
        _check_cost(self.commission, "EtfCostConfig.commission")
        if "default" not in self.slippage:
            raise ConfigError("EtfCostConfig.slippage: needs a 'default' entry")
        for key, value in self.slippage.items():
            if key != "default" and not _TICKER.fullmatch(key):
                raise ConfigError(f"EtfCostConfig.slippage: expected upper-case tickers or 'default', got {key!r}")
            _check_cost(value, f"EtfCostConfig.slippage[{key!r}]")

    def slippage_for(self, ticker: str) -> float:
        return self.slippage.get(ticker, self.slippage["default"])


@dataclass(frozen=True)
class EtfCashConfig(_Config):
    """The prereg ``cash`` block: idle cash earns this FRED series, accrued per calendar day over ``day_count``."""

    series: str  # "DTB3"; must be data.yaml's rates.series
    day_count: int  # 360: what frame.cash_returns computes

    def _validate(self) -> None:
        if not re.fullmatch(r"[A-Z0-9]+", self.series):
            raise ConfigError(f"EtfCashConfig.series: expected a FRED series id, got {self.series!r}")
        if self.day_count != CASH_DAY_COUNT:
            raise ConfigError(f"EtfCashConfig.day_count: must be {CASH_DAY_COUNT} (frame.cash_returns), "
                              f"got {self.day_count}")


@dataclass(frozen=True)
class EtfRepriceConfig(_Config):
    """The prereg ``reprice`` grid: slippage × commission, each per side (Phase 4)."""

    slippage: tuple[float, ...]
    commission: tuple[float, ...]

    def _validate(self) -> None:
        for name in ("slippage", "commission"):
            values = getattr(self, name)
            if not values:
                raise ConfigError(f"EtfRepriceConfig.{name}: must not be empty")
            if len(set(values)) != len(values):
                raise ConfigError(f"EtfRepriceConfig.{name}: duplicates in {list(values)}")
            for k, x in enumerate(values):
                _check_cost(x, f"EtfRepriceConfig.{name}[{k}]")


def load_data_config(path: str | Path) -> EtfDataConfig:
    """``configs/etf/data.yaml`` as an ``EtfDataConfig``; duplicate, unknown or missing keys raise ConfigError."""
    raw = read_yaml_unique(path)
    if raw is None:
        raw = {}
    return from_dict(EtfDataConfig, raw)
