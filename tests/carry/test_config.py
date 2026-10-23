"""Carry configuration types (carry spec Phase 1 §1.3; Phase 2 §2.1 ``signal`` block)."""
import pytest

from perpbt.config import ConfigError, from_dict, to_dict
from perpbt.carry.config import CarrySignalConfig

PREREG = {"lookback_days": 7, "entry_annual": 0.10, "exit_annual": 0.0}


def test_signal_config_round_trip():
    cfg = from_dict(CarrySignalConfig, PREREG)
    assert (cfg.lookback_days, cfg.entry_annual, cfg.exit_annual) == (7, 0.10, 0.0)
    assert to_dict(cfg) == PREREG


@pytest.mark.parametrize("bad", [
    {"lookback_days": 0},
    {"exit_annual": 0.2},
    {"entry_annual": float("nan")},
    {"exit_annual": float("-inf")},
])
def test_signal_config_rejects(bad):
    with pytest.raises(ConfigError):
        from_dict(CarrySignalConfig, {**PREREG, **bad})


def test_signal_config_unknown_or_missing_key():
    with pytest.raises(ConfigError):
        from_dict(CarrySignalConfig, {**PREREG, "extra": 1})
    with pytest.raises(ConfigError):
        from_dict(CarrySignalConfig, {"lookback_days": 7, "entry_annual": 0.1})


def test_always_on_thresholds_accepted():
    cfg = from_dict(CarrySignalConfig, {**PREREG, "entry_annual": -1.0e9, "exit_annual": -1.0e9})
    assert cfg.entry_annual == cfg.exit_annual == -1.0e9
