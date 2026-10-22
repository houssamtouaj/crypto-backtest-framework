"""Spec test 5.4: baselines A and B and their p-values."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from perpbt.config import ExecConfig, HoldRule, StrategyParams
from perpbt.data.sessions import SessionCalendar
from perpbt.stats.baselines import (
    b_plan,
    b_spec,
    make_setup,
    observed_components,
    p_value,
    precompute_table_a,
    r_subset,
    run_means,
    runs_a,
    runs_b,
    table_from_outcome,
)
from perpbt.stats.evaluator import COMPONENTS, evaluate, net_r, specs_from_trades
from perpbt.stats.repricing import reprice
from tests.coinflip import CoinFlipStrategy
from tests.sim_harness import WALK_COSTS, WALK_START, run_walk, walk
from tests.strategy_harness import NY, UTC
from tests.synthetic import STEP_15M_MS, aggregate

NO_1M = ExecConfig(**{**vars(WALK_COSTS), "use_1m": False})


def market(seed, spec, params, *, strategy=None, cfg=WALK_COSTS, n15=1_500):
    m1, fund = walk(seed, n15)
    res = run_walk(m1, fund, spec, params=params, strategy=strategy, cfg=cfg)
    c15 = aggregate(m1, 15, tf="15m")
    end = int(c15.ts[-1]) + STEP_15M_MS
    return res, c15, m1, fund, end


def setup_for(c15, m1, fund, spec, params, cfg, end, **kw):
    return make_setup(c15, m1 if cfg.use_1m else None, fund, SessionCalendar(spec, c15.ts), cfg, params,
                      WALK_START, end, **kw)


# --- p-values ------------------------------------------------------------------------------------

def test_p_value_formula():
    runs = np.array([0.1, 0.2, 0.3, 0.4])
    assert p_value(0.25, runs)[0] == pytest.approx(3 / 5)
    assert p_value(9.0, runs)[0] == pytest.approx(1 / 5)  # never 0
    p, z = p_value(0.25, runs)
    assert z == pytest.approx((0.25 - 0.25) / runs.std(ddof=1))
    assert p_value(0.1, np.array([np.nan, np.nan])) == (None, None)
    assert p_value(0.1, np.array([0.1, 0.1]))[1] is None  # zero spread: no z


# --- baseline A ----------------------------------------------------------------------------------

def test_forced_slots_reproduce_the_real_mean_r():
    params = StrategyParams()
    res, c15, m1, fund, end = market(3, UTC, params)
    tr = r_subset(res.trades)
    assert len(tr) > 5
    spec = specs_from_trades(tr, c15)  # the real fills, the real stops: slots forced to the real entries
    out = evaluate(spec, c15, m1, fund, WALK_COSTS, last_idx=res.summary["last_idx"])
    table = table_from_outcome(np.arange(len(tr)), spec.entry_idx, out, tr["trade_id"].to_numpy())
    runs = runs_a(table, 40, np.random.default_rng(0))
    for costs in ((0.0002, 0.0005, 0.0002), (0.0, 0.0005, 0.001)):
        want = reprice(tr, *costs).mean()
        np.testing.assert_allclose(run_means(runs, *costs), want, rtol=0, atol=1e-12)
        assert reprice(observed_components(tr), *costs).mean() == pytest.approx(want, abs=1e-12)


def test_table_a_slots_are_the_in_window_candles_of_the_trades_session():
    params = StrategyParams(hold_rule=HoldRule("session_end"))
    res, c15, m1, fund, end = market(2, NY, params)
    s = setup_for(c15, m1, fund, NY, params, WALK_COSTS, end)
    table = precompute_table_a(res.trades, s)
    tr = r_subset(res.trades).set_index("trade_id")
    assert len(table) > 0 and table.n_excluded == 0
    cal = s.calendar
    for row, tid in enumerate(table.trade_ids):
        sid = tr.loc[tid, "session_id"]
        want = np.flatnonzero((cal.session_id == sid) & cal.in_window)
        assert table.entry_idx[row, : table.n_slots[row]].tolist() == want.tolist()
        assert (table.entry_idx[row, table.n_slots[row]:] == -1).all()
    frame = table.to_frame()
    assert len(frame) == table.n_slots.sum() and list(frame.columns[3:]) == list(COMPONENTS)


def test_table_a_drops_slots_that_reach_the_data_end_and_counts_excluded_trades():
    params = StrategyParams()
    res, c15, m1, fund, end = market(3, UTC, params)
    s = setup_for(c15, m1, fund, UTC, params, WALK_COSTS, end)
    trades = res.trades.copy()
    trades.loc[0, "stop_dist_atr"] = np.nan  # a real trade with no ATR multiple
    table = precompute_table_a(trades, s)
    frame = table.to_frame()
    assert table.n_excluded >= 1 and trades.loc[0, "trade_id"] not in set(table.trade_ids)
    assert np.isfinite(frame[list(COMPONENTS)].to_numpy()).all()


def test_runs_a_mean_is_the_slot_average_in_expectation():
    params = StrategyParams()
    res, c15, m1, fund, end = market(4, UTC, params)
    table = precompute_table_a(res.trades, setup_for(c15, m1, fund, UTC, params, WALK_COSTS, end))
    runs = runs_a(table, 4000, np.random.default_rng(1))
    exact = np.nanmean(table.comps[:, :, 0], axis=1).mean()  # gross_r: mean over trades of slot means
    assert runs["gross_r"].mean() == pytest.approx(exact, abs=4 * runs["gross_r"].std() / np.sqrt(4000))
    assert (runs["n"] == len(table)).all()


# --- baseline B ----------------------------------------------------------------------------------

def test_baseline_b_respects_weekdays_listing_and_period():
    params = StrategyParams()
    res, c15, m1, fund, end = market(5, NY, params, n15=2_000)
    listing = WALK_START + 6 * 96 * STEP_15M_MS
    s = setup_for(c15, m1, fund, NY, params, WALK_COSTS, end, listing_ms=listing)
    plan = b_plan(res.trades, s)
    g = np.random.default_rng(0)
    e = np.concatenate([b_spec(s, plan, g).entry_idx for _ in range(200)])
    assert len(np.unique(s.calendar.session_id[e])) == len(plan.first)  # every eligible session drawn
    ts = c15.ts[e]
    assert (ts >= listing).all() and (e >= s.first_idx).all() and (e <= s.last_idx).all()
    assert s.calendar.in_window[e].all()
    ny = ZoneInfo("America/New_York")
    days = {datetime.fromtimestamp(int(t) / 1000, timezone.utc).astimezone(ny).weekday() for t in ts}
    assert days <= {0, 1, 2, 3, 4} and len(days) == 5


def test_baseline_b_run_means_reprice_to_direct_evaluation():
    params = StrategyParams(hold_rule=HoldRule("max_hold", hours=6.0), pierce=0.0005)
    res, c15, m1, fund, end = market(6, UTC, params)
    s = setup_for(c15, m1, fund, UTC, params, WALK_COSTS, end)
    runs = runs_b(res.trades, s, 3, np.random.default_rng(9))
    g = np.random.default_rng(9)
    plan = b_plan(res.trades, s)
    for r in range(3):
        out = evaluate(b_spec(s, plan, g), c15, m1, fund, WALK_COSTS, last_idx=s.last_idx)
        keep = out.exit_reason != "data_end"
        assert runs.loc[r, "n"] == keep.sum()
        for costs in ((0.0002, 0.0005, 0.0002), (0.0, 0.0005, 0.001)):
            direct = net_r(out, *costs)[keep].mean()
            assert run_means(runs.iloc[[r]], *costs)[0] == pytest.approx(direct, abs=1e-12)


def test_baseline_b_does_not_depend_on_the_chunk_size():
    params = StrategyParams()
    res, c15, m1, fund, end = market(7, UTC, params)
    s = setup_for(c15, m1, fund, UTC, params, WALK_COSTS, end)
    a = runs_b(res.trades, s, 7, np.random.default_rng(2), chunk_trades=1)
    b = runs_b(res.trades, s, 7, np.random.default_rng(2))
    pd.testing.assert_frame_equal(a, b)


def test_baselines_with_no_trades():
    params = StrategyParams()
    res, c15, m1, fund, end = market(7, UTC, params)
    s = setup_for(c15, m1, fund, UTC, params, WALK_COSTS, end)
    none = res.trades.iloc[0:0]
    assert len(precompute_table_a(none, s)) == 0
    assert runs_a(precompute_table_a(none, s), 5, np.random.default_rng(0))[list(COMPONENTS)].isna().all().all()
    assert runs_b(none, s, 5, np.random.default_rng(0))["gross_r"].isna().all()


# --- null calibration: a coin-flip strategy on random walks ----------------------------------------

def test_coin_flip_p_values_are_not_concentrated_below_005():
    params = StrategyParams()
    pa, pb = [], []
    for seed in range(20):
        strategy = CoinFlipStrategy(params, seed, p=1 / 24)
        res, c15, m1, fund, end = market(100 + seed, UTC, params, strategy=strategy, cfg=NO_1M, n15=2_880)
        s = setup_for(c15, m1, fund, UTC, params, NO_1M, end)
        costs = (NO_1M.fee_maker, NO_1M.fee_taker, NO_1M.slippage)
        table = precompute_table_a(res.trades, s)
        obs_a = reprice(r_subset(res.trades).set_index("trade_id").loc[table.trade_ids], *costs).mean()
        pa.append(p_value(obs_a, run_means(runs_a(table, 199, np.random.default_rng(seed)), *costs))[0])
        obs_b = reprice(r_subset(res.trades), *costs).mean()
        pb.append(p_value(obs_b, run_means(runs_b(res.trades, s, 199, np.random.default_rng(seed)), *costs))[0])
    pa, pb = np.array(pa), np.array(pb)
    assert (pa > 0).all() and (pb > 0).all()
    assert (pa < 0.05).sum() <= 3, pa
    assert (pb < 0.05).sum() <= 3, pb
    assert 0.2 < np.median(pa) < 0.8 and 0.2 < np.median(pb) < 0.8, (pa, pb)


@pytest.mark.parametrize("hold", [HoldRule("none"), HoldRule("session_end"), HoldRule("max_hold", hours=6.0)],
                         ids=["none", "session_end", "max_hold"])
def test_slot_at_a_coin_flip_entry_is_that_trade(hold):
    """A coin-flip trade buys at the open with a stop ``mult × ATR14[e−1]`` below: baseline geometry exactly."""
    from perpbt.stats.baselines import slot_spec
    params = StrategyParams(hold_rule=hold, r_target=1.5)  # pierce 0: the coin flip's entries fill at the open
    res, c15, m1, fund, end = market(8, NY, params, strategy=CoinFlipStrategy(params, 8, p=1 / 8))
    tr = res.trades[res.trades["fill_resolution"] == "open_gap"].reset_index(drop=True)
    assert len(tr) > 3
    s = setup_for(c15, m1, fund, NY, params, WALK_COSTS, end)
    got = slot_spec(s, tr["entry_idx"].to_numpy(), tr["stop_dist_atr"].to_numpy())
    real = specs_from_trades(tr, c15)
    np.testing.assert_allclose(got.entry_price, tr["entry_price"], rtol=1e-12)
    np.testing.assert_allclose(got.stop, tr["stop_price"], rtol=1e-12)
    np.testing.assert_allclose(got.target, tr["target_price"], rtol=1e-12)
    np.testing.assert_allclose(got.pierce_abs, tr["pierce_abs"], rtol=1e-12)
    assert got.deadline_idx.tolist() == real.deadline_idx.tolist()
    assert set(got.entry_kind) == {"market_open"} and set(got.entry_role) == {"maker"}
    pierced = setup_for(c15, m1, fund, NY, StrategyParams(hold_rule=hold, pierce=0.0005), WALK_COSTS, end)
    e = tr["entry_idx"].to_numpy()
    assert slot_spec(pierced, e, tr["stop_dist_atr"].to_numpy()).pierce_abs.tolist() == (0.0005 * c15.o[e]).tolist()
