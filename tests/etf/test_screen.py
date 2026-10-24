"""The gross ETF screen: alignment, the cash leg, shared circular shift, random-days paths (ETF spec Phase 2 §2.3-§2.4)."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from perpbt.config import from_dict
from perpbt.data.store import DAY_MS, date_ms
from perpbt.etf.config import EtfSizingConfig
from perpbt.etf.frame import build_frame
from perpbt.etf.screen import (
    ScreenInputs, portfolio_excess, run_screen, screen_inputs, screen_path, sharpe252, shift_offsets,
    shifted_exposure, write_screen,
)
from perpbt.etf.signals import compute_signals, etf_target_weight
from perpbt.trend.config import MEMBERS, SignalConfig
from tests.etf.synth import START, TICKERS, load_rows, trading_days

SHORT = from_dict(SignalConfig, {"members": list(MEMBERS), "lookbacks": [4, 8], "donchian_exit": "half",
                                 "direction": "long_flat"})
PRIMARY = from_dict(SignalConfig, {"members": list(MEMBERS), "lookbacks": [20, 40, 80, 160],
                                   "donchian_exit": "half", "direction": "long_flat"})
SIZE5 = from_dict(EtfSizingConfig, {"target_vol": 0.15, "vol_window": 5, "max_weight": 1.0, "annualise_days": 252})
SIZE60 = from_dict(EtfSizingConfig, {"target_vol": 0.15, "vol_window": 60, "max_weight": 1.0, "annualise_days": 252})
DAYS = trading_days(START, date(2010, 12, 31))


def walk(n, seed, drift=None, sigma=0.01):
    rng = np.random.default_rng(seed)
    mu = np.zeros(n) if drift is None else drift
    return 100.0 * np.exp(np.cumsum(mu + rng.normal(0.0, sigma, n)))


def rates_for(days, rate=2.0):
    """A rate every calendar day from 14 days before the first; ``rate=None`` cycles 1%..7% day by day."""
    lo = date_ms(days[0]) - 14 * DAY_MS
    ms = np.arange(lo, date_ms(days[-1]) + DAY_MS, DAY_MS)
    r = 1.0 + np.arange(len(ms)) % 7 if rate is None else np.full(len(ms), rate)
    return pd.DataFrame({"day_ms": ms, "rate_pct": r})


def frames_for(closes, rate=2.0):
    n = len(next(iter(closes.values())))
    days = DAYS[:n]
    rates = rates_for(days, rate)
    return {t: build_frame(t, load_rows(days, c), rates, first_day=days[0]) for t, c in closes.items()}


def inputs_for(frames, sig, sizing, start, end):
    signals = {t: compute_signals(f, sig, sizing) for t, f in frames.items()}
    return screen_inputs(frames, signals, start=start, end=end), signals


def four(n, seed, **kw):
    return {t: walk(n, seed + j, **kw) for j, t in enumerate(TICKERS)}


def test_screen_inputs_alignment():
    frames = frames_for(four(120, 1), rate=None)
    start, end = DAYS[30], DAYS[99]
    inp, sig = inputs_for(frames, SHORT, SIZE5, start, end)
    assert inp.tickers == TICKERS
    assert inp.day_ms[0] == date_ms(DAYS[29]) and inp.day_ms[-1] == date_ms(DAYS[98]) and len(inp.day_ms) == 70
    j = np.arange(29, 99)
    for i, t in enumerate(TICKERS):
        np.testing.assert_array_equal(inp.ret_next[i], frames[t].ret[j + 1])
        np.testing.assert_array_equal(inp.weight[i], sig[t].weight[j])
        np.testing.assert_array_equal(inp.exposure[i], sig[t].exposure[j])
        np.testing.assert_array_equal(inp.sigma[i], sig[t].sigma[j])
    np.testing.assert_array_equal(inp.cash_next, frames["SPY"].cash[j + 1])
    assert len(np.unique(inp.cash_next)) > 3  # the cash leg varies (weekends, rates), so misalignment would show
    np.testing.assert_allclose(inp.weight, etf_target_weight(inp.exposure, inp.sigma, SIZE5), rtol=0, atol=0)


def test_end_on_a_weekend_takes_the_last_trading_day():
    frames = frames_for(four(120, 2))
    sat = next(d for d in (date(2005, 3, 5), date(2005, 3, 12)) if d.isoformat() > DAYS[40]).isoformat()
    inp, _ = inputs_for(frames, SHORT, SIZE5, DAYS[30], sat)
    last_return = max(d for d in DAYS if d <= sat)
    assert date_ms(DAYS[DAYS.index(last_return) - 1]) == inp.day_ms[-1]


def test_screen_inputs_refusals():
    frames = frames_for(four(120, 3))
    signals = {t: compute_signals(f, SHORT, SIZE5) for t, f in frames.items()}
    with pytest.raises(ValueError, match="no decision day"):
        screen_inputs(frames, signals, start=DAYS[0], end=DAYS[50])
    with pytest.raises(ValueError, match="not valid"):
        screen_inputs(frames, signals, start=DAYS[5], end=DAYS[50])  # warmup: valid from index 8
    with pytest.raises(ValueError, match="no trading day"):
        screen_inputs(frames, signals, start="2010-01-02", end="2010-01-03")
    short = {**frames, "GLD": frames_for({"GLD": walk(119, 9)})["GLD"]}
    with pytest.raises(ValueError, match="GLD"):
        screen_inputs(short, signals, start=DAYS[30], end=DAYS[50])
    other_cash = {**frames, "IWM": frames_for({"IWM": walk(120, 5)}, rate=3.0)["IWM"]}
    with pytest.raises(ValueError, match="cash"):
        screen_inputs(other_cash, signals, start=DAYS[30], end=DAYS[50])


def _hand_inputs(weight):
    return ScreenInputs(
        tickers=("SPY", "GLD"), day_ms=np.arange(3, dtype=np.int64) * DAY_MS,
        ret_next=np.array([[0.01, 0.02, -0.01], [0.03, -0.02, 0.05]]), cash_next=np.array([1e-4, 2e-4, 3e-4]),
        exposure=np.ones((2, 3)), sigma=np.full((2, 3), 0.1), weight=np.asarray(weight, dtype=float),
    )


def test_portfolio_excess_hand():
    w = np.array([[1.0, 0.5, 0.0], [0.25, 0.0, 1.0]])
    inp = _hand_inputs(w)
    cash = inp.cash_next
    etf = w * inp.ret_next + (1 - w) * cash
    np.testing.assert_allclose(portfolio_excess(w, inp), etf.mean(axis=0) - cash, rtol=1e-15)
    # day 1: SPY half in at 2%, half cash; GLD all cash; minus cash
    assert portfolio_excess(w, inp)[1] == pytest.approx(((0.5 * 0.02 + 0.5 * 2e-4) + 2e-4) / 2 - 2e-4, rel=1e-14)
    np.testing.assert_allclose(portfolio_excess(np.stack([w, 2 * w]), inp)[0], portfolio_excess(w, inp), rtol=1e-15)


def test_cash_leg_credited_on_the_uninvested_share():
    inp = _hand_inputs(np.zeros((2, 3)))
    np.testing.assert_allclose(portfolio_excess(inp.weight, inp), 0.0, atol=1e-18)  # all cash: no excess
    full = _hand_inputs(np.ones((2, 3)))
    np.testing.assert_allclose(portfolio_excess(full.weight, full), full.ret_next.mean(axis=0) - full.cash_next,
                               rtol=1e-15)
    # with the cash rate raised, a flat book's raw return rises with it and its excess stays 0
    frames = frames_for(four(150, 4), rate=5.0)
    flat, _ = inputs_for(frames, SHORT, SIZE5, DAYS[30], DAYS[140])
    raw = (flat.weight * flat.ret_next + (1 - flat.weight) * flat.cash_next).mean(axis=0)
    np.testing.assert_allclose(raw - flat.cash_next, portfolio_excess(flat.weight, flat), rtol=1e-12, atol=1e-18)
    assert flat.cash_next.min() > 0 and (flat.weight < 1).any()


def test_the_weight_decided_on_d_earns_d_plus_1():
    # perturbing prices from day m+1 on must leave every decision up to day m unchanged
    base = four(200, 6)
    start, end = DAYS[30], DAYS[190]
    a, _ = inputs_for(frames_for(base), SHORT, SIZE5, start, end)
    for m in (40, 100, 150):
        bumped = {t: np.concatenate([c[:m + 1], c[m + 1:] * 1.07]) for t, c in base.items()}
        b, _ = inputs_for(frames_for(bumped), SHORT, SIZE5, start, end)
        cut = m - 29  # decision day m is position m − 29
        np.testing.assert_array_equal(a.weight[:, :cut + 1], b.weight[:, :cut + 1])
        np.testing.assert_array_equal(a.ret_next[:, :cut], b.ret_next[:, :cut])
        assert (a.ret_next[:, cut] != b.ret_next[:, cut]).all()  # day m+1's return carries the jump
        np.testing.assert_allclose(portfolio_excess(a.weight, a)[:cut + 1] - portfolio_excess(b.weight, b)[:cut + 1],
                                   np.r_[np.zeros(cut), portfolio_excess(a.weight, a)[cut] -
                                         portfolio_excess(b.weight, b)[cut]], atol=0)


def test_sharpe252():
    rng = np.random.default_rng(0)
    x = rng.normal(0.001, 0.01, 500)
    assert sharpe252(x) == pytest.approx(np.sqrt(252) * x.mean() / x.std(ddof=1), rel=1e-12)
    rows = np.stack([x, 2 * x, -x])
    np.testing.assert_allclose(sharpe252(rows), [sharpe252(x), sharpe252(x), -sharpe252(x)], rtol=1e-12)
    assert np.isnan(sharpe252(np.zeros(10))) and np.isnan(sharpe252(np.ones(1)))


def test_shift_offsets():
    k = shift_offsets(300, 20_000, 60, np.random.default_rng(0))
    assert k.min() == 60 and k.max() == 240
    with pytest.raises(ValueError):
        shift_offsets(100, 5, 60, np.random.default_rng(0))


def _shift_inputs():
    inp, _ = inputs_for(frames_for(four(500, 7, sigma=0.03)), SHORT, SIZE5, DAYS[30], DAYS[490])
    return inp


def test_shift_rotates_and_preserves_time_in_market_and_changes():
    inp = _shift_inputs()
    n = inp.exposure.shape[1]
    k = shift_offsets(n, 50, 60, np.random.default_rng(1))
    e2 = shifted_exposure(inp, k)
    assert e2.shape == (50, 4, n)
    for i in range(4):
        e = inp.exposure[i]
        assert len(np.unique(e)) > 3
        for m, km in enumerate(k):
            s = e2[m, i]
            np.testing.assert_array_equal(s, e[(np.arange(n) + km) % n])
            np.testing.assert_array_equal(np.sort(s), np.sort(e))
            assert (s != np.roll(s, 1)).sum() == (e != np.roll(e, 1)).sum()


def test_one_shared_offset():
    c = walk(400, 9, sigma=0.03)
    inp, _ = inputs_for(frames_for({t: c for t in TICKERS}), SHORT, SIZE5, DAYS[30], DAYS[390])
    e2 = shifted_exposure(inp, shift_offsets(inp.exposure.shape[1], 30, 60, np.random.default_rng(2)))
    for i in range(1, 4):
        np.testing.assert_array_equal(e2[:, 0], e2[:, i])


def _run(closes, sig, sizing, start, end, *, paths, seed):
    inp, _ = inputs_for(frames_for(closes), sig, sizing, start, end)
    return run_screen(inp, sizing, paths=paths, margin=60, rng=np.random.default_rng(seed), stop_if_p_at_least=0.10)


def test_trending_beats_shifted():
    n = 900
    # aperiodic regimes: a fixed period would let a shift by a multiple of it copy the real timing
    lens = np.random.default_rng(100).integers(60, 200, 20)
    regime = np.repeat(np.resize([0.003, -0.003], 20), lens)[:n]
    r = _run(four(n, 10, drift=regime), PRIMARY, SIZE60, DAYS[200], DAYS[n - 1], paths=199, seed=3)
    assert r["p_screen"] < 0.05 and r["decision"] == "go"
    assert r["observed_sharpe"] > r["paths"]["p95"]


def test_random_walk_p_roughly_uniform():
    ps = np.array([_run(four(700, 1000 * seed), SHORT, SIZE5, DAYS[30], DAYS[699], paths=99, seed=seed)["p_screen"]
                   for seed in range(30)])
    assert 0.3 <= ps.mean() <= 0.7
    assert (ps < 0.1).sum() <= 9


def test_run_screen_deterministic_p_formula_and_benchmarks():
    inp, _ = inputs_for(frames_for(four(500, 20)), SHORT, SIZE5, DAYS[30], DAYS[499])
    a = run_screen(inp, SIZE5, paths=150, margin=60, rng=np.random.default_rng(4), stop_if_p_at_least=0.10)
    b = run_screen(inp, SIZE5, paths=150, margin=60, rng=np.random.default_rng(4), stop_if_p_at_least=0.10)
    assert a == b
    assert a["paths"]["n"] == 150 and a["n_days"] == len(inp.day_ms)
    k = round(a["p_screen"] * 151) - 1
    assert a["p_screen"] == (1 + k) / 151
    assert a["decision"] == ("stop" if a["p_screen"] >= 0.10 else "go")
    assert set(a) == {"n_days", "observed_sharpe", "paths", "p_screen", "stop_if_p_at_least", "decision",
                      "bh_sharpe", "spy_sharpe", "per_etf", "cash_annual_mean"}
    assert set(a["paths"]) == {"n", "mean", "std", "p05", "p50", "p95"}
    assert a["observed_sharpe"] == sharpe252(portfolio_excess(inp.weight, inp))
    bh_w = np.minimum(0.15 / inp.sigma, 1.0)
    assert a["bh_sharpe"] == pytest.approx(sharpe252(portfolio_excess(bh_w, inp)), rel=1e-12)
    assert a["spy_sharpe"] == pytest.approx(sharpe252(inp.ret_next[0] - inp.cash_next), rel=1e-12)
    assert list(a["per_etf"]) == list(TICKERS)
    gld = a["per_etf"]["GLD"]
    assert gld["sharpe"] == pytest.approx(sharpe252(inp.weight[3] * (inp.ret_next[3] - inp.cash_next)), rel=1e-10)
    assert gld["mean_weight"] == pytest.approx(inp.weight[3].mean())
    assert a["cash_annual_mean"] == pytest.approx(inp.cash_next.mean() * 252)


def test_paths_recompute_weights_with_the_actual_sigma():
    inp, _ = inputs_for(frames_for(four(400, 21)), SHORT, SIZE5, DAYS[30], DAYS[399])
    k = shift_offsets(inp.exposure.shape[1], 1, 60, np.random.default_rng(5))
    a = run_screen(inp, SIZE5, paths=1, margin=60, rng=np.random.default_rng(5), stop_if_p_at_least=0.1)
    w = etf_target_weight(shifted_exposure(inp, k)[0], inp.sigma, SIZE5)
    assert a["paths"]["mean"] == pytest.approx(sharpe252(portfolio_excess(w, inp)), rel=1e-12)


def test_run_screen_chunking_does_not_change_result():
    inp, _ = inputs_for(frames_for(four(500, 30)), SHORT, SIZE5, DAYS[30], DAYS[499])
    a = run_screen(inp, SIZE5, paths=120, margin=60, rng=np.random.default_rng(5), stop_if_p_at_least=0.1, chunk=7)
    b = run_screen(inp, SIZE5, paths=120, margin=60, rng=np.random.default_rng(5), stop_if_p_at_least=0.1, chunk=120)
    assert a == b


def test_nan_sharpe_refuses():
    falling = 100.0 * np.exp(-0.01 * np.arange(300))  # long/flat exposure 0 throughout: excess exactly 0
    inp, _ = inputs_for(frames_for({t: falling for t in TICKERS}), SHORT, SIZE5, DAYS[30], DAYS[299])
    with pytest.raises(ValueError, match="not finite"):
        run_screen(inp, SIZE5, paths=20, margin=60, rng=np.random.default_rng(0), stop_if_p_at_least=0.1)


def test_benchmark_required():
    closes = {t: walk(300, j) for j, t in enumerate(("QQQ", "IWM"))}
    inp, _ = inputs_for(frames_for(closes), SHORT, SIZE5, DAYS[30], DAYS[299])
    with pytest.raises(ValueError, match="SPY"):
        run_screen(inp, SIZE5, paths=20, margin=60, rng=np.random.default_rng(0), stop_if_p_at_least=0.1)


def _result(decision, p):
    q = {"n": 3, "mean": 0.1, "std": 0.1, "p05": 0.0, "p50": 0.1, "p95": 0.2}
    return {"prereg_hash": "a" * 64, "manifest_sha256": "b" * 64, "period": ["2005-08-01", "2017-12-31"],
            "tickers": ["SPY"], "first_decision_day": "2005-07-29", "last_return_day": "2017-12-29", "n_days": 3127,
            "observed_sharpe": 0.3, "paths": q, "p_screen": p, "stop_if_p_at_least": 0.1, "decision": decision,
            "bh_sharpe": 0.4, "spy_sharpe": 0.5, "per_etf": {"SPY": {"sharpe": 0.3, "bh_sharpe": 0.4,
                                                                      "mean_weight": 0.6, "mean_exposure": 0.65}},
            "cash_annual_mean": 0.012, "computed_on": "2026-10-24", "git_commit": "abc"}


def test_write_screen_go_once(tmp_path):
    paths = write_screen(_result("go", 0.02), tmp_path)
    assert paths == [screen_path(tmp_path)] and screen_path(tmp_path) == tmp_path / "etf" / "screen.json"
    assert not (tmp_path / "etf" / "summary.md").exists()
    before = screen_path(tmp_path).read_bytes()
    with pytest.raises(FileExistsError):
        write_screen(_result("stop", 0.5), tmp_path)
    assert screen_path(tmp_path).read_bytes() == before


def test_write_screen_stop_writes_the_summary(tmp_path):
    paths = write_screen(_result("stop", 0.25), tmp_path)
    assert paths == [screen_path(tmp_path), tmp_path / "etf" / "summary.md"]
    text = paths[1].read_text(encoding="utf-8")
    assert "Decision: stop" in text and "p_screen = 0.2500" in text and "| SPY | 0.300 | 0.400 | 0.600 |" in text


def test_write_screen_refuses_nan_and_leaves_no_file(tmp_path):
    with pytest.raises(ValueError):
        write_screen({**_result("go", 0.02), "p_screen": float("nan")}, tmp_path)
    assert not screen_path(tmp_path).exists()
