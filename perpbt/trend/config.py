"""Trend configuration types (trend spec overview §6; Phase 2 §2.1 ``signal`` and ``sizing`` blocks).

Frozen dataclasses on the first strategy's ``_Config`` base: fields are
coerced to their annotation, validated, and hash through
``config.canonical_json``. The pre-registration (Phase 2) supplies the
values; nothing here has a default.
"""
from __future__ import annotations

from dataclasses import dataclass

from perpbt.config import ConfigError, _check_literal, _check_positive, _Config

MEMBERS = ("return_sign", "ma_filter", "donchian")
DIRECTIONS = ("long_flat", "long_short")
DONCHIAN_EXITS = ("half",)  # exit channel = N // 2 days


@dataclass(frozen=True)
class SignalConfig(_Config):
    members: tuple[str, ...]  # subset of MEMBERS; each runs at every lookback
    lookbacks: tuple[int, ...]  # days; even, so the Donchian exit window N/2 is whole
    donchian_exit: str  # "half"
    direction: str  # "long_flat" | "long_short"

    def _validate(self) -> None:
        if not self.members:
            raise ConfigError("SignalConfig.members: must not be empty")
        for k, m in enumerate(self.members):
            _check_literal(m, MEMBERS, f"SignalConfig.members[{k}]")
        if len(set(self.members)) != len(self.members):
            raise ConfigError(f"SignalConfig.members: duplicate member in {list(self.members)}")
        if not self.lookbacks:
            raise ConfigError("SignalConfig.lookbacks: must not be empty")
        bad = [n for n in self.lookbacks if n < 2 or n % 2]
        if bad:
            raise ConfigError(f"SignalConfig.lookbacks: each must be even and >= 2, got {bad}")
        if len(set(self.lookbacks)) != len(self.lookbacks):
            raise ConfigError(f"SignalConfig.lookbacks: duplicate lookback in {list(self.lookbacks)}")
        _check_literal(self.donchian_exit, DONCHIAN_EXITS, "SignalConfig.donchian_exit")
        _check_literal(self.direction, DIRECTIONS, "SignalConfig.direction")

    @property
    def long_short(self) -> bool:
        return self.direction == "long_short"


@dataclass(frozen=True)
class SizingConfig(_Config):
    target_vol: float  # annualised, e.g. 0.30
    vol_window: int  # completed daily returns in the vol forecast, e.g. 60
    max_weight: float  # cap on |w*| per pair, e.g. 2.0
    max_gross: float  # cap on the portfolio's gross notional / equity (applied by the executor, Phase 3)

    def _validate(self) -> None:
        _check_positive(self.target_vol, "SizingConfig.target_vol")
        if self.vol_window < 2:
            raise ConfigError(f"SizingConfig.vol_window: must be >= 2, got {self.vol_window}")
        _check_positive(self.max_weight, "SizingConfig.max_weight")
        _check_positive(self.max_gross, "SizingConfig.max_gross")
