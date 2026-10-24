"""The two-leg carry executor's reference loop (carry spec Phase 3 §3.2-§3.4, §3.6); synthetic data only."""
import numpy as np
import pytest

from perpbt.carry.executor import (
    COUNTS, DAILY_COLUMNS, EVENT_COLUMNS, FILL_COLUMNS, PAIR_COLUMNS, START_EQUITY, ExecSpec, exec_spec, run_path,
)
from tests.carry.execframes import DAY_MS, H_MS, T0_MS, pairframe, spec, states, zero_cost_spec

X = 2.0 / 3.0  # L = 2
FS, FP, SL = 0.001, 0.0005, 0.0005  # spot taker, perp taker, slippage per leg (spec())
E0 = START_EQUITY


def entry_amount(spend, ps, pp, fs=FS, fp=FP, sl=SL):
    """Hand version: A + x·A·(fs + sl) + (x·A / ps)·pp·(fp + sl) = spend."""
    per_a = 1.0 + X * (fs + sl) + X / ps * pp * (fp + sl)
    return spend / per_a


def one(pf):
    return {pf.pair: pf}


# --- entry, exit, funding -----------------------------------------------------------------------------------

def test_entry_quantities_costs_and_mark():
    pf = pairframe("AUSDT", spot=[100, 100, 102, 102, 102], perp=[100, 101, 103, 103, 103],
                   spot_fill=[100, 99.8, 102, 102, 102], perp_fill=[100, 100.5, 103, 103, 103])
    r = run_path(one(pf), states(one(pf), {"AUSDT": [0, 1, 2, 3, 4]}), spec())
    a = entry_amount(E0, 99.8, 100.5)
    q = X * a / 99.8
    w = X * a / 2.0
    costs = X * a * (FS + SL) + q * 100.5 * (FP + SL)
    assert r.counts["entries"] == 1 and r.counts["capped_entries"] == 0
    f = r.fills
    assert f["reason"].tolist() == ["enter", "enter"] and f["leg"].tolist() == ["spot", "perp"]
    np.testing.assert_allclose(f["qty"], [q, -q], rtol=1e-14)
    np.testing.assert_allclose(f["price"], [99.8, 100.5])
    np.testing.assert_allclose(f["fee"], [X * a * FS, q * 100.5 * FP], rtol=1e-14)
    np.testing.assert_allclose(f["slippage"], [X * a * SL, q * 100.5 * SL], rtol=1e-14)
    d = r.daily
    assert d["cash"].iloc[0] == pytest.approx(E0 - a - costs, abs=1e-9) and d["cash"].iloc[0] >= 0
    day1 = E0 - a - costs + q * 100 + w + q * (100.5 - 101)
    assert d["equity"].iloc[0] == pytest.approx(day1, rel=1e-13)
    assert d["fees"].iloc[0] == pytest.approx(X * a * FS + q * 100.5 * FP, rel=1e-13)
    assert d["basis_pnl"].iloc[0] == pytest.approx(q * (100 - 99.8) - q * (101 - 100.5), rel=1e-12)
    day2 = day1 + q * (102 - 100) - q * (103 - 101)
    assert d["equity"].iloc[1] == pytest.approx(day2, rel=1e-13)
    assert d["n_on"].tolist() == [1, 1, 1, 1] and d["n_live"].tolist() == [1, 1, 1, 1]
    p = r.daily_pair
    assert p["perp_qty"].iloc[0] == pytest.approx(-q) and p["spot_qty"].iloc[0] == pytest.approx(q)
    assert p["perp_wallet"].iloc[0] == pytest.approx(w)


def test_exit_closes_both_legs_at_the_fill():
    pf = pairframe("AUSDT", spot=[100, 100, 104, 104, 104], perp=[100, 100, 105, 106, 106],
                   spot_fill=[100, 100, 100, 103, 104], perp_fill=[100, 100, 100, 104, 106])
    r = run_path(one(pf), states(one(pf), {"AUSDT": [0, 1]}), spec())  # off at row 2: exit on day 3
    a = entry_amount(E0, 100, 100)
    q = X * a / 100
    cash1 = E0 - a - (X * a * (FS + SL) + q * 100 * (FP + SL))
    w = X * a / 2
    got = q * 103 + (w + q * (100 - 104)) - q * 103 * (FS + SL) - q * 104 * (FP + SL)
    d = r.daily
    assert r.counts["exits"] == 1
    assert d["cash"].iloc[2] == pytest.approx(max(cash1, 0.0) + got, rel=1e-13)
    assert d["equity"].iloc[2] == d["cash"].iloc[2] and d["n_on"].tolist() == [1, 1, 0, 0]
    ex = r.fills[r.fills["reason"] == "exit"]
    np.testing.assert_allclose(ex["qty"], [-q, q], rtol=1e-14)
    np.testing.assert_allclose(ex["price"], [103, 104])
    assert d["ret"].iloc[3] == 0.0


def test_funding_before_and_after_the_fill():
    # rates per event (00:00, 08:00, 16:00): entry day 1 and exit day 3 split at the 00:15 fill
    rates = [0.0] * 3 + [0.001, 0.002, 0.003] + [0.0001, -0.0004, 0.0002] + [0.004, 0.005, 0.006] + [0.0] * 3
    pf = pairframe("AUSDT", spot=[100, 100, 110, 120, 120], perp=[100, 100, 110, 120, 120], rates=rates)
    r = run_path(one(pf), states(one(pf), {"AUSDT": [0, 1]}), zero_cost_spec())
    q = X * E0 / 100
    f = r.daily["funding"].tolist()
    # day 1: the 00:00 event precedes the entry (no position); 08:00 and 16:00 on q at the day's price 100
    assert f[0] == pytest.approx(0.002 * q * 100 + 0.003 * q * 100, rel=1e-13)
    # day 2: held all day; 00:00 at the previous close 100, then 110; a negative rate is paid
    assert f[1] == pytest.approx(0.0001 * q * 100 + (-0.0004) * q * 110 + 0.0002 * q * 110, rel=1e-13)
    assert f[1] < 0.0001 * q * 100 + 0.0002 * q * 110
    # day 3: the 00:00 event (at the previous close 110) precedes the exit; 08:00 and 16:00 come after it
    assert f[2] == pytest.approx(0.004 * q * 110, rel=1e-13)
    assert f[3] == 0.0
    p = r.daily_pair
    np.testing.assert_allclose(p["funding"], f)


def test_funding_goes_to_the_perp_wallet_then_to_cash_at_exit():
    rates = [0.0] * 3 + [0.0, 0.001, 0.0] + [0.0] * 3 + [0.002, 0.0, 0.0] + [0.0] * 3
    pf = pairframe("AUSDT", spot=[100] * 5, perp=[100] * 5, rates=rates)
    r = run_path(one(pf), states(one(pf), {"AUSDT": [0, 1]}), zero_cost_spec())
    q = X * E0 / 100
    w = X * E0 / 2
    assert r.daily_pair["perp_wallet"].iloc[0] == pytest.approx(w + 0.001 * q * 100)
    assert r.daily["cash"].iloc[2] == pytest.approx(E0 + 0.001 * q * 100 + 0.002 * q * 100, rel=1e-13)


def test_pre_listing_event_never_priced():
    # XLM shape: the perp's first candle is 08:00 on day 0 and a 00:00 event precedes it (funding_px NaN, no
    # earlier candle); the pair is on from its first row; the period starts on day 0, before the pair is live
    pf = pairframe("XLMUSDT", spot=[100] * 4, perp=[100] * 4, rates=[0.01] * 12, drop_perp=[(0, s) for s in range(32)])
    assert np.isnan(pf.perp.funding_px[0]) and np.isnan(pf.funding_px_prev[0])
    r = run_path(one(pf), states(one(pf), {"XLMUSDT": [0, 1, 2, 3]}), zero_cost_spec(start="2020-01-01",
                                                                                     end="2020-01-04"))
    d = r.daily
    assert d["n_live"].tolist() == [0, 1, 1, 1] and d["funding"].iloc[0] == 0.0 and d["equity"].iloc[0] == E0
    assert r.counts["entries"] == 1 and r.counts["funding_px_fallbacks"] == 0


# --- margin ---------------------------------------------------------------------------------------------------

def test_liquidation_on_a_60pct_high_none_at_30pct():
    for jump, liquidated in ((1.6, True), (1.3, False)):
        pf = pairframe("AUSDT", spot=[100, 100, 100, 100, 100], perp=[100, 100, 100, 100, 100],
                       perp_high=[None, None, 100 * jump, None, None])
        r = run_path(one(pf), states(one(pf), {"AUSDT": [0, 1, 2, 3]}), spec(rebalance_below=0.01))
        assert r.counts["liquidations"] == int(liquidated)
        if not liquidated:
            assert r.daily["n_on"].tolist() == [1, 1, 1, 1]
            continue
        a = entry_amount(E0, 100, 100)
        q = X * a / 100
        w = X * a / 2
        pl = (w + q * 100) / (q * 1.01)  # equity = mmr × q × P
        assert pl == pytest.approx(150 / 1.01 * 100 / 100, rel=1e-3)
        liq = r.fills[r.fills["reason"] == "liquidation"]
        assert liq["leg"].tolist() == ["perp", "spot"]  # the short on day 2, the spot at the next fill (day 3)
        assert liq["price"].iloc[0] == pytest.approx(pl, rel=1e-13)
        assert liq["fee"].iloc[0] == pytest.approx(0.01 * q * pl, rel=1e-13)
        assert liq["date"].tolist() == ["2020-01-03", "2020-01-04"]
        d = r.daily
        assert d["n_on"].tolist() == [1, 0, 0, 0]
        # out until a decision is off: still on at rows 2 and 3, so no re-entry
        assert r.counts["entries"] == 1
        # the day's equity: the wallet after the buy-back and the fee, the spot leg at the close
        cash1 = d["cash"].iloc[0]
        w_after = w + q * (100 - pl) - 0.01 * q * pl
        assert d["equity"].iloc[1] == pytest.approx(cash1 + q * 100 + w_after, rel=1e-12)
        assert (d["cash"] >= 0).all()


def test_reentry_after_liquidation_needs_an_off_decision():
    pf = pairframe("AUSDT", spot=[100] * 7, perp=[100] * 7, perp_high=[None, None, 160, None, None, None, None])
    r = run_path(one(pf), states(one(pf), {"AUSDT": [0, 1, 2, 4, 5]}), spec(end="2020-01-07"))
    # liquidated on day 2; on at row 2 → no entry on day 3; off at row 3; on at row 4 → entry on day 5
    assert r.counts["liquidations"] == 1 and r.counts["entries"] == 2
    assert r.fills[r.fills["reason"] == "enter"]["date"].unique().tolist() == ["2020-01-02", "2020-01-06"]


def test_rebalance_restores_the_wallet():
    pf = pairframe("AUSDT", spot=[100, 100, 125, 125, 125], perp=[100, 100, 125, 125, 125])
    r = run_path(one(pf), states(one(pf), {"AUSDT": [0, 1, 2, 3]}), zero_cost_spec())
    q = X * E0 / 100
    assert r.counts["rebalances"] == 1 and r.counts["liquidations"] == 0
    reb = r.fills[r.fills["reason"] == "rebalance"]
    assert reb["date"].tolist() == ["2020-01-04", "2020-01-04"]  # triggered at day 2's close, filled on day 3
    np.testing.assert_allclose(reb["qty"], [-0.2 * q, 0.2 * q], rtol=1e-12)  # δ = (q·125/2 − 25q) / 187.5
    p = r.daily_pair
    assert p["perp_wallet"].iloc[2] == pytest.approx(70 * q, rel=1e-12)
    w, qq = p["perp_wallet"].iloc[2], -p["perp_qty"].iloc[2]
    assert w + qq * (100 - 125) == pytest.approx(qq * 125 / 2, rel=1e-12)  # back at target
    np.testing.assert_allclose(r.daily["equity"], E0, rtol=1e-13)  # zero costs, equal legs: nothing lost


def test_rebalance_with_costs_pays_from_the_sleeve():
    pf = pairframe("AUSDT", spot=[100, 100, 125, 125, 125], perp=[100, 100, 125, 125, 125])
    r = run_path(one(pf), states(one(pf), {"AUSDT": [0, 1, 2, 3]}), spec())
    p = r.daily_pair
    w, qq = p["perp_wallet"].iloc[2], -p["perp_qty"].iloc[2]
    assert w + qq * (100 - 125) == pytest.approx(qq * 125 / 2, rel=1e-12)
    reb = r.fills[r.fills["reason"] == "rebalance"]
    d = r.daily
    assert d["cash"].iloc[2] == d["cash"].iloc[1]  # the free cash pays nothing
    assert d["fees"].iloc[2] == pytest.approx(reb["fee"].sum(), rel=1e-12)
    assert d["equity"].iloc[2] == pytest.approx(d["equity"].iloc[1] - reb["fee"].sum() - reb["slippage"].sum(),
                                                rel=1e-12)


def test_no_rebalance_when_the_wallet_recovered_by_the_fill():
    pf = pairframe("AUSDT", spot=[100, 100, 125, 100, 100], perp=[100, 100, 125, 100, 100],
                   spot_fill=[100, 100, 125, 100, 100], perp_fill=[100, 100, 125, 100, 100])
    r = run_path(one(pf), states(one(pf), {"AUSDT": [0, 1, 2, 3]}), zero_cost_spec())
    assert r.counts["rebalances"] == 0  # due at day 2's close; the day-3 fill at 100 leaves the wallet at target


# --- delisting, end of period ---------------------------------------------------------------------------------

def test_delisting_closes_at_the_last_candle_and_leaves_n_live():
    a = pairframe("AUSDT", spot=[100] * 5, perp=[100] * 5)
    b = pairframe("BUSDT", spot=[50, 50, 50, 52], perp=[50, 50, 50, 53])  # ends day 3, the period on day 4
    frames = {"AUSDT": a, "BUSDT": b}
    r = run_path(frames, states(frames, {"AUSDT": [0, 1, 2, 3], "BUSDT": [0, 1, 2, 3]}), spec())
    d = r.daily
    assert d["n_live"].tolist() == [2, 2, 2, 1]  # B leaves from the decision after its last day
    assert r.counts["delists"] == 1
    dl = r.fills[r.fills["reason"] == "delist"]
    assert dl["pair"].tolist() == ["BUSDT", "BUSDT"] and dl["date"].tolist() == ["2020-01-04"] * 2
    np.testing.assert_allclose(dl["price"], [52, 53])  # the last candle's close of each leg
    assert (r.events["kind"] == "delisting").sum() == 1
    assert d["n_on"].tolist() == [2, 2, 1, 1]
    q = dl["qty"].iloc[1]
    assert dl["fee"].tolist() == pytest.approx([q * 52 * FS, q * 53 * FP])


def test_period_end_is_not_a_delisting():
    # the in-sample end shape: the frame ends on the period's last day (also when that day is partial)
    for drop in ((), [(4, s) for s in range(40, 96)]):
        pf = pairframe("AUSDT", spot=[100] * 5, perp=[100] * 5, drop_spot=drop, drop_perp=drop)
        r = run_path(one(pf), states(one(pf), {"AUSDT": [0, 1, 2, 3, 4]}), spec())
        assert r.counts["delists"] == 0 and r.daily["n_on"].iloc[-1] == 1
        assert set(r.fills["reason"]) == {"enter"}


# --- late fills and postponement -----------------------------------------------------------------------------

def test_late_perp_fill_after_the_8h_event():
    # perp candles 00:00..07:45 of day 3 missing: the exit's perp leg fills at the 08:00 candle's close (08:15), so
    # the 00:00 and 08:00 events are on the old quantity; the 00:00 one is priced at the previous close (fallback)
    rates = [0.0] * 9 + [0.001, 0.002, 0.003] + [0.0] * 3
    pf = pairframe("AUSDT", spot=[100, 100, 100, 110, 110], perp=[100, 100, 100, 110, 110], rates=rates,
                   drop_perp=[(3, s) for s in range(32)])
    assert pf.fill_late_ms["perp"][15][3] == T0_MS + 3 * DAY_MS + 8 * H_MS + 15 * 60_000
    r = run_path(one(pf), states(one(pf), {"AUSDT": [0, 1]}), zero_cost_spec())
    q = X * E0 / 100
    assert r.daily["funding"].iloc[2] == pytest.approx(0.001 * q * 100 + 0.002 * q * 110, rel=1e-13)
    assert r.counts["funding_px_fallbacks"] == 1 and r.counts["late_fills"] == 1
    late = r.events[r.events["kind"] == "late_fill"]
    assert late["leg"].tolist() == ["perp"]
    ex = r.fills[r.fills["reason"] == "exit"]
    assert ex["price"].tolist() == [110, 110]  # spot on time at day 3's 00:15 (fill = close), perp at 08:15


def test_late_fill_entry_skips_the_8h_event():
    rates = [0.0] * 3 + [0.001, 0.002, 0.003] + [0.0] * 9
    pf = pairframe("AUSDT", spot=[100] * 5, perp=[100] * 5, rates=rates, drop_perp=[(1, s) for s in range(32)])
    r = run_path(one(pf), states(one(pf), {"AUSDT": [0, 1, 2, 3]}), zero_cost_spec())
    q = X * E0 / 100
    assert r.daily["funding"].iloc[0] == pytest.approx(0.003 * q * 100, rel=1e-13)  # only 16:00 after the fill
    assert r.counts["funding_px_fallbacks"] == 0  # the missing 00:00 candle's event has no quantity to price


def test_missing_fill_candle_postpones_both_legs():
    pf = pairframe("AUSDT", spot=[100] * 5, perp=[100] * 5, drop_spot=[(1, s) for s in range(96)])
    r = run_path(one(pf), states(one(pf), {"AUSDT": [0, 1, 2, 3]}), spec())
    assert r.counts["postponed"] == 1 and r.counts["entries"] == 1
    assert r.fills["date"].unique().tolist() == ["2020-01-03"]
    ev = r.events[r.events["kind"] == "postponed"]
    assert ev["detail"].tolist() == ["enter"] and ev["date"].tolist() == ["2020-01-02"]
    assert r.daily["equity"].iloc[0] == E0


def test_postponed_trade_is_redecided():
    # the day-1 entry cannot fill; the decision for day 2 is off, so nothing is traded
    pf = pairframe("AUSDT", spot=[100] * 5, perp=[100] * 5, drop_perp=[(1, s) for s in range(96)])
    r = run_path(one(pf), states(one(pf), {"AUSDT": [0]}), spec())
    assert r.counts["postponed"] == 1 and len(r.fills) == 0


# --- the account ------------------------------------------------------------------------------------------------

def test_capped_entries_share_free_cash_pro_rata():
    up = [100, 100, 150, 150, 150]
    a = pairframe("AUSDT", spot=up, perp=[100] * 5)  # A's sleeve grows: spot up, perp flat
    b = pairframe("BUSDT", spot=[10] * 5, perp=[10] * 5)
    c = pairframe("CUSDT", spot=[20] * 5, perp=[20] * 5)
    frames = {"AUSDT": a, "BUSDT": b, "CUSDT": c}
    r = run_path(frames, states(frames, {"AUSDT": [0, 1, 2, 3], "BUSDT": [2, 3], "CUSDT": [2, 3]}),
                 zero_cost_spec())
    d = r.daily
    cash2, e2 = d["cash"].iloc[1], d["equity"].iloc[1]
    assert cash2 == pytest.approx(E0 * 2 / 3) and e2 > E0
    lam = cash2 / (2 * e2 / 3)
    assert r.counts["capped_entries"] == 2
    np.testing.assert_allclose(r.events.loc[r.events["kind"] == "capped_entry", "value"], [lam, lam], rtol=1e-14)
    ent = r.fills[(r.fills["reason"] == "enter") & (r.fills["leg"] == "spot")]
    spent = (ent["qty"] * ent["price"] / X).to_numpy()
    np.testing.assert_allclose(spent[1:], [lam * e2 / 3] * 2, rtol=1e-12)
    assert d["cash"].iloc[2] == 0.0


def test_capped_to_zero_retries_the_next_day():
    a = pairframe("AUSDT", spot=[100] * 5, perp=[100] * 5)
    b = pairframe("BUSDT", spot=[100] * 5, perp=[100] * 5)
    frames = {"AUSDT": a, "BUSDT": b}
    # both on from row 0 (cash spent), B off then on again while A holds all the free cash's share
    r = run_path(frames, states(frames, {"AUSDT": [0, 1, 2, 3], "BUSDT": [0, 2, 3]}), zero_cost_spec())
    assert r.counts["entries"] == 3 and r.counts["exits"] == 1 and (r.daily["cash"] >= 0).all()


def test_always_off_trades_nothing():
    a = pairframe("AUSDT", spot=[100, 120, 90, 95, 101], perp=[100, 121, 88, 96, 100], rates=[0.001] * 15)
    r = run_path(one(a), states(one(a), {}), spec())
    assert len(r.fills) == 0 and len(r.events) == 0 and all(v == 0 for v in r.counts.values())
    assert (r.daily["ret"] == 0).all() and (r.daily["equity"] == E0).all() and (r.daily["funding"] == 0).all()


def test_delta_neutral_with_equal_legs_and_no_costs():
    rng = np.random.default_rng(7)
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 30)))
    pf = pairframe("AUSDT", spot=px, perp=px)
    r = run_path(one(pf), states(one(pf), {"AUSDT": list(range(30))}), zero_cost_spec(end="2020-01-30"))
    np.testing.assert_allclose(r.daily["equity"], E0, rtol=1e-12)
    np.testing.assert_allclose(r.daily_pair["sleeve_value"], E0, rtol=1e-12)


# --- invariants on a messy random case ------------------------------------------------------------------------

def messy_frames(seed: int = 3, days: int = 40, *, after: int | None = None):
    """Three pairs: noisy legs with big moves (rebalances, a liquidation), mixed funding signs, gaps that force
    late fills and postponements, a pair listed late and one delisted before the period end. ``after``: every
    price and rate of the days after that day index is changed (a different random stream), nothing before."""
    rng = np.random.default_rng(seed)
    bump_rng = np.random.default_rng(seed + 1000)

    def path(n, vol, start):
        return start * np.exp(np.cumsum(rng.normal(0, vol, n)))

    def bump(arr, per_day=1):
        arr = np.asarray(arr, dtype=np.float64).copy()
        if after is not None:
            k = (after + 1) * per_day
            arr[k:] = arr[k:] * (1 + bump_rng.normal(0, 0.3, len(arr) - k) ** 2) + (per_day == 3) * 0.01
        return arr

    out = {}
    s = path(days, 0.04, 100)
    perp = s * (1 + rng.normal(0, 0.003, days))
    out["AUSDT"] = pairframe("AUSDT", spot=bump(s), perp=bump(perp), rates=bump(rng.normal(1e-4, 3e-4, 3 * days), 3),
                             perp_high=[x * 1.7 if k == 20 else None for k, x in enumerate(bump(perp))],
                             drop_perp=[(5, k) for k in range(40)] + [(d, k) for d in (11, 17, 25) for k in range(96)],
                             drop_spot=[(8, k) for k in range(3)] + [(14, k) for k in range(96)])
    s = path(days - 8, 0.03, 20)
    out["BUSDT"] = pairframe("BUSDT", spot=bump(s), perp=bump(s * (1 + rng.normal(0, 0.004, days - 8))),
                             rates=bump(rng.normal(2e-4, 4e-4, 3 * (days - 8)), 3))  # ends 8 days early: delisted
    s = path(days, 0.05, 3)
    out["CUSDT"] = pairframe(  # perp listed on day 3 at 12:30
        "CUSDT", spot=bump(s), perp=bump(s * (1 + rng.normal(0, 0.002, days))),
        rates=bump(rng.normal(0, 5e-4, 3 * days), 3),
        drop_perp=[(k, j) for k in range(3) for j in range(96)] + [(3, j) for j in range(50)], first_day="2020-01-04")
    return out


def random_states(frames, rng, p_switch=0.15):
    out = {}
    for pair, pf in frames.items():
        flips = rng.random(len(pf)) < p_switch
        out[pair] = (np.cumsum(flips) % 2).astype(bool)
    return out


def test_identity_cash_and_counts_on_random_paths():
    frames = messy_frames()
    cfg = spec(end="2020-02-09")
    rng = np.random.default_rng(11)
    seen = dict.fromkeys(("rebalances", "liquidations", "postponed", "late_fills", "delists", "capped_entries"), 0)
    for _ in range(20):
        r = run_path(frames, random_states(frames, rng), cfg)
        d = r.daily
        prev = np.concatenate(([E0], d["equity"].to_numpy()[:-1]))
        lhs = d["equity"].to_numpy() - prev
        rhs = (d["funding"] - d["fees"] - d["slippage"] + d["basis_pnl"]).to_numpy()
        np.testing.assert_allclose(lhs, rhs, rtol=0, atol=1e-9 * E0)
        assert (d["cash"] >= 0).all()
        np.testing.assert_allclose(d["ret"], d["equity"] / prev - 1, rtol=0, atol=1e-15)
        assert r.counts["entries"] == (r.fills["reason"] == "enter").sum() / 2
        assert r.counts["late_fills"] == (r.events["kind"] == "late_fill").sum()
        assert d["fees"].sum() == pytest.approx(r.fills["fee"].sum(), rel=1e-12)
        assert d["slippage"].sum() == pytest.approx(r.fills["slippage"].sum(), rel=1e-12)
        assert d["funding"].sum() == pytest.approx(r.daily_pair["funding"].sum(), rel=1e-12, abs=1e-12)
        for k in seen:
            seen[k] += r.counts[k]
    assert all(v > 0 for v in seen.values()), seen  # the case exercises every rule


def test_byte_identical_rerun():
    frames = messy_frames()
    st = random_states(frames, np.random.default_rng(5))
    a, b = run_path(frames, st, spec(end="2020-02-09")), run_path(frames, st, spec(end="2020-02-09"))
    for name in ("daily", "daily_pair", "fills", "events"):
        assert getattr(a, name).to_csv().encode() == getattr(b, name).to_csv().encode()
    assert a.counts == b.counts


def test_columns():
    frames = messy_frames()
    r = run_path(frames, random_states(frames, np.random.default_rng(5)), spec(end="2020-02-09"))
    assert tuple(r.daily.columns) == DAILY_COLUMNS and tuple(r.daily_pair.columns) == PAIR_COLUMNS
    assert tuple(r.fills.columns) == FILL_COLUMNS and tuple(r.events.columns) == EVENT_COLUMNS
    assert set(r.counts) == set(COUNTS) and len(r.daily) == 39
    empty = run_path(frames, states(frames, {}), spec(end="2020-02-09"))
    assert tuple(empty.fills.columns) == FILL_COLUMNS and empty.fills["qty"].dtype == np.float64


# --- causality -------------------------------------------------------------------------------------------------

def test_causal_in_state_and_data():
    frames = messy_frames()
    cfg = spec(end="2020-02-09")
    st = random_states(frames, np.random.default_rng(9))
    base = run_path(frames, st, cfg)
    for cut in (6, 15, 22, 30):  # return-day index; its decision is the day before
        t_ms = int(base.daily["day_ms"].iloc[cut])
        st2 = {}
        for pair, pf in frames.items():  # every decision from day t on flipped
            arr = st[pair].copy()
            arr[pf.day_ms >= t_ms] = ~arr[pf.day_ms >= t_ms]
            st2[pair] = arr
        after = int((t_ms - frames["AUSDT"].day_ms[0]) // DAY_MS)  # every price and rate after day t changed
        for alt in (run_path(frames, st2, cfg), run_path(messy_frames(after=after), st, cfg)):
            for name, cols in (("daily", DAILY_COLUMNS), ("daily_pair", PAIR_COLUMNS)):
                a, b = getattr(base, name), getattr(alt, name)
                a, b = a[a["date"] <= base.daily["date"].iloc[cut]], b[b["date"] <= base.daily["date"].iloc[cut]]
                for col in cols:
                    assert a[col].tolist() == b[col].tolist(), (cut, name, col)
            assert alt.daily["equity"].iloc[cut + 2:].tolist() != base.daily["equity"].iloc[cut + 2:].tolist()


# --- ExecSpec ----------------------------------------------------------------------------------------------------

def test_exec_spec_from_the_prereg():
    from pathlib import Path

    from perpbt.carry.prereg import load_prereg
    pre = load_prereg(Path(__file__).resolve().parents[2] / "configs" / "carry" / "prereg.yaml")
    cfg = exec_spec(pre)
    assert (cfg.sleeve, cfg.margin, cfg.costs, cfg.rebalance) == (pre.sleeve, pre.margin, pre.costs, pre.rebalance)
    assert (cfg.start, cfg.end, cfg.equity0) == ("2020-07-01", "2023-12-31", 10_000.0)
    lev1 = exec_spec(pre, sleeve=pre.exploratory_row("lev_1").sleeve)
    assert lev1.sleeve.perp_leverage == 1.0
    with pytest.raises(ValueError):
        ExecSpec(pre.sleeve, pre.margin, pre.costs, pre.rebalance, "2020-07-02", "2020-07-01")


def test_input_refusals():
    pf = pairframe("AUSDT", spot=[100] * 5, perp=[100] * 5)
    good = states(one(pf), {})
    with pytest.raises(ValueError, match="differ"):
        run_path(one(pf), {}, spec())
    with pytest.raises(ValueError, match="bool"):
        run_path(one(pf), {"AUSDT": good["AUSDT"].astype(int)}, spec())
    with pytest.raises(ValueError, match="bool"):
        run_path(one(pf), {"AUSDT": good["AUSDT"][:-1]}, spec())
    with pytest.raises(ValueError, match="PairFrame"):
        run_path({"BUSDT": pf}, {"BUSDT": good["AUSDT"]}, spec())


# --- review fixes: rules the first test set did not pin ----------------------------------------------------------

def path_and_batch(frames, st, cfg):
    """run_path, checked against a one-path run_batch (counts exactly, money to 1e-12 of the starting equity)."""
    from perpbt.carry.executor import run_batch
    r = run_path(frames, st, cfg)
    b = run_batch(frames, {k: v[None] for k, v in st.items()}, cfg)
    assert {c: int(v[0]) for c, v in b.counts.items()} == r.counts
    for c in ("equity", "cash", "funding", "fees"):
        np.testing.assert_allclose(getattr(b, c)[0], r.daily[c], rtol=0, atol=1e-12 * E0)
    return r


def test_rebalance_trigger_is_at_the_close_not_the_high():
    # CD7: at the +30% high the wallet equity (20q) is below half the margin needed (32.5q), at the 115 close
    # (35q against 28.75q) it is not; the next fill at 115 would still rebalance (35q < 57.5q) had the high
    # triggered it
    pf = pairframe("AUSDT", spot=[100, 100, 115, 115, 115], perp=[100, 100, 115, 115, 115],
                   perp_high=[None, None, 130, None, None])
    r = path_and_batch(one(pf), states(one(pf), {"AUSDT": [0, 1, 2, 3]}), zero_cost_spec())
    assert r.counts["rebalances"] == 0 and r.counts["liquidations"] == 0


def test_fill_exactly_at_the_8h_event_comes_first():
    # perp candles 00:00..07:30 of day 3 missing: the exit's perp leg fills at the 07:45 candle's close, 08:00
    # exactly; the exchange closes that candle 1 ms earlier, so the 08:00 event is on the new (zero) quantity
    rates = [0.0] * 9 + [0.001, 0.002, 0.003] + [0.0] * 3
    pf = pairframe("AUSDT", spot=[100] * 5, perp=[100] * 5, rates=rates, drop_perp=[(3, s) for s in range(31)])
    assert pf.fill_late_ms["perp"][15][3] == pf.perp.funding_ts[10] == T0_MS + 3 * DAY_MS + 8 * H_MS
    r = path_and_batch(one(pf), states(one(pf), {"AUSDT": [0, 1]}), zero_cost_spec())
    q = X * E0 / 100
    assert r.daily["funding"].iloc[2] == pytest.approx(0.001 * q * 100, rel=1e-13)


def test_spot_sale_after_liquidation_needs_only_the_spot_leg():
    pf = pairframe("AUSDT", spot=[100] * 5, perp=[100] * 5, perp_high=[None, None, 160, None, None],
                   drop_perp=[(3, s) for s in range(96)])
    r = path_and_batch(one(pf), states(one(pf), {"AUSDT": [0, 1, 2, 3]}), spec())
    liq = r.fills[r.fills["reason"] == "liquidation"]
    assert liq["date"].tolist() == ["2020-01-03", "2020-01-04"] and r.counts["postponed"] == 0


def test_no_rebalance_when_the_perp_fills_beyond_the_liquidation_price():
    # due at day 2's close (125); day 3's perp fill 150 is above the liquidation price 150/1.01: no rebalance,
    # and day 3's high (>= the fill) liquidates
    pf = pairframe("AUSDT", spot=[100, 100, 125, 150, 150], perp=[100, 100, 125, 150, 150])
    r = path_and_batch(one(pf), states(one(pf), {"AUSDT": [0, 1, 2, 3]}), zero_cost_spec())
    assert r.counts["rebalances"] == 0 and r.counts["liquidations"] == 1
    assert r.events.loc[r.events["kind"] == "liquidation", "date"].tolist() == ["2020-01-04"]


def test_a_one_ulp_shortfall_is_not_capped():
    frames = {f"P{i}USDT": pairframe(f"P{i}USDT", spot=[100 + i] * 5, perp=[100 + i] * 5) for i in range(7)}
    cfg = spec(end="2020-01-05")
    r = path_and_batch(frames, states(frames, {p: [0, 1, 2, 3] for p in frames}), cfg)
    assert r.counts["entries"] == 7 and r.counts["capped_entries"] == 0 and (r.daily["cash"] >= 0).all()


def test_refuses_frames_that_all_end_before_the_period_end():
    pf = pairframe("AUSDT", spot=[100] * 5, perp=[100] * 5)
    with pytest.raises(ValueError, match="period end"):
        run_path(one(pf), states(one(pf), {}), spec(end="2020-01-06"))
