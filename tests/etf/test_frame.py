"""The ETF day frame (ETF spec Phase 1 §1.1-§1.3): total return, the overnight/intraday split, the T-bill cash return."""
import numpy as np
import pandas as pd
import pytest

from perpbt.data.store import DAY_MS, date_ms
from perpbt.etf.frame import (
    MAX_RATE_AGE_DAYS, EtfFrame, build_frame, cash_returns, check_aligned, load_frames,
)
from perpbt.etf.store import ACTION_COLUMNS, PRICE_COLUMNS, EtfStore, FileSource, HoldoutAccessError
from perpbt.etf.validate import total_return
from tests.etf.synth import load_rows, make_config, make_data, walk_inputs

ARRAYS = ("day_ms", "o", "h", "l", "c", "div", "ret", "r_on", "r_id", "cash", "stale_open")


def _rates(pairs):
    return pd.DataFrame({"day_ms": [date_ms(d) for d, _ in pairs], "rate_pct": [r for _, r in pairs]})


DAYS3 = ["2005-01-04", "2005-01-05", "2005-01-06"]
RATES3 = _rates([("2005-01-03", 2.0), ("2005-01-04", 2.0), ("2005-01-05", 2.0)])


def test_total_return_identities():
    f = build_frame("SPY", load_rows(DAYS3, [100, 101, 102], o=[100, 100.5, 101.5], div=[0, 0, 0.5]), RATES3,
                    first_day=DAYS3[0])
    assert f.r_on[2] == pytest.approx((101.5 + 0.5) / 101 - 1, abs=1e-15)
    assert f.r_id[2] == pytest.approx(102 / 101.5 - 1, abs=1e-15)
    np.testing.assert_allclose((1 + f.r_on[1:]) * (1 + f.r_id[1:]) - 1, f.ret[1:], rtol=0, atol=1e-15)
    np.testing.assert_array_equal(f.ret, total_return(f.o, f.c, f.div))
    assert np.isnan(f.ret[0]) and np.isnan(f.r_on[0]) and np.isnan(f.r_id[0])
    assert len(f) == 3 and f.ticker == "SPY"


def test_dividend_in_overnight_only():
    kw = dict(o=[100, 100.5, 101.5])
    a = build_frame("SPY", load_rows(DAYS3, [100, 101, 102], **kw), RATES3, first_day=DAYS3[0])
    b = build_frame("SPY", load_rows(DAYS3, [100, 101, 102], div=[0, 0, 0.5], **kw), RATES3, first_day=DAYS3[0])
    np.testing.assert_array_equal(a.r_id, b.r_id)
    assert a.ret[1] == b.ret[1] and a.r_on[1] == b.r_on[1]
    assert b.r_on[2] > a.r_on[2] and b.ret[2] > a.ret[2]
    assert b.div[2] == 0.5 and b.div[1] == 0


def test_split_day_is_continuous():
    f = build_frame("IWM", load_rows(DAYS3, [100, 100.4, 100.0], split=[0, 2.0, 0]), RATES3, first_day=DAYS3[0])
    assert f.ret[1] == pytest.approx(0.004, abs=1e-12)


def test_stale_open_flag():
    f = build_frame("SPY", load_rows(DAYS3, [100, 101, 102], o=[99, 100, 101.5]), RATES3, first_day=DAYS3[0])
    assert f.stale_open.tolist() == [False, True, False]


def test_cash_weekend_holiday_and_ffill():
    # Thu, Fri, Mon, Tue; a rate on Saturday (after Monday's previous trading day) is not used for Monday
    days = ["2005-01-06", "2005-01-07", "2005-01-10", "2005-01-11"]
    rates = _rates([("2005-01-06", 2.0), ("2005-01-07", 3.0), ("2005-01-08", 9.0), ("2005-01-10", 4.0)])
    cash = cash_returns(np.array([date_ms(d) for d in days]), rates)
    assert np.isnan(cash[0])
    assert cash[1] == pytest.approx(0.02 / 360, abs=1e-18)
    assert cash[2] == pytest.approx(0.03 * 3 / 360, abs=1e-18)
    assert cash[3] == pytest.approx(0.04 / 360, abs=1e-18)
    # Monday 2005-01-17 a holiday: Friday -> Tuesday is 4 days at Friday's rate; no rate on Friday forward-fills Thursday's
    days = ["2005-01-13", "2005-01-14", "2005-01-18"]
    cash = cash_returns(np.array([date_ms(d) for d in days]), _rates([("2005-01-13", 2.5), ("2005-01-17", 7.0)]))
    assert cash[1] == pytest.approx(0.025 / 360, abs=1e-18)
    assert cash[2] == pytest.approx(0.025 * 4 / 360, abs=1e-18)


def test_negative_rate_used_as_is():
    cash = cash_returns(np.array([date_ms(d) for d in DAYS3]), _rates([("2005-01-04", -0.05), ("2005-01-05", 1.0)]))
    assert cash[1] == pytest.approx(-0.0005 / 360, abs=1e-18) and cash[2] > 0


def test_stale_or_missing_rate_raises():
    ms = np.array([date_ms(d) for d in DAYS3])
    with pytest.raises(ValueError, match="no published rate"):
        cash_returns(ms, _rates([("2005-01-05", 2.0)]))
    old = (date_ms("2005-01-05") - date_ms("2004-12-24")) // DAY_MS
    assert old > MAX_RATE_AGE_DAYS
    with pytest.raises(ValueError, match="days old"):
        cash_returns(ms, _rates([("2004-12-24", 2.0)]))


def test_first_row_must_be_first_day():
    with pytest.raises(ValueError, match="first"):
        build_frame("SPY", load_rows(DAYS3[1:], [100, 101]), RATES3, first_day=DAYS3[0])


def test_bad_prices_refused():
    with pytest.raises(ValueError, match="positive"):
        build_frame("SPY", load_rows(DAYS3, [100, 0.0, 102]), RATES3, first_day=DAYS3[0])


def test_frame_arrays_read_only():
    f = build_frame("SPY", load_rows(DAYS3, [100, 101, 102]), RATES3, first_day=DAYS3[0])
    with pytest.raises(ValueError):
        f.c[0] = 1.0


# --- causality -----------------------------------------------------------------------

def _perturbed(prices, rates, d, column, seed):
    """``prices`` with ``column`` changed after row ``d``, or ``rates`` from day ``d``'s date on."""
    rng = np.random.default_rng(seed)
    p, r = prices.copy(), rates.copy()
    if column == "rates":
        later = r["day_ms"] >= p["day_ms"].iloc[d]
        r.loc[later, "rate_pct"] = rng.uniform(-1, 9, int(later.sum()))
    else:
        later = p.index > d
        p.loc[later, column] = p.loc[later, column] * rng.uniform(0.9, 1.1, int(later.sum())) + (column == "dividend")
        p["high"] = np.maximum(p["high"], p[["open", "close"]].max(axis=1))
        p["low"] = np.minimum(p["low"], p[["open", "close"]].min(axis=1))
    return p, r


def _assert_head_equal(a: EtfFrame, b: EtfFrame, d: int):
    for name in ARRAYS:
        np.testing.assert_array_equal(getattr(a, name)[:d + 1], getattr(b, name)[:d + 1], err_msg=name)


@pytest.mark.parametrize("column", ["open", "high", "low", "close", "dividend", "rates"])
def test_frame_causal(column):
    days, prices, rates = walk_inputs()
    full = build_frame("SPY", prices, rates, first_day=days[0])
    for k, d in enumerate((5, 40, 200)):
        p, r = _perturbed(prices, rates, d, column, seed=k)
        moved = build_frame("SPY", p, r, first_day=days[0])
        _assert_head_equal(full, moved, d)
        assert any(not np.array_equal(getattr(full, a)[d + 1:], getattr(moved, a)[d + 1:], equal_nan=True)
                   for a in ARRAYS), "the perturbation changed nothing"
        cut = build_frame("SPY", prices.iloc[:d + 1], rates[rates["day_ms"] <= prices["day_ms"].iloc[d]],
                          first_day=days[0])
        assert len(cut) == d + 1
        _assert_head_equal(full, cut, d)


# --- loader ----------------------------------------------------------------------------

def _store(tmp_path, data, holdout_end="2026-10-23"):
    store = EtfStore(tmp_path / "etf")
    prov = {rel: FileSource("test", f"test://{rel}", {}, "2026-10-24T00:00:00Z", "a" * 64) for rel in data.files()}
    store.write(data, prov, download_date="2026-10-24", holdout_end=holdout_end)
    return store


def test_load_frames_aligned_and_guarded(tmp_path):
    store = _store(tmp_path, make_data())
    frames = load_frames(store, make_config(), end="2006-12-29")
    assert list(frames) == ["SPY", "QQQ", "IWM", "GLD"]
    for f in frames.values():
        assert f.day_ms[0] == date_ms("2004-11-18") and f.day_ms[-1] == date_ms("2006-12-29")
        np.testing.assert_array_equal(f.day_ms, frames["SPY"].day_ms)
        assert np.isfinite(f.cash[1:]).all() and np.isfinite(f.ret[1:]).all()
    assert frames["SPY"].div.sum() > 0 and frames["GLD"].div.sum() == 0
    with pytest.raises(HoldoutAccessError):
        load_frames(store, make_config(), end="2018-01-02")


def test_check_aligned_refuses_misaligned():
    days, prices, rates = walk_inputs(n=50)
    a = build_frame("SPY", prices, rates, first_day=days[0])
    b = build_frame("GLD", prices.drop(index=20).reset_index(drop=True), rates, first_day=days[0])
    check_aligned({"SPY": a, "QQQ": a})
    with pytest.raises(ValueError, match="GLD"):
        check_aligned({"SPY": a, "GLD": b})


def test_end_after_holdout_end_refused(tmp_path):
    store = _store(tmp_path, make_data(), holdout_end="2006-12-29")
    with pytest.raises(ValueError, match="holdout_end"):
        load_frames(store, make_config(), end="2007-01-03")


def test_columns_are_the_store_columns():
    # the frame reads the store's load columns; a renamed store column must fail here, not silently
    assert set(PRICE_COLUMNS) | set(ACTION_COLUMNS) == set(load_rows(DAYS3, [1, 2, 3]).columns)
