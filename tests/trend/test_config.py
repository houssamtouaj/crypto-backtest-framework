"""SignalConfig and SizingConfig (trend spec Phase 1 §1.4, Phase 2 §2.1)."""
import pytest

from perpbt.config import ConfigError, config_hash, from_dict, to_dict
from perpbt.trend.config import MEMBERS, SignalConfig, SizingConfig

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
