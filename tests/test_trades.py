"""Output tables, MAE/MFE, hold time and data end (spec §4.9, §4.10, test 4.5)."""
import math

import numpy as np
import pandas as pd
import pytest

from perpbt.config import ExecConfig, HoldRule
from perpbt.execution.fills import MinuteIndex
from perpbt.execution.trades import (
    DAILY_SCHEMA,
    EVENTS_SCHEMA,
    FILLS_SCHEMA,
    ORDERS_SCHEMA,
    TRADES_SCHEMA,
    frame,
    mae_mfe,
)
from tests.sim_harness import FLAT, ZERO_COST, bracket, minute_candles, sim
from tests.synthetic import STEP_15M_MS, T0_MS, candles_from_rows

# Columns the spec's §4.10 tables name; the frames may carry more (documented in the spec).
SPEC_ORDERS = ("order_id variant_id pair session_variant session_id kind side price qty status placed_ms "
               "placed_idx expires_ms filled_ms fill_price cancelled_ms cancel_reason trade_id tag").split()
SPEC_FILLS = "fill_id order_id trade_id ts_ms ref_price price qty fee fee_role slippage resolution".split()
SPEC_TRADES = (
    "trade_id variant_id pair session_variant session_id session_open_ms candidate_ms impulse_ms "
    "displacement_ms placed_ms entry_ms entry_price planned_entry stop_price target_price stop_dist exit_ms "
    "exit_ref_price exit_price exit_reason exit_role qty notional equity_at_entry risk_usd actual_risk_usd "
    "implied_leverage max_iso_leverage gross_pnl fees funding slippage_cost net_pnl gross_r net_r cost_r "
    "funding_r c_maker_entry c_maker_exit c_taker_exit c_slip mae_r mfe_r hold_minutes atr_at_entry "
    "stop_dist_atr regime_trend regime_vol dow entry_hour_utc fill_resolution exit_resolution"
).split()
SPEC_DAILY = "variant_id pair session_variant date equity ret n_open exposure_notional funding_paid".split()


@pytest.mark.parametrize("schema, spec_cols", [
    (ORDERS_SCHEMA, SPEC_ORDERS), (FILLS_SCHEMA, SPEC_FILLS), (TRADES_SCHEMA, SPEC_TRADES), (DAILY_SCHEMA, SPEC_DAILY),
])
def test_tables_carry_every_spec_column(schema, spec_cols):
    assert [c for c in schema if c in spec_cols] == spec_cols  # same relative order as the spec


def test_frames_have_fixed_dtypes_also_when_empty():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), (100.5, 100.6, 98.0, 99.0), FLAT]
    res, _ = sim(rows, {0: [bracket()]})
    empty, _ = sim([FLAT, FLAT], {})
    for name, schema in (("orders", ORDERS_SCHEMA), ("fills", FILLS_SCHEMA), ("trades", TRADES_SCHEMA),
                         ("daily", DAILY_SCHEMA), ("events", EVENTS_SCHEMA)):
        for r in (res, empty):
            df = getattr(r, name)
            assert list(df.columns) == list(schema), name
            assert [str(t) for t in df.dtypes] == list(schema.values()), name
    assert len(empty.trades) == 0 and len(empty.daily) == 1


def test_frame_rejects_a_missing_column():
    with pytest.raises(KeyError):
        frame([{"fill_id": 1}], FILLS_SCHEMA)


def test_trade_row_fields_on_a_hand_trade():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), FLAT, (100.5, 100.6, 98.0, 99.0), FLAT]
    res, _ = sim(rows, {0: [bracket(tag={"candidate_idx": 0, "impulse_idx": 0, "other": "x"})]})
    t = res.trades.iloc[0]
    assert (t.entry_ms, t.exit_ms, t.hold_minutes) == (T0_MS + STEP_15M_MS, T0_MS + 3 * STEP_15M_MS, 30)
    assert (t.dow, t.entry_hour_utc) == (2, 0)  # 2020-01-01 was a Wednesday
    assert t.candidate_ms == T0_MS and pd.isna(t.displacement_ms)
    assert (t.notional, t.equity_at_entry, t.implied_leverage) == (10_000.0, 10_000.0, 1.0)
    assert pd.isna(t.regime_trend) and pd.isna(t.regime_vol)
    assert math.isnan(t.atr_at_entry) and math.isnan(t.stop_dist_atr)  # ATR14 not valid at candle 0
    o = res.orders.set_index("order_id")
    assert o.loc[1, "trade_id"] == 1 and o.loc[2, "kind"] == "stop" and o.loc[2, "status"] == "filled"
    assert (o.loc[3, "kind"], o.loc[3, "status"], o.loc[3, "cancel_reason"]) == ("target_limit", "cancelled", "oco")
    f = res.fills
    assert f["order_id"].tolist() == [1, 2] and f["fee_role"].tolist() == ["maker", "taker"]


def test_atr_at_entry_is_the_atr_before_the_fill_candle():
    rows = [FLAT] * 20 + [(100.5, 100.8, 99.9, 100.5)] + [FLAT] * 2
    res, _ = sim(rows, {19: [bracket()]})
    t = res.trades.iloc[0]
    tr = FLAT[1] - FLAT[2]  # flat candles: every true range is the same
    assert t.atr_at_entry == pytest.approx(tr) and t.stop_dist_atr == pytest.approx(1.0 / tr)


# --- MAE / MFE ------------------------------------------------------------------------------


def test_mae_mfe_on_15m_and_on_1m():
    rows = [FLAT, (100.5, 101.5, 99.6, 100.0), (100.0, 100.9, 99.3, 100.5), (100.5, 102.2, 100.4, 102.0), FLAT]
    cd = candles_from_rows(rows, start_ms=T0_MS)
    mae, mfe = mae_mfe(100.0, 1.0, T0_MS + STEP_15M_MS, T0_MS + 4 * STEP_15M_MS, cd, None)
    assert (mae, mfe) == pytest.approx((-0.7, 2.2))
    res, _ = sim(rows, {0: [bracket()]})
    t = res.trades.iloc[0]
    assert (t.exit_reason, t.mae_r, t.mfe_r) == ("target", pytest.approx(-0.7), pytest.approx(2.2))
    # with 1m: the fill is in minute 1 of candle 1 and the target in minute 2 of candle 3
    m1 = minute_candles(rows)
    res, _ = sim(rows, {0: [bracket()]}, cfg=ExecConfig(fee_maker=0, fee_taker=0, slippage=0), candles1m=m1)
    t = res.trades.iloc[0]
    assert (t.entry_ms, t.exit_ms) == (T0_MS + STEP_15M_MS, T0_MS + 3 * STEP_15M_MS)  # unambiguous: 15m stamps
    idx = MinuteIndex(m1)
    hl = idx.span(T0_MS + STEP_15M_MS, T0_MS + 4 * STEP_15M_MS)
    assert len(hl[0]) == 45 and (t.mae_r, t.mfe_r) == pytest.approx((-0.7, 2.2))


def test_mae_mfe_window_narrows_to_the_minutes_with_1m_resolution():
    rows = [FLAT, (100.5, 102.5, 99.9, 102.0), FLAT]
    m1 = minute_candles(rows)  # fill at minute 1 (low 99.9), target at minute 2 (high 102.5)
    res, _ = sim(rows, {0: [bracket()]}, cfg=ExecConfig(fee_maker=0, fee_taker=0, slippage=0), candles1m=m1)
    t = res.trades.iloc[0]
    assert (t.exit_reason, t.exit_resolution) == ("target", "1m")
    assert (t.mae_r, t.mfe_r) == pytest.approx((-0.1, 2.5))  # minutes 1..2 only


def test_mae_mfe_falls_back_to_15m_when_a_minute_is_missing():
    rows = [FLAT, (100.5, 101.0, 99.5, 100.5), FLAT]
    m1 = minute_candles(rows, drop=[20])
    cd = candles_from_rows(rows, start_ms=T0_MS)
    assert MinuteIndex(m1).span(T0_MS + STEP_15M_MS, T0_MS + 2 * STEP_15M_MS) is None
    mae, mfe = mae_mfe(100.0, 1.0, T0_MS + STEP_15M_MS, T0_MS + 2 * STEP_15M_MS, cd, MinuteIndex(m1))
    assert (mae, mfe) == pytest.approx((-0.5, 1.0))


# --- data end -------------------------------------------------------------------------------


def test_data_end_closes_positions_and_cancels_pending_orders():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5), FLAT, (100.5, 100.9, 100.3, 100.6)]
    res, strat = sim(rows, {0: [bracket()], 2: [bracket(price=99.0, stop=98.0, target=101.0)]},
                     cfg=ExecConfig(use_1m=False))
    t = res.trades.iloc[0]
    assert (t.exit_reason, t.exit_idx, t.exit_ref_price, t.exit_role) == ("data_end", 3, 100.6, "taker")
    assert t.exit_price == pytest.approx(100.6 * (1 - 0.0002))
    o = res.orders.set_index("order_id")
    assert (o.loc[4, "status"], o.loc[4, "cancel_reason"]) == ("cancelled", "data_end")
    assert o.loc[5, "kind"] == "time_exit" and o.loc[5, "status"] == "filled"
    s = res.summary
    assert (s["n_trades"], s["n_data_end"], s["n_trades_r"], s["n_orders"], s["n_filled"]) == (1, 1, 0, 2, 1)
    r_subset = res.trades[res.trades["exit_reason"] != "data_end"]
    assert len(r_subset) == 0
    # in order_id order: the position (entry order 1) closes before the pending order 4 is cancelled
    assert [(e.kind, e.reason) for e in strat.events if e.idx == 3] == [("closed", "data_end"), ("cancelled", "data_end")]


def test_hold_minutes_of_a_max_hold_trade():
    rows = [FLAT, (100.5, 100.8, 99.9, 100.5)] + [FLAT] * 100
    res, _ = sim(rows, {0: [bracket(hold=HoldRule("max_hold", hours=24.0))]}, cfg=ZERO_COST)
    t = res.trades.iloc[0]
    assert (t.exit_reason, t.hold_minutes, t.exit_idx) == ("max_hold", 1440, 1 + 96)
    assert np.isclose(t.gross_r, 0.5)
