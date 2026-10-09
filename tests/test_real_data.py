"""Real-data checks (slow). Run after `perpbt data fetch` and `perpbt data validate`."""
import json
from pathlib import Path

import pytest

from perpbt.config import StrategyParams
from perpbt.data.sessions import SessionCalendar
from perpbt.data.store import CandleStore, FundingStore, date_ms
from perpbt.data.validate import consistency_1m_15m
from tests.strategy_harness import LONDON, NY, UTC, assert_counts_consistent, run_strategy

pytestmark = pytest.mark.slow

PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


@pytest.mark.parametrize("pair", PAIRS)
def test_one_real_month_1m_agrees_with_15m(real_cfg, pair):
    store = CandleStore(real_cfg)
    start, end = date_ms("2024-05-01"), date_ms("2024-06-01")
    c1m = store.load(pair, "1m", start, end)
    c15 = store.load(pair, "15m", start, end)
    assert len(c15) > 0.95 * 31 * 96 and len(c1m) > 0.95 * 31 * 1440
    report = consistency_1m_15m(c1m, c15)
    assert report["candles_compared"] > 0.95 * len(c15)
    assert report["mismatching_candles"] == 0, report


@pytest.mark.parametrize("pair", PAIRS)
def test_manifests_are_complete(real_cfg, pair):
    for tf in ("1m", "15m"):
        m = json.loads((Path(real_cfg.data_dir) / "candles" / pair / tf / "manifest.json").read_text(encoding="utf-8"))
        assert m["rows"] > 0 and m["download_date"] and m["files"]
        assert m["last_open_ms"] >= date_ms("2026-01-01")
        if tf == "15m":
            assert m["consistency_1m_15m"] is not None
    fm = json.loads((Path(real_cfg.data_dir) / "funding" / pair / "manifest.json").read_text(encoding="utf-8"))
    assert fm["rows"] > 0 and fm["bad_intervals"] == []


def test_head_backfill_present_for_btc_and_eth_only(real_cfg):
    store = CandleStore(real_cfg)
    btc = store.load("BTCUSDT", "15m", date_ms("2019-11-01"), date_ms("2020-01-01"))
    assert btc.ts[0] == date_ms("2019-11-01") and len(btc) == 61 * 96
    eth = store.load("ETHUSDT", "15m", date_ms("2019-11-01"), date_ms("2020-01-01"))
    assert 0 < len(eth) <= 61 * 96 and eth.ts[0] >= date_ms("2019-11-27")
    assert len(store.load("SOLUSDT", "15m", date_ms("2019-11-01"), date_ms("2020-09-14"))) == 0


def test_funding_loads_in_sample_and_is_guarded(real_cfg):
    store = FundingStore(real_cfg)
    f = store.load("BTCUSDT", date_ms("2020-01-01"), date_ms("2026-01-01"))
    assert f.ts[0] == date_ms("2020-01-01") and len(f) > 6 * 365 * 3 * 0.95
    assert set(f.interval_h.tolist()) <= {1, 2, 4, 8}


@pytest.mark.parametrize("pair", PAIRS)
def test_order_block_rule_on_real_candles(real_cfg, pair):
    cd = CandleStore(real_cfg).load(pair, "15m", date_ms(real_cfg.warmup_start), date_ms("2026-01-01"))
    runs = {}
    for spec in (UTC, NY, LONDON):
        run = runs[spec.name] = run_strategy(cd, StrategyParams(), spec)
        assert_counts_consistent(run.counts)
        assert run.counts["intents"] > 300, (spec.name, run.counts)
        cal = SessionCalendar(spec, cd.ts)
        for i, x in run.intents:
            assert x.stop < x.price < cd.c[i] and x.expires_ms == cal.end_ms[i], (spec.name, i)
    i0 = cd.index_at(date_ms("2024-01-01"))  # a later first call rebuilds the same live levels
    late = run_strategy(cd, StrategyParams(), UTC, start_i=i0)
    assert late.intents == [(i, x) for i, x in runs["utc"].intents if i >= i0]


def test_simulator_smoke_btc_2024(real_cfg, capsys):
    """Spec test 4.7: primary config, BTCUSDT 2024, UTC session, 1m resolution on."""
    from perpbt.config import ExecConfig
    from perpbt.execution.simulator import run
    from perpbt.strategy.order_block import OrderBlockStrategy

    start, end = date_ms("2024-01-01"), date_ms("2025-01-01")
    cd = CandleStore(real_cfg).load("BTCUSDT", "15m", date_ms(real_cfg.warmup_start), end)
    m1 = CandleStore(real_cfg).load("BTCUSDT", "1m", start, end)
    fund = FundingStore(real_cfg).load("BTCUSDT", start, end)
    params = StrategyParams()
    res = run(cd, m1, fund, SessionCalendar(UTC, cd.ts), OrderBlockStrategy(params), ExecConfig(),
              start, end, variant_id="smoke")
    tr, s = res.trades, res.summary
    assert s["n_trades"] > 50 and tr["exit_ms"].notna().all() and (tr["exit_reason"] != "").all()
    plain = tr[tr["fill_resolution"] != "open_gap"]
    assert (plain["net_r"] >= -1 - plain["cost_r"] - 1e-9).all()
    assert (plain["net_r"] <= params.r_target + 1e-9).all()
    assert 10_000 + tr["net_pnl"].sum() == pytest.approx(s["final_equity"], rel=1e-12)
    assert len(res.daily) == 366  # 2024 is a leap year
    with capsys.disabled():
        print(f"\nsmoke BTCUSDT 2024 UTC: {json.dumps(s, indent=1)}\nskips {res.skips}")
        print(tr.groupby("exit_reason")["net_r"].agg(["count", "mean"]))


def test_stats_json_from_the_smoke_run(real_cfg, tmp_path, capsys):
    """Phase 5 exit criterion: stats.json from the Phase 4 smoke run is produced and read back without loss."""
    import time

    from perpbt.config import ExecConfig, StatsConfig, VariantConfig
    from perpbt.execution.simulator import run
    from perpbt.stats.variant import Market, compute_stats, read_stats, run_baselines, write_stats
    from perpbt.strategy.order_block import OrderBlockStrategy

    start, end = date_ms("2024-01-01"), date_ms("2025-01-01")
    cd = CandleStore(real_cfg).load("BTCUSDT", "15m", date_ms(real_cfg.warmup_start), end)
    m1 = CandleStore(real_cfg).load("BTCUSDT", "1m", start, end)
    fund = FundingStore(real_cfg).load("BTCUSDT", start, end)
    cfg = VariantConfig(pair="BTCUSDT", session=UTC, params=StrategyParams(), exec=ExecConfig(), stats=StatsConfig(),
                        period_start="2024-01-01", period_end="2024-12-31", is_holdout=False)
    cal = SessionCalendar(UTC, cd.ts)
    res = run(cd, m1, fund, cal, OrderBlockStrategy(cfg.params), cfg.exec, start, end, variant_id="smoke")
    market = Market(cd, m1, fund, cal, start, end, listing_ms=date_ms(real_cfg.listing["BTCUSDT"]))
    t0 = time.perf_counter()
    base = run_baselines(res, market, cfg, variant_id="smoke", n_runs=500)
    t1 = time.perf_counter()
    st = compute_stats(res, market, cfg, variant_id="smoke", baselines=base)
    t2 = time.perf_counter()
    write_stats(st, tmp_path / "stats.json")
    assert read_stats(tmp_path / "stats.json") == st
    assert st["headline"]["n"] == st["summary"]["n_trades_r"] > 50
    assert 0 < st["baselines"]["p_a"] <= 1 and 0 < st["baselines"]["p_b"] <= 1
    assert len(st["repricing"]) == 8 and st["buy_and_hold"]["n_days"] == 366
    with capsys.disabled():
        h, b, bh = st["headline"], st["baselines"], st["buy_and_hold"]
        print(f"\nstats BTCUSDT 2024 UTC: n {h['n']}, mean net R {h['mean_net_r']:.3f} "
              f"CI [{h['mean_net_r_ci']['lo']:.3f}, {h['mean_net_r_ci']['hi']:.3f}] "
              f"block [{h['mean_net_r_ci_block']['lo']:.3f}, {h['mean_net_r_ci_block']['hi']:.3f}], "
              f"Sharpe {h['sharpe_ann']:.2f}, max DD {h['max_dd']:.3f}")
        print(f"baselines (500 runs, {t1 - t0:.1f} s): p_A {b['p_a']:.3f} z_A {b['z_a']:.2f}, "
              f"p_B {b['p_b']:.3f} z_B {b['z_b']:.2f}, n_A {b['n_a']} excluded {b['n_excluded_a']}")
        print(f"B&H Sharpe {bh['sharpe']:.2f}, diff {bh['sharpe_diff']:.2f}, p_BH {bh['p_bh']:.3f}; "
              f"stats {t2 - t1:.1f} s; cost_r>1 share {st['costs']['share_cost_r_gt_1']:.3f}")
        for r in st["repricing"]:
            print(f"  slip {r['slippage']:.4f} maker {r['fee_maker']:.4f}: mean {r['mean_net_r']:.3f} "
                  f"p_A {r['p_a']:.3f} p_B {r['p_b']:.3f}")
