"""Spec test 5.6: cost re-pricing grid (reprice itself is pinned bit for bit in test_evaluator)."""
import numpy as np
import pytest

from perpbt.config import StatsConfig, StrategyParams
from perpbt.stats.baselines import p_value, precompute_table_a, r_subset, run_means, runs_a, runs_b
from perpbt.stats.bootstrap import percentile_ci, trade_bootstrap
from perpbt.stats.repricing import cost_summary, reprice_grid
from tests.sim_harness import WALK_COSTS
from tests.strategy_harness import UTC
from tests.test_baselines import market, setup_for

STATS = StatsConfig(bootstrap_n=500)


def boot():
    return np.random.default_rng(42)


@pytest.fixture(scope="module")
def variant():
    params = StrategyParams()
    res, c15, m1, fund, end = market(3, UTC, params)
    s = setup_for(c15, m1, fund, UTC, params, WALK_COSTS, end)
    table = precompute_table_a(res.trades, s)
    return res, table, runs_a(table, 99, np.random.default_rng(1)), runs_b(res.trades, s, 49, np.random.default_rng(2))


def test_grid_has_eight_cells_and_the_primary_cell_matches_direct_computations(variant):
    res, table, ra, rb = variant
    tr = r_subset(res.trades)
    grid = reprice_grid(tr, WALK_COSTS, STATS, boot, table_ids=table.trade_ids, runs_a=ra, runs_b=rb)
    assert len(grid) == 8 and grid["fee_taker"].eq(WALK_COSTS.fee_taker).all()
    assert sorted(set(zip(grid["slippage"], grid["fee_maker"]))) == sorted(
        (s, m) for s in STATS.reprice_slippage for m in STATS.reprice_maker)
    prim = grid[grid["is_primary"]]
    assert len(prim) == 1
    row = prim.iloc[0]
    assert row["mean_net_r"] == pytest.approx(tr["net_r"].mean(), abs=1e-12)
    assert (row["ci_lo"], row["ci_hi"]) == percentile_ci(trade_bootstrap(tr["net_r"].to_numpy(), 500, boot()))
    assert row["win_rate"] == (tr["net_r"] > 0).mean() and row["n"] == len(tr)
    costs = (WALK_COSTS.fee_maker, WALK_COSTS.fee_taker, WALK_COSTS.slippage)
    obs_a = tr.set_index("trade_id").loc[table.trade_ids, "net_r"].mean()
    assert (row["p_a"], row["z_a"]) == pytest.approx(p_value(obs_a, run_means(ra, *costs)))
    assert (row["p_b"], row["z_b"]) == pytest.approx(p_value(tr["net_r"].mean(), run_means(rb, *costs)))


def test_cheaper_cells_have_higher_mean_net_r(variant):
    res, table, ra, rb = variant
    grid = reprice_grid(r_subset(res.trades), WALK_COSTS, STATS, boot, table_ids=table.trade_ids, runs_a=ra,
                        runs_b=rb).set_index(["slippage", "fee_maker"])
    assert grid.loc[(0.0, 0.0), "mean_net_r"] > grid.loc[(0.001, 0.0002), "mean_net_r"]


def test_grid_without_baselines_has_null_p_values(variant):
    res = variant[0]
    grid = reprice_grid(r_subset(res.trades), WALK_COSTS, STATS, boot)
    assert grid[["p_a", "z_a", "p_b", "z_b"]].isna().all().all() and len(grid) == 8


def test_grid_with_no_trades():
    grid = reprice_grid(r_subset(variant_empty()), WALK_COSTS, STATS, boot)
    assert len(grid) == 8 and grid["n"].eq(0).all() and grid["mean_net_r"].isna().all()


def variant_empty():
    res = market(3, UTC, StrategyParams())[0]
    return res.trades.iloc[0:0]


def test_cost_summary(variant):
    tr = r_subset(variant[0].trades)
    out = cost_summary(tr)
    assert out["share_cost_r_gt_1"] == pytest.approx((tr["cost_r"] > 1).mean())
    assert out["stop_dist_pct"]["p50"] == pytest.approx(np.median(100 * tr["stop_dist"] / tr["planned_entry"]))
    assert out["stop_dist_atr"]["max"] == pytest.approx(tr["stop_dist_atr"].max())
    assert cost_summary(tr.iloc[0:0])["share_cost_r_gt_1"] is None
