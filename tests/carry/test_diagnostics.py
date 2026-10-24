"""The carry diagnostics (carry spec Phase 4 §4.4); synthetic frames and hand-built tables only."""
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from perpbt.carry.diagnostics import (
    annual_stats, decomposition, draw_offsets, dsr_block, filter_value, live_window, margin_events, path_returns,
    random_timing, rank_summary, reprice, reprice_specs, row_names, shifted_states,
)
from perpbt.carry.executor import (
    COUNTS, DAILY_COLUMNS, EVENT_COLUMNS, FILL_COLUMNS, PAIR_COLUMNS, PathResult, run_batch, run_path,
)
from perpbt.carry.prereg import RepriceConfig, load_prereg
from perpbt.carry.screen import v2_stats
from perpbt.config import from_dict
from perpbt.stats.bootstrap import paired_sharpe_diff, sharpe
from perpbt.stats.dsr import dsr_from_trials, sr_moments
from perpbt.stats.seeds import rng_for
from tests.carry.execframes import pairframe, spec, states
from tests.carry.test_executor import E0, messy_frames, random_states

MESSY_END = "2020-02-09"


def one(pf):
    return {pf.pair: pf}


# --- per-pair columns -----------------------------------------------------------------------------------------

def test_per_pair_columns_sum_to_the_daily_columns():
    frames = messy_frames()
    rng = np.random.default_rng(21)
    for _ in range(10):
        r = run_path(frames, random_states(frames, rng), spec(end=MESSY_END))
        by_day = r.daily_pair.groupby("date", sort=True)[["funding", "fees", "slippage", "basis_pnl"]].sum()
        d = r.daily.set_index("date")
        for c in ("funding", "fees", "slippage", "basis_pnl"):
            np.testing.assert_allclose(by_day[c].to_numpy(), d.loc[by_day.index, c].to_numpy(), rtol=0,
                                       atol=1e-12 * E0, err_msg=c)
        assert r.daily_pair["fees"].sum() == pytest.approx(r.fills["fee"].sum(), rel=1e-12)


# --- annual stats ---------------------------------------------------------------------------------------------

def hand_daily(ret, start="2020-01-30", equity0=100.0):
    dates = pd.date_range(start, periods=len(ret), freq="D").strftime("%Y-%m-%d")
    equity = equity0 * np.cumprod(1.0 + np.asarray(ret))
    return pd.DataFrame({"date": dates, "ret": ret, "equity": equity})


def test_annual_stats_hand_case():
    ret = [0.01, -0.02, 0.03, -0.05, 0.01]
    d = hand_daily(ret)
    v1 = {"sharpe": 1.5, "sharpe_ci": [0.1, 2.9], "p": 0.01, "passes": True}
    a = annual_stats(d, equity0=100.0, v1=v1)
    assert a["ann_return"] == pytest.approx(365 * np.mean(ret), rel=1e-15)
    assert a["ann_vol"] == pytest.approx(math.sqrt(365) * np.std(ret, ddof=1), rel=1e-15)
    assert a["cagr"] == pytest.approx((d["equity"].iloc[-1] / 100.0) ** (365 / 5) - 1, rel=1e-12)
    assert a["sharpe"] == 1.5 and a["sharpe_ci"] == [0.1, 2.9]  # V1's, not drawn again
    assert a["max_drawdown"] == pytest.approx(0.05, rel=1e-12)  # from the 2020-02-01 peak
    assert a["worst_day"] == {"date": "2020-02-02", "ret": -0.05}
    assert a["worst_month"]["month"] == "2020-02"
    assert a["worst_month"]["ret"] == pytest.approx(1.03 * 0.95 * 1.01 - 1, rel=1e-12)
    assert a["n_days"] == 5


# --- decomposition --------------------------------------------------------------------------------------------

def test_decomposition_sums_to_the_returns():
    frames = messy_frames()
    rng = np.random.default_rng(3)
    for _ in range(10):
        r = run_path(frames, random_states(frames, rng), spec(end=MESSY_END))
        dec = decomposition(r, equity0=E0)
        tot = dec["total"]
        s = r.daily["ret"].sum()
        assert tot["ret_sum"] == pytest.approx(s, rel=1e-12, abs=1e-15)
        assert tot["funding"] - tot["fees"] - tot["slippage"] + tot["basis"] == pytest.approx(s, abs=1e-10)
        for key in ("funding", "basis", "fees", "slippage", "rebalance_cost", "liquidation_cost", "ret_sum"):
            assert sum(y[key] for y in dec["per_year"]) == pytest.approx(tot[key], rel=1e-12, abs=1e-15)
        for key in ("funding", "basis", "fees", "slippage", "rebalance_cost", "liquidation_cost"):
            assert sum(p[key] for p in dec["per_pair"]) == pytest.approx(tot[key], rel=1e-12, abs=1e-15)
        assert tot["entries"] == r.counts["entries"] == sum(p["entries"] for p in dec["per_pair"])
        assert tot["pair_days_on"] == int((r.daily_pair["perp_qty"] < 0).sum())
        assert [p["pair"] for p in dec["per_pair"]] == ["AUSDT", "BUSDT", "CUSDT"]
        assert dec["per_pair"][1]["days_live"] == int((r.daily_pair["pair"] == "BUSDT").sum())


def hand_result(equity0=100.0):
    """Four days over a new year: two pairs; a rebalance in 2020, a liquidation in 2021 and its spot sale."""
    dates = ["2020-12-30", "2020-12-31", "2021-01-01", "2021-01-02"]
    daily = pd.DataFrame({
        "date": dates, "day_ms": np.arange(4, dtype=np.int64), "equity": [101.0, 99.0, 99.0, 108.9],
        "ret": [0.01, -2 / 101, 0.0, 0.1], "cash": [0.0] * 4, "funding": [2.0, 1.0, 0.0, 0.0],
        "fees": [0.5, 2.0, 1.0, 0.0], "slippage": [0.5, 0.0, 0.0, 0.0], "basis_pnl": [0.0, -1.0, 1.0, 9.9],
        "n_on": np.array([2, 1, 0, 0]), "n_live": np.array([2, 2, 2, 2]),
    })
    rows = []
    for k, date in enumerate(dates):
        for pair in ("AUSDT", "BUSDT"):
            a = pair == "AUSDT"
            rows.append({"date": date, "pair": pair, "state": True, "spot_qty": 1.0,
                         "perp_qty": -1.0 if (a and k < 1) or (not a and k < 2) else 0.0, "perp_wallet": 1.0,
                         "sleeve_value": 1.0, "funding": daily["funding"][k] / 2, "fees": daily["fees"][k] / 2,
                         "slippage": daily["slippage"][k] / 2, "basis_pnl": daily["basis_pnl"][k] / 2})
    daily_pair = pd.DataFrame(rows)[list(PAIR_COLUMNS)]
    fills = pd.DataFrame([
        ("2020-12-30", "AUSDT", "perp", -1.0, 10.0, "enter", 0.25, 0.25),
        ("2020-12-30", "BUSDT", "perp", -1.0, 10.0, "enter", 0.25, 0.25),
        ("2020-12-31", "BUSDT", "spot", -0.1, 10.0, "rebalance", 1.0, 0.0),
        ("2020-12-31", "BUSDT", "perp", 0.1, 10.0, "rebalance", 1.0, 0.0),
        ("2021-01-01", "BUSDT", "perp", 0.9, 12.0, "liquidation", 1.0, 0.0),
        ("2021-01-02", "BUSDT", "spot", -0.9, 12.0, "liquidation", 0.0, 0.0),
    ], columns=list(FILL_COLUMNS))
    events = pd.DataFrame([
        ("2020-12-31", "BUSDT", "rebalance", "", "", 0.1),
        ("2021-01-01", "BUSDT", "liquidation", "perp", "", 12.0),
    ], columns=list(EVENT_COLUMNS))
    counts = dict.fromkeys(COUNTS, 0)
    counts.update(entries=2, rebalances=1, liquidations=1)
    return PathResult(daily[list(DAILY_COLUMNS)], daily_pair, fills, events, counts)


def test_decomposition_per_year_hand_case():
    r = hand_result()
    dec = decomposition(r, equity0=100.0)
    y20, y21 = dec["per_year"]
    assert (y20["year"], y21["year"]) == ("2020", "2021") and (y20["n_days"], y21["n_days"]) == (2, 2)
    prev = [100.0, 101.0, 99.0, 99.0]
    assert y20["funding"] == pytest.approx(2.0 / 100 + 1.0 / 101)
    assert y20["fees"] == pytest.approx(0.5 / 100 + 2.0 / 101)
    assert y21["basis"] == pytest.approx(1.0 / 99 + 9.9 / 99)
    assert y20["rebalance_cost"] == pytest.approx(2.0 / 101) and y21["rebalance_cost"] == 0.0
    assert y21["liquidation_cost"] == pytest.approx(1.0 / prev[2]) and y20["liquidation_cost"] == 0.0
    assert y20["ret_compounded"] == pytest.approx(0.99 - 1.0) and y21["ret_compounded"] == pytest.approx(0.1)
    assert (y20["pair_days_on"], y21["pair_days_on"]) == (3, 0) and y20["share_on"] == pytest.approx(0.75)
    assert (y20["entries"], y21["entries"]) == (2, 0)
    b = dec["per_pair"][1]
    assert b["pair"] == "BUSDT" and b["rebalance_cost"] == pytest.approx(2.0 / 101)
    assert b["days_on"] == 2 and b["days_live"] == 4


def test_margin_events_hand_case():
    m = margin_events(hand_result(), equity0=100.0)
    reb, liq = m["rebalances"], m["liquidations"]
    assert reb["count"] == 1 and liq["count"] == 1
    assert reb["events"] == [{"date": "2020-12-31", "pair": "BUSDT", "qty": 0.1, "cost": 2.0,
                              "cost_frac": pytest.approx(2.0 / 101)}]
    assert liq["events"][0]["date"] == "2021-01-01" and liq["events"][0]["price"] == 12.0
    assert liq["events"][0]["cost"] == pytest.approx(1.0)  # the buy-back's fee and the spot sale's (0)
    assert reb["cost"] == pytest.approx(2.0) and liq["cost_frac"] == pytest.approx(1.0 / 99)


def test_margin_events_match_the_fills_on_messy_paths():
    frames = messy_frames()
    rng = np.random.default_rng(8)
    seen = {"rebalances": 0, "liquidations": 0}
    for _ in range(20):
        r = run_path(frames, random_states(frames, rng), spec(end=MESSY_END))
        m = margin_events(r, equity0=E0)
        f = r.fills
        for kind, reason in (("rebalances", "rebalance"), ("liquidations", "liquidation")):
            assert m[kind]["count"] == r.counts[kind] == len(m[kind]["events"])
            sel = f[f["reason"] == reason]
            assert m[kind]["cost"] == pytest.approx((sel["fee"] + sel["slippage"]).sum(), rel=1e-12, abs=1e-12)
            assert sum(e["cost"] for e in m[kind]["events"]) == pytest.approx(m[kind]["cost"], rel=1e-12, abs=1e-12)
            seen[kind] += m[kind]["count"]
    assert all(v > 0 for v in seen.values()), seen


def test_liquidation_cost_includes_the_later_spot_sale():
    pf = pairframe("AUSDT", spot=[100] * 5, perp=[100] * 5, perp_high=[None, None, 160, None, None])
    r = run_path(one(pf), states(one(pf), {"AUSDT": [0, 1, 2, 3]}), spec(rebalance_below=0.01))
    m = margin_events(r, equity0=E0)
    sel = r.fills[r.fills["reason"] == "liquidation"]
    assert sel["date"].tolist() == ["2020-01-03", "2020-01-04"]
    (event,) = m["liquidations"]["events"]
    assert event["date"] == "2020-01-03"
    assert event["cost"] == pytest.approx((sel["fee"] + sel["slippage"]).sum(), rel=1e-13)


def test_always_off_has_nothing():
    frames = messy_frames()
    r = run_path(frames, states(frames, {}), spec(end=MESSY_END))
    dec = decomposition(r, equity0=E0)
    assert dec["total"]["funding"] == 0 and dec["total"]["entries"] == 0 and dec["total"]["pair_days_on"] == 0
    m = margin_events(r, equity0=E0)
    assert m["rebalances"] == {"count": 0, "cost": 0.0, "cost_frac": 0.0, "events": []}
    a = annual_stats(r.daily, equity0=E0, v1={"sharpe": None, "sharpe_ci": [None, None]})
    assert a["ann_return"] == 0 and a["max_drawdown"] == 0 and a["sharpe"] is None
    assert math.isnan(sharpe(r.daily["ret"].to_numpy()))


# --- random timing --------------------------------------------------------------------------------------------

def test_draw_offsets_range_and_seed():
    k = draw_offsets(200, 60, 5000, rng_for(1, "h", "p"))
    assert k.dtype == np.int64 and k.min() == 60 and k.max() == 140  # both ends reachable
    np.testing.assert_array_equal(k, draw_offsets(200, 60, 5000, rng_for(1, "h", "p")))
    assert draw_offsets(120, 60, 3, rng_for(1, "h", "p")).tolist() == [60, 60, 60]
    with pytest.raises(ValueError):
        draw_offsets(119, 60, 3, rng_for(1, "h", "p"))
    with pytest.raises(ValueError):
        draw_offsets(200, 60, 0, rng_for(1, "h", "p"))


def test_live_window():
    frames = messy_frames()
    cfg = spec(end=MESSY_END)  # return days 2020-01-02 .. 2020-02-09: decisions 2020-01-01 .. 2020-02-08
    assert live_window(frames["AUSDT"], cfg) == (0, 39)
    assert live_window(frames["BUSDT"], cfg) == (0, 31)  # 32 days: the last has no next day
    assert live_window(frames["CUSDT"], cfg) == (0, 36)  # first day 2020-01-04


def test_shifted_states_hand_case():
    frames = messy_frames()
    cfg = spec(end=MESSY_END)
    rng = np.random.default_rng(1)
    st = {pair: rng.random(len(pf)) < 0.4 for pair, pf in frames.items()}
    offsets = np.array([3, 17, 35])
    out = shifted_states(frames, st, cfg, offsets)
    for pair, pf in frames.items():
        j0, n_i = live_window(pf, cfg)
        assert out[pair].shape == (3, len(pf)) and out[pair].dtype == bool
        for p, k in enumerate(offsets):
            want = [st[pair][j0 + (t + k) % n_i] for t in range(n_i)]  # one offset shared, mod each pair's n_i
            assert out[pair][p, j0:j0 + n_i].tolist() == want
            assert not out[pair][p, j0 + n_i:].any()
            assert out[pair][p, j0:j0 + n_i].sum() == st[pair][j0:j0 + n_i].sum()  # time on kept
    zero = shifted_states(frames, st, cfg, np.array([0]))
    for pair, pf in frames.items():
        j0, n_i = live_window(pf, cfg)
        assert zero[pair][0, j0:j0 + n_i].tolist() == st[pair][j0:j0 + n_i].tolist()


def test_path_returns_equal_run_path():
    frames = messy_frames()
    cfg = spec(end=MESSY_END)
    st = random_states(frames, np.random.default_rng(2))
    offsets = np.array([0, 5, 9, 20])
    shifted = shifted_states(frames, st, cfg, offsets)
    batch = run_batch(frames, shifted, cfg)
    ret = path_returns(batch.equity, cfg.equity0)
    for p in range(len(offsets)):
        path = run_path(frames, {pair: shifted[pair][p] for pair in frames}, cfg)
        np.testing.assert_allclose(ret[p], path.daily["ret"].to_numpy(), rtol=0, atol=1e-12)
    # offset 0 is the original path (inside the live windows, the only rows the executor reads)
    np.testing.assert_allclose(ret[0], run_path(frames, st, cfg).daily["ret"].to_numpy(), rtol=0, atol=1e-12)


def test_rank_summary():
    s = rank_summary(np.array([1.0, 2.0, 3.0, 4.0, np.nan]), 3.0)
    assert s["n_finite"] == 4 and s["rank"] == 2 and s["share_at_least"] == pytest.approx(3 / 5)
    assert s["mean"] == 2.5 and s["std"] == pytest.approx(np.std([1, 2, 3, 4], ddof=1))
    assert s["p50"] == 2.5 and s["primary"] == 3.0
    best = rank_summary(np.array([1.0, 2.0]), 5.0)
    assert best["rank"] == 1 and best["share_at_least"] == pytest.approx(1 / 3)
    none = rank_summary(np.array([1.0, 2.0]), None)
    assert none["rank"] is None and none["share_at_least"] is None


def test_random_timing_on_messy_frames():
    frames = messy_frames()
    cfg = spec(end=MESSY_END)
    st = random_states(frames, np.random.default_rng(4))
    primary = run_path(frames, st, cfg).daily["ret"].to_numpy()
    summary, table = random_timing(frames, st, cfg, runs=40, margin=5, rng=rng_for(3, "h", "insample/random_timing"),
                                   primary_ret=primary)
    offsets = draw_offsets(39, 5, 40, rng_for(3, "h", "insample/random_timing"))
    assert table["offset"].tolist() == offsets.tolist() and table["path"].tolist() == list(range(40))
    assert list(table.columns) == ["path", "offset", "ann_return", "sharpe", "entries", "rebalances", "liquidations"]
    shifted = shifted_states(frames, st, cfg, offsets[:2])
    for p in range(2):
        path = run_path(frames, {pair: shifted[pair][p] for pair in frames}, cfg)
        assert table["ann_return"][p] == pytest.approx(365 * path.daily["ret"].mean(), rel=1e-12, abs=1e-15)
        assert table["entries"][p] == path.counts["entries"]
    assert summary["runs"] == 40 and summary["n_days"] == 39 and summary["shift_margin_days"] == 5
    assert summary["ann_return"]["primary"] == pytest.approx(365 * primary.mean(), rel=1e-15)
    assert summary["ann_return"] == rank_summary(table["ann_return"].to_numpy(), 365 * float(primary.mean()))
    again, table2 = random_timing(frames, st, cfg, runs=40, margin=5, rng=rng_for(3, "h", "insample/random_timing"),
                                  primary_ret=primary)
    assert again == summary and table2.to_csv() == table.to_csv()
    with pytest.raises(ValueError):
        random_timing(frames, st, cfg, runs=4, margin=5, rng=rng_for(3, "h", "x"), primary_ret=primary[:-1])


# --- re-pricing -----------------------------------------------------------------------------------------------

GRID = from_dict(RepriceConfig, {"slippage": [0.0, 0.0002, 0.0005, 0.001], "spot_taker": [0.00075, 0.001]})


def test_reprice_specs_change_only_the_costs():
    cfg = spec(end=MESSY_END, mmr=0.02)
    cells = reprice_specs(cfg, GRID)
    assert [(s, t) for s, t, _ in cells] == [(s, t) for s in GRID.slippage for t in GRID.spot_taker]
    for s, t, c in cells:
        assert c.costs.spot_taker == t and c.costs.slippage == {"default": s}
        assert c.costs.perp_taker == cfg.costs.perp_taker
        assert (c.sleeve, c.margin, c.rebalance, c.start, c.end, c.equity0) == (
            cfg.sleeve, cfg.margin, cfg.rebalance, cfg.start, cfg.end, cfg.equity0)
    assert cfg.costs.slippage == {"default": 0.0005}  # the base is untouched


def test_reprice_cells():
    frames = messy_frames()
    cfg = spec(end=MESSY_END)  # slippage 0.0005 every pair, spot taker 0.001: one cell of the grid
    st = random_states(frames, np.random.default_rng(6))
    cells = reprice(frames, st, cfg, GRID, hurdle=0.05, alpha=0.05, B=500, block_len=10,
                    rng_v2=lambda: rng_for(1, "h", "insample/primary/reprice/v2"))
    assert len(cells) == 8
    base = run_path(frames, st, cfg).daily["ret"].to_numpy()
    (same,) = [c for c in cells if (c["slippage"], c["spot_taker"]) == (0.0005, 0.001)]
    assert same["ann_return"] == 365 * float(base.mean())
    assert same["v2"] == v2_stats(base, hurdle=0.05, alpha=0.05, B=500, block_len=10,
                                  rng=rng_for(1, "h", "insample/primary/reprice/v2"))
    for t in GRID.spot_taker:
        ann = [c["ann_return"] for c in cells if c["spot_taker"] == t]
        assert ann == sorted(ann, reverse=True) and ann[0] > ann[-1]  # more slippage, less return
    assert cells[0]["slippage_cost"] == 0.0 and cells[-1]["fees"] > 0


# --- filter value, DSR ----------------------------------------------------------------------------------------

def test_filter_value_is_the_paired_sharpe_difference():
    frames = messy_frames()
    cfg = spec(end=MESSY_END)
    a = run_path(frames, random_states(frames, np.random.default_rng(7)), cfg).daily
    b = run_path(frames, {p: np.ones(len(pf), dtype=bool) for p, pf in frames.items()}, cfg).daily
    out = filter_value(a, b, B=300, block_len=10, rng=rng_for(1, "h", "insample/filter_value"))
    ref = paired_sharpe_diff(a["ret"].to_numpy(), b["ret"].to_numpy(), 300, rng_for(1, "h", "insample/filter_value"),
                             10)
    assert out == {"rows": ["primary", "always_on"], "sharpe_primary": ref["sharpe_a"],
                   "sharpe_always_on": ref["sharpe_b"], "diff": ref["diff"], "diff_ci": [ref["ci_lo"], ref["ci_hi"]],
                   "p": ref["p"]}
    with pytest.raises(ValueError):
        filter_value(a, b.iloc[1:], B=10, block_len=10, rng=rng_for(1, "h", "x"))


ROOT = Path(__file__).resolve().parents[2]


def test_row_names_are_the_seven_rows():
    prereg = load_prereg(ROOT / "configs" / "carry" / "prereg.yaml")
    assert row_names(prereg) == ("primary", "always_on", "entry_5", "entry_20", "lookback_3d", "lookback_14d", "lev_1")


def test_dsr_block():
    rows = ("primary", "always_on", "entry_5", "entry_20", "lookback_3d", "lookback_14d", "lev_1")
    rng = np.random.default_rng(12)
    rets = {row: rng.normal(2e-4 * k, 1e-3, 300) for k, row in enumerate(rows)}
    out = dsr_block(rets, rows)
    m = sr_moments(rets["primary"])
    ref = dsr_from_trials(m, [sr_moments(rets[r])["sr"] for r in rows])
    assert out["N"] == 7 and out["dsr"] == ref["dsr"] and out["sr_star"] == ref["sr_star"] and out["V"] == ref["V"]
    assert out["sr"] == m["sr"] and list(out["trial_sr"]) == list(rows) and out["rows"] == list(rows)
    with pytest.raises(ValueError):
        dsr_block({r: v for r, v in rets.items() if r != "lev_1"}, rows)
    with pytest.raises(ValueError):
        dsr_block(rets, rows[1:])
