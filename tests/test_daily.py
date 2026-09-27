"""Completed-day SMA / ADX aligned to the 15m index (spec §2.3)."""
import numpy as np
import pandas as pd
import pytest

from perpbt.data.store import DAY_MS, CandleStore, Candles, date_ms
from perpbt.indicators.daily import (
    daily_adx,
    daily_adx_aligned,
    daily_bars,
    daily_sma,
    daily_sma_aligned,
)
from tests.synthetic import T0_MS, STEP_15M_MS, assert_causal, random_walk

PER_DAY = 96


def walk_days(days, seed, step_sigma=0.004):
    return random_walk(days * PER_DAY, seed=seed, start_ms=T0_MS, step_sigma=step_sigma)


def drop(cd: Candles, keep: np.ndarray) -> Candles:
    return Candles(cd.pair, cd.tf, cd.ts[keep], cd.o[keep], cd.h[keep], cd.l[keep], cd.c[keep], cd.v[keep])


def pandas_daily(cd: Candles) -> pd.DataFrame:
    """Independent daily bars: pandas groupby on the UTC calendar day."""
    df = pd.DataFrame({
        "t": pd.to_datetime(cd.ts, unit="ms", utc=True),
        "o": cd.o, "h": cd.h, "l": cd.l, "c": cd.c, "v": cd.v,
    })
    g = df.groupby(df["t"].dt.floor("D"))
    return pd.DataFrame({
        "o": g["o"].first(), "h": g["h"].max(), "l": g["l"].min(), "c": g["c"].last(), "v": g["v"].sum(),
    })


def reference_adx(daily: pd.DataFrame, n: int) -> pd.Series:
    """Textbook Wilder ADX with plain floats, one day at a time."""
    h, l, c = daily["h"].tolist(), daily["l"].tolist(), daily["c"].tolist()
    m = len(c)
    nan = float("nan")
    tr, pdm, mdm = [nan] * m, [nan] * m, [nan] * m
    for d in range(1, m):
        tr[d] = max(h[d] - l[d], abs(h[d] - c[d - 1]), abs(l[d] - c[d - 1]))
        up, down = h[d] - h[d - 1], l[d - 1] - l[d]
        pdm[d] = up if (up > down and up > 0) else 0.0
        mdm[d] = down if (down > up and down > 0) else 0.0

    def rma(x, start):
        out = [nan] * m
        if m - start < n:
            return out
        acc = sum(x[start : start + n]) / n
        out[start + n - 1] = acc
        for t in range(start + n, m):
            acc = (acc * (n - 1) + x[t]) / n
            out[t] = acc
        return out

    atr_, p, q = rma(tr, 1), rma(pdm, 1), rma(mdm, 1)
    dx = [nan] * m
    for d in range(n, m):
        dip = 100 * p[d] / atr_[d] if atr_[d] > 0 else 0.0
        dim = 100 * q[d] / atr_[d] if atr_[d] > 0 else 0.0
        dx[d] = 100 * abs(dip - dim) / (dip + dim) if dip + dim > 0 else 0.0
    return pd.Series(rma(dx, n), index=daily.index)


def aligned_reference(cd: Candles, per_day: pd.Series) -> np.ndarray:
    """Value of each candle = per-day series at the previous present day."""
    days = pd.to_datetime(cd.ts, unit="ms", utc=True).floor("D")
    shifted = per_day.shift(1)  # the previous *present* day, since per_day has one row per present day
    return shifted.reindex(days).to_numpy(dtype=np.float64)


def test_daily_bars_match_pandas():
    cd = walk_days(10, seed=21)
    bars = daily_bars(cd)
    ref = pandas_daily(cd)
    assert len(bars) == 10
    np.testing.assert_array_equal(bars.day_ms, T0_MS + DAY_MS * np.arange(10))
    for col in "ohlcv":
        np.testing.assert_allclose(getattr(bars, col), ref[col].to_numpy(), rtol=1e-12)


@pytest.mark.parametrize("n", [5, 50])
def test_sma_matches_pandas_on_previous_completed_days(n):
    cd = walk_days(80, seed=22)
    ref = aligned_reference(cd, pandas_daily(cd)["c"].rolling(n).mean())
    np.testing.assert_allclose(daily_sma_aligned(cd, n), ref, rtol=1e-12, equal_nan=True)
    assert np.isfinite(daily_sma_aligned(cd, n)[n * PER_DAY :]).all()
    assert np.isnan(daily_sma_aligned(cd, n)[: n * PER_DAY]).all()


@pytest.mark.parametrize("n", [3, 14])
def test_adx_matches_reference_on_previous_completed_days(n):
    cd = walk_days(70, seed=23)
    ref = aligned_reference(cd, reference_adx(pandas_daily(cd), n))
    out = daily_adx_aligned(cd, n)
    np.testing.assert_allclose(out, ref, rtol=1e-12, equal_nan=True)
    # first valid daily bar is index 2n - 1, so the aligned value starts on day 2n
    assert np.isnan(out[: 2 * n * PER_DAY]).all()
    assert np.isfinite(out[2 * n * PER_DAY :]).all()
    assert ((out[2 * n * PER_DAY :] >= 0) & (out[2 * n * PER_DAY :] <= 100)).all()


def test_value_changes_at_midnight_and_uses_only_previous_days():
    cd = walk_days(12, seed=24)
    bars = daily_bars(cd)
    sma = daily_sma(bars, 3)
    out = daily_sma_aligned(cd, 3)
    for d in range(4, 12):
        day = out[d * PER_DAY : (d + 1) * PER_DAY]
        assert (day == sma[d - 1]).all()          # constant through day d, from bars < d
        assert out[d * PER_DAY - 1] == sma[d - 2]  # 23:45 of day d-1 still uses bars < d-1
        assert out[d * PER_DAY] != out[d * PER_DAY - 1]


def test_changing_day_d_never_changes_day_d_values():
    cd = walk_days(30, seed=25)
    base_sma, base_adx = daily_sma_aligned(cd, 5), daily_adx_aligned(cd, 5)
    for d in (12, 20, 29):
        sl = slice(d * PER_DAY, (d + 1) * PER_DAY)
        h, l, c = cd.h.copy(), cd.l.copy(), cd.c.copy()
        j = sl.stop - 1  # the day's last candle: the day's close moves, so the next day must change
        c[j] *= 1.05
        h[j] = max(h[j], c[j]) * 1.01
        l[j] = min(l[j], c[j]) * 0.99
        changed = Candles(cd.pair, cd.tf, cd.ts, cd.o, h, l, c, cd.v)
        np.testing.assert_array_equal(daily_sma_aligned(changed, 5)[: sl.stop], base_sma[: sl.stop])
        np.testing.assert_array_equal(daily_adx_aligned(changed, 5)[: sl.stop], base_adx[: sl.stop])
        if d + 1 < 30:  # the next day does see the change
            assert daily_sma_aligned(changed, 5)[sl.stop] != base_sma[sl.stop]


def test_outage_gap_day_counts_with_its_last_available_close():
    cd = walk_days(12, seed=26)
    d = 6
    keep = np.ones(len(cd), dtype=bool)
    keep[d * PER_DAY + 72 : (d + 1) * PER_DAY] = False  # day 6 ends at 17:45 (outage 18:00-24:00)
    gapped = drop(cd, keep)
    bars = daily_bars(gapped)
    assert len(bars) == 12
    assert bars.c[d] == cd.c[d * PER_DAY + 71]
    ref = aligned_reference(gapped, pandas_daily(gapped)["c"].rolling(3).mean())
    np.testing.assert_allclose(daily_sma_aligned(gapped, 3), ref, rtol=1e-12, equal_nan=True)


def test_missing_midnight_candle_still_completes_the_day():
    cd = walk_days(8, seed=27)
    keep = np.ones(len(cd), dtype=bool)
    keep[5 * PER_DAY] = False  # the 00:00 candle of day 5 is missing
    gapped = drop(cd, keep)
    out = daily_sma_aligned(gapped, 2)
    first_of_day5 = 5 * PER_DAY  # index in gapped of day 5's 00:15 candle
    expected = (cd.c[4 * PER_DAY - 1] + cd.c[5 * PER_DAY - 1]) / 2  # closes of days 3 and 4
    assert out[first_of_day5] == pytest.approx(expected, rel=1e-12)


def test_whole_missing_day_is_skipped():
    cd = walk_days(10, seed=28)
    keep = np.ones(len(cd), dtype=bool)
    keep[4 * PER_DAY : 5 * PER_DAY] = False  # no candles at all on day 4
    gapped = drop(cd, keep)
    bars = daily_bars(gapped)
    assert len(bars) == 9 and (T0_MS + 4 * DAY_MS) not in bars.day_ms.tolist()
    out = daily_sma_aligned(gapped, 2)
    idx_day5 = 4 * PER_DAY  # day 5's first candle in gapped
    assert out[idx_day5] == pytest.approx((cd.c[3 * PER_DAY - 1] + cd.c[4 * PER_DAY - 1]) / 2, rel=1e-12)


def test_too_few_days_is_all_nan():
    for days in (0, 1, 3):
        cd = walk_days(days, seed=29)
        assert np.isnan(daily_sma_aligned(cd, 5)).all() and len(daily_sma_aligned(cd, 5)) == len(cd)
        assert np.isnan(daily_adx_aligned(cd, 3)).all()
    cd = walk_days(6, seed=29)  # 6 bars: ADX(3) valid at bar 5, aligned only after it (never)
    assert np.isnan(daily_adx_aligned(cd, 3)).all()


def test_flat_series_gives_zero_adx_not_nan():
    n_days = 12
    flat = Candles("T", "15m", T0_MS + STEP_15M_MS * np.arange(n_days * PER_DAY, dtype=np.int64),
                   *(np.full(n_days * PER_DAY, 100.0) for _ in range(4)), np.ones(n_days * PER_DAY))
    out = daily_adx_aligned(flat, 3)
    assert (out[6 * PER_DAY :] == 0.0).all()


def test_rejects_non_15m_candles():
    cd = random_walk(10, seed=1, start_ms=T0_MS, step_ms=60_000, tf="1m")
    with pytest.raises(ValueError, match="15m"):
        daily_bars(cd)


@pytest.mark.parametrize("fn, n", [(daily_sma_aligned, 5), (daily_adx_aligned, 3)])
def test_daily_indicators_are_causal(fn, n):
    for seed in range(20):
        cd = walk_days(30, seed=300 + seed)
        # cuts: mid-day, the last candle of a day, the first candle of a day, the last candle
        assert_causal(lambda c: fn(c, n), cd, cuts=[10 * PER_DAY + 37, 15 * PER_DAY - 1, 20 * PER_DAY, len(cd) - 1],
                      seeds=[seed], truncate=True)


def test_docstrings_state_lag():
    assert "Lag 0" in daily_sma_aligned.__doc__
    assert "Lag 0" in daily_adx_aligned.__doc__


# --- real data (slow) -----------------------------------------------------------------


def _first_valid_day(cd: Candles, values: np.ndarray) -> int:
    return int(cd.ts[np.flatnonzero(np.isfinite(values))[0]])


@pytest.mark.slow
def test_btc_sma50_and_adx14_valid_on_2020_01_01(real_cfg):
    cd = CandleStore(real_cfg).load("BTCUSDT", "15m", date_ms("2019-11-01"), date_ms("2020-01-02"))
    i = cd.index_at(date_ms("2020-01-01"), exact=True)
    assert np.isfinite(daily_sma_aligned(cd, 50)[i])
    assert np.isfinite(daily_adx_aligned(cd, 14)[i])


@pytest.mark.slow
def test_eth_warmup_starts_at_listing(real_cfg):
    cd = CandleStore(real_cfg).load("ETHUSDT", "15m", date_ms("2019-11-01"), date_ms("2020-02-01"))
    assert _first_valid_day(cd, daily_adx_aligned(cd, 14)) == date_ms("2019-12-25")
    assert _first_valid_day(cd, daily_sma_aligned(cd, 50)) == date_ms("2020-01-16")


@pytest.mark.slow
def test_sol_valid_from_listing_plus_warmup(real_cfg):
    cd = CandleStore(real_cfg).load("SOLUSDT", "15m", date_ms("2020-09-01"), date_ms("2020-12-31"))
    first_day = (int(cd.ts[0]) // DAY_MS) * DAY_MS
    assert _first_valid_day(cd, daily_sma_aligned(cd, 50)) == first_day + 50 * DAY_MS
    assert _first_valid_day(cd, daily_adx_aligned(cd, 14)) == first_day + 28 * DAY_MS
