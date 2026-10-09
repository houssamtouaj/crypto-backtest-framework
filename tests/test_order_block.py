"""The ICT order-block rule (spec phase-3 §3.2–§3.12)."""
import numpy as np

from perpbt.config import StopBuffer, StrategyParams
from perpbt.indicators.swings import swing_highs
from perpbt.strategy.order_block import Level, LiveLevels, block_prices
from tests.synthetic import T0_MS, random_walk

PRIMARY = StrategyParams()  # k = 2, N = 3, full zone, top entry, 0.1 x ATR buffer, 2 R


# --- 3.1 live levels and impulse -----------------------------------------------------------


def test_level_retires_on_the_first_close_above_it():
    lv = LiveLevels()
    a = Level(1, 103.0, 3)
    assert lv.step([a], 100.0) == a
    assert lv.step([], 103.0) == a and lv.live == (a,)  # a close equal to the level does not retire it
    assert lv.step([], 103.5) == a and lv.live == ()  # the impulse candle retires it
    assert lv.step([], 110.0) is None


def test_most_recent_live_level_is_the_reference_even_below_an_older_higher_one():
    lv = LiveLevels()
    old, new = Level(1, 110.0, 3), Level(5, 105.0, 7)
    lv.step([old], 100.0)
    assert lv.step([new], 100.0) == new
    assert lv.step([], 104.0) == new


def test_close_above_older_level_but_below_reference_retires_it_and_is_not_an_impulse():
    lv = LiveLevels()
    old, ref = Level(1, 105.0, 3), Level(5, 110.0, 7)
    lv.step([old], 100.0)
    lv.step([ref], 100.0)
    got = lv.step([], 106.0)
    assert got == ref and not 106.0 > got.level  # not an impulse
    assert lv.live == (ref,)


def test_after_the_reference_breaks_the_surviving_older_level_is_the_reference_next_candle():
    lv = LiveLevels()
    old, ref = Level(1, 110.0, 3), Level(5, 105.0, 7)
    lv.step([old], 100.0)
    lv.step([ref], 100.0)
    assert lv.step([], 106.0) == ref  # impulse on ref; ref retires
    assert lv.step([], 106.0) == old


def test_literal_mode_ignores_retirement():
    lv = LiveLevels(fresh=False)
    a = Level(1, 103.0, 3)
    lv.step([a], 100.0)
    assert lv.step([], 120.0) == a
    assert lv.step([], 120.0) == a and lv.live == (a,)
    b = Level(6, 125.0, 8)
    assert lv.step([b], 121.0) == b


def test_a_level_confirmed_on_t_cannot_be_broken_on_t():
    for seed in range(10):
        cd = random_walk(2000, seed=seed, start_ms=T0_MS)
        for k in (1, 2, 3):
            sw = swing_highs(cd, k)
            assert (cd.c[sw.confirmed_at] < sw.level).all()


def test_a_level_confirmed_on_t_is_the_reference_on_t():
    lv = LiveLevels()
    lv.step([Level(1, 110.0, 3)], 100.0)
    new = Level(6, 108.0, 8)
    assert lv.step([new], 100.0) == new


# --- 3.6 block prices -------------------------------------------------------------------------


def test_block_prices_full_top_atr_buffer():
    bp = block_prices(100.0, 100.8, 99.0, 99.2, 2.0, PRIMARY)
    assert (bp.zone_low, bp.zone_high, bp.entry) == (99.0, 100.8, 100.8)
    assert bp.stop == 99.0 - 0.1 * 2.0 and bp.stop_dist == 100.8 - bp.stop
    assert bp.target == 100.8 + 2.0 * bp.stop_dist and bp.pierce_abs == 0.0


def test_block_prices_body_mid_pct_buffer_and_pierce():
    p = StrategyParams(zone="body", entry_level="mid", stop_buffer=StopBuffer("pct", 0.0025), r_target=1.5,
                       pierce=0.0005)
    bp = block_prices(100.0, 100.8, 99.0, 99.2, float("nan"), p)
    entry = (99.2 + 100.0) / 2
    assert (bp.zone_low, bp.zone_high, bp.entry) == (99.2, 100.0, entry)
    assert bp.stop == 99.0 - 0.0025 * entry  # anchored at the true low, not the body
    assert bp.target == entry + 1.5 * bp.stop_dist and bp.pierce_abs == 0.0005 * entry


def test_block_prices_zero_range_candle_with_zero_buffer_is_degenerate():
    bp = block_prices(100.0, 100.0, 100.0, 100.0, 1.0, StrategyParams(stop_buffer=StopBuffer("atr", 0.0)))
    assert bp.stop_dist == 0 and not bp.stop_dist > 0


def test_block_prices_nan_atr():
    assert np.isnan(block_prices(100.0, 100.8, 99.0, 99.2, float("nan"), PRIMARY).stop_dist)
    zero = StrategyParams(stop_buffer=StopBuffer("atr", 0.0))
    assert block_prices(100.0, 100.8, 99.0, 99.2, float("nan"), zero).stop_dist == 100.8 - 99.0
