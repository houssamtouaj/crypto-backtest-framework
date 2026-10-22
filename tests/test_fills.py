"""Fill rules on one candle and the 1m resolver (spec §4.3, §4.4)."""
import math

import numpy as np
import pytest

from perpbt.execution import fills
from perpbt.execution.fills import MISSING, MinuteIndex, apply_rules, levels, resolve_candle, walk_minutes
from tests.synthetic import T0_MS, candles_from_rows

LV = levels(entry=100.0, stop=99.0, target=102.0, pierce_abs=0.0)


def minutes(rows):
    """15 one-minute (o, h, l) rows padded with a quiet bar that touches nothing."""
    quiet = (100.5, 100.6, 100.4)
    rows = list(rows) + [quiet] * (15 - len(rows))
    a = np.asarray(rows, dtype=np.float64)
    return a[:, 0], a[:, 1], a[:, 2]


# --- apply_rules: one candle, pessimistic ------------------------------------------------


def test_e1_fill_when_low_equals_entry_with_zero_pierce():
    s = apply_rules(True, 100.5, 101.0, 100.0, LV)
    assert (s.filled, s.fill_price, s.fill_gap, s.touched, s.exit) == (True, 100.0, False, 1, None)


def test_e1_no_fill_at_entry_with_pierce_but_fill_at_entry_minus_pierce():
    lv = levels(entry=100.0, stop=99.0, target=102.0, pierce_abs=0.05)
    assert not apply_rules(True, 100.5, 101.0, 100.0, lv).filled
    s = apply_rules(True, 100.5, 101.0, 100.0 - 0.05, lv)
    assert s.filled and s.fill_price == 100.0  # the limit price: pierce is queue position, not improvement


def test_e2_open_gap_fills_at_the_open_on_the_first_look_only():
    s = apply_rules(True, 99.6, 99.8, 99.5, LV, first_look=True)
    assert (s.filled, s.fill_price, s.fill_gap) == (True, 99.6, True)
    s = apply_rules(True, 99.6, 99.8, 99.5, LV)  # a resting limit fills at its price, never better
    assert (s.filled, s.fill_price, s.fill_gap, s.touched) == (True, 100.0, False, 1)


def test_first_look_in_the_walk_is_minute_zero_only():
    m = minutes([(100.5, 100.6, 100.4), (99.7, 99.8, 99.6)])  # minute 1 opens below the entry
    out = walk_minutes(True, *m, LV, first_look=True)
    assert (out.filled, out.fill_price, out.fill_gap, out.fill_minute) == (True, 100.0, False, 1)
    m = minutes([(99.7, 99.8, 99.6)])
    out = walk_minutes(True, *m, LV, first_look=True)
    assert (out.fill_price, out.fill_gap, out.fill_minute) == (99.7, True, 0)
    assert walk_minutes(True, *m, LV).fill_price == 100.0


def test_e3_fill_and_stop_on_one_candle_is_stopped_at_the_stop():
    s = apply_rules(True, 100.5, 101.0, 98.5, LV)
    assert (s.filled, s.exit, s.exit_ref, s.touched) == (True, "stop", 99.0, 2)


def test_e4_fill_and_target_on_one_candle_is_not_a_target():
    s = apply_rules(True, 100.5, 102.5, 99.9, LV)
    assert (s.filled, s.exit, s.touched) == (True, None, 2)


def test_e5_stop_and_target_on_one_candle_is_a_stop():
    s = apply_rules(False, 100.5, 102.5, 98.5, LV)
    assert (s.filled, s.exit, s.exit_ref, s.touched) == (False, "stop", 99.0, 2)


def test_target_needs_target_plus_pierce_and_exits_at_the_target():
    lv = levels(entry=100.0, stop=99.0, target=102.0, pierce_abs=0.05)
    assert apply_rules(False, 100.5, 102.04, 100.4, lv).exit is None
    s = apply_rules(False, 100.5, 102.05, 100.4, lv)
    assert (s.exit, s.exit_ref, s.touched) == ("target", 102.0, 1)


def test_a_gap_through_the_stop_exits_at_the_open_a_target_exit_is_always_at_the_target():
    s = apply_rules(False, 98.0, 98.5, 97.5, LV)
    assert (s.exit, s.exit_ref) == ("stop", 98.0)
    s = apply_rules(False, 103.0, 103.5, 102.5, LV)  # no price improvement on a resting sell limit
    assert (s.exit, s.exit_ref) == ("target", 102.0)


def test_suppressed_target_then_open_above_it_still_exits_at_the_target():
    # minute 0 fills and touches the target (ignored, same minute); minute 1 opens above the target
    m = minutes([(101.9, 102.4, 99.9), (102.3, 102.5, 102.2)])
    out = walk_minutes(True, *m, LV)
    assert (out.filled, out.exit, out.exit_ref, out.exit_minute) == (True, "target", 102.0, 1)


def test_open_gap_below_the_stop_fills_and_stops_at_the_open():
    s = apply_rules(True, 98.0, 98.5, 97.5, LV, first_look=True)
    assert (s.filled, s.fill_price, s.fill_gap, s.exit, s.exit_ref) == (True, 98.0, True, "stop", 98.0)


def test_untouched_candles():
    assert apply_rules(True, 100.5, 101.9, 100.1, LV).touched == 0
    s = apply_rules(False, 100.5, 101.9, 99.1, LV)
    assert (s.touched, s.exit, s.filled) == (0, None, False) and math.isnan(s.exit_ref)


# --- resolve_candle and the 1m walk ------------------------------------------------------


def test_unambiguous_candle_is_never_sent_to_the_resolver(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("walk_minutes called on an unambiguous candle")

    monkeypatch.setattr(fills, "walk_minutes", boom)
    out = resolve_candle(True, 100.5, 101.0, 99.9, LV, minutes([]))
    assert (out.filled, out.resolution, out.fill_minute) == (True, "15m_unambiguous", -1)
    out = resolve_candle(False, 100.5, 102.5, 99.5, LV, minutes([]))
    assert (out.exit, out.resolution) == ("target", "15m_unambiguous")


def test_ambiguous_without_1m_is_pessimistic():
    out = resolve_candle(False, 100.5, 102.5, 98.5, LV, None)
    assert (out.exit, out.exit_ref, out.resolution, out.exit_minute) == ("stop", 99.0, "15m_pessimistic", -1)


def test_ambiguous_with_a_missing_minute_is_pessimistic_and_labelled():
    out = resolve_candle(False, 100.5, 102.5, 98.5, LV, MISSING)
    assert (out.exit, out.resolution) == ("stop", "15m_pessimistic_missing_1m")


def test_1m_target_before_stop_is_a_target():
    m = minutes([(100.5, 101.0, 100.2), (101.0, 102.2, 100.9), (102.0, 102.1, 98.5)])
    out = resolve_candle(False, 100.5, 102.5, 98.5, LV, m)
    assert (out.exit, out.exit_ref, out.exit_minute, out.resolution) == ("target", 102.0, 1, "1m")


def test_1m_stop_before_target_is_a_stop():
    m = minutes([(100.5, 101.0, 100.2), (100.0, 100.1, 98.9), (99.5, 102.5, 99.4)])
    out = resolve_candle(False, 100.5, 102.5, 98.5, LV, m)
    assert (out.exit, out.exit_ref, out.exit_minute, out.resolution) == ("stop", 99.0, 1, "1m")


def test_1m_fill_then_target_in_a_later_minute_is_a_target():
    m = minutes([(100.5, 100.6, 99.95), (100.1, 101.5, 100.0), (101.5, 102.3, 101.4)])
    out = resolve_candle(True, 100.5, 102.5, 99.9, LV, m)
    assert (out.filled, out.fill_price, out.fill_minute) == (True, 100.0, 0)
    assert (out.exit, out.exit_ref, out.exit_minute, out.resolution) == ("target", 102.0, 2, "1m")


def test_1m_fill_and_target_in_the_same_minute_is_no_target_and_pessimistic():
    m = minutes([(100.5, 102.5, 99.9)])
    out = resolve_candle(True, 100.5, 102.5, 99.9, LV, m)
    assert (out.filled, out.exit, out.resolution) == (True, None, "1m_pessimistic")


def test_1m_single_minute_touching_stop_and_target_is_a_stop():
    m = minutes([(100.5, 102.5, 98.5)])
    out = resolve_candle(False, 100.5, 102.5, 98.5, LV, m)
    assert (out.exit, out.exit_ref, out.exit_minute, out.resolution) == ("stop", 99.0, 0, "1m_pessimistic")


def test_walk_that_never_fills_leaves_the_order_pending():
    # The 15m bar says entry and target were touched; its minutes (inconsistent data) never reach the entry.
    m = minutes([(100.5, 102.5, 100.2)])
    out = resolve_candle(True, 100.5, 102.5, 99.9, LV, m)
    assert (out.filled, out.exit, out.resolution) == (False, None, "1m")


def test_walk_minutes_can_be_called_directly():
    out = walk_minutes(False, *minutes([(100.5, 100.6, 98.0)]), LV)
    assert (out.exit, out.exit_ref, out.exit_minute) == ("stop", 99.0, 0)


# --- MinuteIndex ----------------------------------------------------------------------------


def test_minute_index_window_and_missing_minutes():
    rows = [(100.0 + k, 101.0 + k, 99.0 + k, 100.5 + k) for k in range(45)]
    m1 = candles_from_rows(rows, start_ms=T0_MS, step_ms=60_000, tf="1m")
    idx = MinuteIndex(m1)
    o1, h1, l1 = idx.window(T0_MS + 900_000)
    assert o1.tolist() == [100.0 + k for k in range(15, 30)] and h1[0] == 116.0 and l1[-1] == 128.0
    assert idx.window(T0_MS + 2 * 900_000 + 60_000) is MISSING  # not 15 minutes left
    assert idx.window(T0_MS - 900_000) is MISSING
    gappy = candles_from_rows(rows[:20] + rows[21:], start_ms=T0_MS, step_ms=60_000, tf="1m")
    gappy = type(gappy)(gappy.pair, "1m", np.delete(m1.ts, 20), gappy.o, gappy.h, gappy.l, gappy.c, gappy.v)
    assert MinuteIndex(gappy).window(T0_MS + 900_000) is MISSING
    assert MinuteIndex(gappy).window(T0_MS) is not MISSING


def test_minute_index_refuses_15m_candles():
    with pytest.raises(ValueError):
        MinuteIndex(candles_from_rows([(1.0, 1.0, 1.0, 1.0)], start_ms=T0_MS))
