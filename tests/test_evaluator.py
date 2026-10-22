"""Spec test 5.3: the batched evaluator, by hand and against the simulator's own trades."""
import math

import numpy as np
import pytest

from perpbt.config import ExecConfig, HoldRule, StrategyParams
from perpbt.stats.evaluator import (
    COMPONENTS,
    TradeSpec,
    deadline_index,
    evaluate,
    net_r,
    specs_from_trades,
)
from perpbt.stats.repricing import reprice
from tests.coinflip import CoinFlipStrategy
from tests.sim_harness import FLAT, WALK_COSTS, funding_at, minute_candles, no_funding, run_walk, walk
from tests.strategy_harness import NY, UTC
from tests.synthetic import STEP_15M_MS, T0_MS, aggregate, candles_from_rows

NO_COST = ExecConfig(fee_maker=0.0, fee_taker=0.0, slippage=0.0, use_1m=False)
COSTS = ExecConfig(fee_maker=0.0002, fee_taker=0.0005, slippage=0.0002, use_1m=False)
FILL = (100.5, 100.6, 99.9, 100.2)  # touches the entry 100 only
TARGET = (101.9, 102.3, 101.8, 102.1)  # touches the target 102 only
STOP = (99.5, 99.6, 98.8, 99.1)  # touches the stop 99 only


def spec(entry_idx=1, *, kind="limit", entry=100.0, stop=99.0, target=102.0, deadline=-1, pierce=0.0,
         role="maker", minute=None, stop_dist=None):
    return TradeSpec(
        entry_idx=[entry_idx], entry_kind=kind, entry_price=[entry], stop=[stop], target=[target],
        deadline_idx=[deadline], pierce_abs=[pierce], entry_role=role,
        entry_minute=None if minute is None else [minute], stop_dist=None if stop_dist is None else [stop_dist],
    )


def one(rows, s, *, cfg=NO_COST, funding=None, m1=None, **kw):
    out = evaluate(s, candles_from_rows(rows, start_ms=T0_MS), m1, funding or no_funding(), cfg, **kw)
    assert len(out) == 1
    return {k: (v[0] if isinstance(v, np.ndarray) else v) for k, v in vars(out).items()}


# --- hand cases ----------------------------------------------------------------------------------

def test_stop_on_the_fill_candle():
    o = one([FLAT, (100.5, 100.6, 98.5, 99.0), FLAT], spec())
    assert (o["exit_idx"], o["exit_ref"], o["exit_reason"], o["exit_role"]) == (1, 99.0, "stop", "taker")
    assert o["gross_r"] == -1.0


def test_target_on_the_fill_candle_is_ignored():
    o = one([FLAT, (100.5, 102.5, 99.9, 102.2), FLAT, TARGET], spec())
    assert (o["exit_idx"], o["exit_ref"], o["exit_reason"], o["exit_role"]) == (3, 102.0, "target", "maker")
    assert o["gross_r"] == 2.0


def test_stop_and_target_on_one_later_candle_is_a_stop():
    o = one([FLAT, FILL, (100.5, 102.5, 98.5, 101.0)], spec())
    assert (o["exit_idx"], o["exit_reason"], o["exit_ref"]) == (2, "stop", 99.0)


def test_gap_below_the_stop_exits_at_the_open():
    o = one([FLAT, FILL, (98.0, 98.5, 97.5, 98.2)], spec())
    assert (o["exit_reason"], o["exit_ref"]) == ("stop", 98.0)


def test_deadline_is_a_taker_time_exit_at_the_close():
    rows = [FLAT, FILL, FLAT, (100.4, 100.7, 100.1, 100.6), FLAT]
    o = one(rows, spec(deadline=3), cfg=COSTS)
    assert (o["exit_idx"], o["exit_reason"], o["exit_ref"], o["exit_role"]) == (3, "time", 100.6, "taker")
    assert o["c_taker_exit"] == o["c_slip"] == 100.6 / 1.0 and o["c_maker_exit"] == 0.0


def test_deadline_on_the_fill_candle_and_a_stop_there_is_a_stop():
    o = one([FLAT, (100.5, 100.6, 98.5, 99.0), FLAT], spec(deadline=1))
    assert o["exit_reason"] == "stop"


def test_data_end_at_the_last_candle_or_at_last_idx():
    rows = [FLAT, FILL, FLAT, FLAT, FLAT]
    o = one(rows, spec())
    assert (o["exit_idx"], o["exit_reason"], o["exit_ref"]) == (4, "data_end", FLAT[3])
    o = one(rows + [STOP], spec(), last_idx=3)
    assert (o["exit_idx"], o["exit_reason"]) == (3, "data_end")
    o = one(rows, spec(deadline=10))  # a deadline after the series end
    assert o["exit_reason"] == "data_end"


def test_pierce_applies_to_the_target_threshold():
    near = (101.9, 102.04, 101.8, 102.0)
    o = one([FLAT, FILL, near, FLAT], spec(pierce=0.05))
    assert o["exit_reason"] == "data_end"
    o = one([FLAT, FILL, (101.9, 102.05, 101.8, 102.0), FLAT], spec(pierce=0.05))
    assert (o["exit_reason"], o["exit_ref"]) == ("target", 102.0)


def test_market_open_fills_at_the_open_and_stop_dist_defaults_to_entry_minus_stop():
    o = one([FLAT, (100.5, 100.6, 100.2, 100.4), TARGET], spec(kind="market_open", entry=-1.0, stop=99.5,
                                                               target=101.5))
    assert o["exit_reason"] == "target"
    assert o["gross_r"] == pytest.approx((101.5 - 100.5) / (100.5 - 99.5))
    assert o["c_maker_entry"] == pytest.approx(100.5 / 1.0)


def test_cost_components_and_net_r_by_hand():
    cfg = COSTS
    o = one([FLAT, FILL, STOP], spec(role="taker", stop_dist=1.25), cfg=cfg)
    assert o["c_taker_entry"] == 100.0 / 1.25 and o["c_maker_entry"] == 0.0
    assert o["c_taker_exit"] == o["c_slip"] == 99.0 / 1.25
    assert o["gross_r"] == (99.0 - 100.0) / 1.25
    s = spec(role="taker", stop_dist=1.25)
    out = evaluate(s, candles_from_rows([FLAT, FILL, STOP], start_ms=T0_MS), None, no_funding(), cfg)
    want = -0.8 - 0.0005 * (80.0 + 79.2) - 0.0002 * 79.2
    assert net_r(out, 0.0002, 0.0005, 0.0002)[0] == pytest.approx(want, abs=1e-12)


def test_funding_from_the_fill_close_to_the_exit_open():
    rows = [FLAT, FILL, FLAT, FLAT, STOP, FLAT]
    t = [T0_MS + k * STEP_15M_MS for k in (1, 2, 3, 4, 5)]  # fill candle open (no), its close .. exit open, exit close (no)
    fund = funding_at(t, [0.01, 0.02, 0.03, 0.04, 0.05])
    o = one(rows, spec(), funding=fund)
    # charged at f = τ2, τ3, τ4, each at the close of the candle before: FILL (100.2), FLAT, FLAT (100.5)
    assert o["funding_r"] == pytest.approx(0.02 * 100.2 + (0.03 + 0.04) * 100.5)


def test_off_grid_funding_raises():
    with pytest.raises(ValueError, match="grid"):
        one([FLAT, FILL, STOP], spec(), funding=funding_at([T0_MS + STEP_15M_MS + 60_000], [0.01]))


def test_a_gap_fill_below_the_stop_is_a_valid_limit_spec():
    """The simulator fills a first-look order at an open already below the stop; exit at that open, gross 0."""
    o = one([FLAT, (98.5, 98.8, 98.0, 98.4), FLAT], spec(entry=98.5, stop=99.0, stop_dist=1.0))
    assert (o["exit_idx"], o["exit_reason"], o["exit_ref"], o["gross_r"]) == (1, "stop", 98.5, 0.0)


@pytest.mark.parametrize("bad", [
    dict(entry_idx=7), dict(kind="market"), dict(role="both"), dict(stop=100.0), dict(stop=float("nan")),
    dict(stop_dist=0.0), dict(kind="market_open", stop=100.6),
])
def test_invalid_specs_raise(bad):
    kw = dict(bad)
    with pytest.raises(ValueError):
        one([FLAT, FILL, STOP], spec(**kw))


def test_use_1m_without_1m_candles_raises():
    with pytest.raises(ValueError, match="1m"):
        one([FLAT, FILL], spec(), cfg=ExecConfig(fee_maker=0, fee_taker=0, slippage=0, use_1m=True))


def test_empty_spec():
    s = TradeSpec(entry_idx=[], entry_kind="limit", entry_price=[], stop=[], target=[], deadline_idx=[],
                  pierce_abs=[], entry_role="maker")
    out = evaluate(s, candles_from_rows([FLAT], start_ms=T0_MS), None, no_funding(), NO_COST)
    assert len(out) == 0 and len(net_r(out, 0, 0, 0)) == 0


def test_deadline_index_is_the_first_candle_closing_at_or_after_the_deadline():
    ts = T0_MS + STEP_15M_MS * np.arange(10, dtype=np.int64)
    assert deadline_index(ts, np.array([T0_MS + 3 * STEP_15M_MS, T0_MS + 3 * STEP_15M_MS + 1])).tolist() == [2, 3]


# --- 1m on the entry candle and on ambiguous candles (Review Focus 1, 2) --------------------------

ONE_M = ExecConfig(fee_maker=0.0, fee_taker=0.0, slippage=0.0, use_1m=True)
AMBIG = (100.5, 102.5, 99.9, 100.5)  # entry and target on one 15m candle


def test_entry_candle_walk_starts_at_the_fill_minute():
    # target touched at minute 1, fill at minute 2: the target came first, so no exit on that candle
    mins = [(100.5, 100.6, 100.4, 100.5), (100.5, 102.5, 100.5, 101.0), (101.0, 101.0, 99.9, 100.2)]
    mins += [(100.2, 100.5, 100.2, 100.5)] * 12
    rows = [FLAT, AMBIG, FLAT]
    m1 = minute_candles(rows, {1: mins})
    o = one(rows, spec(minute=2), cfg=ONE_M, m1=m1)
    assert o["exit_reason"] == "data_end"
    # fill at minute 7, target at minute 9: a target on the fill candle, resolved by 1m
    mins = [(100.5, 100.6, 100.4, 100.5)] * 7 + [(100.5, 100.5, 99.9, 100.1), (100.1, 100.5, 100.1, 100.5)]
    mins += [(100.5, 102.5, 100.5, 102.2)] + [(102.2, 102.3, 102.1, 102.2)] * 5
    m1 = minute_candles(rows, {1: mins})
    o = one(rows, spec(minute=7), cfg=ONE_M, m1=m1)
    assert (o["exit_idx"], o["exit_minute"], o["exit_reason"]) == (1, 9, "target")


def test_fill_minute_allows_the_stop_but_not_the_target():
    rows = [FLAT, (100.5, 102.5, 98.5, 100.5), FLAT]
    mins = [(100.5, 100.6, 100.4, 100.5)] * 3 + [(100.5, 102.5, 99.9, 100.0)]  # fill and target on minute 3
    mins += [(100.0, 100.1, 98.5, 98.9)] + [(98.9, 99.0, 98.8, 98.9)] * 10  # then the stop at minute 4
    m1 = minute_candles(rows, {1: mins})
    o = one(rows, spec(minute=3), cfg=ONE_M, m1=m1)
    assert (o["exit_minute"], o["exit_reason"]) == (4, "stop")


def test_walk_with_no_exit_falls_through_to_deadline():
    # candle 2 touches stop and target on 15m, but its minutes (disagreeing data) touch neither
    rows = [FLAT, FILL, (100.5, 102.5, 98.5, 100.5), FLAT]
    m1 = minute_candles(rows, {2: [(100.5, 100.6, 100.4, 100.5)] * 15})
    o = one(rows, spec(deadline=2), cfg=ONE_M, m1=m1)
    assert (o["exit_idx"], o["exit_reason"], o["exit_ref"]) == (2, "time", 100.5)
    o = one(rows, spec(), cfg=ONE_M, m1=m1)
    assert o["exit_reason"] == "data_end"


def test_missing_minute_falls_back_to_the_15m_rule():
    rows = [FLAT, FILL, (100.5, 102.5, 98.5, 100.5), FLAT]
    m1 = minute_candles(rows, drop=[2 * 15 + 4])
    o = one(rows, spec(), cfg=ONE_M, m1=m1)
    assert (o["exit_idx"], o["exit_reason"]) == (2, "stop")


# --- equivalence with the simulator (spec test 5.3) ------------------------------------------------

REASON = {"stop": "stop", "target": "target", "session_end": "time", "max_hold": "time", "data_end": "data_end"}
CASES = [
    ("ob_primary_utc", UTC, StrategyParams(), "ob"),
    ("ob_max_hold_pierce", UTC, StrategyParams(hold_rule=HoldRule("max_hold", hours=2.0), pierce=0.0005), "ob"),
    ("ob_session_end_ny", NY, StrategyParams(hold_rule=HoldRule("session_end"), r_target=1.0, entry_level="mid"),
     "ob"),
    ("coin_utc", UTC, StrategyParams(), "coin"),
    ("coin_pierce_max_hold", UTC, StrategyParams(hold_rule=HoldRule("max_hold", hours=6.0), pierce=0.0005),
     "coin"),
]


def equivalence(seed, spec_, params, kind, use_1m):
    m1, fund = walk(seed)
    cfg = WALK_COSTS if use_1m else ExecConfig(**{**vars(WALK_COSTS), "use_1m": False})
    strategy = CoinFlipStrategy(params, seed, p=1 / 24) if kind == "coin" else None
    res = run_walk(m1, fund, spec_, params=params, strategy=strategy, cfg=cfg)
    tr = res.trades
    c15 = aggregate(m1, 15, tf="15m")
    s = specs_from_trades(tr, c15)
    out = evaluate(s, c15, m1 if use_1m else None, fund, cfg, last_idx=res.summary["last_idx"])
    assert out.exit_idx.tolist() == tr["exit_idx"].tolist()
    assert out.exit_ref.tolist() == tr["exit_ref_price"].tolist()
    assert out.exit_reason.tolist() == [REASON[r] for r in tr["exit_reason"]]
    assert out.exit_role.tolist() == tr["exit_role"].tolist()
    np.testing.assert_allclose(out.funding_r, tr["funding_r"].to_numpy(), rtol=0, atol=1e-12)
    got = net_r(out, cfg.fee_maker, cfg.fee_taker, cfg.slippage)
    np.testing.assert_allclose(got, tr["net_r"].to_numpy(), rtol=0, atol=1e-12)
    return tr, s, out, c15, m1, fund, cfg, res


@pytest.mark.parametrize("use_1m", [True, False], ids=["1m", "15m"])
@pytest.mark.parametrize("name,spec_,params,kind", CASES, ids=[c[0] for c in CASES])
def test_evaluator_matches_the_simulator(name, spec_, params, kind, use_1m):
    seen = {"open_gap": 0, "data_end": 0, "1m": 0, "n": 0}
    for seed in range(4):
        tr = equivalence(seed, spec_, params, kind, use_1m)[0]
        seen["n"] += len(tr)
        seen["open_gap"] += int((tr["fill_resolution"] == "open_gap").sum())
        seen["data_end"] += int((tr["exit_reason"] == "data_end").sum())
        seen["1m"] += int(tr["exit_resolution"].isin(["1m", "1m_pessimistic"]).sum())
    assert seen["n"] > 10, seen
    if kind == "coin" and params.pierce == 0:
        assert seen["open_gap"] > 0, seen
    if params.hold_rule.kind == "none" and kind == "ob":
        assert seen["data_end"] > 0, seen
    if use_1m and kind == "ob":
        assert seen["1m"] > 0, seen


@pytest.mark.parametrize("name,spec_,params,kind", CASES[:2] + CASES[3:4], ids=[c[0] for c in CASES[:2] + CASES[3:4]])
def test_straggler_and_vectorized_paths_agree(name, spec_, params, kind):
    for seed in range(3):
        _, s, out, c15, m1, fund, cfg, res = equivalence(seed, spec_, params, kind, True)
        last = res.summary["last_idx"]
        for kw in (dict(min_batch=0, max_steps=10**9), dict(max_steps=0), dict(min_batch=10**9)):
            alt = evaluate(s, c15, m1, fund, cfg, last_idx=last, **kw)
            for k in ("exit_idx", "exit_minute", "exit_ref", "exit_reason", "exit_role", *COMPONENTS):
                assert np.array_equal(getattr(alt, k), getattr(out, k), equal_nan=k == "exit_ref"), (kw, k)


def test_reprice_of_the_simulators_trades_is_bit_for_bit():
    """Spec test 5.6, first clause: reprice at the primary costs reproduces the simulator's net_r."""
    for seed in range(3):
        tr = equivalence(seed, UTC, StrategyParams(), "ob", True)[0]
        got = reprice(tr, 0.0002, 0.0005, 0.0002)
        assert np.array_equal(got, tr["net_r"].to_numpy())


def test_components_are_linear_in_the_cost_rates():
    _, _, out, *_ = equivalence(1, UTC, StrategyParams(), "coin", True)
    a = net_r(out, 0.0, 0.0, 0.0)
    b = net_r(out, 0.0002, 0.0005, 0.001)
    want = out.gross_r - 0.0002 * (out.c_maker_entry + out.c_maker_exit) - 0.0005 * out.c_taker_exit \
        - 0.001 * out.c_slip - out.funding_r
    np.testing.assert_allclose(b, want, atol=1e-12)
    np.testing.assert_allclose(a, out.gross_r - out.funding_r, atol=1e-15)
    assert math.isfinite(float(np.sum(b)))
