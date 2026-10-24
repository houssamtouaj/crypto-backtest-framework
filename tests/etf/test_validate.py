"""Validation rules 1-7 (ETF spec Phase 0 §0.4): a clean synthetic pass and one failure per rule."""
import numpy as np
import pandas as pd
import pytest

from perpbt.data.store import date_ms
from perpbt.etf.store import ACTION_COLUMNS
from perpbt.etf.validate import (
    ADJ_TOL_MAX, NYSE_CLOSED, detect_basis, format_table, total_return, validate,
)
from tests.etf.synth import make_config, make_data, replace

CFG = make_config()


def _run(data, **kw):
    results, basis = validate(data, CFG, **kw)
    return {r.rule.split()[0]: r for r in results}, basis


def _only_failing(results, rule):
    assert [k for k, r in results.items() if not r.ok] == [rule], format_table(list(results.values()))
    return results[rule].failures


def _row(frame, day):
    return int(np.flatnonzero(frame["day_ms"].to_numpy() == date_ms(day))[0])


def test_clean_synthetic_passes_every_rule():
    results, basis = _run(make_data())
    assert all(r.ok for r in results.values())
    assert basis == {"SPY": "raw", "QQQ": "raw", "IWM": "raw", "GLD": "raw"}
    assert results["1"].info["first"] == "2004-11-18"
    assert results["4"].info["per_year"]["QQQ"] == {"2006": 4}  # QQQ is checked from 2006


def test_total_return_matches_phase_1():
    r = total_return([10.0, 10.0, 8.0], [10.0, 9.0, 10.0], [0.0, 0.0, 1.0])
    assert np.isnan(r[0])
    assert r[1] == pytest.approx(-0.1)
    assert r[2] == pytest.approx((10 + 1 * 10 / 8) / 9 - 1)


def test_nyse_closures_include_the_special_closures():
    assert {"2007-01-02", "2012-10-29", "2012-10-30", "2018-12-05", "2025-01-09"} <= NYSE_CLOSED


# --- rule 1 ----------------------------------------------------------------------------------------------------

def test_calendar_day_missing_from_one_ticker():
    d = make_data()
    q = d.prices["QQQ"]
    fails = _only_failing(_run(replace(d, prices={**d.prices, "QQQ": q.drop(index=100)}))[0], "1")
    assert fails == (f"QQQ {pd.Timestamp(q['day_ms'][100], unit='ms').date()}: missing (another ticker has prices)",)


def test_calendar_weekday_missing_everywhere_is_not_a_closure():
    d = make_data()
    day = date_ms("2005-03-02")
    prices = {t: f[f["day_ms"] != day] for t, f in d.prices.items()}
    xp = d.xprices
    results, _ = _run(replace(d, prices=prices, xprices=xp))
    assert any("2005-03-02: a weekday without prices" in m for m in results["1"].failures)


def test_calendar_prices_on_a_listed_closure_and_first_date():
    d = make_data()
    extra = {}
    for t, f in d.prices.items():
        row = f.iloc[[0]].assign(day_ms=date_ms("2005-01-17"))  # Martin Luther King Day
        extra[t] = pd.concat([f, row]).sort_values("day_ms").reset_index(drop=True)
    results, _ = _run(replace(d, prices=extra))
    assert "2005-01-17: prices on a listed NYSE closure" in results["1"].failures
    late = {t: f.iloc[1:] for t, f in d.prices.items()}
    results, _ = _run(replace(d, prices=late))
    assert "SPY: first date 2004-11-19, expected 2004-11-18" in results["1"].failures


# --- rule 2 ----------------------------------------------------------------------------------------------------

def test_prices_nonpositive_bracket_and_jump():
    d = make_data()
    p = d.prices["IWM"].copy()
    p.loc[50, "close"] = -1.0
    p.loc[60, "low"] = p.loc[60, "open"] * 1.01
    k = _row(p, "2006-05-01")
    p.loc[k:, ["open", "high", "low", "close"]] *= 1.4  # a 40% jump with no split
    results, _ = _run(replace(d, prices={**d.prices, "IWM": p}))
    fails = results["2"].failures
    assert any("a price is not positive and finite" in m for m in fails)
    assert any("low/high do not bracket" in m for m in fails)
    assert "IWM 2006-05-01: close moved more than 25% without a split" in fails


# --- rule 3 ----------------------------------------------------------------------------------------------------

def test_split_must_leave_the_adjusted_close_continuous():
    d = make_data()
    day = "2005-06-09"
    a = pd.concat([d.actions["IWM"], pd.DataFrame([{"day_ms": date_ms(day), "dividend": 0.0, "split": 2.0,
                                                     "capital_gain": 0.0}], columns=list(ACTION_COLUMNS))])
    a = a.sort_values("day_ms").reset_index(drop=True)
    results, _ = _run(replace(d, actions={**d.actions, "IWM": a}))
    assert results["3"].ok and results["3"].info["splits"] == {"IWM": [{"date": day, "ratio": 2.0}]}
    p = d.prices["IWM"].copy()
    k = _row(p, day)
    p.loc[:k - 1, ["open", "high", "low", "close", "adj_close"]] *= 2  # the history before the split not adjusted
    results, _ = _run(replace(d, prices={**d.prices, "IWM": p}, actions={**d.actions, "IWM": a}))
    assert f"IWM {day}: the split-adjusted close is not continuous across the split" in results["3"].failures
    assert results["2"].ok  # rule 2 leaves split days to rule 3


# --- rule 4 ----------------------------------------------------------------------------------------------------

def test_dividend_counts_gld_and_capital_gains():
    d = make_data()
    spy = d.actions["SPY"]
    spy = spy[~spy["day_ms"].isin([date_ms("2006-03-15"), date_ms("2006-06-15")])].reset_index(drop=True)
    gld = pd.DataFrame([{"day_ms": date_ms("2006-03-15"), "dividend": 0.1, "split": 0.0, "capital_gain": 0.0}],
                       columns=list(ACTION_COLUMNS))
    iwm = d.actions["IWM"].copy()
    iwm.loc[0, "capital_gain"] = 0.05
    results, _ = _run(replace(d, actions={**d.actions, "SPY": spy, "GLD": gld, "IWM": iwm}))
    fails = results["4"].failures
    assert "SPY 2006: 2 dividends, expected 4..4" in fails
    assert any(m.startswith("SPY 2005-12-15..2006-09-15: 274 days without a dividend") for m in fails)
    assert "GLD 2006-03-15: a dividend, but GLD pays none" in fails
    assert any(m.startswith("IWM ") and "capital gain distribution" in m for m in fails)


def test_one_missing_dividend_and_a_late_last_dividend():
    d = make_data()
    spy = d.actions["SPY"]
    one = spy[spy["day_ms"] != date_ms("2005-06-15")].reset_index(drop=True)  # a single missing quarter
    fails = _only_failing(_run(replace(d, actions={**d.actions, "SPY": one}))[0], "4")
    assert "SPY 2005: 3 dividends, expected 4..4" in fails
    last = spy[spy["day_ms"] < date_ms("2006-09-01")].reset_index(drop=True)  # none since June 2006 (partial year)
    fails = _run(replace(d, actions={**d.actions, "SPY": last}))[0]["4"].failures
    assert any(m.startswith("SPY 2006-06-15..2007-01-31: 230 days without a dividend") for m in fails)


def test_an_equal_dividend_a_week_later_is_a_duplicate():
    d = make_data()
    q = d.actions["QQQ"]
    first = q.iloc[[1]]
    dup = first.assign(day_ms=first["day_ms"].iloc[0] + 7 * 86_400_000)
    q2 = pd.concat([q, dup]).sort_values("day_ms").reset_index(drop=True)
    results, _ = _run(replace(d, actions={**d.actions, "QQQ": q2}))
    assert any("a likely duplicate" in m for m in results["4"].failures)


def test_unknown_ticker_has_no_dividend_expectation():
    d = make_data()
    rename = lambda m: {("XLK" if t == "GLD" else t): v for t, v in m.items()}  # noqa: E731
    data = replace(d, prices=rename(d.prices), actions=rename(d.actions), xprices=rename(d.xprices),
                   xdividends=rename(d.xdividends))
    results, _ = validate(data, make_config(tickers=["SPY", "QQQ", "IWM", "XLK"]))
    assert "XLK: no dividend expectation for this ticker" in results[3].failures


# --- rule 5 ----------------------------------------------------------------------------------------------------

def test_a_dividend_missing_from_the_actions_breaks_adj_close_consistency():
    d = make_data(dividend_yield=0.008)
    spy = d.actions["SPY"].iloc[1:].reset_index(drop=True)  # Adj Close still has the first dividend
    first = d.actions["SPY"]["day_ms"].iloc[0]
    results, _ = _run(replace(d, actions={**d.actions, "SPY": spy}))
    fails = results["5"].failures
    assert len(fails) == 1 and fails[0].startswith(f"SPY {pd.Timestamp(first, unit='ms').date()}: total return")
    assert ADJ_TOL_MAX < 0.008


def test_adj_close_share_rule():
    d = make_data()
    p = d.prices["GLD"].copy()
    p.loc[1::50, "adj_close"] *= 1.001  # about 2% of days off by 0.1%
    results, _ = _run(replace(d, prices={**d.prices, "GLD": p}))
    assert any(m.startswith("GLD: total return and Adj Close return agree") for m in results["5"].failures)


# --- rule 6 ----------------------------------------------------------------------------------------------------

def _shift_close(d, ticker, day, factor):
    x = d.xprices[ticker].copy()
    x.loc[_row(x, day), "close"] *= factor
    return replace(d, xprices={**d.xprices, ticker: x})


def test_cross_check_close_difference_fails_unless_accepted():
    d = _shift_close(make_data(), "SPY", "2006-06-01", 1.01)
    fails = _only_failing(_run(d)[0], "6")
    assert fails == ("SPY 2006-06-01: close differs by 1.0000% (raw basis)",)
    results, _ = _run(d, accepted={("SPY", "2006-06-01", "close")})
    assert results["6"].ok and results["6"].info["accepted"] == ["SPY 2006-06-01 close"]


def test_cross_check_accepted_difference_that_no_longer_occurs_fails():
    fails = _only_failing(_run(make_data(), accepted={("SPY", "2006-06-01", "open")})[0], "6")
    assert fails == ("SPY 2006-06-01: accepted open difference no longer occurs; review the corrections file",)


def test_cross_check_open_and_unknown_day_and_coverage():
    d = make_data()
    x = d.xprices["IWM"].copy()
    x.loc[_row(x, "2006-02-01"), "open"] *= 0.99
    extra = x.iloc[[0]].assign(day_ms=date_ms("2006-01-16"))  # a closure: Yahoo has no such day
    x = pd.concat([x, extra]).sort_values("day_ms").reset_index(drop=True)
    results, _ = _run(replace(d, xprices={**d.xprices, "IWM": x}))
    fails = results["6"].failures
    assert "IWM 2006-02-01: open differs by 1.0000% (raw basis)" in fails
    assert "IWM 2006-01-16: a cross-check day Yahoo does not have" in fails
    sparse = d.xprices["GLD"].iloc[::2].reset_index(drop=True)
    results, _ = _run(replace(d, xprices={**d.xprices, "GLD": sparse}))
    assert any(m.startswith("GLD: the cross-check has 50") for m in results["6"].failures)


def test_cross_check_span_missing_opens_and_required_dividends():
    d = make_data()
    short = {**d.xprices, "SPY": d.xprices["SPY"].iloc[-100:].reset_index(drop=True)}  # a short reply: 100% coverage
    results, _ = _run(replace(d, xprices=short))
    assert any(m.startswith("SPY: the cross-check spans") for m in results["6"].failures)
    early = {**d.xprices, "IWM": d.xprices["IWM"].iloc[:-3].reset_index(drop=True)}  # ends before Yahoo
    assert any(m.startswith("IWM: the cross-check spans") for m in _run(replace(d, xprices=early))[0]["6"].failures)
    x = d.xprices["GLD"].copy()
    x.loc[5, "open"] = np.nan
    assert "GLD: 1 cross-check opens are not finite" in _run(
        replace(d, xprices={**d.xprices, "GLD": x}))[0]["6"].failures
    for none in (None, d.xdividends["QQQ"].iloc[:0]):
        fails = _run(replace(d, xdividends={**d.xdividends, "QQQ": none}))[0]["6"].failures
        assert any(m.startswith("QQQ: ") and "dividend" in m for m in fails)


def test_cross_check_dividends_both_ways():
    d = make_data()
    xd = d.xdividends["QQQ"].copy()
    first, second = xd["day_ms"].iloc[0], xd["day_ms"].iloc[1]
    xd.loc[0, "dividend"] *= 1.1
    xd = xd.drop(index=1)
    extra = pd.DataFrame({"day_ms": [date_ms("2006-02-01")], "dividend": [0.2]})
    xd = pd.concat([xd, extra]).sort_values("day_ms").reset_index(drop=True)
    results, _ = _run(replace(d, xdividends={**d.xdividends, "QQQ": xd}))
    fails = results["6"].failures
    day = lambda ms: pd.Timestamp(ms, unit="ms").date()  # noqa: E731
    assert any(m.startswith(f"QQQ {day(first)}: dividend ") for m in fails)
    assert f"QQQ {day(second)}: Yahoo has a dividend the cross-check lacks" in fails
    assert "QQQ 2006-02-01: the cross-check has a 0.2 dividend, Yahoo has none" in fails
    assert results["6"].info["SPY"]["dividends"].startswith("no independent source")


def test_cross_check_adjusted_basis_compares_returns():
    d = make_data()
    adj = {t: f.assign(close=d.prices[t].set_index("day_ms").loc[f["day_ms"], "adj_close"].to_numpy())
           for t, f in d.xprices.items()}
    data = replace(d, xprices=adj)
    assert detect_basis(data.prices["SPY"], data.xprices["SPY"]) == "adjusted"
    results, basis = _run(data)
    assert basis["SPY"] == "adjusted" and results["6"].ok
    x = adj["SPY"].copy()
    k = _row(x, "2006-06-01")
    x.loc[k:, "close"] *= 1.01  # one 1% return error; the levels after it stay shifted, the returns do not
    results, _ = _run(replace(d, xprices={**adj, "SPY": x}))
    (fail,) = results["6"].failures
    assert fail.startswith("SPY 2006-06-01: daily return differs by 1.0") and fail.endswith("(adjusted basis)")


# --- rule 7 ----------------------------------------------------------------------------------------------------

def test_rates_range_gap_and_coverage():
    d = make_data()
    r = d.rates.copy()
    r.loc[10, "rate_pct"] = -2.0
    r.loc[11, "rate_pct"] = -0.05  # DTB3 printed small negatives in 2015 and 2020
    gap = (r["day_ms"] > date_ms("2006-03-01")) & (r["day_ms"] < date_ms("2006-03-13"))
    r = r[~gap].reset_index(drop=True)
    results, _ = _run(replace(d, rates=r))
    fails = results["7"].failures
    assert len([m for m in fails if "outside" in m]) == 1
    assert "2006-03-01..2006-03-13: 7 business days without a rate" in fails
    late = d.rates[d.rates["day_ms"] >= date_ms("2004-12-01")].reset_index(drop=True)
    short = d.rates[d.rates["day_ms"] <= date_ms("2007-01-26")].reset_index(drop=True)
    assert any("after warmup_start" in m for m in _run(replace(d, rates=late))[0]["7"].failures)
    assert any("the trading day before the last price day" in m for m in _run(replace(d, rates=short))[0]["7"].failures)
    ok = d.rates[d.rates["day_ms"] <= date_ms("2007-01-30")].reset_index(drop=True)  # the day before the last
    assert _run(replace(d, rates=ok))[0]["7"].ok


def test_validate_refuses_a_ticker_mismatch_and_bad_basis():
    with pytest.raises(ValueError, match="differ from the config"):
        validate(make_data(), make_config(tickers=["SPY", "QQQ", "IWM"]))
    with pytest.raises(ValueError, match="bad cross-check basis"):
        validate(make_data(), CFG, basis={"SPY": "raw"})


def test_format_table_caps_the_listed_failures():
    d = make_data()
    x = d.xprices["SPY"].copy()
    x["close"] *= 1.02
    results, _ = validate(replace(d, xprices={**d.xprices, "SPY": x}), CFG)
    text = format_table(results, max_failures=2)
    assert "6 cross-check   NO" in text and "... and " in text
