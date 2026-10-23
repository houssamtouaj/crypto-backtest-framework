"""The 12 ensemble members, exposure, vol forecast and target weights (trend spec Phase 1 §1.2-§1.6)."""
import warnings

import numpy as np
import pytest

from perpbt.config import from_dict
from perpbt.data.store import DAY_MS, Candles, Funding
from perpbt.indicators.daily import daily_bars
from perpbt.trend.config import MEMBERS, SignalConfig, SizingConfig
from perpbt.trend.daily import build_dayframe
from perpbt.trend.signals import (
    compute_signals, donchian, donchian_states, ma_filter, member_matrix, return_sign, target_weight, vol_forecast,
    warmup_days,
)
from tests.synthetic import T0_MS, assert_causal, random_walk

nan = np.nan


def arr(*xs):
    return np.asarray(xs, dtype=np.float64)


def same(a, b):
    np.testing.assert_array_equal(np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64))


def test_return_sign():
    c = arr(1, 2, 3, 3, 2)
    same(return_sign(c, 2), [nan, nan, 1, 1, 0])
    same(return_sign(c, 2, long_short=True), [nan, nan, 1, 1, -1])
    same(return_sign(arr(1, 2, 1), 2), [nan, nan, 0])
    same(return_sign(arr(1, 2, 1), 2, long_short=True), [nan, nan, 0])


def test_ma_filter():
    c = arr(1, 3, 2, 2)
    same(ma_filter(c, 2), [nan, 1, 0, 0])
    same(ma_filter(c, 2, long_short=True), [nan, 1, -1, 0])


def test_donchian_state_machine():
    c = arr(10, 10, 10, 10, 11, 12, 11.5, 10.5, 9, 9, 10.6)
    same(donchian(c, c, c, 4), [nan] * 4 + [1, 1, 1, 0, 0, 0, 0])
    same(donchian(c, c, c, 4, long_short=True), [nan] * 4 + [1, 1, 1, 0, -1, -1, 0])


def test_donchian_tie_does_not_enter():
    c = arr(10, 10, 10, 10, 10)
    same(donchian(c, c, c, 4), [nan] * 4 + [0])
    same(donchian(c, c, c, 4, long_short=True), [nan] * 4 + [0])


def test_donchian_uses_highs_and_lows():
    c = arr(10, 10, 10, 10, 10.5)
    h = arr(11, 10, 10, 10, 10.5)
    same(donchian(c, h, c, 4), [nan] * 4 + [0])
    # day 6's close 11.8 is above the prior two closes (11, 12) but below the prior two lows only with l3

    c2 = arr(10, 10, 10, 10, 11, 12, 11.8)
    l2 = arr(10, 10, 10, 10, 11, 11.9, 11.8)
    same(donchian(c2, c2, l2, 4), [nan] * 4 + [1, 1, 1])
    l3 = arr(10, 10, 10, 10, 11.85, 11.9, 11.8)
    same(donchian(c2, c2, l3, 4), [nan] * 4 + [1, 1, 0])


@pytest.mark.parametrize("seed", range(20))
def test_donchian_never_both_states(seed):
    bars = daily_bars(random_walk(400 * 96, seed=seed, start_ms=T0_MS, step_sigma=0.004))
    for n in (20, 40, 80, 160):
        long, short = donchian_states(bars.c, bars.h, bars.l, n)
        assert not (long & short).any()
        assert not long[:n].any() and not short[:n].any()
        ls = donchian(bars.c, bars.h, bars.l, n, long_short=True)
        assert set(np.unique(ls[n:])) <= {-1.0, 0.0, 1.0}
        assert np.isnan(ls[:n]).all()


# --- exposure, vol forecast, target weights, causality (§1.3-§1.6) -----------------------------

SIGNAL = from_dict(SignalConfig, {"members": list(MEMBERS), "lookbacks": [20, 40, 80, 160],
                                  "donchian_exit": "half", "direction": "long_flat"})
SIGNAL_LS = from_dict(SignalConfig, {**{k: getattr(SIGNAL, k) for k in ("members", "lookbacks", "donchian_exit")},
                                     "direction": "long_short"})
SIZING = from_dict(SizingConfig, {"target_vol": 0.30, "vol_window": 60, "max_weight": 2.0, "max_gross": 2.0})
FIRST = "2020-01-01"
H_MS = 3_600_000


def funding_of(events) -> Funding:
    ts = np.asarray([t for t, _ in events], dtype=np.int64)
    return Funding("TEST", ts, np.asarray([r for _, r in events], dtype=np.float64), np.full(len(ts), 8, dtype=np.int8))


def frame_from_closes(closes, *, funding=()):
    """One 15m candle per day at 23:45 with o = h = l = c."""
    c = np.asarray(closes, dtype=np.float64)
    ts = T0_MS + DAY_MS * np.arange(len(c), dtype=np.int64) + DAY_MS - 900_000
    return build_dayframe(Candles("TEST", "15m", ts, c, c, c, c, np.ones(len(c))), funding_of(funding), first_day=FIRST)


def walk_frame(days, seed=11):
    return build_dayframe(random_walk(days * 96, seed=seed, start_ms=T0_MS, step_sigma=0.004),
                          funding_of([]), first_day=FIRST)


def test_member_matrix_names_and_shape():
    names, m = member_matrix(walk_frame(200), SIGNAL)
    assert names[:4] == ("return_sign_20", "return_sign_40", "return_sign_80", "return_sign_160")
    assert names[-1] == "donchian_160" and len(names) == 12
    assert m.shape == (12, 200)


def test_exposure_ramp_and_flat():
    for sig in (SIGNAL, SIGNAL_LS):
        up = compute_signals(frame_from_closes(100.0 + np.arange(200)), sig, SIZING)
        assert (up.exposure[160:] == 1.0).all()
        flat = compute_signals(frame_from_closes(np.full(200, 100.0)), sig, SIZING)
        assert (flat.exposure[160:] == 0.0).all()


def test_exposure_steps_of_one_twelfth():
    f = walk_frame(400)
    for sig, lo in ((SIGNAL, 0), (SIGNAL_LS, -12)):
        s = compute_signals(f, sig, SIZING)
        k = s.exposure[s.valid] * 12
        np.testing.assert_allclose(k, np.round(k), atol=1e-12)
        assert k.min() >= lo and k.max() <= 12
        assert len(np.unique(np.round(k))) > 3  # the walk moves the ensemble


def test_warmup():
    f = walk_frame(300)
    s = compute_signals(f, SIGNAL, SIZING)
    assert not s.valid[:160].any() and s.valid[160:].all()
    assert np.isnan(s.exposure[159]) and np.isnan(s.weight[159]) and np.isfinite(s.weight[160])
    assert warmup_days(SIGNAL, SIZING) == 160
    sz = from_dict(SizingConfig, {"target_vol": 0.30, "vol_window": 200, "max_weight": 2.0, "max_gross": 2.0})
    s2 = compute_signals(f, SIGNAL, sz)
    assert int(np.argmax(s2.valid)) == 200 == warmup_days(SIGNAL, sz)


def test_vol_forecast():
    r = arr(nan, .01, -.02, .03, .00)
    got = vol_forecast(r, 3)
    a = np.std([.01, -.02, .03], ddof=1) * np.sqrt(365)
    b = np.std([-.02, .03, .00], ddof=1) * np.sqrt(365)
    np.testing.assert_allclose(got, [nan, nan, nan, a, b], rtol=1e-15)


def test_target_weight():
    e = arr(0.5, 0.5, -1.0, nan, 0.5)
    s = arr(0.6, 0.1, 0.3, 0.3, nan)
    np.testing.assert_allclose(target_weight(e, s, SIZING), [0.25, 1.0, -1.0, nan, nan], rtol=1e-15)


def test_zero_vol_hits_cap():
    f = frame_from_closes(np.full(250, 100.0))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        s = compute_signals(f, SIGNAL, SIZING)
        w = target_weight(np.full(250, 0.5), s.sigma, SIZING)
    assert (s.sigma[60:] == 0).all()
    assert (w[60:] == 1.0).all()
    assert (s.weight[160:] == 0.0).all()  # flat price: every member ties, e = 0


def test_ret_includes_funding_in_sigma():
    closes = 100.0 * np.exp(np.cumsum(np.random.default_rng(4).normal(0, 0.03, 120)))
    every_8h = T0_MS + 8 * H_MS * np.arange(120 * 3)
    base = compute_signals(frame_from_closes(closes), SIGNAL, SIZING).sigma
    flat = compute_signals(frame_from_closes(closes, funding=[(t, 1e-4) for t in every_8h]), SIGNAL, SIZING).sigma
    np.testing.assert_allclose(flat[60:], base[60:], rtol=1e-9)
    alt = [(t, 1e-3 * (-1) ** (k // 3)) for k, t in enumerate(every_8h)]
    moved = compute_signals(frame_from_closes(closes, funding=alt), SIGNAL, SIZING).sigma
    assert not np.allclose(moved[60:], base[60:], rtol=1e-6)


FUND = [(T0_MS + 8 * H_MS * k, 1e-4 * np.sin(k)) for k in range(260 * 3)]


def _outputs(sig):
    def fn(candles: Candles):
        f = build_dayframe(candles, funding_of(FUND), first_day=FIRST)
        s = compute_signals(f, sig, SIZING)
        values = np.column_stack([s.members.T, s.exposure, s.sigma, s.weight])
        confirmed_at = np.searchsorted(candles.ts, f.day_ms + DAY_MS) - 1
        return values, confirmed_at
    fn.__name__ = f"signals_{sig.direction}"
    return fn


WALK = random_walk(260 * 96, seed=21, start_ms=T0_MS, step_sigma=0.004)


@pytest.mark.parametrize("sig", [SIGNAL, SIGNAL_LS])
def test_signals_causal_day_end(sig):
    cuts = [(d + 1) * 96 - 1 for d in (159, 160, 200, 259)]
    assert_causal(_outputs(sig), WALK, cuts=cuts, seeds=[1, 2], truncate=True)


@pytest.mark.parametrize("sig", [SIGNAL, SIGNAL_LS])
def test_signals_causal_mid_day(sig):
    cuts = [d * 96 + k for d in (170, 230) for k in (37, 80)]
    assert_causal(_outputs(sig), WALK, cuts=cuts, seeds=[1, 2], truncate=False)


def test_funding_after_cut_changes_nothing_before():
    f0 = build_dayframe(WALK, funding_of(FUND), first_day=FIRST)
    extra = sorted(FUND + [(T0_MS + 200 * DAY_MS + 3 * H_MS, 5e-3)])
    f1 = build_dayframe(WALK, funding_of(extra), first_day=FIRST)
    a, b = compute_signals(f0, SIGNAL, SIZING), compute_signals(f1, SIGNAL, SIZING)
    for name in ("exposure", "sigma", "weight"):
        np.testing.assert_array_equal(getattr(a, name)[:200], getattr(b, name)[:200])
    np.testing.assert_array_equal(a.members[:, :200], b.members[:, :200])
    assert a.sigma[200] != b.sigma[200]
