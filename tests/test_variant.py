"""Spec §5.10: one variant's statistics and stats.json (written, read back without loss, byte-identical)."""
import numpy as np
import pytest

from perpbt.config import StatsConfig, StrategyParams, VariantConfig
from perpbt.data.sessions import SessionCalendar
from perpbt.stats.baselines import p_value, r_subset, run_means
from perpbt.stats.bootstrap import percentile_ci, trade_bootstrap
from perpbt.stats.seeds import rng_for
from perpbt.stats.variant import Market, compute_stats, read_stats, run_baselines, sanitize, write_stats
from tests.coinflip import CoinFlipStrategy
from tests.sim_harness import WALK_COSTS, WALK_START, run_walk, walk
from tests.strategy_harness import UTC
from tests.synthetic import STEP_15M_MS, aggregate

STATS = StatsConfig(bootstrap_n=300, baseline_runs=49)
KEYS = {"schema", "variant_id", "pair", "session_variant", "is_holdout", "period_start_ms", "period_end_ms", "seeds",
        "summary", "skips", "headline", "baselines", "buy_and_hold", "dsr", "repricing", "costs", "distributions",
        "rolling", "per_year", "regimes", "breakdowns", "insample_ref", "n_sessions"}


def cfg_for(params=StrategyParams()):
    return VariantConfig(pair="TEST", session=UTC, params=params, exec=WALK_COSTS, stats=STATS,
                         period_start="2020-01-02", period_end="2020-01-16", is_holdout=False)


def variant(seed=3, strategy=None, params=StrategyParams(), n15=1_500):
    m1, fund = walk(seed, n15)
    res = run_walk(m1, fund, UTC, params=params, strategy=strategy)
    c15 = aggregate(m1, 15, tf="15m")
    market = Market(c15, m1, fund, SessionCalendar(UTC, c15.ts), WALK_START, int(c15.ts[-1]) + STEP_15M_MS)
    return res, market


@pytest.fixture(scope="module")
def full():
    res, market = variant()
    cfg = cfg_for()
    base = run_baselines(res, market, cfg, variant_id="v1")
    return res, market, cfg, base, compute_stats(res, market, cfg, variant_id="v1", baselines=base)


def test_every_block_is_present_and_consistent(full):
    res, market, cfg, base, st = full
    assert set(st) == KEYS
    tr = r_subset(res.trades)
    h = st["headline"]
    assert h["n"] == len(tr) == st["summary"]["n_trades_r"] and h["mean_net_r"] == pytest.approx(tr["net_r"].mean())
    lo, hi = percentile_ci(trade_bootstrap(tr["net_r"].to_numpy(), 300, rng_for(STATS.master_seed, "v1",
                                                                                 "trade_bootstrap")))
    assert (h["mean_net_r_ci"]["lo"], h["mean_net_r_ci"]["hi"]) == (lo, hi)
    prim = [r for r in st["repricing"] if r["is_primary"]]
    assert len(st["repricing"]) == 8 and len(prim) == 1
    assert (prim[0]["ci_lo"], prim[0]["ci_hi"]) == (lo, hi) and prim[0]["p_a"] == st["baselines"]["p_a"]
    costs = (cfg.exec.fee_maker, cfg.exec.fee_taker, cfg.exec.slippage)
    assert st["baselines"]["p_b"] == p_value(tr["net_r"].mean(), run_means(base.runs_b, *costs))[0]
    assert st["baselines"]["n_runs"] == 49 and 0 < st["baselines"]["p_a"] <= 1
    assert st["buy_and_hold"]["n_days"] == len(res.daily)
    assert st["dsr"]["T"] == len(res.daily) and st["dsr"]["local"] is None
    assert st["insample_ref"]["vol_median"] is None  # 15 days of data: no 30-day vol yet
    assert st["insample_ref"]["sigma_bh"] == pytest.approx(st["buy_and_hold"]["sigma_bh"])
    assert sum(r["n"] for r in st["breakdowns"]["dow"]) == len(tr)
    assert h["std_net_r"] == pytest.approx(tr["net_r"].std(ddof=1))  # Phase 7 holdout power
    assert st["n_sessions"] == len(market.calendar.eligible_days(market.period_start_ms, market.period_end_ms)) == 15
    assert sum(r["n"] for r in st["regimes"]["trend"]) == len(tr)


def test_stats_json_round_trips_and_is_byte_identical(full, tmp_path):
    res, market, cfg, base, st = full
    write_stats(st, tmp_path / "a.json")
    assert read_stats(tmp_path / "a.json") == st
    again = compute_stats(res, market, cfg, variant_id="v1", baselines=run_baselines(res, market, cfg, variant_id="v1"))
    write_stats(again, tmp_path / "b.json")
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_without_baselines_the_p_values_are_null(full):
    res, market, cfg, _, _ = full
    st = compute_stats(res, market, cfg, variant_id="v1")
    assert st["baselines"]["p_a"] is None and st["baselines"]["n_runs"] is None
    assert all(r["p_a"] is None and r["p_b"] is None for r in st["repricing"])


def test_insample_reference_is_used_for_a_holdout():
    res, market = variant(n15=45 * 96)
    cfg = cfg_for()
    own = compute_stats(res, market, cfg, variant_id="v1")
    assert own["insample_ref"]["vol_median"] > 0
    assert {r["key"] for r in own["regimes"]["vol"]} == {"high", "low", None}  # None: the first 30 days
    st = compute_stats(res, market, cfg, variant_id="v1",
                       insample_ref={"vol_median": 0.01, "sigma_strategy": 0.002, "sigma_bh": 0.004})
    assert st["insample_ref"] == {"vol_median": 0.01, "sigma_strategy": 0.002, "sigma_bh": 0.004}
    assert st["buy_and_hold"]["scale"] == 0.5
    from perpbt.stats.regimes import daily_vol_asof
    v = daily_vol_asof(market.candles15)[r_subset(res.trades)["entry_idx"].to_numpy()]
    want = {"high": int((v > 0.01).sum()), "low": int((v <= 0.01).sum()), None: int(np.isnan(v).sum())}
    assert {r["key"]: r["n"] for r in st["regimes"]["vol"]} == {k: n for k, n in want.items() if n}


def test_stats_json_with_no_trades(tmp_path):
    res, market = variant(strategy=CoinFlipStrategy(StrategyParams(), 0, p=0.0))
    assert len(res.trades) == 0
    cfg = cfg_for()
    st = compute_stats(res, market, cfg, variant_id="empty",
                       baselines=run_baselines(res, market, cfg, variant_id="empty"))
    write_stats(st, tmp_path / "s.json")
    assert read_stats(tmp_path / "s.json") == st
    assert st["headline"]["n"] == 0 and st["headline"]["mean_net_r"] is None
    assert st["baselines"]["p_a"] is None and st["headline"]["sharpe_ann"] is None


def test_sanitize():
    out = sanitize({"a": np.float64("nan"), "b": np.int8(3), "c": (1.5, np.inf), "d": np.bool_(True)})
    assert out == {"a": None, "b": 3, "c": [1.5, None], "d": True}
    with pytest.raises(TypeError):
        sanitize({"x": object()})
