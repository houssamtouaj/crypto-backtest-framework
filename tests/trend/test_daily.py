"""DayFrame: per-pair calendar-day arrays from 15m candles and funding (trend spec Phase 1 §1.1)."""
import logging

import numpy as np
import pandas as pd
import pytest

from perpbt.data.store import DAY_MS, CandleStore, Candles, Funding, FundingStore, HoldoutAccessError, date_ms
from perpbt.stats.buyhold import bh_daily
from perpbt.trend.daily import FILL_OFFSETS_MIN, MAX_FILLED_RUN, build_dayframe, first_day, load_dayframe
from tests.synthetic import STEP_15M_MS, T0_MS, random_walk

PER_DAY = 96
FIRST = "2020-01-01"  # = T0_MS
H_MS = 3_600_000


def keep(candles: Candles, mask: np.ndarray) -> Candles:
    return Candles(candles.pair, candles.tf, candles.ts[mask], candles.o[mask], candles.h[mask],
                   candles.l[mask], candles.c[mask], candles.v[mask])


def walk(days: int, *, seed: int = 7, start_ms: int = T0_MS, pair: str = "TEST") -> Candles:
    return random_walk(days * PER_DAY, seed=seed, start_ms=start_ms, pair=pair)


def funding(ts, rates=None, pair: str = "TEST") -> Funding:
    ts = np.asarray(ts, dtype=np.int64)
    rates = np.full(len(ts), 1e-4) if rates is None else np.asarray(rates, dtype=np.float64)
    return Funding(pair, ts, rates, np.full(len(ts), 8, dtype=np.int8))


NO_FUNDING = funding([])


def test_calendar_days_and_filled():
    cd = walk(4)
    day = (cd.ts - T0_MS) // DAY_MS
    cd = keep(cd, day != 2)
    fr = funding([T0_MS + 2 * DAY_MS + 8 * H_MS], [3e-4])
    f = build_dayframe(cd, fr, first_day=FIRST)
    assert len(f) == 4
    assert f.filled.tolist() == [False, False, True, False]
    assert f.o[2] == f.h[2] == f.l[2] == f.c[2] == f.c[1]
    assert np.isnan(f.fill_px[15][2])
    assert f.ret[2] == pytest.approx(-3e-4, abs=1e-18)
    assert f.day_ms.tolist() == [T0_MS + k * DAY_MS for k in range(4)]


def test_funding_day_boundary():
    cd = walk(3)
    at_midnight = T0_MS + DAY_MS
    late = T0_MS + DAY_MS - 60_000
    f = build_dayframe(cd, funding([late, at_midnight], [1e-4, 2e-4]), first_day=FIRST)
    assert f.funding[0] == pytest.approx(1e-4)
    assert f.funding[1] == pytest.approx(2e-4)
    assert f.funding_ts[f.events(1)].tolist() == [at_midnight]
    assert f.funding_ts[f.events(0)].tolist() == [late]


def test_ret_matches_bh_daily():
    cd = walk(5)
    fts = T0_MS + 8 * H_MS * np.arange(15)
    fr = funding(fts, np.random.default_rng(3).normal(1e-4, 1e-4, 15))
    f = build_dayframe(cd, fr, first_day=FIRST)
    ref = bh_daily(cd, fr, PER_DAY, len(cd) - 1)["ret"].to_numpy()
    assert np.isnan(f.ret[0])
    np.testing.assert_allclose(f.ret[1:], ref, rtol=0, atol=1e-15)


def test_fill_px_per_offset():
    cd = walk(3)
    k = 1
    at = lambda minutes: int(np.searchsorted(cd.ts, T0_MS + k * DAY_MS + minutes * 60_000))  # noqa: E731
    f = build_dayframe(cd, NO_FUNDING, first_day=FIRST)
    assert set(f.fill_px) == set(FILL_OFFSETS_MIN)
    assert f.fill_px[15][k] == cd.c[at(0)]
    assert f.fill_px[60][k] == cd.c[at(45)]
    assert f.fill_px[480][k] == cd.c[at(465)]
    gone = keep(cd, cd.ts != T0_MS + k * DAY_MS + 45 * 60_000)
    g = build_dayframe(gone, NO_FUNDING, first_day=FIRST)
    assert np.isnan(g.fill_px[60][k])
    assert g.fill_px[15][k] == cd.c[at(0)]


def test_fill_offset_validation():
    cd = walk(2)
    for bad in ((10,), (0,), (1455,)):
        with pytest.raises(ValueError):
            build_dayframe(cd, NO_FUNDING, first_day=FIRST, fill_offsets_min=bad)


def test_funding_event_px():
    cd = walk(3)
    ev_ok = T0_MS + DAY_MS + 8 * H_MS
    ev_missing = T0_MS + 2 * DAY_MS + 16 * H_MS
    cd2 = keep(cd, cd.ts != ev_missing)
    f = build_dayframe(cd2, funding([ev_ok, ev_missing]), first_day=FIRST)
    assert f.funding_px[0] == cd.o[int(np.searchsorted(cd.ts, ev_ok))]
    assert np.isnan(f.funding_px[1])


def test_intraday_gap_uses_present_candles():
    cd = walk(3)
    k = 1
    gap = (cd.ts >= T0_MS + k * DAY_MS + 10 * H_MS) & (cd.ts < T0_MS + k * DAY_MS + 12 * H_MS)
    cd2 = keep(cd, ~gap)
    f = build_dayframe(cd2, NO_FUNDING, first_day=FIRST)
    in_day = (cd2.ts >= T0_MS + k * DAY_MS) & (cd2.ts < T0_MS + (k + 1) * DAY_MS)
    assert not f.filled[k]
    assert f.h[k] == cd2.h[in_day].max()
    assert f.l[k] == cd2.l[in_day].min()
    assert f.c[k] == cd2.c[in_day][-1]


def test_first_day_from_config_not_store():
    cd = walk(5, start_ms=T0_MS - 2 * DAY_MS)
    f = build_dayframe(cd, NO_FUNDING, first_day=FIRST)
    assert f.day_ms[0] == date_ms(FIRST)
    assert np.isnan(f.ret[0])
    day0 = (cd.ts >= T0_MS) & (cd.ts < T0_MS + DAY_MS)
    assert f.c[0] == cd.c[day0][-1]
    assert f.o[0] == cd.o[day0][0]
    assert len(f) == 3


def test_first_day_without_candle_raises():
    cd = walk(3, start_ms=T0_MS + DAY_MS)
    with pytest.raises(ValueError, match="first day"):
        build_dayframe(cd, NO_FUNDING, first_day=FIRST)


def test_funding_after_last_candle_dropped():
    cd = walk(5)
    last = T0_MS + 3 * DAY_MS + 9 * H_MS
    cd = keep(cd, cd.ts <= last)
    ev = [T0_MS + 3 * DAY_MS + 8 * H_MS, T0_MS + 3 * DAY_MS + 16 * H_MS, T0_MS + 5 * DAY_MS]
    f = build_dayframe(cd, funding(ev, [1e-4, 5e-4, 7e-4]), first_day=FIRST)
    assert len(f) == 4
    assert f.last_candle_ms == last
    assert f.funding_ts.max() == ev[0]
    assert f.funding[3] == pytest.approx(1e-4)
    assert f.funding_ptr[-1] == 1


def test_long_filled_run_warns(caplog):
    cd = walk(12)
    day = (cd.ts - T0_MS) // DAY_MS
    with caplog.at_level(logging.WARNING, logger="perpbt.trend.daily"):
        f = build_dayframe(keep(cd, (day < 2) | (day > 2 + MAX_FILLED_RUN)), NO_FUNDING, first_day=FIRST)
    assert (T0_MS + 2 * DAY_MS, MAX_FILLED_RUN + 1) in f.filled_runs
    msgs = [r.getMessage() for r in caplog.records if r.name == "perpbt.trend.daily"]
    assert len(msgs) == 1 and "TEST" in msgs[0] and "2020-01-03" in msgs[0]
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="perpbt.trend.daily"):
        g = build_dayframe(keep(cd, (day < 2) | (day > 1 + MAX_FILLED_RUN)), NO_FUNDING, first_day=FIRST)
    assert (T0_MS + 2 * DAY_MS, MAX_FILLED_RUN) in g.filled_runs
    assert not [r for r in caplog.records if r.name == "perpbt.trend.daily"]


def test_empty_funding():
    cd = walk(3)
    f = build_dayframe(cd, NO_FUNDING, first_day=FIRST)
    assert (f.funding == 0).all()
    assert f.funding_ptr.tolist() == [0, 0, 0, 0]
    np.testing.assert_allclose(f.ret[1:], f.c[1:] / f.c[:-1] - 1.0, rtol=0, atol=0)


def test_first_day_is_max_of_listing_and_warmup(data_cfg):
    from perpbt.config import DataConfig, to_dict

    def cfg(listing, warmup):
        return DataConfig(**{**to_dict(data_cfg), "listing": listing, "warmup_start": warmup})

    assert first_day(cfg({"X": "2020-01-17"}, "2020-01-01"), "X") == "2020-01-17"
    assert first_day(cfg({"X": "2017-08-17"}, "2017-08-01"), "X") == "2017-08-17"
    assert first_day(cfg({"X": "2019-09-08"}, "2019-11-01"), "X") == "2019-11-01"
    with pytest.raises(ValueError, match="listing"):
        first_day(cfg({"X": "2020-01-01"}, "2020-01-01"), "Y")


def _frame(candles: Candles) -> pd.DataFrame:
    return pd.DataFrame({
        "open_ms": candles.ts, "open": candles.o, "high": candles.h, "low": candles.l, "close": candles.c,
        "volume": candles.v, "quote_volume": candles.v, "trades": 1, "taker_buy_volume": 0.0, "source": "bulk_monthly",
    })


def _trend_cfg(data_cfg, listing):
    from perpbt.config import DataConfig, to_dict
    return DataConfig(**{**to_dict(data_cfg), "warmup_start": "2020-01-01", "listing": listing})


def test_load_dayframe_perp(data_cfg):
    cfg = _trend_cfg(data_cfg, {"BTCUSDT": "2020-01-01"})
    start = date_ms("2019-11-01")
    days = (date_ms("2020-01-10") - start) // DAY_MS
    cd = random_walk(days * PER_DAY, seed=5, start_ms=start, pair="BTCUSDT")
    CandleStore(cfg).write("BTCUSDT", "15m", _frame(cd))
    fts = start + 8 * H_MS * np.arange(days * 3)
    FundingStore(cfg).write("BTCUSDT", pd.DataFrame({
        "funding_ms": fts, "rate": 1e-4, "interval_h": 8, "source": "bulk_monthly"}))
    f = load_dayframe(cfg, "BTCUSDT", end="2020-01-05")
    assert f.pair == "BTCUSDT"
    assert f.day_ms[0] == date_ms("2020-01-01") and f.day_ms[-1] == date_ms("2020-01-05")
    assert f.funding_ts.min() == date_ms("2020-01-01")
    assert f.funding[1] == pytest.approx(3e-4)


def test_load_dayframe_spot_needs_allow_holdout(data_cfg):
    cfg = _trend_cfg(data_cfg, {"BTCUSDT": "2020-01-01"})
    cd = random_walk(3 * PER_DAY, seed=5, start_ms=T0_MS, pair="BTCUSDT")
    CandleStore(cfg, market="spot").write("BTCUSDT", "15m", _frame(cd))
    with pytest.raises(HoldoutAccessError):
        load_dayframe(cfg, "BTCUSDT", end="2020-01-03", market="spot")
    f = load_dayframe(cfg, "BTCUSDT", end="2020-01-03", market="spot", allow_holdout=True)
    assert len(f) == 3 and (f.funding == 0).all() and len(f.funding_ts) == 0
