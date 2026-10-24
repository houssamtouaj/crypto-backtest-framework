"""The simplified net screen (carry spec Phase 2 §2.3-§2.4) and its V2 statistic (Phase 4 §4.2)."""
import json

import numpy as np
import pytest

from perpbt.carry.config import CarrySignalConfig
from perpbt.carry.frames import build_pairframe
from perpbt.carry.screen import (
    ScreenInputs, daily_hurdle, portfolio_returns, run_screen, screen_inputs, sleeve_returns,
    v2_stats, write_screen,
)
from perpbt.carry.signals import compute_signals
from perpbt.config import from_dict
from perpbt.data.store import DAY_MS, Candles, Funding, date_ms
from perpbt.stats.bootstrap import block_indices
from perpbt.stats.seeds import rng_for

X = 2.0 / 3.0  # L = 2
PRIMARY = from_dict(CarrySignalConfig, {"lookback_days": 7, "entry_annual": 0.10, "exit_annual": 0.0})
ALWAYS = from_dict(CarrySignalConfig, {"lookback_days": 7, "entry_annual": -1.0e9, "exit_annual": -1.0e9})


def inputs_of(r_spot, r_perp, funding, live=None) -> ScreenInputs:
    r_spot, r_perp, funding = (np.asarray(a, dtype=np.float64) for a in (r_spot, r_perp, funding))
    live = np.ones(r_spot.shape, dtype=bool) if live is None else np.asarray(live, dtype=bool)
    index = np.where(live, np.arange(r_spot.shape[1])[None, :], -1)
    nan = lambda a: np.where(live, a, np.nan)  # noqa: E731
    pairs = tuple(f"P{i}USDT" for i in range(r_spot.shape[0]))
    day_ms = date_ms("2021-01-01") + DAY_MS * np.arange(r_spot.shape[1], dtype=np.int64)
    return ScreenInputs(pairs, day_ms, index, nan(r_spot), nan(r_perp), nan(funding), live)


# two pairs, three decision days: A on, on, off; B off, on, on
HAND = inputs_of(
    r_spot=[[0.01, -0.02, 0.0], [0.05, 0.0, 0.01]],
    r_perp=[[0.012, -0.018, 0.001], [0.05, 0.001, 0.0]],
    funding=[[0.0003, -0.0001, 0.0002], [0.001, 0.0003, 0.0003]],
)
HAND_ON = np.array([[True, True, False], [False, True, True]])
HAND_COST = np.array([0.002, 0.0013])


def test_hand_three_days_two_pairs():
    s = sleeve_returns(HAND_ON, HAND, leverage=2.0, cost=HAND_COST)
    expected = np.array([
        # A: entry (spot − perp + funding, minus the round), hold (a negative rate is paid), exit (cost only)
        [X * (0.01 - 0.012 + 0.0003) - X * 0.002, X * (-0.02 + 0.018 - 0.0001), -X * 0.002],
        # B: off (the 0.001 funding day is not earned), entry, hold
        [0.0, X * (0.0 - 0.001 + 0.0003) - X * 0.0013, X * (0.01 - 0.0 + 0.0003)],
    ])
    np.testing.assert_allclose(s, expected, rtol=0, atol=1e-15)
    np.testing.assert_allclose(portfolio_returns(s, HAND), expected.mean(axis=0), rtol=0, atol=1e-15)


def test_costs_on_transitions_only():
    flat = inputs_of(np.zeros((1, 6)), np.zeros((1, 6)), np.zeros((1, 6)))
    on = np.array([[True, True, False, False, True, True]])  # on from the first decision: an entry there
    s = sleeve_returns(on, flat, leverage=2.0, cost=np.array([0.01]))
    np.testing.assert_allclose(s, [[-X * 0.01, 0, -X * 0.01, 0, -X * 0.01, 0]], atol=1e-15)
    lev1 = sleeve_returns(on, flat, leverage=1.0, cost=np.array([0.01]))
    np.testing.assert_allclose(lev1[0, 0], -0.5 * 0.01)


def test_off_sleeve_contributes_zero():
    on = np.array([[True, True, True], [False, False, False]])
    s = sleeve_returns(on, HAND, leverage=2.0, cost=np.zeros(2))
    assert (s[1] == 0).all()
    r = portfolio_returns(s, HAND)
    np.testing.assert_allclose(r, s[0] / 2)  # the off sleeve still counts in E / N_live


def test_delisted_pair_leaves_mean():
    live = np.array([[True, True, True], [True, True, False]])
    inp = inputs_of(HAND.r_spot, HAND.r_perp, HAND.funding, live)
    on = np.array([[True, True, True], [True, True, True]])
    s = sleeve_returns(on, inp, leverage=2.0, cost=np.zeros(2))
    assert np.isnan(s[1, 2])
    r = portfolio_returns(s, inp)
    np.testing.assert_allclose(r[:2], s[:, :2].mean(axis=0))
    np.testing.assert_allclose(r[2], s[0, 2])  # the mean over the remaining pair
    none = inputs_of(HAND.r_spot, HAND.r_perp, HAND.funding, np.zeros((2, 3), dtype=bool))
    assert (portfolio_returns(sleeve_returns(on, none, leverage=2.0, cost=np.zeros(2)), none) == 0).all()


# --- on synthetic PairFrames -------------------------------------------------------------------------

FIRST = "2020-01-01"
NDAYS = 500


def pairframe(pair: str, annual_funding, *, seed: int, days: int = NDAYS, basis: float = 0.0, reseed_after=None):
    """One 15m candle per day at 23:45 (perp = spot × (1 + basis noise)), funding every 8 h.

    ``annual_funding`` is a constant or a per-day array of annualised rates. ``reseed_after = (k, seed)`` redraws
    every price after day ``k`` (a perturbation that days ``<= k`` cannot see).
    """
    rng = np.random.default_rng(seed)
    c = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.03, days)))
    if reseed_after is not None:
        k, s2 = reseed_after
        c[k + 1:] = c[k] * np.exp(np.cumsum(np.random.default_rng(s2).normal(0.0, 0.03, days - k - 1)))
    ts = date_ms(FIRST) + DAY_MS * np.arange(days, dtype=np.int64) + DAY_MS - 900_000
    pc = c * (1.0 + basis * rng.normal(size=days))
    one = np.ones(days)
    perp = Candles(pair, "15m", ts, pc, pc, pc, pc, one)
    spot = Candles(pair, "15m", ts, c, c, c, c, one)
    fts = date_ms(FIRST) + 8 * 3_600_000 * np.arange(3 * days, dtype=np.int64)
    rate = np.repeat(np.broadcast_to(np.asarray(annual_funding, dtype=np.float64), (days,)), 3) / 365 / 3
    return build_pairframe(perp, Funding(pair, fts, rate, np.full(len(fts), 8, dtype=np.int8)), spot,
                           first_day=FIRST)


def test_screen_inputs_alignment():
    a = pairframe("AAAUSDT", 0.001 * np.arange(NDAYS), seed=1)  # every day's funding differs
    b = pairframe("BBBUSDT", 0.3, seed=2, days=200)  # ends early: leaves after its last day
    inp = screen_inputs({"AAAUSDT": a, "BBBUSDT": b}, start="2020-03-01", end="2021-01-31")
    d0 = (date_ms("2020-02-29") - date_ms(FIRST)) // DAY_MS
    assert inp.day_ms[0] == date_ms("2020-02-29") and inp.day_ms[-1] == date_ms("2021-01-30")
    assert inp.index[0, 0] == d0 and inp.live[0].all()
    np.testing.assert_allclose(inp.r_perp[0, 0], a.perp.c[d0 + 1] / a.perp.c[d0] - 1)
    np.testing.assert_allclose(inp.r_spot[0, 5], a.spot.c[d0 + 6] / a.spot.c[d0 + 5] - 1)
    k = a.perp.events(d0 + 1)
    np.testing.assert_allclose(inp.funding[0, 0], a.perp.funding_rate[k].sum())
    assert inp.funding[0, 0] == pytest.approx(0.001 * (d0 + 1) / 365)  # day d+1's, not day d's
    np.testing.assert_allclose(inp.funding[0], 0.001 * (d0 + 1 + np.arange(inp.live.shape[1])) / 365, rtol=1e-12)
    rows = inp.align({"AAAUSDT": np.arange(len(a)), "BBBUSDT": np.arange(len(b))}, fill=-1)
    np.testing.assert_array_equal(rows, inp.index)  # align picks the decision day's own row
    last_b = 200 - 2 - d0  # decision on b's second-to-last day earns its last day
    assert inp.live[1, : last_b + 1].all() and not inp.live[1, last_b + 1:].any()
    assert np.isnan(inp.r_perp[1, last_b + 1:]).all()
    np.testing.assert_array_equal(inp.align({"AAAUSDT": np.arange(len(a)), "BBBUSDT": np.arange(len(b))})[1, -1], 0)


def test_perp_funding_sums_each_days_events():
    # the screen's F_{d+1} is perp.funding: the DayFrame's per-day sum of the same events
    a = pairframe("AAAUSDT", 0.01 * np.arange(10), seed=1, days=10)
    for k in range(10):
        assert a.perp.funding[k] == pytest.approx(a.perp.funding_rate[a.perp.events(k)].sum(), abs=1e-18)


def test_funding_step_first_decision_entry_and_first_funding():
    # funding steps from 0 to 50%/yr on day S; with W = 7 the time-weighted f̄ is (days high in window) / 7 × 50%,
    # first above 10% on day S + 1 (2/7 × 50% = 14.3%); that decision earns day S + 2 and pays the entry there
    S = 100
    annual = np.where(np.arange(NDAYS) >= S, 0.5, 0.0)
    pf = pairframe("AAAUSDT", annual, seed=3, basis=1e-3)
    inp = screen_inputs({"AAAUSDT": pf}, start="2020-03-01", end="2020-12-31")
    on = inp.align({"AAAUSDT": compute_signals(pf, PRIMARY).on})
    first = int(np.argmax(on[0]))
    assert on[0].any() and inp.index[0, first] == S + 1 and not on[0, :first].any()
    s = sleeve_returns(on, inp, leverage=2.0, cost=np.array([0.002]))
    j = S + 1
    r = pf.spot.c[j + 1] / pf.spot.c[j] - pf.perp.c[j + 1] / pf.perp.c[j] + 0.5 / 365
    assert s[0, first] == pytest.approx(X * r - X * 0.002, abs=1e-15)
    assert (s[0, :first] == 0).all()


def test_decisions_and_returns_causal():
    # redrawing every price and funding after day K leaves the states of decision rows <= K and the returns of
    # rows <= K - 1 (which earn days <= K) unchanged
    K = 200
    annual = 0.3 * (1 + np.sin(np.arange(NDAYS) / 9.0))
    later = annual.copy()
    later[K + 1:] = 0.7 - annual[K + 1:]
    base = pairframe("AAAUSDT", annual, seed=4, basis=1e-3)
    moved = pairframe("AAAUSDT", later, seed=4, basis=1e-3, reseed_after=(K, 99))
    out = []
    for pf in (base, moved):
        inp = screen_inputs({"AAAUSDT": pf}, start="2020-03-01", end="2021-03-31")
        on = inp.align({"AAAUSDT": compute_signals(pf, PRIMARY).on})
        out.append((inp.index[0], on[0], sleeve_returns(on, inp, leverage=2.0, cost=np.array([0.002]))[0]))
    (idx, on0, s0), (_, on1, s1) = out
    np.testing.assert_array_equal(on0[idx <= K], on1[idx <= K])
    np.testing.assert_array_equal(s0[idx <= K - 1], s1[idx <= K - 1])
    assert not np.array_equal(s0[idx >= K], s1[idx >= K])  # the perturbation does reach later rows


def _screen(frames, B=2000):
    inp = screen_inputs(frames, start="2020-03-01", end="2021-03-31")
    states = {"primary": inp.align({p: compute_signals(pf, PRIMARY).on for p, pf in frames.items()}),
              "always_on": inp.align({p: compute_signals(pf, ALWAYS).on for p, pf in frames.items()})}
    return run_screen(inp, states, leverage={"primary": 2.0, "always_on": 2.0}, cost=np.full(len(frames), 0.002),
                      hurdle=0.05, alpha=0.05, B=B, block_len=30, rng=lambda p: rng_for(7, "h", p),
                      stop_if_p_at_least=0.10)


def test_constant_30pct_funding_passes_v2():
    frames = {p: pairframe(p, 0.30, seed=i, basis=1e-4) for i, p in enumerate(("AAAUSDT", "BBBUSDT"))}
    r = _screen(frames)
    v2 = r["rows"]["primary"]["v2"]
    assert r["decision"] == "go" and v2["passes"] and v2["reading"] == "above_hurdle"
    assert r["p_screen"] < 0.01 and v2["ann_return"] == pytest.approx(X * 0.30, abs=0.01)
    assert r["rows"]["primary"]["share_on"] == 1.0 and r["rows"]["primary"]["switches"] == 2  # one entry per pair


def test_constant_2pct_funding_fails_v2():
    frames = {p: pairframe(p, 0.02, seed=i, basis=1e-4) for i, p in enumerate(("AAAUSDT", "BBBUSDT"))}
    r = _screen(frames)
    assert r["decision"] == "stop" and r["p_screen"] == 1.0  # never on: every replicate is the hurdle's −5%
    assert r["rows"]["primary"]["share_on"] == 0.0
    assert r["rows"]["primary"]["v1"]["sharpe"] is None  # a zero series has no Sharpe; reported as null
    a = r["rows"]["always_on"]["v2"]
    assert a["ann_return"] == pytest.approx(X * 0.02, abs=0.005) and a["reading"] == "below_hurdle"


def test_v2_p_formula():
    ret = np.random.default_rng(5).normal(2e-4, 1e-3, 400)
    got = v2_stats(ret, hurdle=0.05, alpha=0.05, B=999, block_len=30, rng=rng_for(1, "x", "screen"))
    e = ret - daily_hurdle(0.05)
    k = np.concatenate(list(block_indices(400, 999, rng_for(1, "x", "screen"), 30)))
    reps = 365 * e[k].mean(axis=1)
    assert got["p"] == (1 + np.sum(reps <= 0)) / 1000
    assert got["ann_excess"] == pytest.approx(365 * e.mean())
    assert got["ann_return"] == pytest.approx(365 * ret.mean())
    lo, hi = np.percentile(reps, [2.5, 97.5])
    assert got["ann_excess_ci"] == pytest.approx([lo, hi])
    assert got["ann_return_ci"] == pytest.approx([lo + 365 * daily_hurdle(0.05), hi + 365 * daily_hurdle(0.05)])
    assert got["passes"] == (got["p"] < 0.05)
    assert (1 + daily_hurdle(0.05)) ** 365 == pytest.approx(1.05)


def test_deterministic():
    frames = {p: pairframe(p, 0.12, seed=i, basis=1e-3) for i, p in enumerate(("AAAUSDT", "BBBUSDT"))}
    assert json.dumps(_screen(frames, B=500)) == json.dumps(_screen(frames, B=500))


def test_non_finite_refuses():
    bad = inputs_of([[0.01, np.nan]], [[0.0, 0.0]], [[0.0, 0.0]])
    states = {"primary": np.ones((1, 2), dtype=bool), "always_on": np.ones((1, 2), dtype=bool)}
    with pytest.raises(ValueError, match="not finite"):
        run_screen(bad, states, leverage={"primary": 2.0, "always_on": 2.0}, cost=np.zeros(1), hurdle=0.05,
                   alpha=0.05, B=50, block_len=30, rng=lambda p: rng_for(1, "h", p), stop_if_p_at_least=0.1)


def test_write_screen_refuses_existing_summary_on_stop(tmp_path):
    (tmp_path / "carry").mkdir()
    (tmp_path / "carry" / "summary.md").write_text("earlier", encoding="utf-8")
    frames = {p: pairframe(p, 0.02, seed=i, basis=1e-4) for i, p in enumerate(("AAAUSDT", "BBBUSDT"))}
    result = {**_screen(frames, B=100), "period": ["2020-03-01", "2021-03-31"], "pairs": ["AAAUSDT", "BBBUSDT"],
              "prereg_hash": "h", "computed_on": "2026-10-24", "git_commit": "abc"}
    assert result["decision"] == "stop"
    with pytest.raises(FileExistsError):
        write_screen(result, tmp_path)
    assert not (tmp_path / "carry" / "screen.json").exists()


def test_write_screen_refuses_nan_and_leaves_no_file(tmp_path):
    with pytest.raises(ValueError):
        write_screen({"decision": "go", "p_screen": float("nan")}, tmp_path)
    assert not (tmp_path / "carry" / "screen.json").exists()
