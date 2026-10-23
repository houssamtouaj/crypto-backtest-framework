"""Trailing funding and the on/off state (carry spec Phase 1 §1.2-§1.4)."""
import numpy as np
import pytest

from perpbt.carry.config import CarrySignalConfig
from perpbt.carry.frames import build_pairframe
from perpbt.carry.signals import CarrySignals, compute_signals, on_off, trailing_funding
from perpbt.config import from_dict
from perpbt.data.store import DAY_MS, Candles, Funding
from tests.synthetic import T0_MS, assert_causal, random_walk

nan = np.nan
H_MS = 3_600_000
FIRST = "2020-01-01"
PREREG = from_dict(CarrySignalConfig, {"lookback_days": 7, "entry_annual": 0.10, "exit_annual": 0.0})


def events(*days):
    """``ptr, rate, interval_h`` from per-day lists of ``(rate, interval_h)``."""
    counts = [len(d) for d in days]
    ptr = np.concatenate(([0], np.cumsum(counts))).astype(np.int64)
    flat = [e for d in days for e in d]
    rate = np.asarray([r for r, _ in flat], dtype=np.float64)
    interval = np.asarray([i for _, i in flat], dtype=np.int8)
    return ptr, rate, interval


def eight_h(rate):
    return [(rate, 8)] * 3


def same(a, b):
    np.testing.assert_array_equal(np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64))


# --- trailing funding (§1.2) ----------------------------------------------------------------------


def test_trailing_mean_annualised_and_warmup():
    f = trailing_funding(*events(eight_h(1e-4), eight_h(2e-4), eight_h(0.0)), 2)
    assert np.isnan(f[0])
    np.testing.assert_allclose(f[1:], [1.5e-4 * 3 * 365, 1e-4 * 3 * 365], rtol=1e-14)
    same(trailing_funding(*events(eight_h(1e-4)), 1), [1e-4 * 3 * 365])
    assert np.isnan(trailing_funding(*events(eight_h(1e-4), eight_h(1e-4)), 3)).all()


def test_interval_change_mid_series():
    # 1e-4 per 8 h and 5e-5 per 4 h are the same per-day rate: f̄ stays 0.1095/yr across the change
    days = [eight_h(1e-4)] * 3 + [[(1e-4, 8), (1e-4, 8), (5e-5, 4), (5e-5, 4)]] + [[(5e-5, 4)] * 6] * 3
    f = trailing_funding(*events(*days), 3)
    assert np.isnan(f[:2]).all()
    np.testing.assert_allclose(f[2:], 0.1095, rtol=1e-12)
    # the same rate per event at 4 h is twice the per-day rate
    g = trailing_funding(*events(*([[(1e-4, 4)] * 6] * 3)), 3)
    np.testing.assert_allclose(g[2:], 0.219, rtol=1e-12)


def test_window_without_events_is_nan_and_off():
    f = trailing_funding(*events(eight_h(5e-4), [], [], eight_h(5e-4)), 2)
    assert np.isnan(f[0]) and np.isnan(f[2])
    np.testing.assert_allclose(f[[1, 3]], 5e-4 * 3 * 365, rtol=1e-14)  # the mean over the events present
    same(on_off(f, 0.10, 0.0), [False, True, False, True])


def test_trailing_funding_validates():
    ptr, rate, interval = events(eight_h(1e-4))
    with pytest.raises(ValueError):
        trailing_funding(ptr, rate, interval, 0)
    with pytest.raises(ValueError):
        trailing_funding(ptr, rate[:2], interval, 1)


# --- the on/off state (§1.3) ----------------------------------------------------------------------


def test_entry_exit_hysteresis_and_ties():
    f = np.array([nan, 0.05, 0.11, 0.05, 0.0, -0.01, 0.05, 0.10, 0.1000001])
    #            warm  band  enter band  =exit exit  band  =entry enter
    same(on_off(f, 0.10, 0.0), [False, False, True, True, True, False, False, False, True])


def test_nan_mid_series_turns_off():
    same(on_off(np.array([0.2, nan, 0.05, 0.2]), 0.10, 0.0), [True, False, False, True])


def test_always_on_thresholds():
    same(on_off(np.array([nan, nan, 0.0, -5.0, 3.0]), -1.0e9, -1.0e9), [False, False, True, True, True])


def test_on_off_rejects_exit_above_entry():
    with pytest.raises(ValueError):
        on_off(np.array([0.0]), 0.0, 0.1)


# --- on a PairFrame --------------------------------------------------------------------------------

DAYS = 60
FUND = [(T0_MS + 8 * H_MS * k, 1e-4 * (0.5 + np.sin(k / 15.0))) for k in range(DAYS * 3)]  # f̄ crosses 10% and 0


def funding_of(evts) -> Funding:
    ts = np.asarray([t for t, _ in evts], dtype=np.int64)
    return Funding("TEST", ts, np.asarray([r for _, r in evts], dtype=np.float64), np.full(len(ts), 8, dtype=np.int8))


PERP = random_walk(DAYS * 96, seed=21, start_ms=T0_MS, step_sigma=0.004)
SPOT = random_walk(DAYS * 96, seed=22, start_ms=T0_MS, step_sigma=0.004)


def test_compute_signals_on_pairframe():
    pf = build_pairframe(PERP, funding_of(FUND), SPOT, first_day=FIRST)
    s = compute_signals(pf, PREREG)
    assert isinstance(s, CarrySignals) and s.on.dtype == bool and len(s.fbar) == len(s.on) == len(pf)
    same(s.fbar, trailing_funding(pf.perp.funding_ptr, pf.perp.funding_rate, pf.funding_interval_h, 7))
    same(s.on, on_off(s.fbar, 0.10, 0.0))
    assert np.isnan(s.fbar[:6]).all() and np.isfinite(s.fbar[6:]).all()
    assert s.on.any() and not s.on.all()  # the fixture crosses both thresholds
    for arr in (s.fbar, s.on):
        with pytest.raises(ValueError):
            arr[0] = 0


# --- causality (§1.4): nothing after a cut moves f̄, on or basis at days ending at or before it ----


def _outputs(leg):
    def fn(candles: Candles):
        perp, spot = (candles, SPOT) if leg == "perp" else (PERP, candles)
        pf = build_pairframe(perp, funding_of(FUND), spot, first_day=FIRST)
        s = compute_signals(pf, PREREG)
        values = np.column_stack([s.fbar, s.on, pf.basis])
        confirmed_at = np.searchsorted(candles.ts, pf.day_ms + DAY_MS) - 1
        return values, confirmed_at
    fn.__name__ = f"carry_signals_{leg}"
    return fn


@pytest.mark.parametrize("leg", ["perp", "spot"])
def test_signals_causal_day_end(leg):
    cuts = [(d + 1) * 96 - 1 for d in (5, 6, 15, 27, DAYS - 1)]
    assert_causal(_outputs(leg), PERP if leg == "perp" else SPOT, cuts=cuts, seeds=[1, 2], truncate=True)


@pytest.mark.parametrize("leg", ["perp", "spot"])
def test_signals_causal_mid_day(leg):
    cuts = [d * 96 + k for d in (8, 21) for k in (1, 37, 80)]
    assert_causal(_outputs(leg), PERP if leg == "perp" else SPOT, cuts=cuts, seeds=[1, 2], truncate=False)


@pytest.mark.parametrize("cut", [T0_MS + 12 * DAY_MS - 1, T0_MS + 12 * DAY_MS, T0_MS + 20 * DAY_MS + 9 * H_MS])
def test_funding_after_cut_changes_nothing_before(cut):
    base = build_pairframe(PERP, funding_of(FUND), SPOT, first_day=FIRST)
    rng = np.random.default_rng(5)
    moved = [(t, r if t <= cut else r + rng.normal(0, 1e-3)) for t, r in FUND]
    moved = sorted(moved + [(cut + 60_000, 5e-3)])  # and one extra event right after the cut
    pf = build_pairframe(PERP, funding_of(moved), SPOT, first_day=FIRST)
    a, b = compute_signals(base, PREREG), compute_signals(pf, PREREG)
    done = int(np.sum(base.day_ms + DAY_MS <= cut + 1))  # days whose end is at or before the cut
    for x, y in ((a.fbar, b.fbar), (a.on, b.on), (base.basis, pf.basis)):
        same(x[:done], y[:done])
    assert not np.array_equal(a.fbar[done:], b.fbar[done:], equal_nan=True)
