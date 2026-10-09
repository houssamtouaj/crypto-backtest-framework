"""The simulator loop end to end (spec §4.1–§4.9, tests 4.1–4.6)."""
import json
import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from perpbt.config import ExecConfig, HoldRule, StrategyParams
from perpbt.data.sessions import SessionCalendar
from perpbt.execution.simulator import run
from perpbt.execution.sizing import LiquidationAboveStopError
from perpbt.strategy.base import CancelOrder, ClosePosition, PlaceBracketLimit
from perpbt.strategy.order_block import OrderBlockStrategy
from tests.sim_harness import (
    FLAT,
    ZERO_COST,
    bracket,
    close_ms,
    funding_at,
    minute_candles,
    no_funding,
    sim,
)
from tests.strategy_harness import NY
from tests.synthetic import STEP_15M_MS, T0_MS, candles_from_rows

DAY = 96  # candles per UTC day; T0 is 2020-01-01 00:00 UTC
COSTS = ExecConfig(fee_maker=0.0002, fee_taker=0.0005, slippage=0.0002, use_1m=False)


def only(df):
    assert len(df) == 1, df
    return df.iloc[0]


def entry_orders(res):
    return res.orders[res.orders["kind"] == "entry_limit"]


# --- 4.1 order lifecycle -------------------------------------------------------------------------


def test_e1_fill_at_entry_with_zero_pierce_not_with_pierce():
    rows = [FLAT, (100.5, 100.8, 100.0, 100.5), FLAT]
    res, _ = sim(rows, {0: [bracket()]})
    t = only(res.trades)
    assert (t.entry_idx, t.entry_price, t.fill_resolution) == (1, 100.0, "15m_unambiguous")
    res, _ = sim(rows, {0: [bracket()]}, params=StrategyParams(pierce=0.0005))
    assert len(res.trades) == 0 and only(entry_orders(res)).cancel_reason == "data_end"
    pierced = [FLAT, (100.5, 100.8, 100.0 - 0.0005 * 100.0, 100.5), FLAT]
    res, _ = sim(pierced, {0: [bracket()]}, params=StrategyParams(pierce=0.0005))
    assert only(res.trades).entry_price == 100.0


def test_e2_open_gap_fills_at_the_open():
    rows = [FLAT, (99.8, 100.4, 99.6, 100.2), FLAT]
    res, _ = sim(rows, {0: [bracket()]})
    t = only(res.trades)
    assert (t.entry_price, t.fill_resolution, t.planned_entry) == (99.8, "open_gap", 100.0)
    assert t.actual_risk_usd < t.risk_usd  # a long's gap fill is below the planned entry: less at risk
    assert t.actual_risk_usd == pytest.approx(t.qty * (99.8 - 99.0))


def test_a_resting_order_never_fills_below_its_limit():
    rows = [FLAT, FLAT, (99.8, 100.4, 99.6, 100.2), FLAT]
    res, _ = sim(rows, {0: [bracket()]})
    t = only(res.trades)
    assert (t.entry_idx, t.entry_price, t.fill_resolution) == (2, 100.0, "15m_unambiguous")
    assert t.actual_risk_usd == pytest.approx(t.risk_usd)


def test_e3_fill_and_stop_on_one_candle_is_minus_one_r():
    rows = [FLAT, (100.5, 100.6, 98.5, 99.0), FLAT]
    res, _ = sim(rows, {0: [bracket()]})
    t = only(res.trades)
    assert (t.entry_idx, t.exit_idx, t.exit_reason, t.exit_ref_price, t.gross_r) == (1, 1, "stop", 99.0, -1.0)
    assert (t.fill_resolution, t.exit_resolution, t.hold_minutes) == ("15m_pessimistic", "15m_pessimistic", 0)


def test_e4_fill_and_target_on_one_candle_is_no_target_that_candle():
    rows = [FLAT, (100.5, 102.5, 99.9, 101.0), (101.0, 102.1, 100.9, 102.0), FLAT]
    res, _ = sim(rows, {0: [bracket()]})
    t = only(res.trades)
    assert (t.entry_idx, t.exit_idx, t.exit_reason, t.exit_price, t.gross_r) == (1, 2, "target", 102.0, 2.0)


def test_e5_stop_and_target_on_one_candle_is_a_stop():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), (100.5, 102.5, 98.5, 100.0), FLAT]
    res, _ = sim(rows, {0: [bracket()]})
    t = only(res.trades)
    assert (t.exit_idx, t.exit_reason, t.exit_resolution) == (2, "stop", "15m_pessimistic")


def test_e6_an_order_expiring_at_a_close_can_fill_on_that_candle():
    rows = [FLAT, FLAT, (100.5, 100.8, 99.9, 100.5), FLAT]
    res, _ = sim(rows, {0: [bracket(expires_ms=close_ms(2))]})
    assert only(res.trades).entry_idx == 2


def test_e6_cancel_at_expiry():
    rows = [FLAT, FLAT, FLAT, (100.5, 100.8, 99.9, 100.5), FLAT]
    res, strat = sim(rows, {0: [bracket(expires_ms=close_ms(2))]})
    assert len(res.trades) == 0
    o = only(entry_orders(res))
    assert (o.status, o.cancel_reason, o.cancelled_ms) == ("cancelled", "expired", close_ms(2))
    ev = [e for e in strat.events if e.kind == "cancelled"]
    assert [(e.idx, e.reason) for e in ev] == [(2, "expired")]
    assert strat.accounts[2].pending_orders == ()  # not pending at the close it expired at
    assert len(strat.accounts[1].pending_orders) == 1


def test_e7_max_hold_exits_on_the_deadline_candle_measured_from_the_fill():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5)] + [FLAT] * 10
    res, _ = sim(rows, {0: [bracket(hold=HoldRule("max_hold", hours=1.0))]})
    t = only(res.trades)
    assert (t.entry_idx, t.exit_idx, t.exit_reason, t.exit_ref_price) == (1, 5, "max_hold", FLAT[3])
    assert (t.deadline_ms, t.hold_minutes, t.exit_role) == (close_ms(5), 60, "taker")


def test_e7_session_end_exits_at_the_window_end():
    rows = [FLAT] * 90 + [(100.5, 100.8, 99.9, 100.5)] + [FLAT] * 10
    res, _ = sim(rows, {80: [bracket(hold=HoldRule("session_end"))]})
    t = only(res.trades)
    assert (t.entry_idx, t.exit_idx, t.exit_reason, t.deadline_ms) == (90, DAY - 1, "session_end", close_ms(DAY - 1))


def test_e7_session_end_fill_on_last_window_candle_exits_at_its_close():
    rows = [FLAT] * (DAY - 1) + [(100.5, 100.8, 99.9, 100.7)] + [FLAT] * 3
    res, _ = sim(rows, {80: [bracket(hold=HoldRule("session_end"))]})
    t = only(res.trades)
    assert (t.entry_idx, t.exit_idx, t.exit_reason, t.exit_ref_price, t.hold_minutes) == (
        DAY - 1, DAY - 1, "session_end", 100.7, 0)


def test_time_exit_on_a_stop_candle_is_a_stop():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), (100.5, 100.6, 98.0, 99.0), FLAT]
    res, _ = sim(rows, {0: [bracket(hold=HoldRule("max_hold", hours=0.25))]})
    assert only(res.trades).exit_reason == "stop"


def test_e8_an_order_placed_at_a_close_does_not_fill_on_that_candle():
    rows = [FLAT, (100.5, 100.8, 99.5, 100.5), FLAT, FLAT]
    res, _ = sim(rows, {1: [bracket()]})
    assert len(res.trades) == 0
    o = only(entry_orders(res))
    assert (o.placed_idx, o.placed_ms, o.cancel_reason) == (1, close_ms(1), "data_end")


def test_e9_an_order_above_the_market_fills_at_the_next_open():
    rows = [(100.5, 100.6, 99.6, 99.7), (99.7, 99.9, 99.65, 99.8), FLAT]
    res, _ = sim(rows, {0: [bracket()]})
    t = only(res.trades)
    assert (t.entry_idx, t.entry_price, t.fill_resolution) == (1, 99.7, "open_gap")


def test_open_gap_below_stop_exits_at_the_open():
    rows = [FLAT, (98.0, 98.4, 97.5, 98.2), FLAT]
    res, _ = sim(rows, {0: [bracket()]})
    t = only(res.trades)
    assert (t.entry_price, t.exit_ref_price, t.exit_reason, t.gross_r) == (98.0, 98.0, "stop", 0.0)
    assert (t.fill_resolution, t.exit_resolution) == ("open_gap", "15m_pessimistic")


# Order-block candles from tests/test_order_block.py (S1 + TAIL): two valid blocks in one UTC session.
OB_FILL = (100.0, 100.5, 99.5, 100.0)
OB_ROWS = [OB_FILL] * 20 + [
    (100.0, 101.0, 99.5, 100.5), (100.5, 103.0, 100.0, 102.0), (101.0, 102.5, 100.5, 101.0),
    (100.0, 101.5, 99.8, 100.0), (100.0, 100.8, 99.0, 99.2), (99.2, 104.0, 99.1, 103.5),
    (103.5, 105.0, 103.0, 104.5), (104.5, 106.0, 104.0, 105.5), (105.5, 105.8, 104.5, 105.0),
    (105.0, 105.2, 104.2, 104.4), (104.4, 104.6, 103.8, 104.0), (104.0, 107.0, 103.9, 106.5),
    (106.5, 107.5, 106.0, 107.0), (107.0, 107.5, 106.5, 107.2),
]


def test_e10_a_leverage_skip_emits_the_event_and_consumes_the_session():
    strat = OrderBlockStrategy(StrategyParams())
    res, _ = sim(OB_ROWS, strategy=strat, cfg=ExecConfig(max_leverage=0.5, use_1m=False))
    assert res.skips["leverage"] == 1 and res.skips["intents"] == 1 and res.skips["session_used"] == 1
    o = only(entry_orders(res))
    assert (o.status, o.cancel_reason, o.placed_idx) == ("cancelled", "leverage_cap", 25)
    assert json.loads(o.tag)["impulse_idx"] == 25
    assert only(res.events[res.events["kind"] == "skipped_leverage"]).idx == 25
    assert res.summary["n_orders"] == 0 and res.summary["n_intents"] == 1 and res.summary["fill_rate"] is None


def test_order_block_intent_is_placed_and_fills_when_price_returns():
    rows = OB_ROWS + [(107.0, 107.2, 100.5, 101.0), (101.0, 101.2, 100.9, 101.0)]
    res, _ = sim(rows, strategy=OrderBlockStrategy(StrategyParams()), cfg=ZERO_COST)
    o = only(entry_orders(res))
    assert (o.placed_idx, o.price, o.status, o.expires_ms) == (25, 100.8, "filled", T0_MS + 96 * STEP_15M_MS)
    t = only(res.trades)
    assert t.impulse_ms == T0_MS + 25 * STEP_15M_MS and t.candidate_ms == T0_MS + 24 * STEP_15M_MS
    assert t.displacement_ms == t.impulse_ms and t.session_open_ms == T0_MS
    assert (t.entry_idx, t.entry_price, t.exit_reason) == (34, 100.8, "data_end")
    assert t.stop_dist == pytest.approx(json.loads(o.tag)["stop_dist"])


def test_overlapping_positions_are_processed_in_order_id_order():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), (100.5, 100.8, 99.9, 100.5), (100.5, 102.6, 100.4, 102.0), FLAT]
    res, strat = sim(rows, {0: [bracket()], 1: [bracket(target=102.5)]})
    assert res.trades["trade_id"].tolist() == [1, 2]
    assert res.trades["entry_idx"].tolist() == [1, 2] and res.trades["exit_idx"].tolist() == [3, 3]
    closed = [e.position_id for e in strat.events if e.kind == "closed"]
    assert closed == [1, 2]
    assert len(strat.accounts[2].open_positions) == 2 and res.summary["max_concurrent"] == 2


def test_cancel_order_and_close_position_intents():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), FLAT, (100.5, 100.9, 100.1, 100.6), FLAT]
    res, strat = sim(rows, {0: [bracket(), bracket(price=99.5, stop=99.0, target=100.5)],
                            1: [CancelOrder(2)], 3: [ClosePosition(1, "manual")]})
    o2 = res.orders[res.orders["order_id"] == 2].iloc[0]
    assert (o2.status, o2.cancel_reason, o2.cancelled_ms) == ("cancelled", "strategy", close_ms(1))
    t = only(res.trades)
    assert (t.exit_idx, t.exit_reason, t.exit_ref_price, t.exit_role) == (3, "strategy", 100.6, "taker")
    with pytest.raises(ValueError, match="not a pending entry"):
        sim(rows, {1: [CancelOrder(7)]})
    with pytest.raises(ValueError, match="not open"):
        sim(rows, {1: [ClosePosition(1, "x")]})


def test_short_brackets_are_refused():
    short = PlaceBracketLimit(side="short", price=100.0, stop=101.0, target=98.0, expires_ms=close_ms(5),
                              hold_rule=HoldRule("none"), tag={})
    with pytest.raises(NotImplementedError):
        sim([FLAT, FLAT], {0: [short]})


def test_use_1m_without_1m_candles_raises():
    with pytest.raises(ValueError, match="use_1m"):
        sim([FLAT, FLAT], {}, cfg=ExecConfig())


def test_period_bounds():
    rows = [FLAT] * 10
    res, strat = sim(rows, {}, first=3, last=6)
    assert (res.summary["first_idx"], res.summary["last_idx"], res.summary["n_candles"]) == (3, 6, 4)
    assert sorted(strat.accounts) == [3, 4, 5, 6]
    cd = candles_from_rows(rows, start_ms=T0_MS)
    with pytest.raises(ValueError, match="no 15m candle"):
        run(cd, None, no_funding(), SessionCalendar(NY, cd.ts), strat, ZERO_COST, close_ms(20), close_ms(30))


# --- 4.2 the 1m resolver in the loop -------------------------------------------------------------


def test_1m_target_before_stop_is_a_target():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), (100.5, 102.5, 98.5, 100.0), FLAT]
    m = [(100.5, 100.6, 100.4, 100.5), (100.5, 102.3, 100.5, 102.2), (102.2, 102.2, 98.5, 100.0)]
    m += [(100.0, 100.0, 100.0, 100.0)] * 12
    m1 = minute_candles(rows, {2: m})
    res, _ = sim(rows, {0: [bracket()]}, cfg=ExecConfig(fee_maker=0, fee_taker=0, slippage=0), candles1m=m1)
    t = only(res.trades)
    assert (t.exit_reason, t.exit_resolution, t.exit_ms, t.exit_ref_price) == (
        "target", "1m", T0_MS + 2 * STEP_15M_MS + 60_000, 102.0)


def test_1m_stop_before_target_is_a_stop():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), (100.5, 102.5, 98.5, 100.0), FLAT]
    m1 = minute_candles(rows)  # default minutes: low before high
    res, _ = sim(rows, {0: [bracket()]}, cfg=ExecConfig(fee_maker=0, fee_taker=0, slippage=0), candles1m=m1)
    t = only(res.trades)
    assert (t.exit_reason, t.exit_resolution) == ("stop", "1m")


def test_1m_fill_then_target_within_one_candle_is_a_target():
    rows = [FLAT, (100.5, 102.5, 99.9, 102.0), FLAT]
    m1 = minute_candles(rows)  # minute 1 fills at the low, minute 2 reaches the high
    res, _ = sim(rows, {0: [bracket()]}, cfg=ExecConfig(fee_maker=0, fee_taker=0, slippage=0), candles1m=m1)
    t = only(res.trades)
    assert (t.entry_ms, t.exit_ms, t.exit_reason, t.fill_resolution) == (
        T0_MS + STEP_15M_MS + 60_000, T0_MS + STEP_15M_MS + 120_000, "target", "1m")
    assert t.hold_minutes == 1


def test_1m_single_minute_touching_both_is_pessimistic():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), (100.5, 102.5, 98.5, 100.0), FLAT]
    m = [(100.5, 102.5, 98.5, 100.0)] + [(100.0, 100.0, 100.0, 100.0)] * 14
    m1 = minute_candles(rows, {2: m})
    res, _ = sim(rows, {0: [bracket()]}, cfg=ExecConfig(fee_maker=0, fee_taker=0, slippage=0), candles1m=m1)
    assert only(res.trades).exit_resolution == "1m_pessimistic"


def test_missing_minute_is_pessimistic_and_logged(caplog):
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), (100.5, 102.5, 98.5, 100.0), FLAT]
    m = [(100.5, 102.3, 100.5, 102.2)] + [(100.0, 100.0, 98.5, 100.0)] * 14  # target first, but ...
    m1 = minute_candles(rows, {2: m}, drop=[2 * 15 + 7])  # ... one minute is missing
    with caplog.at_level("WARNING"):
        res, _ = sim(rows, {0: [bracket()]}, cfg=ExecConfig(fee_maker=0, fee_taker=0, slippage=0), candles1m=m1)
    t = only(res.trades)
    assert (t.exit_reason, t.exit_resolution) == ("stop", "15m_pessimistic_missing_1m")
    assert res.summary["n_missing_1m"] == 1 and only(res.events[res.events["kind"] == "missing_1m"]).idx == 2
    assert "1m candles missing" in caplog.text


def test_use_1m_false_ignores_1m_candles():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), (100.5, 102.5, 98.5, 100.0), FLAT]
    m = [(100.5, 102.3, 100.5, 102.2)] + [(100.0, 100.0, 98.5, 100.0)] * 14
    res, _ = sim(rows, {0: [bracket()]}, cfg=ZERO_COST, candles1m=minute_candles(rows, {2: m}))
    assert only(res.trades).exit_resolution == "15m_pessimistic"


# --- 4.3 costs and funding in the loop --------------------------------------------------------


def test_hand_trade_with_costs_and_funding():
    # Fill at 100 on candle 1, funding at the open of candle 4 (rate 0.0001 at close[3] = 101), target 102 on 5.
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), FLAT, (100.5, 101.2, 100.4, 101.0), FLAT,
            (100.5, 102.2, 100.4, 102.0), FLAT]
    res, _ = sim(rows, {0: [bracket()]}, cfg=COSTS, funding=funding_at([T0_MS + 4 * STEP_15M_MS], [0.0001]))
    t = only(res.trades)
    assert t.qty == pytest.approx(100.0) and t.funding == pytest.approx(0.0001 * 100 * 101.0)
    assert t.fees == pytest.approx(2.0 + 2.04) and t.slippage_cost == 0.0
    assert t.net_r == pytest.approx(2.0 - 0.0002 * (100 + 102) - 0.0101 / 1.0 * 1.0)
    assert t.net_pnl == pytest.approx(200.0 - 4.04 - 1.01)
    assert res.summary["final_equity"] == pytest.approx(10_000 + 200.0 - 4.04 - 1.01)
    f = only(res.events[res.events["kind"] == "funding"])
    assert (f.idx, f.price, f.trade_id) == (4, 101.0, 1)


def boundary_rows():
    # fill on candle 1 (closes at τ_2), position open through candle 4, stop on candle 5
    return [FLAT, (100.5, 100.8, 99.9, 100.5), FLAT, FLAT, FLAT, (100.5, 100.6, 98.0, 98.5), FLAT, FLAT]


@pytest.mark.parametrize("f_idx, charged", [
    (2, True),   # fill candle close == f
    (5, True),   # exit candle open == f
    (6, False),  # exit candle close == f
    (1, False),  # fill candle open == f: not yet open
])
def test_funding_boundaries(f_idx, charged):
    res, _ = sim(boundary_rows(), {0: [bracket()]}, funding=funding_at([T0_MS + f_idx * STEP_15M_MS], [0.001]))
    t = only(res.trades)
    assert bool(t.funding != 0.0) is charged
    if charged:
        assert t.funding == pytest.approx(0.001 * t.qty * FLAT[3])


def test_four_hour_funding_is_honoured_and_negative_rates_pay_the_long():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5)] + [FLAT] * (DAY + 2)
    times = [T0_MS + 4 * 3_600_000 * k for k in range(1, 7)]  # 04:00 .. 24:00
    res, _ = sim(rows, {0: [bracket()]}, funding=funding_at(times, [0.0001, -0.0002] * 3))
    t = only(res.trades)  # closed at data end
    assert (res.events["kind"] == "funding").sum() == 6
    assert t.funding == pytest.approx(3 * (0.0001 - 0.0002) * t.qty * FLAT[3])
    assert t.funding < 0 and t.funding_r == pytest.approx(t.funding / t.risk_usd)


def test_data_end_position_pays_funding_at_last_open():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), FLAT, FLAT]
    res, _ = sim(rows, {0: [bracket()]}, funding=funding_at([T0_MS + 3 * STEP_15M_MS], [0.001]))
    t = only(res.trades)
    assert (t.exit_reason, t.exit_idx) == ("data_end", 3) and t.funding == pytest.approx(0.001 * t.qty * FLAT[3])


def test_off_grid_funding_raises():
    with pytest.raises(ValueError, match="15m grid"):
        sim([FLAT, FLAT], {}, funding=funding_at([T0_MS + 60_000], [0.0001]))


def test_funding_without_positions_moves_nothing():
    rows = [FLAT] * (2 * DAY)
    res, _ = sim(rows, {}, funding=funding_at([T0_MS + 8 * 3_600_000 * k for k in range(6)], [0.01] * 6))
    assert res.daily["ret"].tolist() == [0.0, 0.0] and res.daily["funding_paid"].tolist() == [0.0, 0.0]


# --- 4.4 sizing, leverage, ledger, daily marks -----------------------------------------------


def test_sizing_at_placement_uses_marked_equity():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), FLAT, (100.5, 100.6, 100.0, 100.2), FLAT]
    # second bracket placed at close of 3 while position 1 is open, marked at 100.2: equity 10_020
    res, strat = sim(rows, {0: [bracket()], 3: [bracket(price=100.1, stop=99.1, target=103.0)]})
    o2 = res.orders[res.orders["order_id"] == 4].iloc[0]  # 1 entry, 2 stop, 3 target, 4 second entry
    assert o2.kind == "entry_limit" and strat.accounts[3].equity_mtm == pytest.approx(10_020.0)
    assert o2.qty == pytest.approx(0.01 * 10_020.0 / 1.0)


def test_per_order_cap_skips_and_counts():
    res, strat = sim([FLAT, FLAT, FLAT], {0: [bracket(stop=99.9, target=100.5)]}, cfg=ExecConfig(
        fee_maker=0, fee_taker=0, slippage=0, max_leverage=5.0, use_1m=False))
    assert res.skips == {"leverage": 1} and [e.kind for e in strat.events] == ["skipped_leverage"]


def test_total_notional_cap_counts_open_positions():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), FLAT, FLAT]
    cfg = ExecConfig(fee_maker=0, fee_taker=0, slippage=0, max_leverage=1.5, use_1m=False)
    res, _ = sim(rows, {0: [bracket()], 2: [bracket()]}, cfg=cfg)  # 1x each: the second would make 2x
    assert res.skips["leverage"] == 1
    assert entry_orders(res)["cancel_reason"].tolist() == [None, "leverage_cap"]
    res, _ = sim(rows, {0: [bracket()], 2: [bracket()]}, cfg=ExecConfig(
        fee_maker=0, fee_taker=0, slippage=0, max_leverage=2.1, use_1m=False))
    assert res.skips["leverage"] == 0


def test_liquidation_assertion_raises_from_the_loop():
    cfg = ExecConfig(fee_maker=0, fee_taker=0, slippage=0, mmr=0.2, use_1m=False)
    with pytest.raises(LiquidationAboveStopError):
        sim([FLAT, FLAT], {0: [bracket(stop=99.9, target=100.5)]}, cfg=cfg)


def test_placement_with_non_positive_equity_raises():
    # risk 50 % (50x) and a gap far through the stop: equity goes negative, the next placement must refuse
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), (50.0, 50.0, 40.0, 45.0), FLAT]
    cfg = ExecConfig(fee_maker=0, fee_taker=0, slippage=0, risk_per_trade=0.5, max_leverage=1000.0, use_1m=False)
    with pytest.raises(ValueError, match="equity"):
        sim(rows, {0: [bracket()], 2: [bracket(price=44.0, stop=43.0, target=46.0)]}, cfg=cfg)


def test_equity_after_a_stop_out_equals_the_hand_computation():
    rows = [FLAT, (100.5, 100.6, 98.5, 99.0), FLAT]
    res, _ = sim(rows, {0: [bracket()]}, cfg=COSTS)
    assert res.summary["final_equity"] == pytest.approx(10_000 - 100 - 2.0 - 4.95 - 1.98)
    assert only(res.daily).equity == pytest.approx(9891.07)


def test_daily_returns_compound_to_the_final_equity_and_flat_days_are_zero():
    rows = [FLAT] * DAY + [FLAT, (100.5, 100.8, 99.9, 100.5)] + [FLAT] * (DAY - 2) + [
        (100.5, 102.5, 100.4, 102.0)] + [FLAT] * (2 * DAY - 1)
    res, _ = sim(rows, {DAY: [bracket()]}, cfg=COSTS)
    d = res.daily
    assert d["date"].tolist() == [date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 4)]
    assert d["ret"].iloc[0] == 0.0 and d["ret"].iloc[3] == 0.0
    assert d["n_open"].tolist() == [0, 1, 0, 0]
    assert d["exposure_notional"].iloc[1] == pytest.approx(only(res.trades).qty * FLAT[3])
    growth = float(np.prod(1.0 + d["ret"].to_numpy()))
    assert growth * 10_000 == pytest.approx(res.summary["final_equity"], rel=1e-12)
    assert d["equity"].iloc[-1] == res.summary["final_equity"]


def test_daily_mark_includes_the_data_end_close():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), FLAT]
    res, _ = sim(rows, {0: [bracket()]}, cfg=COSTS)
    t = only(res.trades)
    assert t.exit_reason == "data_end" and only(res.daily).equity == pytest.approx(10_000 + t.net_pnl)


# --- 4.6 simulator-level look-ahead, determinism, properties on random walks ------------------

from perpbt.execution.orders import RESOLUTIONS  # noqa: E402
from perpbt.execution.trades import utc_date  # noqa: E402
from perpbt.data.store import Funding  # noqa: E402
from tests.sim_harness import N15, run_walk, walk  # noqa: E402
from tests.strategy_harness import UTC  # noqa: E402
from tests.synthetic import perturb_after  # noqa: E402


def perturbed(m1, fund, cut, seed):
    m1p = perturb_after(m1, cut * 15 + 14, seed=seed, step_sigma=0.0006)
    after = fund.ts >= T0_MS + (cut + 1) * STEP_15M_MS
    rates = fund.rate.copy()
    rates[after] = np.random.default_rng(seed).normal(0.0, 0.001, int(after.sum()))
    return m1p, Funding("TEST", fund.ts, rates, fund.interval_h)


def visible(res, cut):
    ev = res.events
    ev = ev[(ev["idx"] <= cut) & (ev["reason"] != "data_end")].reset_index(drop=True)
    tr = res.trades
    tr = tr[(tr["exit_idx"] <= cut) & (tr["exit_reason"] != "data_end")].reset_index(drop=True)
    cut_day = utc_date(T0_MS + cut * STEP_15M_MS)
    day = res.daily
    day = day[day["date"] < cut_day].reset_index(drop=True)
    return ev, tr, day


def assert_simulator_causal(seed, spec, params=None):
    m1, fund = walk(seed)
    full = run_walk(m1, fund, spec, params=params)
    assert len(full.trades) > 0
    # Fixed cuts plus cuts exactly on candles where something happens: a one-candle peek shows only there.
    ev = full.events
    kinds = ("placed", "filled", "closed", "cancelled")
    busy = [int(x) for kind in kinds for x in ev.loc[ev["kind"] == kind, "idx"].iloc[:2]]
    tr = full.trades
    busy += [int(x) for x in tr.loc[tr["exit_reason"].isin(["max_hold", "session_end"]), "exit_idx"].iloc[:2]]
    for cut in sorted({400, 900, N15 - 2, *(b for b in busy if 96 < b < N15 - 1)}):
        want = visible(full, cut)
        if cut == N15 - 2:
            assert len(want[0]) > 0 and len(want[2]) > 0  # never a vacuous comparison
        for got in (visible(run_walk(m1, fund, spec, last=cut, params=params), cut),
                    visible(run_walk(*perturbed(m1, fund, cut, seed + 1000), spec, params=params), cut)):
            for a, b in zip(want, got, strict=True):
                pd.testing.assert_frame_equal(a, b)


@pytest.mark.parametrize("spec", [UTC, NY], ids=["utc", "ny"])
@pytest.mark.parametrize("seed", range(20))
def test_simulator_is_causal(seed, spec):
    assert_simulator_causal(seed, spec)


@pytest.mark.parametrize("seed", range(8))
def test_simulator_is_causal_with_time_exits_and_pierce(seed):
    params = StrategyParams(hold_rule=HoldRule("max_hold", hours=2.0), pierce=0.0005)
    assert_simulator_causal(seed, UTC, params)


def test_two_runs_write_byte_identical_parquet(tmp_path):
    m1, fund = walk(7)
    for k in (1, 2):
        run_walk(m1, fund, UTC).to_parquet(tmp_path / f"run{k}")
    for name in ("orders", "fills", "trades", "daily", "events"):
        a = (tmp_path / "run1" / f"{name}.parquet").read_bytes()
        assert a == (tmp_path / "run2" / f"{name}.parquet").read_bytes(), name
    back = pd.read_parquet(tmp_path / "run1" / "daily.parquet")
    assert isinstance(back["date"].iloc[0], date) and len(back) > 0


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("params", [
    StrategyParams(),
    StrategyParams(hold_rule=HoldRule("max_hold", hours=2.0), pierce=0.0005),
    StrategyParams(hold_rule=HoldRule("session_end"), r_target=1.0, entry_level="mid"),
], ids=["primary", "max_hold_pierce", "session_end_mid"])
def test_random_walk_properties(seed, params):
    m1, fund = walk(seed)
    res = run_walk(m1, fund, NY, params=params)
    tr, s = res.trades, res.summary
    assert len(tr) > 0
    # the ledger: every USDT is accounted for by the trades
    assert 10_000 + tr["net_pnl"].sum() == pytest.approx(s["final_equity"], rel=1e-12)
    assert res.daily["equity"].iloc[-1] == s["final_equity"]
    assert float(np.prod(1 + res.daily["ret"])) * 10_000 == pytest.approx(s["final_equity"], rel=1e-12)
    # cross-references
    filled = res.orders[(res.orders["kind"] == "entry_limit") & (res.orders["status"] == "filled")]
    assert sorted(filled["trade_id"].tolist()) == tr["trade_id"].tolist() == list(range(1, len(tr) + 1))
    assert len(res.fills) == 2 * len(tr) and set(res.fills["resolution"]) <= set(RESOLUTIONS)
    exits = res.orders[res.orders["kind"] != "entry_limit"]
    assert (exits.groupby("trade_id")["status"].apply(lambda x: (x == "filled").sum()) == 1).all()
    assert (res.orders["status"] != "pending").all()
    assert s["n_filled"] == len(tr) and (res.events["kind"] == "closed").sum() == len(tr)
    # trade-level bounds
    assert (tr["hold_minutes"] >= 0).all() and (tr["mae_r"] <= 1e-12).all() and (tr["mfe_r"] >= -1e-12).all()
    assert (tr["exit_ms"] >= tr["entry_ms"]).all() and (tr["exit_idx"] >= tr["entry_idx"]).all()
    gap = tr["fill_resolution"] == "open_gap"
    r_target = params.r_target
    assert (tr.loc[~gap, "gross_r"] >= -1 - 1e-9).all() and (tr["gross_r"] <= r_target + 1e-9).all()
    assert (tr.loc[~gap, "net_r"] >= -1 - tr.loc[~gap, "cost_r"] - 1e-9).all()
    assert np.allclose(tr["cost_r"], tr["gross_r"] - tr["net_r"])
    assert (tr["implied_leverage"] <= 25.0).all()
    if params.hold_rule.kind == "max_hold":
        assert (tr["hold_minutes"] <= 120).all()
    if params.hold_rule.kind == "session_end":
        assert (tr["exit_ms"] < tr["session_open_ms"] + 7 * 3_600_000).all()  # inside the NY window


# --- review fixes: stamps of time exits on a 1m-filled candle, labels, coverage -----------------

# Candle 1 touches entry and target (ambiguous); its minutes reach the target at minute 1, fill at minute 2.
AMBIG = (100.5, 102.5, 99.9, 100.5)
AMBIG_MINUTES = [(100.5, 100.6, 100.4, 100.5), (100.5, 102.5, 100.5, 101.0), (101.0, 101.0, 99.9, 100.2)]
AMBIG_MINUTES += [(100.2, 100.5, 100.2, 100.5)] * 12
NO_COST_1M = ExecConfig(fee_maker=0, fee_taker=0, slippage=0)


def ambiguous_rows(n_before, n_after):
    rows = [FLAT] * n_before + [AMBIG] + [FLAT] * n_after
    return rows, minute_candles(rows, {n_before: AMBIG_MINUTES})


@pytest.mark.parametrize("case", ["data_end", "session_end", "strategy"])
def test_time_exit_on_the_candle_of_a_1m_fill_is_never_before_the_fill(case):
    if case == "data_end":
        rows, m1 = ambiguous_rows(1, 0)
        script = {0: [bracket()]}
    elif case == "session_end":
        rows, m1 = ambiguous_rows(DAY - 1, 2)
        script = {DAY - 2: [bracket(hold=HoldRule("session_end"))]}
    else:
        rows, m1 = ambiguous_rows(1, 2)
        script = {0: [bracket()], 1: [ClosePosition(1, "manual")]}
    res, _ = sim(rows, script, cfg=NO_COST_1M, candles1m=m1)
    t = only(res.trades)
    fill_candle = t.entry_idx
    assert t.entry_ms == T0_MS + fill_candle * STEP_15M_MS + 2 * 60_000  # the 1m walk filled at minute 2
    assert (t.exit_reason, t.exit_idx, t.exit_ms, t.hold_minutes) == (case, fill_candle, t.entry_ms, 0)
    assert t.exit_resolution == ("15m_unambiguous" if case == "data_end" else "1m")
    exit_fill = res.fills.iloc[-1]
    assert exit_fill.ts_ms == t.entry_ms
    o = res.orders[res.orders["kind"] == "time_exit"].iloc[0]
    assert o.placed_ms == o.filled_ms == t.exit_ms  # placed and filled at the same instant
    # MAE/MFE window is the fill minute to the 15m close: minutes 2..14 (lows 99.9, highs <= 101.0)
    assert (t.mae_r, t.mfe_r) == pytest.approx((-0.1, 1.0))


def test_strategy_close_on_an_unevaluated_candle_is_unambiguous():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), FLAT, FLAT]
    res, _ = sim(rows, {0: [bracket()], 2: [ClosePosition(1, "x")]})
    assert only(res.trades).exit_resolution == "15m_unambiguous"


def test_summary_reports_coverage_and_warns_when_the_data_is_short(caplog):
    rows = [FLAT] * 20
    cd = candles_from_rows(rows, start_ms=T0_MS)
    strat = OrderBlockStrategy(StrategyParams())
    with caplog.at_level("WARNING"):
        res = run(cd, None, no_funding(), SessionCalendar(NY, cd.ts), strat, ZERO_COST,
                  T0_MS - STEP_15M_MS, close_ms(30))
    s = res.summary
    assert (s["first_candle_ms"], s["last_close_ms"], s["warmup_candles"]) == (T0_MS, close_ms(19), 0)
    assert "cover" in caplog.text and "warmup" in caplog.text
    caplog.clear()
    with caplog.at_level("WARNING"):
        sim(rows, {})  # ScriptedStrategy wants no warmup and the data cover the period
    assert caplog.text == ""


def test_parquet_schema_is_fixed_whatever_the_rows(tmp_path):
    import pyarrow.dataset as ds
    import pyarrow.parquet as pq

    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), (100.5, 100.6, 98.0, 99.0), FLAT]
    full, _ = sim(rows, {0: [bracket()]})
    empty, _ = sim([FLAT, FLAT], {})
    full.to_parquet(tmp_path / "a")
    empty.to_parquet(tmp_path / "b")
    for name in ("orders", "fills", "trades", "daily", "events"):
        fa, fb = tmp_path / "a" / f"{name}.parquet", tmp_path / "b" / f"{name}.parquet"
        assert pq.read_schema(fa).remove_metadata() == pq.read_schema(fb).remove_metadata(), name
        both = ds.dataset([str(fb), str(fa)]).to_table()  # the empty file first: no null-typed column
        assert both.num_rows == len(getattr(full, name)) + len(getattr(empty, name))
    sch = pq.read_schema(tmp_path / "b" / "trades.parquet")
    assert str(sch.field("regime_trend").type) == "string" and str(sch.field("exit_reason").type) == "string"
    assert str(pq.read_schema(tmp_path / "a" / "daily.parquet").field("date").type) == "date32[day]"
