"""The carry diagnostics (carry spec Phase 4 §4.4); synthetic frames and hand-built tables only."""
import math

import numpy as np
import pandas as pd
import pytest

from perpbt.carry.diagnostics import annual_stats, decomposition, margin_events
from perpbt.carry.executor import COUNTS, DAILY_COLUMNS, EVENT_COLUMNS, FILL_COLUMNS, PAIR_COLUMNS, PathResult, run_path
from perpbt.stats.bootstrap import sharpe
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
