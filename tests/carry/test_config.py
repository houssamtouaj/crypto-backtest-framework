"""Carry configuration types (carry spec Phase 1 §1.3; Phase 2 §2.1 ``signal`` block)."""
import pytest

from perpbt.config import ConfigError, from_dict, to_dict
from perpbt.carry.config import (
    CarryCostConfig, CarryRebalanceConfig, CarrySignalConfig, MarginConfig, SleeveConfig,
)

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


# --- sleeve, margin, costs, rebalance (Phase 2 §2.1 blocks) ---------------------------------------

SLEEVE = {"perp_leverage": 2.0}
MARGIN = {"rebalance_below": 0.5, "mmr": {"default": 0.01, "BTCUSDT": 0.004, "ETHUSDT": 0.005},
          "liquidation_fee": 0.01}
COSTS = {"perp_taker": 0.0005, "spot_taker": 0.0010,
         "slippage": {"default": 0.0005, "BTCUSDT": 0.0002, "ETHUSDT": 0.0002}}


def test_block_configs_round_trip_and_lookups():
    assert from_dict(SleeveConfig, SLEEVE).perp_leverage == 2.0
    m = from_dict(MarginConfig, MARGIN)
    assert m.mmr_for("BTCUSDT") == 0.004 and m.mmr_for("XRPUSDT") == 0.01
    c = from_dict(CarryCostConfig, COSTS)
    assert c.slippage_for("ETHUSDT") == 0.0002 and c.slippage_for("LTCUSDT") == 0.0005
    assert to_dict(c) == COSTS and to_dict(m) == MARGIN
    assert from_dict(CarryRebalanceConfig, {"fill_offset_min": 15}).fill_offset_min == 15


@pytest.mark.parametrize("cls, base, bad", [
    (SleeveConfig, SLEEVE, {"perp_leverage": 0.0}),
    (SleeveConfig, SLEEVE, {"perp_leverage": float("inf")}),
    (MarginConfig, MARGIN, {"rebalance_below": 0.0}),
    (MarginConfig, MARGIN, {"rebalance_below": 1.5}),
    (MarginConfig, MARGIN, {"mmr": {"BTCUSDT": 0.004}}),
    (MarginConfig, MARGIN, {"mmr": {"default": 0.0}}),
    (MarginConfig, MARGIN, {"liquidation_fee": -0.01}),
    (MarginConfig, MARGIN, {"liquidation_fee": float("nan")}),
    (CarryCostConfig, COSTS, {"perp_taker": -0.0001}),
    (CarryCostConfig, COSTS, {"spot_taker": float("nan")}),
    (CarryCostConfig, COSTS, {"slippage": {"BTCUSDT": 0.0002}}),
    (CarryCostConfig, COSTS, {"slippage": {"default": -0.1}}),
    (CarryRebalanceConfig, {"fill_offset_min": 15}, {"fill_offset_min": 30}),
])
def test_block_configs_reject(cls, base, bad):
    with pytest.raises(ConfigError):
        from_dict(cls, {**base, **bad})
