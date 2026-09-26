"""Configuration types: frozen dataclasses, YAML round-trip, canonical JSON, hashes.

Every type here is immutable and fully explicit. Field values are coerced to
the declared annotation on construction (so ``r_target=2`` and
``r_target=2.0`` are the same config and hash the same) and then validated.
YAML loading goes through :func:`from_dict`, which rejects unknown keys.
Hashes are over the canonical JSON form (sorted keys, compact separators,
floats rendered by ``repr``).
"""
from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import math
import re
import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

__all__ = [
    "ConfigError",
    "SessionSpec",
    "StopBuffer",
    "HoldRule",
    "StrategyParams",
    "ExecConfig",
    "StatsConfig",
    "DataConfig",
    "VariantConfig",
    "from_dict",
    "to_dict",
    "canonical_json",
    "config_hash",
    "load_yaml",
    "dump_yaml",
]

T = TypeVar("T")


class ConfigError(ValueError):
    """Unknown or missing keys, wrong types, or invalid values in a config."""


# --- type-directed coercion -----------------------------------------------------


@functools.lru_cache(maxsize=None)
def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def _coerce(tp: Any, value: Any, where: str) -> Any:
    """Return ``value`` converted to annotation ``tp``, or raise ConfigError."""
    if isinstance(tp, type) and is_dataclass(tp):
        if isinstance(value, tp):
            return value
        if isinstance(value, Mapping):
            return from_dict(tp, value)
        raise ConfigError(f"{where}: expected a {tp.__name__} mapping, got {type(value).__name__}")

    origin = typing.get_origin(tp)
    if origin is None:
        if tp is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(f"{where}: expected a number, got {value!r}")
            try:
                v = float(value)
            except OverflowError:
                raise ConfigError(f"{where}: number too large, got {value!r}") from None
            if not math.isfinite(v):
                raise ConfigError(f"{where}: must be finite, got {value!r}")
            return v + 0.0  # normalises -0.0 to 0.0 so equal configs hash equal
        if tp is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(f"{where}: expected an integer, got {value!r}")
            return value
        if tp is bool:
            if not isinstance(value, bool):
                raise ConfigError(f"{where}: expected true/false, got {value!r}")
            return value
        if tp is str:
            if isinstance(value, str):
                return value
            if isinstance(value, date) and not isinstance(value, datetime):
                return value.isoformat()  # YAML parses an unquoted 2020-01-01 as a date
            if isinstance(value, int) and not isinstance(value, bool):
                raise ConfigError(
                    f"{where}: expected a string, got {value!r} "
                    "(YAML reads unquoted values like 24:00 as numbers; quote them: '24:00')"
                )
            raise ConfigError(f"{where}: expected a string, got {value!r}")
        raise ConfigError(f"{where}: unsupported field type {tp!r}")

    args = typing.get_args(tp)
    if origin in (types.UnionType, typing.Union):
        if value is None:
            if type(None) in args:
                return None
            raise ConfigError(f"{where}: null is not allowed")
        inner = [a for a in args if a is not type(None)]
        if len(inner) != 1:
            raise ConfigError(f"{where}: unsupported field type {tp!r}")
        return _coerce(inner[0], value, where)
    if origin is tuple:
        if isinstance(value, str) or not isinstance(value, (list, tuple)):
            raise ConfigError(f"{where}: expected a list, got {value!r}")
        if len(args) != 2 or args[1] is not Ellipsis:
            raise ConfigError(f"{where}: unsupported field type {tp!r}")
        return tuple(_coerce(args[0], v, f"{where}[{k}]") for k, v in enumerate(value))
    if origin is dict:
        if not isinstance(value, Mapping):
            raise ConfigError(f"{where}: expected a mapping, got {value!r}")
        key_tp, val_tp = args
        return {
            _coerce(key_tp, k, f"{where} key"): _coerce(val_tp, v, f"{where}[{k!r}]")
            for k, v in value.items()
        }
    raise ConfigError(f"{where}: unsupported field type {tp!r}")


def from_dict(cls: type[T], data: Mapping[str, Any]) -> T:
    """Build dataclass ``cls`` from a mapping. Unknown or missing keys raise."""
    if not (isinstance(cls, type) and is_dataclass(cls)):
        raise TypeError(f"{cls!r} is not a dataclass type")
    if not isinstance(data, Mapping):
        raise ConfigError(f"{cls.__name__}: expected a mapping, got {type(data).__name__}")
    names = [f.name for f in fields(cls)]
    unknown = [k for k in data if k not in names]
    if unknown:
        raise ConfigError(f"{cls.__name__}: unknown keys {unknown}")
    missing = [
        f.name
        for f in fields(cls)
        if f.name not in data
        and f.default is dataclasses.MISSING
        and f.default_factory is dataclasses.MISSING
    ]
    if missing:
        raise ConfigError(f"{cls.__name__}: missing required keys {missing}")
    return cls(**dict(data))  # __post_init__ coerces and validates


def _plain(x: Any) -> Any:
    if isinstance(x, datetime):
        raise ConfigError("canonical_json: datetime values are not allowed; use an ISO date string")
    if isinstance(x, date):
        return x.isoformat()
    if is_dataclass(x) and not isinstance(x, type):
        return _plain(dataclasses.asdict(x))
    if isinstance(x, Mapping):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return x


def to_dict(obj: Any) -> Any:
    """Nested plain dicts and lists (tuples become lists), ready for YAML or JSON."""
    return _plain(obj)


# --- validation helpers --------------------------------------------------------

_HHMM = re.compile(r"^(\d{2}):(\d{2})$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _check_hhmm(value: str, where: str, *, allow_24: bool) -> None:
    m = _HHMM.fullmatch(value)
    if not m:
        raise ConfigError(f"{where}: expected 'HH:MM', got {value!r}")
    hh, mm = int(m[1]), int(m[2])
    if mm > 59 or hh > 24 or (hh == 24 and (mm != 0 or not allow_24)):
        raise ConfigError(f"{where}: invalid time {value!r}")


def _check_iso_date(value: str, where: str) -> None:
    if not _ISO_DATE.fullmatch(value):
        raise ConfigError(f"{where}: expected an ISO date 'YYYY-MM-DD', got {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ConfigError(f"{where}: invalid date {value!r}") from None


def _check_literal(value: str, allowed: tuple[str, ...], where: str) -> None:
    if value not in allowed:
        raise ConfigError(f"{where}: expected one of {list(allowed)}, got {value!r}")


def _check_positive(value: float, where: str) -> None:
    if not value > 0:
        raise ConfigError(f"{where}: must be > 0, got {value!r}")


def _check_non_negative(value: float, where: str) -> None:
    if value < 0:
        raise ConfigError(f"{where}: must be >= 0, got {value!r}")


# --- dataclasses ---------------------------------------------------------------


@dataclass(frozen=True)
class _Config:
    """Base: coerce every field to its annotation, then run ``_validate``."""

    def __post_init__(self) -> None:
        hints = _hints(type(self))
        for f in fields(self):
            where = f"{type(self).__name__}.{f.name}"
            object.__setattr__(self, f.name, _coerce(hints[f.name], getattr(self, f.name), where))
        self._validate()

    def _validate(self) -> None:
        pass


@dataclass(frozen=True)
class SessionSpec(_Config):
    name: str  # "utc" | "ny" | "london"
    tz: str  # IANA zone, e.g. "America/New_York"
    open: str  # "HH:MM" local
    close: str  # "HH:MM" local; "24:00" allowed
    days: tuple[int, ...]  # ISO weekday numbers Mon=0..Sun=6, in session-local time

    def _validate(self) -> None:
        try:
            ZoneInfo(self.tz)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise ConfigError(f"SessionSpec.tz: unknown IANA zone {self.tz!r}") from None
        _check_hhmm(self.open, "SessionSpec.open", allow_24=False)
        _check_hhmm(self.close, "SessionSpec.close", allow_24=True)
        if not self.days:
            raise ConfigError("SessionSpec.days: must not be empty")
        bad = [d for d in self.days if not 0 <= d <= 6]
        if bad:
            raise ConfigError(f"SessionSpec.days: weekdays must be 0 (Mon) .. 6 (Sun), got {bad}")
        if len(set(self.days)) != len(self.days):
            raise ConfigError(f"SessionSpec.days: duplicate weekday in {list(self.days)}")


@dataclass(frozen=True)
class StopBuffer(_Config):
    kind: str  # "atr" | "pct"
    value: float  # multiple of ATR14, or fraction of entry price

    def _validate(self) -> None:
        _check_literal(self.kind, ("atr", "pct"), "StopBuffer.kind")
        _check_non_negative(self.value, "StopBuffer.value")


@dataclass(frozen=True)
class HoldRule(_Config):
    kind: str  # "none" | "session_end" | "max_hold"
    hours: float | None = None  # required iff kind == "max_hold"

    def _validate(self) -> None:
        _check_literal(self.kind, ("none", "session_end", "max_hold"), "HoldRule.kind")
        if self.kind == "max_hold":
            if self.hours is None:
                raise ConfigError("HoldRule.hours: required when kind is 'max_hold'")
            _check_positive(self.hours, "HoldRule.hours")
        elif self.hours is not None:
            raise ConfigError(f"HoldRule.hours: only allowed when kind is 'max_hold' (kind is {self.kind!r})")


@dataclass(frozen=True)
class StrategyParams(_Config):
    swing_k: int = 2
    confirm_n: int = 3
    zone: str = "full"  # "full" | "body"
    entry_level: str = "top"  # "top" | "mid"
    stop_buffer: StopBuffer = StopBuffer("atr", 0.1)
    r_target: float = 2.0
    hold_rule: HoldRule = HoldRule("none")
    trend_filter: bool = False
    pierce: float = 0.0  # fraction of price, e.g. 0.0005 = 0.05%
    structure_break: str = "fresh"  # D2; "literal" is accepted but not in the grid
    skip_mitigated: str = "continue"  # D4; "stop" is accepted but not in the grid

    def _validate(self) -> None:
        _check_literal(self.zone, ("full", "body"), "StrategyParams.zone")
        _check_literal(self.entry_level, ("top", "mid"), "StrategyParams.entry_level")
        _check_literal(self.structure_break, ("fresh", "literal"), "StrategyParams.structure_break")
        _check_literal(self.skip_mitigated, ("continue", "stop"), "StrategyParams.skip_mitigated")
        _check_positive(self.swing_k, "StrategyParams.swing_k")
        _check_positive(self.confirm_n, "StrategyParams.confirm_n")
        _check_positive(self.r_target, "StrategyParams.r_target")
        _check_non_negative(self.pierce, "StrategyParams.pierce")


@dataclass(frozen=True)
class ExecConfig(_Config):
    fee_maker: float = 0.0002
    fee_taker: float = 0.0005
    slippage: float = 0.0002  # fraction of price on stop and time exits
    mmr: float = 0.004  # maintenance margin rate, per pair
    risk_per_trade: float = 0.01
    max_leverage: float = 25.0
    start_equity: float = 10_000.0
    use_1m: bool = True

    def _validate(self) -> None:
        for name in ("fee_maker", "fee_taker", "slippage", "mmr"):
            _check_non_negative(getattr(self, name), f"ExecConfig.{name}")
        if not 0 < self.risk_per_trade <= 1:
            raise ConfigError(f"ExecConfig.risk_per_trade: must be in (0, 1], got {self.risk_per_trade!r}")
        _check_positive(self.max_leverage, "ExecConfig.max_leverage")
        _check_positive(self.start_equity, "ExecConfig.start_equity")


@dataclass(frozen=True)
class StatsConfig(_Config):
    bootstrap_n: int = 10_000
    block_len_days: int = 10
    baseline_runs: int = 5_000
    alpha: float = 0.05
    master_seed: int = 20260926
    reprice_slippage: tuple[float, ...] = (0.0, 0.0002, 0.0005, 0.001)
    reprice_maker: tuple[float, ...] = (0.0, 0.0002)

    def _validate(self) -> None:
        _check_positive(self.bootstrap_n, "StatsConfig.bootstrap_n")
        _check_positive(self.block_len_days, "StatsConfig.block_len_days")
        _check_positive(self.baseline_runs, "StatsConfig.baseline_runs")
        if not 0 < self.alpha < 1:
            raise ConfigError(f"StatsConfig.alpha: must be in (0, 1), got {self.alpha!r}")
        for name in ("reprice_slippage", "reprice_maker"):
            values = getattr(self, name)
            if not values:
                raise ConfigError(f"StatsConfig.{name}: must not be empty")
            for k, x in enumerate(values):
                _check_non_negative(x, f"StatsConfig.{name}[{k}]")


@dataclass(frozen=True)
class DataConfig(_Config):
    data_dir: str
    insample_start: str  # ISO date
    insample_end: str  # ISO date, inclusive
    holdout_start: str
    holdout_end: str | None  # None until data_download_date is set
    warmup_start: str  # "2019-11-01": first candle needed for indicators
    listing: dict[str, str]  # pair -> first perpetual trading date

    def _validate(self) -> None:
        for name in ("insample_start", "insample_end", "holdout_start", "warmup_start"):
            _check_iso_date(getattr(self, name), f"DataConfig.{name}")
        if self.holdout_end is not None:
            _check_iso_date(self.holdout_end, "DataConfig.holdout_end")
        for pair, first in self.listing.items():
            _check_iso_date(first, f"DataConfig.listing[{pair!r}]")
        if self.insample_start > self.insample_end:
            raise ConfigError(
                f"DataConfig: insample_start {self.insample_start} is after insample_end {self.insample_end}"
            )


@dataclass(frozen=True)
class VariantConfig(_Config):
    pair: str
    session: SessionSpec
    params: StrategyParams
    exec: ExecConfig
    stats: StatsConfig
    period_start: str  # ISO date; first decision candle
    period_end: str  # ISO date, inclusive
    is_holdout: bool

    def _validate(self) -> None:
        _check_iso_date(self.period_start, "VariantConfig.period_start")
        _check_iso_date(self.period_end, "VariantConfig.period_end")
        if self.period_start > self.period_end:
            raise ConfigError(
                f"VariantConfig: period_start {self.period_start} is after period_end {self.period_end}"
            )

    def canonical_json(self) -> str:
        return canonical_json(self)

    def config_hash(self) -> str:
        return config_hash(self)

    def variant_id(self, code_version: str) -> str:
        """sha256(canonical_json + code_version): the identity of one experiment (D15)."""
        return _sha256(self.canonical_json() + code_version)


# --- canonical JSON, hashes, YAML ------------------------------------------------


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(obj: Any) -> str:
    """Compact JSON with sorted keys; floats in ``repr`` form. Accepts dataclasses or plain mappings."""
    return json.dumps(to_dict(obj), sort_keys=True, separators=(",", ":"), allow_nan=False)


def config_hash(obj: Any) -> str:
    """SHA-256 of :func:`canonical_json`."""
    return _sha256(canonical_json(obj))


def load_yaml(path: str | Path, cls: type[T]) -> T:
    """Read a YAML file into dataclass ``cls``. Unknown keys raise ConfigError."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {}
    return from_dict(cls, data)


def dump_yaml(obj: Any, path: str | Path) -> None:
    """Write a dataclass (or plain mapping) as YAML, fields in declaration order."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        yaml.safe_dump(to_dict(obj), f, sort_keys=False, default_flow_style=False, allow_unicode=True)
