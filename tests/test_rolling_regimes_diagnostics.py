"""Spec test 5.9: rolling windows, per-year table, regime labels, diagnostics, shared-trade fraction."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from perpbt.data.store import DAY_MS
from perpbt.indicators.daily import daily_adx_aligned, daily_bars
from perpbt.stats.bootstrap import sharpe
from perpbt.stats.diagnostics import breakdown, exposure, headline, max_drawdown, quantiles, shared_fraction
from perpbt.stats.regimes import daily_vol_asof, label_regimes, vol_median
from perpbt.stats.rolling import per_year, rolling_sharpe, rolling_windows
from tests.synthetic import T0_MS, perturb_after, random_walk

D = date.fromisoformat


def ms(iso):
    return int(pd.Timestamp(iso, tz="UTC").value // 1_000_000)


def trades(rows):
    """rows: (entry date iso, net_r[, extra dict])."""
    out = []
    for k, r in enumerate(rows):
        extra = r[2] if len(r) > 2 else {}
        out.append({"trade_id": k + 1, "entry_ms": ms(r[0]), "net_r": r[1], "gross_r": r[1] + 0.1, "cost_r": 0.1,
                    "funding_r": 0.0, "hold_minutes": 60, "exit_reason": "stop" if r[1] < 0 else "target", **extra})
    return pd.DataFrame(out)


# --- rolling ------------------------------------------------------------------------------------

def test_rolling_six_month_windows_stepped_monthly():
    tr = trades([("2020-01-15", 1.0), ("2020-03-01", -1.0), ("2020-06-30", 2.0), ("2020-07-01", -1.0),
                 ("2020-08-10", 1.0)])
    w = rolling_windows(tr, ms("2020-01-01"), ms("2020-09-01"))
    assert [(x["start"], x["end"]) for x in w] == [("2020-01-01", "2020-07-01"), ("2020-02-01", "2020-08-01"),
                                                   ("2020-03-01", "2020-09-01")]
    assert (w[0]["n"], w[0]["mean_net_r"], w[0]["win_rate"]) == (3, pytest.approx(2 / 3), pytest.approx(2 / 3))
    assert (w[1]["n"], w[2]["n"]) == (3, 4)
    assert rolling_windows(tr, ms("2020-01-01"), ms("2020-05-01")) == []
    empty = rolling_windows(tr.iloc[0:0], ms("2020-01-01"), ms("2020-08-01"))
    assert empty[0]["n"] == 0 and empty[0]["mean_net_r"] is None


def daily_frame(start, rets, equity0=10_000.0):
    dates = pd.date_range(start, periods=len(rets), freq="D").date
    eq = equity0 * np.cumprod(1 + np.asarray(rets))
    return pd.DataFrame({"date": dates, "ret": rets, "equity": eq})


def test_rolling_sharpe_over_182_days_at_month_ends(rng):
    rets = rng.normal(0.001, 0.01, 400)
    daily = daily_frame("2020-01-01", rets)
    out = rolling_sharpe(daily)
    assert out[0]["end"] == "2020-06-30"  # the first month end with 182 days of history
    window = daily[(daily["date"] > D("2020-06-30") - pd.Timedelta(days=182)) & (daily["date"] <= D("2020-06-30"))]
    assert len(window) == 182 and out[0]["sharpe"] == pytest.approx(sharpe(window["ret"]))
    assert [x["end"] for x in out][-1] == "2021-01-31"


def test_per_year_table():
    tr = trades([("2020-03-01", 1.0), ("2020-11-01", -1.0), ("2021-02-01", 2.0)])
    rets = np.zeros(731)
    rets[10], rets[20], rets[400] = 0.02, -0.03, 0.01
    daily = daily_frame("2020-01-01", rets)
    orders = pd.DataFrame({"kind": ["entry_limit"] * 5 + ["stop"],
                           "placed_ms": [ms("2020-02-01"), ms("2020-03-01"), ms("2020-10-01"), ms("2021-01-01"),
                                         ms("2021-05-01"), ms("2020-03-01")],
                           "status": ["filled", "cancelled", "filled", "filled", "cancelled", "filled"],
                           "cancel_reason": [None, "expired", None, None, "leverage_cap", None]})
    out = {r["year"]: r for r in per_year(tr, daily, orders, 10_000.0)}
    assert (out[2020]["n"], out[2020]["mean_net_r"], out[2020]["win_rate"]) == (2, 0.0, 0.5)
    assert out[2020]["fill_rate"] == pytest.approx(2 / 3) and out[2021]["fill_rate"] == 1.0
    eq20 = daily["equity"][daily["date"] < D("2021-01-01")]
    assert out[2020]["max_dd"] == pytest.approx(max_drawdown(eq20, 10_000.0))
    assert out[2021]["max_dd"] == 0.0 and out[2021]["n"] == 1
    assert out[2020]["sharpe"] == pytest.approx(sharpe(rets[:366]))


# --- regimes ------------------------------------------------------------------------------------

def walk15(seed=1, days=80):
    return random_walk(days * 96, seed=seed, start_ms=T0_MS, step_sigma=0.003)


def test_daily_vol_is_the_30_day_std_of_completed_days():
    cd = walk15()
    v = daily_vol_asof(cd)
    bars = daily_bars(cd)
    lr = np.diff(np.log(bars.c))
    i = 50 * 96 + 7  # a candle of day 50: uses the returns of days 20..49
    assert v[i] == pytest.approx(np.std(lr[19:49], ddof=1))
    assert np.isnan(v[30 * 96])  # day 30: only 29 completed returns
    assert not np.isnan(v[31 * 96])


def test_regime_labels_use_the_day_before_entry():
    cd = walk15()
    i = 60 * 96 + 40
    tr = pd.DataFrame({"entry_idx": [i, 10], "regime_trend": [None, None], "regime_vol": [None, None]})
    med = vol_median(cd, T0_MS, T0_MS + 80 * DAY_MS)
    a = label_regimes(tr, cd, med)
    b = label_regimes(tr, perturb_after(cd, 60 * 96 - 1, seed=9, step_sigma=0.05), med)  # the entry day goes wild
    assert a[["regime_trend", "regime_vol"]].equals(b[["regime_trend", "regime_vol"]])
    adx = daily_adx_aligned(cd)[i]
    assert a.loc[0, "regime_trend"] == ("trend" if adx > 25 else "no_trend")
    assert a.loc[0, "regime_vol"] == ("high" if daily_vol_asof(cd)[i] > med else "low")
    assert a.loc[1, "regime_trend"] is None and a.loc[1, "regime_vol"] is None  # warmup: NaN indicators
    assert tr["regime_trend"].isna().all()  # the input is not modified


def test_vol_median_is_over_the_days_of_the_period():
    cd = walk15()
    v = daily_vol_asof(cd)
    firsts = np.arange(40, 70) * 96
    assert vol_median(cd, T0_MS + 40 * DAY_MS, T0_MS + 70 * DAY_MS) == pytest.approx(np.median(v[firsts]))
    assert vol_median(cd, T0_MS, T0_MS + 10 * DAY_MS) is None


# --- diagnostics --------------------------------------------------------------------------------

def test_headline_numbers():
    tr = trades([("2020-01-01", 2.0), ("2020-01-02", -1.0), ("2020-01-03", -0.5), ("2020-01-04", 1.0)])
    h = headline(tr)
    assert (h["n"], h["win_rate"], h["mean_net_r"], h["median_net_r"]) == (4, 0.5, 0.375, 0.25)
    assert h["profit_factor"] == pytest.approx(3.0 / 1.5) and h["mean_gross_r"] == pytest.approx(0.475)
    assert headline(tr.iloc[:1])["profit_factor"] is None and headline(tr.iloc[0:0])["mean_net_r"] is None


def test_exposure_counts_candles_with_an_open_position():
    tr = pd.DataFrame({"entry_idx": [10, 12, 30, 95], "exit_idx": [14, 20, 30, 120]})
    daily = pd.DataFrame({"equity": [100.0, 200.0], "exposure_notional": [50.0, 0.0]})
    out = exposure(tr, daily, 0, 99)
    assert out["exposure_time"] == pytest.approx((11 + 1 + 5) / 100)
    assert out["exposure_notional"] == pytest.approx(0.25)


def test_quantiles_and_breakdowns():
    q = quantiles([1.0, 2.0, 3.0, float("nan")])
    assert (q["min"], q["p50"], q["max"], q["mean"]) == (1.0, 2.0, 3.0, 2.0)
    assert quantiles([]) is None
    tr = trades([("2020-01-06", 1.0, {"dow": 0}), ("2020-01-07", -1.0, {"dow": 1}),
                 ("2020-01-13", 3.0, {"dow": 0})])
    out = breakdown(tr, "dow")
    assert out == [{"key": 0, "n": 2, "win_rate": 1.0, "mean_net_r": 2.0},
                   {"key": 1, "n": 1, "win_rate": 0.0, "mean_net_r": -1.0}]
    tr["regime_vol"] = [None, "high", "high"]
    assert [r["key"] for r in breakdown(tr, "regime_vol")] == ["high", None]


def test_shared_fraction_on_three_synthetic_tables():
    t = lambda c: pd.DataFrame({"candidate_ms": pd.array(c, dtype="Int64")})  # noqa: E731
    out = shared_fraction({"utc": t([1, 2, 3, 4]), "ny": t([2, 3, 9]), "london": t([3, 4, None])})
    assert out["utc"] == {"ny": 0.5, "london": 0.5, "any": 0.75}
    assert out["ny"] == {"utc": pytest.approx(2 / 3), "london": pytest.approx(1 / 3), "any": pytest.approx(2 / 3)}
    assert out["london"] == {"utc": 1.0, "ny": 0.5, "any": 1.0}  # the trade without a candidate is left out
    assert shared_fraction({"utc": t([]), "ny": t([1])})["utc"] == {"ny": None, "any": None}
