"""ETF signals (ETF spec Phase 1 §1.4-§1.6): the trend members reused, exposure, √252 vol, target weights."""
import inspect
import re
import warnings

import numpy as np
import pytest

import perpbt.trend.signals as trend_signals
from perpbt.config import from_dict
from perpbt.data.store import date_ms
from perpbt.etf import signals as etf_signals
from perpbt.etf.config import EtfSizingConfig
from perpbt.etf.frame import build_frame
from perpbt.etf.signals import (
    ANNUALISE, EtfSignals, compute_signals, first_valid_index, summarise, vol_forecast252,
)
from perpbt.etf.store import day_iso
from perpbt.trend.config import MEMBERS, SignalConfig
from tests.etf.synth import load_rows, walk_inputs

SIGNAL = from_dict(SignalConfig, {"members": list(MEMBERS), "lookbacks": [20, 40, 80, 160],
                                  "donchian_exit": "half", "direction": "long_flat"})
SIZING = from_dict(EtfSizingConfig, {"target_vol": 0.15, "vol_window": 60, "max_weight": 1.0, "annualise_days": 252})
NO_VOL = from_dict(EtfSizingConfig, {"target_vol": None, "vol_window": 60, "max_weight": 1.0, "annualise_days": 252})


def _frame(n=400, seed=7):
    days, prices, rates = walk_inputs(n, seed)
    return build_frame("SPY", prices, rates, first_day=days[0])


def test_members_are_the_trend_objects():
    for name in ("return_sign", "ma_filter", "donchian", "member_matrix", "target_weight"):
        assert getattr(etf_signals, name) is getattr(trend_signals, name), name


def test_target_weight_reads_only_two_fields():
    src = inspect.getsource(trend_signals.target_weight)
    assert set(re.findall(r"sizing\.(\w+)", src)) == {"target_vol", "max_weight"}


def test_vol_forecast252_against_numpy():
    rng = np.random.default_rng(3)
    ret = rng.normal(0, 0.01, 200)
    ret[0] = np.nan
    sigma = vol_forecast252(ret, 60)
    assert np.isnan(sigma[:60]).all()
    for d in range(60, 200):
        assert sigma[d] == pytest.approx(np.std(ret[d - 59:d + 1], ddof=1) * np.sqrt(252), rel=1e-12)
    assert ANNUALISE == np.sqrt(252.0)


def test_exposure_steps_and_member_names():
    s = compute_signals(_frame(), SIGNAL, SIZING)
    assert s.names == tuple(f"{m}_{n}" for m in MEMBERS for n in (20, 40, 80, 160))
    assert s.members.shape == (12, 400)
    e = s.exposure[s.valid]
    np.testing.assert_allclose(e * 12, np.round(e * 12), atol=1e-12)
    assert (e >= 0).all() and (e <= 1).all() and len(np.unique(e)) > 3


def test_valid_from_first_valid_index():
    s = compute_signals(_frame(), SIGNAL, SIZING)
    assert first_valid_index(SIGNAL, SIZING) == 160
    assert not s.valid[:160].any() and s.valid[160:].all()
    assert np.isnan(s.exposure[:160]).all() and np.isnan(s.weight[:160]).all()
    assert np.isfinite(s.members[:, 160:]).all()
    assert s.first_valid_ms == s.day_ms[160]


def test_weight_formula():
    s = compute_signals(_frame(), SIGNAL, SIZING)
    v = s.valid
    np.testing.assert_allclose(s.weight[v], s.exposure[v] * np.minimum(0.15 / s.sigma[v], 1.0), rtol=1e-14)
    assert (s.weight[v] <= s.exposure[v]).all()


def test_zero_vol_hits_cap():
    days, prices, rates = walk_inputs(400)
    prices = prices.copy()
    for k in ("open", "high", "low", "close"):
        prices.loc[:259, k] = 100.0
    prices.loc[:259, "dividend"] = 0.0
    f = build_frame("SPY", prices, rates, first_day=days[0])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        s = compute_signals(f, SIGNAL, SIZING)
    zero = s.valid & (s.sigma == 0)
    assert zero.sum() == 100  # days 160..259
    np.testing.assert_array_equal(s.weight[zero], s.exposure[zero] * 1.0)
    assert np.isfinite(s.weight[s.valid]).all()
    # flat closes give exposure 0 there; the cap itself, on a full exposure:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        w = etf_signals.etf_target_weight(np.array([1.0, 0.5, 1.0]), np.array([0.0, 0.0, np.nan]), SIZING)
    np.testing.assert_array_equal(w[:2], [1.0, 0.5])
    assert np.isnan(w[2])


def test_no_vol_target_branch(monkeypatch):
    f = _frame()
    primary = compute_signals(f, SIGNAL, SIZING)

    def boom(*a, **k):
        raise AssertionError("target_weight called with target_vol None")

    monkeypatch.setattr(etf_signals, "target_weight", boom)
    s = compute_signals(f, SIGNAL, NO_VOL)
    np.testing.assert_array_equal(s.valid, primary.valid)
    np.testing.assert_array_equal(s.weight[s.valid], s.exposure[s.valid] * 1.0)
    assert np.isnan(s.weight[~s.valid]).all()


def test_long_short_refused():
    ls = from_dict(SignalConfig, {"members": list(MEMBERS), "lookbacks": [20, 40, 80, 160],
                                  "donchian_exit": "half", "direction": "long_short"})
    with pytest.raises(ValueError, match="long_flat"):
        compute_signals(_frame(), ls, SIZING)


OUTPUTS = ("members", "exposure", "sigma", "weight", "valid")


@pytest.mark.parametrize("column", ["open", "high", "low", "close", "dividend", "rates"])
def test_signals_causal(column):
    days, prices, rates = walk_inputs(400)
    full = compute_signals(build_frame("SPY", prices, rates, first_day=days[0]), SIGNAL, SIZING)
    for k, d in enumerate((159, 160, 250)):
        rng = np.random.default_rng(k)
        p, r = prices.copy(), rates.copy()
        if column == "rates":
            later = r["day_ms"] >= p["day_ms"].iloc[d]
            r.loc[later, "rate_pct"] = rng.uniform(-1, 9, int(later.sum()))
        else:
            later = p.index > d
            p.loc[later, column] = p.loc[later, column] * rng.uniform(0.8, 1.2, int(later.sum())) + (column == "dividend")
            p["high"] = np.maximum(p["high"], p[["open", "close"]].max(axis=1))
            p["low"] = np.minimum(p["low"], p[["open", "close"]].min(axis=1))
        moved = compute_signals(build_frame("SPY", p, r, first_day=days[0]), SIGNAL, SIZING)
        cut = compute_signals(build_frame("SPY", prices.iloc[:d + 1], rates[rates["day_ms"] <= prices["day_ms"].iloc[d]],
                                          first_day=days[0]), SIGNAL, SIZING)
        for other in (moved, cut):
            for name in OUTPUTS:
                np.testing.assert_array_equal(getattr(full, name)[..., :d + 1], getattr(other, name)[..., :d + 1],
                                              err_msg=f"{column} {d} {name}")
        if column in ("close", "dividend"):  # what σ̂ and the members read: the perturbation must reach them
            assert not np.array_equal(full.sigma[d + 1:], moved.sigma[d + 1:], equal_nan=True)


def _hand_signals(ticker, exposure, weight, start="2005-01-03", k_members=12):
    n = len(exposure)
    day_ms = date_ms(start) + 86_400_000 * np.arange(n)
    valid = np.isfinite(exposure)
    return EtfSignals(ticker, day_ms, tuple(f"m{k}" for k in range(k_members)), np.zeros((k_members, n)),
                      np.asarray(exposure, float), np.ones(n), np.asarray(weight, float), valid)


def test_summarise():
    a = _hand_signals("SPY", [np.nan, 0.5, 0.5, 1.0, 0.5, 1 / 12], [np.nan, 0.4, 0.4, 0.8, 0.5, 0.05])
    b = _hand_signals("GLD", [np.nan, 0.0, 0.0, 0.0, 1.0, 1.0], [np.nan, 0.0, 0.0, 0.0, 0.9, 0.7])
    out = summarise({"SPY": a, "GLD": b}, day_iso(a.day_ms[2]), day_iso(a.day_ms[-1]))
    assert list(out) == ["SPY", "GLD"]
    spy = out["SPY"]
    assert spy["first_valid"] == day_iso(a.day_ms[1]) and spy["days"] == 4
    assert spy["exposure_hist"] == [0, 1, 0, 0, 0, 0, 2, 0, 0, 0, 0, 0, 1]
    assert spy["mean_exposure"] == pytest.approx(np.mean([0.5, 1.0, 0.5, 1 / 12]))
    assert spy["mean_weight"] == pytest.approx(np.mean([0.4, 0.8, 0.5, 0.05]))
    assert sum(out["GLD"]["exposure_hist"]) == 4
    with pytest.raises(ValueError, match="not valid"):  # a window day before the first valid one
        summarise({"SPY": a}, day_iso(a.day_ms[0]), day_iso(a.day_ms[-1]))


def test_summarise_needs_the_decision_day_before_start():
    a = _hand_signals("SPY", [np.nan, 0.5, 0.5, 1.0], [np.nan, 0.4, 0.4, 0.8])
    summarise({"SPY": a}, day_iso(a.day_ms[2]), day_iso(a.day_ms[-1]))
    with pytest.raises(ValueError, match="decision day"):  # every window day valid, the day before not
        summarise({"SPY": a}, day_iso(a.day_ms[1]), day_iso(a.day_ms[-1]))


def test_summarise_bins_by_the_member_count():
    a = _hand_signals("SPY", [1 / 3, 1 / 3, 2 / 3, 1.0], [0.3, 0.3, 0.6, 0.9], k_members=3)
    assert summarise({"SPY": a}, day_iso(a.day_ms[1]), day_iso(a.day_ms[-1]))["SPY"]["exposure_hist"] == [0, 1, 1, 1]
    b = _hand_signals("SPY", [0.5, 0.5, 0.5], [0.5, 0.5, 0.5], k_members=3)
    with pytest.raises(ValueError, match="multiple"):
        summarise({"SPY": b}, day_iso(b.day_ms[1]), day_iso(b.day_ms[-1]))
