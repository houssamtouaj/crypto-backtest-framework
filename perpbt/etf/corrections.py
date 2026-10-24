"""Reviewed corrections to the Yahoo data, and accepted cross-check differences (ETF spec Phase 0 §0.9).

``configs/etf/corrections.yaml`` has three lists, each entry with a
``reason``:

- ``prices``: replace some of a day's open, high, low, close (a stale Yahoo
  bar: the previous close copied into the open and one extreme);
- ``dividends``: set a day's cash dividend (one Yahoo lacks);
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
    open: float | None = None  # None: keep Yahoo's
    high: float | None = None
    low: float | None = None
    close: float | None = None

    def _validate(self) -> None:
        _check_iso_date(self.date, "PriceFix.date")
        if all(getattr(self, k) is None for k in ("open", "high", "low", "close")):
            raise ConfigError(f"PriceFix {self.ticker} {self.date}: sets no price")
        for k in ("open", "high", "low", "close"):
            _positive(getattr(self, k), f"PriceFix.{k}")
        _reason(self.reason, "PriceFix")


@dataclass(frozen=True)
class DividendFix(_Config):
    ticker: str
    date: str
    amount: float
    reason: str

    def _validate(self) -> None:
        _check_iso_date(self.date, "DividendFix.date")
        _positive(self.amount, "DividendFix.amount")
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


def apply(data: EtfData, corrections: Corrections) -> EtfData:
    """``data`` with the price and dividend fixes applied; a fix for an unknown ticker or day raises ValueError."""
    prices = {t: f.copy() for t, f in data.prices.items()}
    actions = {t: f.copy() for t, f in data.actions.items()}
    for fix in corrections.prices:
        if fix.ticker not in prices:
            raise ValueError(f"corrections: price fix for unknown ticker {fix.ticker}")
        f = prices[fix.ticker]
        rows = np.flatnonzero(f["day_ms"].to_numpy() == date_ms(fix.date))
        if len(rows) != 1:
            raise ValueError(f"corrections: {fix.ticker} has no price row on {fix.date}")
        for k in ("open", "high", "low", "close"):
            if getattr(fix, k) is not None:
                f.loc[rows[0], k] = getattr(fix, k)
    for fix in corrections.dividends:
        if fix.ticker not in actions:
            raise ValueError(f"corrections: dividend fix for unknown ticker {fix.ticker}")
        day = date_ms(fix.date)
        if not (prices[fix.ticker]["day_ms"] == day).any():
            raise ValueError(f"corrections: {fix.ticker} has no price row on {fix.date}")
        a = actions[fix.ticker]
        hit = a["day_ms"] == day
        if hit.any():
            a.loc[hit, "dividend"] = fix.amount
        else:
            a = pd.concat([a, pd.DataFrame([{"day_ms": day, "dividend": fix.amount, "split": 0.0,
                                             "capital_gain": 0.0}], columns=list(ACTION_COLUMNS))])
            a = a.sort_values("day_ms", kind="stable").reset_index(drop=True)
        actions[fix.ticker] = a
    return EtfData(prices=prices, actions=actions, rates=data.rates, xprices=data.xprices,
                   xdividends=data.xdividends)
