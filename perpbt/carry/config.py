"""Carry configuration types (carry spec overview §6; Phase 2 §2.1 ``signal``, ``sleeve``, ``margin``,
``rebalance`` and ``costs`` blocks).

Frozen dataclasses on the first strategy's ``_Config`` base: fields are
coerced to their annotation, validated, and hash through
``config.canonical_json``. The pre-registration (Phase 2) supplies the
values; nothing here has a default.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from perpbt.config import ConfigError, _Config
from perpbt.trend.daily import FILL_OFFSETS_MIN


@dataclass(frozen=True)
class CarrySignalConfig(_Config):
    lookback_days: int  # W: calendar days of funding events in the trailing mean, e.g. 7
    entry_annual: float  # turn on when the annualised trailing funding is above this, e.g. 0.10
    exit_annual: float  # turn off when it is below this, e.g. 0.0; <= entry_annual

    def _validate(self) -> None:
        if self.lookback_days < 1:
            raise ConfigError(f"CarrySignalConfig.lookback_days: must be >= 1, got {self.lookback_days}")
        for name in ("entry_annual", "exit_annual"):
            if not math.isfinite(getattr(self, name)):
                raise ConfigError(f"CarrySignalConfig.{name}: must be finite, got {getattr(self, name)!r}")
        if self.exit_annual > self.entry_annual:
            raise ConfigError(
                f"CarrySignalConfig.exit_annual: must be <= entry_annual ({self.entry_annual}), got {self.exit_annual}"
            )


def _finite(value: float, where: str, *, low: float, strict: bool) -> None:
    if not math.isfinite(value) or value < low or (strict and value == low):
        raise ConfigError(f"{where}: must be finite and {'>' if strict else '>='} {low}, got {value!r}")


def _table(table: dict[str, float], where: str, *, strict: bool) -> None:
    if "default" not in table:
        raise ConfigError(f"{where}: needs a 'default' entry")
    for pair, value in table.items():
        _finite(value, f"{where}[{pair!r}]", low=0.0, strict=strict)


@dataclass(frozen=True)
class SleeveConfig(_Config):
    perp_leverage: float  # L: spot notional x = L / (L + 1) of a sleeve, perp margin x / L

    def _validate(self) -> None:
        _finite(self.perp_leverage, "SleeveConfig.perp_leverage", low=0.0, strict=True)


@dataclass(frozen=True)
class MarginConfig(_Config):
    rebalance_below: float  # perp wallet equity / margin needed at the target leverage, checked at the close
    mmr: dict[str, float]  # pair -> maintenance margin rate; "default" for the others
    liquidation_fee: float  # fraction of the liquidated notional

    def _validate(self) -> None:
        _finite(self.rebalance_below, "MarginConfig.rebalance_below", low=0.0, strict=True)
        if self.rebalance_below > 1:
            raise ConfigError(f"MarginConfig.rebalance_below: must be <= 1, got {self.rebalance_below}")
        _table(self.mmr, "MarginConfig.mmr", strict=True)
        _finite(self.liquidation_fee, "MarginConfig.liquidation_fee", low=0.0, strict=False)

    def mmr_for(self, pair: str) -> float:
        return self.mmr.get(pair, self.mmr["default"])


@dataclass(frozen=True)
class CarryCostConfig(_Config):
    perp_taker: float  # fraction of traded notional, every perp fill
    spot_taker: float  # fraction of traded notional, every spot fill
    slippage: dict[str, float]  # pair -> fraction of traded notional per leg; "default" for the others

    def _validate(self) -> None:
        _finite(self.perp_taker, "CarryCostConfig.perp_taker", low=0.0, strict=False)
        _finite(self.spot_taker, "CarryCostConfig.spot_taker", low=0.0, strict=False)
        _table(self.slippage, "CarryCostConfig.slippage", strict=False)

    def slippage_for(self, pair: str) -> float:
        return self.slippage.get(pair, self.slippage["default"])


@dataclass(frozen=True)
class CarryRebalanceConfig(_Config):
    fill_offset_min: int  # both legs fill at the close of the 15m candle closing this many minutes after 00:00

    def _validate(self) -> None:
        if self.fill_offset_min not in FILL_OFFSETS_MIN:
            raise ConfigError(
                f"CarryRebalanceConfig.fill_offset_min: expected one of {list(FILL_OFFSETS_MIN)}, "
                f"got {self.fill_offset_min}"
            )
