"""The gross signal screen: alignment, shared circular shift, random-days paths (trend spec Phase 2 §2.3-§2.4)."""
import numpy as np
import pytest

from perpbt.config import from_dict
from perpbt.data.store import DAY_MS, Candles, Funding, date_ms
from perpbt.trend.config import MEMBERS, SignalConfig, SizingConfig
from perpbt.trend.daily import build_dayframe
from perpbt.trend.screen import (
    ScreenInputs, portfolio_returns, run_screen, screen_inputs, shift_offsets, shifted_exposure,
)
from perpbt.trend.signals import compute_signals, target_weight

SHORT = from_dict(SignalConfig, {"members": list(MEMBERS), "lookbacks": [4, 8], "donchian_exit": "half",
                                 "direction": "long_flat"})
PRIMARY = from_dict(SignalConfig, {"members": list(MEMBERS), "lookbacks": [20, 40, 80, 160],
                                   "donchian_exit": "half", "direction": "long_flat"})
SIZE5 = from_dict(SizingConfig, {"target_vol": 0.30, "vol_window": 5, "max_weight": 2.0, "max_gross": 2.0})
SIZE60 = from_dict(SizingConfig, {"target_vol": 0.30, "vol_window": 60, "max_weight": 2.0, "max_gross": 2.0})
H_MS = 3_600_000


def frame(pair, closes, first="2020-01-01", funding_per_8h=0.0):
    c = np.asarray(closes, dtype=np.float64)
    t0 = date_ms(first)
    ts = t0 + DAY_MS * np.arange(len(c), dtype=np.int64) + DAY_MS - 900_000
    fts = t0 + 8 * H_MS * np.arange(3 * len(c), dtype=np.int64) if funding_per_8h else np.zeros(0, dtype=np.int64)
    fund = Funding(pair, fts, np.full(len(fts), funding_per_8h), np.full(len(fts), 8, dtype=np.int8))
    return build_dayframe(Candles(pair, "15m", ts, c, c, c, c, np.ones(len(c))), fund, first_day=first)


def walk(n, seed, drift=None, sigma=0.01):
    rng = np.random.default_rng(seed)
    mu = np.zeros(n) if drift is None else drift
    return 100.0 * np.exp(np.cumsum(mu + rng.normal(0.0, sigma, n)))


def inputs_for(frames, sig, sizing, start, end):
    signals = {p: compute_signals(f, sig, sizing) for p, f in frames.items()}
    return screen_inputs(frames, signals, start=start, end=end), signals


def test_screen_inputs_alignment():
    a = frame("A", walk(30, 1), first="2020-01-01")
    b = frame("B", walk(28, 2), first="2020-01-03")
    inp, sig = inputs_for({"A": a, "B": b}, SHORT, SIZE5, "2020-01-08", "2020-01-25")
    assert inp.pairs == ("A", "B")
    assert inp.day_ms[0] == date_ms("2020-01-07") and inp.day_ms[-1] == date_ms("2020-01-24")
    assert len(inp.day_ms) == 18
    for i, (f, s) in enumerate(((a, sig["A"]), (b, sig["B"]))):
        idx = (inp.day_ms - f.day_ms[0]) // DAY_MS
        np.testing.assert_allclose(inp.ret_next[i], f.c[idx + 1] / f.c[idx] - 1.0, rtol=1e-15)
        np.testing.assert_array_equal(inp.active[i], s.valid[idx])
        np.testing.assert_array_equal(inp.sigma[i], s.sigma[idx])
    # valid from frame index 8: A (first 01-01) from 01-09 = decision day 2; B (first 01-03) from 01-11 = day 4
    assert not inp.active[0, :2].any() and inp.active[0, 2:].all()
    assert not inp.active[1, :4].any() and inp.active[1, 4:].all()


def test_portfolio_returns_hand():
    inp = ScreenInputs(
        pairs=("A", "B"), day_ms=np.arange(3, dtype=np.int64) * DAY_MS,
        ret_next=np.array([[0.01, 0.02, -0.01], [0.03, np.nan, 0.05]]),
        exposure=np.ones((2, 3)), sigma=np.ones((2, 3)),
        active=np.array([[True, True, False], [True, False, False]]),
    )
    w = np.array([[1.0, 0.5, 9.0], [2.0, 9.0, 9.0]])
    np.testing.assert_allclose(portfolio_returns(w, inp), [(0.01 + 0.06) / 2, 0.01, 0.0], rtol=1e-15)
    np.testing.assert_allclose(portfolio_returns(np.stack([w, 2 * w]), inp)[1], [0.07, 0.02, 0.0], rtol=1e-15)


def test_delisted_pair_leaves_active_set():
    a = frame("A", walk(60, 3))
    b = frame("B", walk(55, 4))  # last day 2020-02-24
    inp, sig = inputs_for({"A": a, "B": b}, SHORT, SIZE5, "2020-01-20", "2020-02-29")
    last_b = date_ms("2020-02-24")
    assert not inp.active[1][inp.day_ms >= last_b].any()
    assert inp.active[1][(inp.day_ms < last_b) & (inp.day_ms >= date_ms("2020-01-19"))].all()
    w = target_weight(inp.exposure, inp.sigma, SIZE5)
    port = portfolio_returns(w, inp)
    after = inp.day_ms >= last_b
    np.testing.assert_allclose(port[after], (w[0] * inp.ret_next[0])[after], rtol=1e-15)


def test_screen_ignores_funding():
    closes = walk(80, 5)
    i0, _ = inputs_for({"A": frame("A", closes)}, SHORT, SIZE5, "2020-01-20", "2020-03-15")
    i1, _ = inputs_for({"A": frame("A", closes, funding_per_8h=1e-3)}, SHORT, SIZE5, "2020-01-20", "2020-03-15")
    np.testing.assert_array_equal(i0.ret_next, i1.ret_next)
    np.testing.assert_array_equal(i0.exposure, i1.exposure)


def test_shift_offsets():
    k = shift_offsets(300, 20_000, 60, np.random.default_rng(0))
    assert k.min() == 60 and k.max() == 240
    with pytest.raises(ValueError):
        shift_offsets(100, 5, 60, np.random.default_rng(0))


def _pair_inputs():
    frames = {"A": frame("A", walk(400, 6, sigma=0.03)), "B": frame("B", walk(400, 7, sigma=0.03)),
              "C": frame("C", walk(300, 8, sigma=0.03))}  # C ends early
    inp, _ = inputs_for(frames, SHORT, SIZE5, "2020-01-20", "2021-01-31")
    return inp


def test_shift_preserves_time_in_market_and_changes():
    inp = _pair_inputs()
    k = shift_offsets(len(inp.day_ms), 50, 60, np.random.default_rng(1))
    e2 = shifted_exposure(inp, k)
    for i in range(len(inp.pairs)):
        e = inp.exposure[i][inp.active[i]]
        assert len(np.unique(e)) > 3
        for m in range(len(k)):
            s = e2[m, i][inp.active[i]]
            np.testing.assert_array_equal(np.sort(s), np.sort(e))
            assert (s != np.roll(s, 1)).sum() == (e != np.roll(e, 1)).sum()


def test_shift_stays_inside_active_days():
    inp = _pair_inputs()
    k = np.array([60, 200, 333])
    e2 = shifted_exposure(inp, k)
    for i in range(len(inp.pairs)):
        np.testing.assert_array_equal(np.isnan(e2[:, i]), np.broadcast_to(~inp.active[i], e2[:, i].shape))
        e = inp.exposure[i][inp.active[i]]
        n_i = len(e)
        for m, km in enumerate(k):
            np.testing.assert_array_equal(e2[m, i][inp.active[i]], e[(np.arange(n_i) + km) % n_i])
    assert inp.active[2].sum() < inp.active[0].sum()


def test_one_shared_offset():
    closes = walk(400, 9, sigma=0.03)
    frames = {"A": frame("A", closes), "B": frame("B", closes)}
    inp, _ = inputs_for(frames, SHORT, SIZE5, "2020-01-20", "2021-01-31")
    e2 = shifted_exposure(inp, shift_offsets(len(inp.day_ms), 30, 60, np.random.default_rng(2)))
    np.testing.assert_array_equal(e2[:, 0], e2[:, 1])


def _run(frames, sig, sizing, start, end, *, paths, seed):
    inp, _ = inputs_for(frames, sig, sizing, start, end)
    return run_screen(inp, sizing, paths=paths, margin=60, rng=np.random.default_rng(seed), stop_if_p_at_least=0.10)


def test_trending_beats_shifted():
    n = 900
    # aperiodic regimes: a fixed period would let a shift by a multiple of it copy the real timing
    lens = np.random.default_rng(100).integers(60, 200, 20)
    regime = np.repeat(np.resize([0.003, -0.003], 20), lens)[:n]
    frames = {p: frame(p, walk(n, 10 + j, drift=regime)) for j, p in enumerate("ABCD")}
    r = _run(frames, PRIMARY, SIZE60, "2020-07-01", "2022-06-15", paths=199, seed=3)
    assert r["p_screen"] < 0.05 and r["decision"] == "go"
    assert r["observed_sharpe"] > r["paths"]["p95"]


def test_random_walk_p_roughly_uniform():
    ps = []
    for seed in range(30):
        frames = {p: frame(p, walk(700, 1000 * seed + j)) for j, p in enumerate("ABCD")}
        ps.append(_run(frames, SHORT, SIZE5, "2020-02-01", "2021-11-30", paths=99, seed=seed)["p_screen"])
    ps = np.array(ps)
    assert 0.3 <= ps.mean() <= 0.7
    assert (ps < 0.1).sum() <= 9


def test_run_screen_deterministic_and_p_formula():
    frames = {p: frame(p, walk(500, 20 + j)) for j, p in enumerate("AB")}
    inp, _ = inputs_for(frames, SHORT, SIZE5, "2020-02-01", "2021-05-01")
    a = run_screen(inp, SIZE5, paths=150, margin=60, rng=np.random.default_rng(4), stop_if_p_at_least=0.10)
    b = run_screen(inp, SIZE5, paths=150, margin=60, rng=np.random.default_rng(4), stop_if_p_at_least=0.10)
    assert a == b
    assert a["paths"]["n"] == 150 and a["n_pairs"] == 2 and a["n_days"] == len(inp.day_ms)
    k = round(a["p_screen"] * 151) - 1
    assert a["p_screen"] == (1 + k) / 151
    assert a["decision"] == ("stop" if a["p_screen"] >= 0.10 else "go")
    assert set(a) == {"n_days", "n_pairs", "observed_sharpe", "paths", "p_screen", "stop_if_p_at_least", "decision",
                      "bh_sharpe", "mean_active_pairs"}
    assert set(a["paths"]) == {"n", "mean", "std", "p05", "p50", "p95"}


def test_run_screen_chunking_does_not_change_result():
    frames = {p: frame(p, walk(500, 30 + j)) for j, p in enumerate("AB")}
    inp, _ = inputs_for(frames, SHORT, SIZE5, "2020-02-01", "2021-05-01")
    a = run_screen(inp, SIZE5, paths=120, margin=60, rng=np.random.default_rng(5), stop_if_p_at_least=0.1, chunk=7)
    b = run_screen(inp, SIZE5, paths=120, margin=60, rng=np.random.default_rng(5), stop_if_p_at_least=0.1, chunk=120)
    assert a == b


def test_nan_sharpe_refuses():
    falling = 100.0 * np.exp(-0.01 * np.arange(300))  # long/flat exposure 0 throughout: zero-variance returns
    frames = {p: frame(p, falling) for p in "AB"}
    inp, _ = inputs_for(frames, SHORT, SIZE5, "2020-02-01", "2020-10-01")
    with pytest.raises(ValueError, match="not finite"):
        run_screen(inp, SIZE5, paths=20, margin=60, rng=np.random.default_rng(0), stop_if_p_at_least=0.1)


def test_write_screen_refuses_nan_and_leaves_no_file(tmp_path):
    from perpbt.trend.screen import screen_path, write_screen

    with pytest.raises(ValueError):
        write_screen({"decision": "go", "p_screen": float("nan")}, tmp_path)
    assert not screen_path(tmp_path).exists()
