"""SignalConfig and SizingConfig (trend spec Phase 1 §1.4, Phase 2 §2.1)."""
import pytest

from perpbt.config import ConfigError, config_hash, from_dict, to_dict
from perpbt.trend.config import MEMBERS, CostConfig, RebalanceConfig, SignalConfig, SizingConfig

SIGNAL = {"members": list(MEMBERS), "lookbacks": [20, 40, 80, 160], "donchian_exit": "half", "direction": "long_flat"}
SIZING = {"target_vol": 0.30, "vol_window": 60, "max_weight": 2.0, "max_gross": 2.0}


def test_round_trip():
    s = from_dict(SignalConfig, SIGNAL)
    assert s.members == ("return_sign", "ma_filter", "donchian")
    assert s.lookbacks == (20, 40, 80, 160)
    assert not s.long_short
    assert from_dict(SignalConfig, {**SIGNAL, "direction": "long_short"}).long_short
    z = from_dict(SizingConfig, SIZING)
    assert from_dict(SizingConfig, to_dict(z)) == z
    assert from_dict(SignalConfig, to_dict(s)) == s


def test_hash_number_forms():
    a = from_dict(SizingConfig, SIZING)
    b = from_dict(SizingConfig, {**SIZING, "target_vol": 0.3, "max_weight": 2})
    assert config_hash(a) == config_hash(b)


@pytest.mark.parametrize("bad", [
    {"lookbacks": [20, 21]}, {"lookbacks": [20, 20]}, {"lookbacks": []}, {"lookbacks": [0]},
    {"members": ["return_sign", "breakout"]}, {"members": ["donchian", "donchian"]}, {"members": []},
    {"direction": "short_only"}, {"donchian_exit": "full"}, {"extra": 1},
])
def test_signal_rejects(bad):
    with pytest.raises(ConfigError):
        from_dict(SignalConfig, {**SIGNAL, **bad})


@pytest.mark.parametrize("bad", [
    {"vol_window": 60.0}, {"vol_window": 1}, {"target_vol": 0}, {"max_weight": -1.0}, {"max_gross": 0},
    {"extra": 1},
])
def test_sizing_rejects(bad):
    with pytest.raises(ConfigError):
        from_dict(SizingConfig, {**SIZING, **bad})


def test_members_must_be_canonical_order():
    with pytest.raises(ConfigError, match="order"):
        from_dict(SignalConfig, {**SIGNAL, "members": ["donchian", "ma_filter", "return_sign"]})
    assert from_dict(SignalConfig, {**SIGNAL, "members": ["return_sign", "donchian"]}).members == ("return_sign", "donchian")


def test_lookbacks_must_increase():
    with pytest.raises(ConfigError, match="increasing"):
        from_dict(SignalConfig, {**SIGNAL, "lookbacks": [40, 20]})


REBALANCE = {"frequency": "daily", "drift_band": 0.20, "fill_offset_min": 15}
COSTS = {"fee_taker": 0.0005, "slippage": {"default": 0.0005, "BTCUSDT": 0.0002, "ETHUSDT": 0.0002},
         "mmr": {"default": 0.01, "BTCUSDT": 0.004, "ETHUSDT": 0.005}, "spot_funding_per_8h": 0.0001}


def test_rebalance_config():
    r = from_dict(RebalanceConfig, REBALANCE)
    assert from_dict(RebalanceConfig, to_dict(r)) == r
    assert from_dict(RebalanceConfig, {**REBALANCE, "frequency": "weekly", "fill_offset_min": 480}).fill_offset_min == 480
    for bad in ({"frequency": "monthly"}, {"drift_band": -0.1}, {"fill_offset_min": 30}, {"fill_offset_min": 15.0}):
        with pytest.raises(ConfigError):
            from_dict(RebalanceConfig, {**REBALANCE, **bad})


def test_cost_config():
    c = from_dict(CostConfig, COSTS)
    assert from_dict(CostConfig, to_dict(c)) == c
    assert c.slippage_for("BTCUSDT") == 0.0002 and c.slippage_for("XRPUSDT") == 0.0005
    assert c.mmr_for("ETHUSDT") == 0.005 and c.mmr_for("LTCUSDT") == 0.01
    for bad in ({"slippage": {"BTCUSDT": 0.0002}}, {"mmr": {"BTCUSDT": 0.004}}, {"slippage": {"default": -1e-4}},
                {"mmr": {"default": 0.0}}, {"fee_taker": -1e-4}, {"spot_funding_per_8h": -1e-4}):
        with pytest.raises(ConfigError):
            from_dict(CostConfig, {**COSTS, **bad})
