"""Reviewed corrections to the Yahoo data, and accepted cross-check differences (ETF spec Phase 0 §0.9).

``configs/etf/corrections.yaml`` has three lists, each entry with a
``reason``:

- ``prices``: replace some of a day's open, high, low, close (a stale Yahoo
  bar: the previous close copied into the open and one extreme);
- ``dividends``: set a day's cash dividend (one Yahoo lacks), or 0 to remove
  one (a duplicate);

Every fix records in ``was`` the Yahoo value it replaces; ``apply`` refuses
a fix whose ``was`` no longer matches (a re-fetch changed Yahoo's value), so
a fix cannot go stale silently either.
- ``accepted``: a cross-check difference (``open``, ``close`` or
  ``dividend``) reviewed and left as Yahoo has it.

The fetch applies ``prices`` and ``dividends`` to the parsed Yahoo frames
before validation (``apply``); rule 6 lists ``accepted`` differences instead
of failing on them, and fails on an accepted difference that no longer
occurs, so the file cannot go stale silently. The manifest records the
content's hash (``corrections_hash``); ``validate_store`` refuses a changed file.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from perpbt.config import ConfigError, _check_iso_date, _Config, config_hash, from_dict, read_yaml_unique, to_dict
from perpbt.data.store import date_ms
from perpbt.etf.store import ACTION_COLUMNS, EtfData

CHECKS = ("open", "close", "dividend")
PRICE_FIELDS = ("open", "high", "low", "close")
WAS_TOL = 1e-6  # relative: Yahoo's float32 noise; a re-fetch that changed the value by more is a stale fix

__all__ = ["CHECKS", "Accepted", "Corrections", "DividendFix", "PriceFix", "apply", "corrections_hash",
           "load_corrections"]


def _reason(value: str, where: str) -> None:
    if not value.strip():
        raise ConfigError(f"{where}.reason: must not be empty")


def _positive(value: float | None, where: str) -> None:
    if value is not None and not (math.isfinite(value) and value > 0):
        raise ConfigError(f"{where}: must be positive and finite, got {value!r}")


@dataclass(frozen=True)
class PriceFix(_Config):
    ticker: str
    date: str
    reason: str
    was: dict[str, float]  # Yahoo's value of every field set here; apply refuses when Yahoo no longer has it
    open: float | None = None  # None: keep Yahoo's
    high: float | None = None
    low: float | None = None
    close: float | None = None

    def fields_set(self) -> dict[str, float]:
        return {k: getattr(self, k) for k in PRICE_FIELDS if getattr(self, k) is not None}

    def _validate(self) -> None:
        _check_iso_date(self.date, "PriceFix.date")
        if not self.fields_set():
            raise ConfigError(f"PriceFix {self.ticker} {self.date}: sets no price")
        for k in PRICE_FIELDS:
            _positive(getattr(self, k), f"PriceFix.{k}")
        if sorted(self.was) != sorted(self.fields_set()):
            raise ConfigError(f"PriceFix {self.ticker} {self.date}: was must give Yahoo's value of exactly "
                              f"{sorted(self.fields_set())}, got {sorted(self.was)}")
        for k, v in self.was.items():
            _positive(v, f"PriceFix.was[{k!r}]")
        _reason(self.reason, "PriceFix")


@dataclass(frozen=True)
class DividendFix(_Config):
    ticker: str
    date: str
    amount: float  # 0 removes Yahoo's dividend (a duplicate)
    was: float  # Yahoo's dividend that day, 0 when it has none; apply refuses when Yahoo no longer has it
    reason: str

    def _validate(self) -> None:
        _check_iso_date(self.date, "DividendFix.date")
        for name in ("amount", "was"):
            v = getattr(self, name)
            if not (math.isfinite(v) and v >= 0):
                raise ConfigError(f"DividendFix.{name}: must be finite and >= 0, got {v!r}")
        if self.amount == self.was:
            raise ConfigError(f"DividendFix {self.ticker} {self.date}: amount equals was; nothing to fix")
        _reason(self.reason, "DividendFix")


@dataclass(frozen=True)
class Accepted(_Config):
    ticker: str
    date: str
    check: str
    reason: str

    def _validate(self) -> None:
        _check_iso_date(self.date, "Accepted.date")
        if self.check not in CHECKS:
            raise ConfigError(f"Accepted.check: expected one of {list(CHECKS)}, got {self.check!r}")
        _reason(self.reason, "Accepted")


@dataclass(frozen=True)
class Corrections(_Config):
    prices: tuple[PriceFix, ...]
    dividends: tuple[DividendFix, ...]
    accepted: tuple[Accepted, ...]

    def _validate(self) -> None:
        for name, keyf in (("prices", lambda e: (e.ticker, e.date)), ("dividends", lambda e: (e.ticker, e.date)),
                           ("accepted", lambda e: (e.ticker, e.date, e.check))):
            keys = [keyf(e) for e in getattr(self, name)]
            if len(set(keys)) != len(keys):
                raise ConfigError(f"Corrections.{name}: duplicate entries")

    def accepted_keys(self) -> set[tuple[str, str, str]]:
        return {(a.ticker, a.date, a.check) for a in self.accepted}

    def as_dict(self) -> dict:
        return to_dict(self)


def load_corrections(path: str | Path) -> Corrections:
    raw = read_yaml_unique(path)
    return from_dict(Corrections, raw if raw is not None else {})


def corrections_hash(corrections: Corrections) -> str:
    """SHA-256 of the canonical JSON of the parsed content: line endings and comments do not count (git may turn
    the file's LF into CRLF on checkout)."""
    return config_hash(corrections)


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= WAS_TOL * max(abs(a), abs(b), 1e-12)


def apply(data: EtfData, corrections: Corrections) -> EtfData:
    """``data`` with the price and dividend fixes applied.

    Raises ValueError for a fix on an unknown ticker or day, and for a stale fix: Yahoo's value is no longer the
    ``was`` the fix was reviewed against (a re-fetch changed it), so the fix must be reviewed again.
    """
    prices = {t: f.copy() for t, f in data.prices.items()}
    actions = {t: f.copy() for t, f in data.actions.items()}
    for fix in corrections.prices:
        if fix.ticker not in prices:
            raise ValueError(f"corrections: price fix for unknown ticker {fix.ticker}")
        f = prices[fix.ticker]
        rows = np.flatnonzero(f["day_ms"].to_numpy() == date_ms(fix.date))
        if len(rows) != 1:
            raise ValueError(f"corrections: {fix.ticker} has no price row on {fix.date}")
        for k, value in fix.fields_set().items():
            if not _same(float(f.loc[rows[0], k]), fix.was[k]):
                raise ValueError(f"corrections: stale price fix {fix.ticker} {fix.date}: Yahoo's {k} is "
                                 f"{f.loc[rows[0], k]!r}, the fix was reviewed against {fix.was[k]!r}")
            f.loc[rows[0], k] = value
    for fix in corrections.dividends:
        if fix.ticker not in actions:
            raise ValueError(f"corrections: dividend fix for unknown ticker {fix.ticker}")
        day = date_ms(fix.date)
        if not (prices[fix.ticker]["day_ms"] == day).any():
            raise ValueError(f"corrections: {fix.ticker} has no price row on {fix.date}")
        a = actions[fix.ticker]
        hit = a["day_ms"] == day
        current = float(a.loc[hit, "dividend"].iloc[0]) if hit.any() else 0.0
        if not _same(current, fix.was):
            raise ValueError(f"corrections: stale dividend fix {fix.ticker} {fix.date}: Yahoo's dividend is "
                             f"{current!r}, the fix was reviewed against {fix.was!r}")
        if hit.any():
            a.loc[hit, "dividend"] = fix.amount
            a = a[(a[["dividend", "split", "capital_gain"]] != 0).any(axis=1)].reset_index(drop=True)
        else:
            a = pd.concat([a, pd.DataFrame([{"day_ms": day, "dividend": fix.amount, "split": 0.0,
                                             "capital_gain": 0.0}], columns=list(ACTION_COLUMNS))])
            a = a.sort_values("day_ms", kind="stable").reset_index(drop=True)
        actions[fix.ticker] = a
    return EtfData(prices=prices, actions=actions, rates=data.rates, xprices=data.xprices,
                   xdividends=data.xdividends)
