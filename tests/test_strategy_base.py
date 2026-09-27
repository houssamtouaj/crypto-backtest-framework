"""MarketView look-ahead guard, SessionInfo, AccountView (spec §2.4)."""
import dataclasses

import numpy as np
import pytest

from perpbt.config import SessionSpec
from perpbt.data.sessions import SessionCalendar
from perpbt.indicators.atr import atr
from perpbt.indicators.daily import daily_adx_aligned, daily_sma_aligned
from perpbt.indicators.swings import swing_highs
from perpbt.strategy.base import (
    AccountView,
    LookaheadError,
    MarketView,
    OrderView,
    PositionView,
    SessionInfo,
)
from tests.synthetic import T0_MS, random_walk

N = 3 * 96  # three UTC days
UTC = SessionSpec(name="utc", tz="UTC", open="00:00", close="24:00", days=(0, 1, 2, 3, 4, 5, 6))


@pytest.fixture(scope="module")
def cd():
    return random_walk(N, seed=31, start_ms=T0_MS)


def make_view(cd, start_i=0):
    return MarketView(
        cd,
        atr=atr(cd, 14),
        daily_sma=daily_sma_aligned(cd, 2),
        daily_adx=daily_adx_aligned(cd, 1),
        swings=swing_highs(cd, 2),
        calendar=SessionCalendar(UTC, cd.ts),
        start_i=start_i,
    )


SCALARS = ("ts", "open", "high", "low", "close", "atr", "daily_sma", "daily_adx", "is_bearish")
RANGES = ("opens", "highs", "lows", "closes")


def test_close_beyond_i_raises(cd):
    v = make_view(cd, start_i=100)
    assert v.close(100) == cd.c[100]
    with pytest.raises(LookaheadError):
        v.close(v.i + 1)


def test_range_beyond_i_raises(cd):
    v = make_view(cd, start_i=100)
    i = v.i
    np.testing.assert_array_equal(v.lows(i - 3, i), cd.l[i - 3 : i + 1])
    with pytest.raises(LookaheadError):
        v.lows(i - 3, i + 1)


def test_negative_index_raises(cd):
    v = make_view(cd, start_i=50)
    for name in SCALARS:
        with pytest.raises(LookaheadError):
            getattr(v, name)(-1)
    for name in RANGES:
        with pytest.raises(LookaheadError):
            getattr(v, name)(-2, 3)
    with pytest.raises(LookaheadError):
        v.swings_confirmed_by(-1)


def test_reversed_range_raises(cd):
    v = make_view(cd, start_i=50)
    with pytest.raises(LookaheadError):
        v.closes(10, 9)


def test_float_index_raises_type_error(cd):
    v = make_view(cd, start_i=50)
    with pytest.raises(TypeError):
        v.close(10.0)
    with pytest.raises(TypeError):
        v.lows(1.5, 3)


def test_bool_index_raises_type_error(cd):
    v = make_view(cd, start_i=50)
    with pytest.raises(TypeError):
        v.close(True)
    with pytest.raises(TypeError):
        v.lows(False, 3)


def test_numpy_integer_indices_work(cd):
    v = make_view(cd, start_i=50)
    assert v.close(np.int64(7)) == cd.c[7]
    assert v.lows(np.int32(1), np.int64(3)).tolist() == cd.l[1:4].tolist()


def test_values_match_the_underlying_arrays(cd):
    v = make_view(cd, start_i=N - 1)
    a, s, x = atr(cd, 14), daily_sma_aligned(cd, 2), daily_adx_aligned(cd, 1)
    for j in (0, 13, 100, 200, N - 1):
        assert v.ts(j) == int(cd.ts[j]) and isinstance(v.ts(j), int)
        assert (v.open(j), v.high(j), v.low(j), v.close(j)) == (cd.o[j], cd.h[j], cd.l[j], cd.c[j])
        assert np.array_equal([v.atr(j), v.daily_sma(j), v.daily_adx(j)], [a[j], s[j], x[j]], equal_nan=True)
        assert v.is_bearish(j) == bool(cd.c[j] < cd.o[j])


def test_swings_confirmed_by_never_returns_future_confirmations(cd):
    full = swing_highs(cd, 2)
    v = make_view(cd)
    for i in range(N):
        v.advance_to(i)
        got = v.swings_confirmed_by(i)
        assert (got.confirmed_at <= i).all()
        mask = full.confirmed_at <= i
        np.testing.assert_array_equal(got.idx, full.idx[mask])
        np.testing.assert_array_equal(got.level, full.level[mask])
        if i >= 5:
            assert len(v.swings_confirmed_by(i - 5)) == int((full.confirmed_at <= i - 5).sum())
    with pytest.raises(LookaheadError):
        v.swings_confirmed_by(N)


def test_advancing_exposes_exactly_one_more_candle(cd):
    v = make_view(cd, start_i=40)
    assert len(v.closes(0, 40)) == 41
    with pytest.raises(LookaheadError):
        v.close(41)
    v.advance_to(41)
    assert v.i == 41 and v.close(41) == cd.c[41]
    assert len(v.closes(0, 41)) == 42
    with pytest.raises(LookaheadError):
        v.close(42)


def test_advance_to_refuses_backwards_and_past_the_end(cd):
    v = make_view(cd, start_i=40)
    with pytest.raises(ValueError):
        v.advance_to(39)
    with pytest.raises(ValueError):
        v.advance_to(N)
    v.advance_to(40)  # staying put is allowed
    with pytest.raises(ValueError):
        make_view(cd, start_i=N)


def test_view_does_not_reveal_the_series_length(cd):
    v = make_view(cd, start_i=10)
    with pytest.raises(TypeError):
        len(v)
    public = {name for name in dir(v) if not name.startswith("_")}
    assert public == set(SCALARS) | set(RANGES) | {"i", "advance_to", "swings_confirmed_by", "session"}


def test_i_is_read_only(cd):
    v = make_view(cd, start_i=10)
    with pytest.raises(AttributeError):
        v.i = 20


def test_returned_arrays_are_read_only_and_exact(cd):
    v = make_view(cd, start_i=100)
    for name in RANGES:
        arr = getattr(v, name)(90, 100)
        assert arr.shape == (11,) and not arr.flags.writeable
        with pytest.raises(ValueError):
            arr[0] = 0.0
    sw = v.swings_confirmed_by(100)
    for arr in (sw.idx, sw.level, sw.confirmed_at):
        assert not arr.flags.writeable
    assert cd.c.flags.writeable  # the caller's arrays are untouched


def test_fuzz_access_succeeds_iff_in_range(cd):
    rng = np.random.default_rng(99)
    v = make_view(cd)
    checked = 0
    for i in np.sort(rng.integers(0, N, 60)):
        v.advance_to(int(i))
        for j in rng.integers(-5, N + 5, 40):
            j = int(j)
            ok = 0 <= j <= i
            for name in SCALARS:
                if ok:
                    getattr(v, name)(j)
                else:
                    with pytest.raises(LookaheadError):
                        getattr(v, name)(j)
            a = int(rng.integers(-5, N + 5))
            ok_range = 0 <= a <= j <= i
            for name in RANGES:
                if ok_range:
                    assert len(getattr(v, name)(a, j)) == j - a + 1
                else:
                    with pytest.raises(LookaheadError):
                        getattr(v, name)(a, j)
            checked += 1
    assert checked == 60 * 40


def test_session_info_follows_the_calendar(cd):
    cal = SessionCalendar(UTC, cd.ts)
    v = make_view(cd)
    for i in (0, 95, 96, 150, N - 1):
        v.advance_to(i)
        s = v.session
        assert s == SessionInfo(
            id=int(cal.session_id[i]), open_ms=int(cal.open_ms[i]), end_ms=int(cal.end_ms[i]),
            in_window=bool(cal.in_window[i]), is_last=bool(cal.is_last[i]),
        )


def test_session_outside_window_is_minus_one():
    ny = SessionSpec(name="ny", tz="America/New_York", open="09:30", close="16:00", days=(0, 1, 2, 3, 4))
    cd = random_walk(96, seed=5, start_ms=T0_MS)  # 2020-01-01, a Wednesday; 00:00 UTC is 19:00 NY
    v = MarketView(cd, atr=atr(cd, 14), daily_sma=daily_sma_aligned(cd, 2), daily_adx=daily_adx_aligned(cd, 1),
                   swings=swing_highs(cd, 2), calendar=SessionCalendar(ny, cd.ts))
    assert v.session == SessionInfo(id=-1, open_ms=-1, end_ms=-1, in_window=False, is_last=False)


def test_constructor_rejects_misaligned_inputs(cd):
    good = dict(atr=atr(cd, 14), daily_sma=daily_sma_aligned(cd, 2), daily_adx=daily_adx_aligned(cd, 1),
                swings=swing_highs(cd, 2), calendar=SessionCalendar(UTC, cd.ts))
    with pytest.raises(ValueError):
        MarketView(cd, **{**good, "atr": good["atr"][:-1]})
    short = random_walk(N - 96, seed=31, start_ms=T0_MS)
    with pytest.raises(ValueError):
        MarketView(cd, **{**good, "calendar": SessionCalendar(UTC, short.ts)})


def test_account_view_is_frozen():
    o = OrderView(order_id=1, side="long", price=10.0, stop=9.0, target=12.0, expires_ms=T0_MS, placed_idx=3)
    p = PositionView(position_id=2, side="long", entry=10.0, stop=9.0, target=12.0, qty=1.5, fill_idx=4)
    acct = AccountView(equity_mtm=10_000.0, open_positions=(p,), pending_orders=(o,))
    with pytest.raises(dataclasses.FrozenInstanceError):
        acct.equity_mtm = 0.0
    assert acct.open_positions[0].position_id == 2 and acct.pending_orders[0].order_id == 1
