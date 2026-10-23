"""Carry configuration types (carry spec overview §6; Phase 2 §2.1 ``signal`` block).

Frozen dataclasses on the first strategy's ``_Config`` base: fields are
coerced to their annotation, validated, and hash through
``config.canonical_json``. The pre-registration (Phase 2) supplies the
values; nothing here has a default.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from perpbt.config import ConfigError, _Config


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
