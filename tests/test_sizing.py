"""Sizing, leverage cap, liquidation assertion (spec §4.6)."""
import math

import pytest

from perpbt.config import ExecConfig
from perpbt.execution.sizing import (
    LiquidationAboveStopError,
    check_liquidation,
    leverage_ok,
    liquidation_price,
    size_order,
)

CFG = ExecConfig(max_leverage=25.0, mmr=0.004)


def test_equity_10000_entry_100_stop_99():
    s = size_order(10_000.0, 100.0, 99.0, CFG)
    assert (s.risk_usd, s.stop_dist, s.qty, s.notional, s.implied_leverage) == (100.0, 1.0, 100.0, 10_000.0, 1.0)
    assert s.max_iso_leverage == pytest.approx(1 / (0.01 + 0.004))


def test_per_order_cap_refuses():
    s = size_order(10_000.0, 100.0, 99.9, ExecConfig(max_leverage=5.0))  # 0.1% stop -> 10x
    assert s.implied_leverage == pytest.approx(10.0)
    assert not leverage_ok(s, 0.0, 10_000.0, ExecConfig(max_leverage=5.0))
    assert leverage_ok(s, 0.0, 10_000.0, ExecConfig(max_leverage=10.0 + 1e-9))


def test_total_notional_cap_refuses():
    cfg = ExecConfig(max_leverage=3.0)
    s = size_order(10_000.0, 100.0, 99.0, cfg)  # 1x
    assert leverage_ok(s, 20_000.0, 10_000.0, cfg)  # exactly 3x is allowed
    assert not leverage_ok(s, 20_000.01, 10_000.0, cfg)


def test_liquidation_assertion_raises_with_a_huge_mmr():
    s = size_order(10_000.0, 100.0, 99.9, CFG)  # 10x
    assert liquidation_price(100.0, 10_000.0, s.notional, 0.004) == pytest.approx(90.4)
    assert check_liquidation(100.0, 99.9, 10_000.0, s.notional, 0.004) == pytest.approx(90.4)
    with pytest.raises(LiquidationAboveStopError):
        check_liquidation(100.0, 99.9, 10_000.0, s.notional, 0.2)  # liq = 100 x (1 - 0.1 + 0.2) = 110


@pytest.mark.parametrize("equity", [0.0, -5.0, math.nan, math.inf])
def test_non_positive_or_nan_equity_raises(equity):
    with pytest.raises(ValueError, match="equity"):
        size_order(equity, 100.0, 99.0, CFG)


def test_stop_not_below_entry_raises():
    with pytest.raises(ValueError, match="stop"):
        size_order(10_000.0, 100.0, 100.0, CFG)
