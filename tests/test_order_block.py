"""The ICT order-block rule (spec phase-3 §3.2–§3.12)."""
import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from perpbt.config import HoldRule, StopBuffer, StrategyParams
from perpbt.data.sessions import SessionCalendar
from perpbt.indicators.atr import atr
from perpbt.indicators.swings import swing_highs
from perpbt.strategy.base import PlaceBracketLimit, SimEvent, Strategy
from perpbt.strategy.order_block import (
    COUNT_KEYS,
    TAG_KEYS,
    Level,
    LiveLevels,
    OrderBlockStrategy,
    block_prices,
)
from tests.strategy_harness import (
    FLAT_ACCOUNT,
    LONDON,
    NY,
    UTC,
    assert_counts_consistent,
    build_view,
    run_strategy,
)
from tests.synthetic import STEP_15M_MS, T0_MS, candles_from_rows, perturb_after, random_walk

DAY_MS = 86_400_000
PRIMARY = StrategyParams()  # k = 2, N = 3, full zone, top entry, 0.1 x ATR buffer, 2 R

# Hand-placed scenario candles (o, h, l, c). With k = 2 and N = 3:
FILL = (100.0, 100.5, 99.5, 100.0)  # equal highs, never a swing; open == close, never bearish
BASE = [FILL] * 20 + [
    (100.0, 101.0, 99.5, 100.5),   # 20
    (100.5, 103.0, 100.0, 102.0),  # 21 swing high, level 103
    (101.0, 102.5, 100.5, 101.0),  # 22 doji
    (100.0, 101.5, 99.8, 100.0),   # 23 doji; confirms swing 21 (s + k)
]
C24 = (100.0, 100.8, 99.0, 99.2)   # bearish candidate c = 24: zone [99.0, 100.8]
T25 = (99.2, 104.0, 99.1, 103.5)   # impulse t = 25: closes above 103 and above high[c]
S1 = BASE + [C24, T25]
TAIL = [  # appended after a candle closing at 103.5: a second, independent valid block
    (103.5, 105.0, 103.0, 104.5),  # +0
    (104.5, 106.0, 104.0, 105.5),  # +1 swing high, level 106
    (105.5, 105.8, 104.5, 105.0),  # +2
    (105.0, 105.2, 104.2, 104.4),  # +3 confirms +1
    (104.4, 104.6, 103.8, 104.0),  # +4 bearish candidate, high 104.6
    (104.0, 107.0, 103.9, 106.5),  # +5 impulse
]


def counts(**kw):
    out = dict.fromkeys(COUNT_KEYS, 0)
    out.update(kw)
    return out


def cd_of(rows, start_ms=T0_MS):
    return candles_from_rows(rows, start_ms=start_ms)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


# --- 3.1 live levels and impulse -----------------------------------------------------------


def test_level_retires_on_the_first_close_above_it():
    lv = LiveLevels()
    a = Level(1, 103.0, 3)
    assert lv.step([a], 100.0) == a
    assert lv.step([], 103.0) == a and lv.live == (a,)  # a close equal to the level does not retire it
    assert lv.step([], 103.5) == a and lv.live == ()  # the impulse candle retires it
    assert lv.step([], 110.0) is None


def test_most_recent_live_level_is_the_reference_even_below_an_older_higher_one():
    lv = LiveLevels()
    old, new = Level(1, 110.0, 3), Level(5, 105.0, 7)
    lv.step([old], 100.0)
    assert lv.step([new], 100.0) == new
    assert lv.step([], 104.0) == new


def test_close_above_older_level_but_below_reference_retires_it_and_is_not_an_impulse():
    lv = LiveLevels()
    old, ref = Level(1, 105.0, 3), Level(5, 110.0, 7)
    lv.step([old], 100.0)
    lv.step([ref], 100.0)
    got = lv.step([], 106.0)
    assert got == ref and not 106.0 > got.level  # not an impulse
    assert lv.live == (ref,)


def test_after_the_reference_breaks_the_surviving_older_level_is_the_reference_next_candle():
    lv = LiveLevels()
    old, ref = Level(1, 110.0, 3), Level(5, 105.0, 7)
    lv.step([old], 100.0)
    lv.step([ref], 100.0)
    assert lv.step([], 106.0) == ref  # impulse on ref; ref retires
    assert lv.step([], 106.0) == old


def test_literal_mode_ignores_retirement():
    lv = LiveLevels(fresh=False)
    a = Level(1, 103.0, 3)
    lv.step([a], 100.0)
    assert lv.step([], 120.0) == a
    assert lv.step([], 120.0) == a and lv.live == (a,)
    b = Level(6, 125.0, 8)
    assert lv.step([b], 121.0) == b


def test_a_level_confirmed_on_t_cannot_be_broken_on_t():
    for seed in range(10):
        cd = random_walk(2000, seed=seed, start_ms=T0_MS)
        for k in (1, 2, 3):
            sw = swing_highs(cd, k)
            assert (cd.c[sw.confirmed_at] < sw.level).all()


def test_a_level_confirmed_on_t_is_the_reference_on_t():
    lv = LiveLevels()
    lv.step([Level(1, 110.0, 3)], 100.0)
    new = Level(6, 108.0, 8)
    assert lv.step([new], 100.0) == new


# --- 3.6 block prices -------------------------------------------------------------------------


def test_block_prices_full_top_atr_buffer():
    bp = block_prices(100.0, 100.8, 99.0, 99.2, 2.0, PRIMARY)
    assert (bp.zone_low, bp.zone_high, bp.entry) == (99.0, 100.8, 100.8)
    assert bp.stop == 99.0 - 0.1 * 2.0 and bp.stop_dist == 100.8 - bp.stop
    assert bp.target == 100.8 + 2.0 * bp.stop_dist and bp.pierce_abs == 0.0


def test_block_prices_body_mid_pct_buffer_and_pierce():
    p = StrategyParams(zone="body", entry_level="mid", stop_buffer=StopBuffer("pct", 0.0025), r_target=1.5,
                       pierce=0.0005)
    bp = block_prices(100.0, 100.8, 99.0, 99.2, float("nan"), p)
    entry = (99.2 + 100.0) / 2
    assert (bp.zone_low, bp.zone_high, bp.entry) == (99.2, 100.0, entry)
    assert bp.stop == 99.0 - 0.0025 * entry  # anchored at the true low, not the body
    assert bp.target == entry + 1.5 * bp.stop_dist and bp.pierce_abs == 0.0005 * entry


def test_block_prices_zero_range_candle_with_zero_buffer_is_degenerate():
    bp = block_prices(100.0, 100.0, 100.0, 100.0, 1.0, StrategyParams(stop_buffer=StopBuffer("atr", 0.0)))
    assert bp.stop_dist == 0 and not bp.stop_dist > 0


def test_block_prices_nan_atr():
    assert np.isnan(block_prices(100.0, 100.8, 99.0, 99.2, float("nan"), PRIMARY).stop_dist)
    zero = StrategyParams(stop_buffer=StopBuffer("atr", 0.0))
    assert block_prices(100.0, 100.8, 99.0, 99.2, float("nan"), zero).stop_dist == 100.8 - 99.0



# --- 3.2 block selection: scenarios -------------------------------------------------------


def test_s1_basic_block():
    cd = cd_of(S1)
    run = run_strategy(cd, PRIMARY)
    a = float(atr(cd, 14)[25])
    stop = 99.0 - 0.1 * a
    stop_dist = 100.8 - stop
    tag = {
        "candidate_idx": 24, "impulse_idx": 25, "displacement_idx": 25, "swing_idx": 21, "swing_level": 103.0,
        "zone_low": 99.0, "zone_high": 100.8, "entry_kind": "top", "atr": a, "stop_dist": stop_dist,
        "session_id": date(2020, 1, 1).toordinal(),
    }
    assert run.intents == [(25, PlaceBracketLimit(
        side="long", price=100.8, stop=stop, target=100.8 + 2.0 * stop_dist,
        expires_ms=T0_MS + DAY_MS, hold_rule=HoldRule("none"), tag=tag,
    ))]
    assert list(run.intents[0][1].tag) == list(TAG_KEYS)
    assert run.counts == counts(impulses=1, blocks_seen=1, intents=1)


def test_s1_carries_the_hold_rule():
    rule = HoldRule("max_hold", 24)
    (_, x), = run_strategy(cd_of(S1), StrategyParams(hold_rule=rule)).intents
    assert x.hold_rule == rule


S2 = BASE + [
    C24,
    (99.2, 100.0, 99.0, 99.8),     # 25 bullish
    (99.8, 100.5, 99.5, 100.2),    # 26 bullish
    (100.2, 101.0, 100.0, 100.6),  # 27 bullish
    (100.6, 104.0, 100.5, 103.5),  # 28 impulse = c + N + 1
]


def test_s2_impulse_at_c_plus_n_plus_1_has_no_candidate():
    run = run_strategy(cd_of(S2), PRIMARY)
    assert run.intents == [] and run.counts == counts(impulses=1, no_candidate=1)
    wider = run_strategy(cd_of(S2), StrategyParams(confirm_n=4))  # N = 4 reaches c
    (i, x), = wider.intents
    assert i == 28 and x.tag["candidate_idx"] == 24 and x.tag["displacement_idx"] == 28


def test_s3_the_later_of_two_bearish_candles_is_the_block():
    rows = BASE[:23] + [(101.0, 101.5, 99.8, 100.0), C24, T25]  # 23 and 24 both bearish
    (_, x), = run_strategy(cd_of(rows), PRIMARY).intents
    assert x.tag["candidate_idx"] == 24 and x.price == 100.8


def test_s4_level_already_closed_above_gives_no_impulse():
    rows = BASE + [
        (100.0, 103.6, 99.9, 103.2),  # 24 closes above 103: impulse, no candidate; 103 retires
        (103.2, 103.4, 102.0, 102.2),  # 25 bearish
        (102.2, 104.5, 102.1, 104.0),  # 26 closes above 103 again: no live level, no impulse
    ]
    run = run_strategy(cd_of(rows), PRIMARY)
    assert run.intents == [] and run.counts == counts(impulses=1, no_candidate=1)


def test_s5_candidate_before_the_session_open_is_ineligible_and_the_next_block_is_taken():
    cd = cd_of(S1 + TAIL, start_ms=T0_MS - 25 * STEP_15M_MS)  # c = 24 at 23:45, t = 25 at 00:00 UTC
    run = run_strategy(cd, PRIMARY)
    assert run.counts == counts(impulses=2, blocks_seen=2, ineligible=1, intents=1)
    (i, x), = run.intents
    assert i == 31 and x.tag["candidate_idx"] == 30 and x.price == 104.6
    assert x.expires_ms == T0_MS + DAY_MS


def test_s6a_the_immediate_pattern_is_not_mitigated():
    (_, x), = run_strategy(cd_of(S1), PRIMARY).intents
    assert x.tag["displacement_idx"] == x.tag["impulse_idx"] == 25


S6B = BASE + [
    C24,                            # 24 c, entry 100.8
    (99.2, 101.5, 99.1, 101.2),     # 25 d: closes above 100.8, below 103
    (101.0, 101.8, 100.7, 101.4),   # 26 trades back to 100.7 <= entry
    (101.4, 104.0, 101.3, 103.5),   # 27 impulse
] + TAIL                            # 28..33: the next block


def test_s6b_traded_back_after_displacement_is_mitigated_and_the_next_block_is_taken():
    run = run_strategy(cd_of(S6B), PRIMARY)
    assert run.counts == counts(impulses=2, blocks_seen=2, mitigated=1, intents=1)
    (i, x), = run.intents
    assert i == 33 and x.tag["candidate_idx"] == 32 and x.tag["displacement_idx"] == 33


def test_s6b_skip_mitigated_stop_ends_the_session():
    run = run_strategy(cd_of(S6B), StrategyParams(skip_mitigated="stop"))
    assert run.intents == [] and run.counts == counts(impulses=2, blocks_seen=2, mitigated=1, session_used=1)


def _dip_rows(low26):
    return BASE + [C24, (99.2, 101.5, 99.1, 101.2), (101.0, 101.8, low26, 101.4), (101.4, 104.0, 101.3, 103.5)]


def test_s6c_with_pierce_a_low_at_entry_is_not_mitigation_but_entry_minus_pierce_is():
    pierce = StrategyParams(pierce=0.0005)
    run = run_strategy(cd_of(_dip_rows(100.8)), pierce)
    assert [i for i, _ in run.intents] == [27] and run.counts["mitigated"] == 0
    run = run_strategy(cd_of(_dip_rows(100.8 - 0.0005 * 100.8)), pierce)
    assert run.intents == [] and run.counts["mitigated"] == 1
    run = run_strategy(cd_of(_dip_rows(100.8)), PRIMARY)  # pierce 0: a low at entry mitigates
    assert run.intents == [] and run.counts["mitigated"] == 1


@pytest.mark.parametrize("events", [
    {},
    {25: [SimEvent("skipped_leverage", 25)]},
    {26: [SimEvent("filled", 26, order_id=1, position_id=1)], 27: [SimEvent("closed", 27, position_id=1, reason="stop")]},
    {26: [SimEvent("cancelled", 26, order_id=1, reason="expired")]},
], ids=["no_events", "leverage_skip", "stopped_out", "expired"])
def test_s7_one_intent_per_session_whatever_happens_to_it(events):
    run = run_strategy(cd_of(S1 + TAIL), PRIMARY, events=events)
    assert [i for i, _ in run.intents] == [25]
    assert run.counts == counts(impulses=2, blocks_seen=2, session_used=1, intents=1)


def test_s7_the_next_session_is_free_again():
    cd = cd_of(S1 + TAIL, start_ms=T0_MS + DAY_MS - 26 * STEP_15M_MS)  # t = 25 at 23:30, the TAIL on the next day
    run = run_strategy(cd, PRIMARY)
    assert [i for i, _ in run.intents] == [25, 31]
    assert [x.expires_ms for _, x in run.intents] == [T0_MS + DAY_MS, T0_MS + 2 * DAY_MS]


@pytest.mark.parametrize("zone,entry_level,entry,zone_low,zone_high", [
    ("body", "top", 100.0, 99.2, 100.0),
    ("full", "mid", (99.0 + 100.8) / 2, 99.0, 100.8),
    ("body", "mid", (99.2 + 100.0) / 2, 99.2, 100.0),
])
def test_s8_zone_and_entry_modes(zone, entry_level, entry, zone_low, zone_high):
    cd = cd_of(S1)
    stop = 99.0 - 0.1 * float(atr(cd, 14)[25])  # from low[c] in both zone modes
    (_, x), = run_strategy(cd, StrategyParams(zone=zone, entry_level=entry_level)).intents
    assert x.price == entry and x.stop == stop and x.target == entry + 2.0 * (entry - stop)
    assert (x.tag["zone_low"], x.tag["zone_high"], x.tag["entry_kind"]) == (zone_low, zone_high, entry_level)


def test_s9_trend_filter():
    cd = cd_of(S1)
    n = len(cd)
    on = StrategyParams(trend_filter=True)
    assert len(run_strategy(cd, on, daily_sma=np.full(n, 103.0)).intents) == 1  # close 103.5 above the SMA
    below = run_strategy(cd, on, daily_sma=np.full(n, 104.0))
    assert below.intents == [] and below.counts == counts(impulses=1, blocks_seen=1, trend=1)
    warmup = run_strategy(cd, on, daily_sma=np.full(n, np.nan))
    assert warmup.intents == [] and warmup.counts["trend"] == 1
    assert len(run_strategy(cd, PRIMARY, daily_sma=np.full(n, 104.0)).intents) == 1  # filter off


@pytest.mark.parametrize("spec,t_utc,window_end", [
    # 2020-03-08 US and 2020-03-29 EU clocks go forward; windows move one hour earlier in UTC
    (UTC, datetime(2020, 3, 29, 12, 0, tzinfo=timezone.utc), datetime(2020, 3, 30, 0, 0, tzinfo=timezone.utc)),
    (NY, datetime(2020, 3, 6, 15, 0, tzinfo=timezone.utc),
     datetime(2020, 3, 6, 16, 0, tzinfo=ZoneInfo("America/New_York"))),
    (NY, datetime(2020, 3, 9, 14, 0, tzinfo=timezone.utc),
     datetime(2020, 3, 9, 16, 0, tzinfo=ZoneInfo("America/New_York"))),
    (LONDON, datetime(2020, 3, 27, 9, 0, tzinfo=timezone.utc),
     datetime(2020, 3, 27, 16, 30, tzinfo=ZoneInfo("Europe/London"))),
    (LONDON, datetime(2020, 3, 30, 8, 0, tzinfo=timezone.utc),
     datetime(2020, 3, 30, 16, 30, tzinfo=ZoneInfo("Europe/London"))),
], ids=["utc", "ny_before", "ny_after", "london_before", "london_after"])
def test_s10_expiry_is_the_window_end_across_dst(spec, t_utc, window_end):
    cd = cd_of(S1, start_ms=ms(t_utc) - 25 * STEP_15M_MS)
    (_, x), = run_strategy(cd, PRIMARY, spec).intents
    assert x.expires_ms == ms(window_end)


def test_s11_candidate_wick_above_every_close_has_no_displacement():
    rows = BASE + [(100.0, 104.5, 99.0, 99.2), T25]  # high[c] 104.5 above close[t] 103.5
    run = run_strategy(cd_of(rows), PRIMARY)
    assert run.intents == [] and run.counts == counts(impulses=1, blocks_seen=1, no_displacement=1)


def test_s11_body_zone_still_needs_a_close_above_the_true_high():
    rows = BASE + [(100.0, 103.6, 99.0, 99.2), (99.2, 103.8, 99.1, 103.5)]  # above open[c], below high[c]
    run = run_strategy(cd_of(rows), StrategyParams(zone="body"))
    assert run.intents == [] and run.counts["no_displacement"] == 1


S12 = [FILL] * 20 + [  # k = 1, N = 5, mid entry
    (100.0, 102.0, 98.0, 99.0),    # 20 c: zone [98, 102], mid entry 100
    (99.0, 102.6, 98.9, 102.5),    # 21 d: closes above 102 (no live level yet, so no impulse)
    (99.1, 99.35, 99.1, 99.3),     # 22 confirms swing 21 (level 102.6)
    (99.3, 99.45, 99.2, 99.4),     # 23 swing high, level 99.45
    (99.35, 99.4, 99.2, 99.4),     # 24 confirms 23
    (99.4, 99.9, 99.3, 99.6),      # 25 t: closes above 99.45, at or below entry 100
]


def test_s12_close_between_entry_minus_pierce_and_entry_is_entry_above_price():
    params = StrategyParams(swing_k=1, confirm_n=5, entry_level="mid", pierce=0.01)  # pierce_abs = 1.0
    run = run_strategy(cd_of(S12), params)
    assert run.intents == [] and run.counts == counts(impulses=1, blocks_seen=1, entry_above_price=1)
    no_pierce = run_strategy(cd_of(S12), StrategyParams(swing_k=1, confirm_n=5, entry_level="mid"))
    assert no_pierce.counts["mitigated"] == 1  # with pierce 0 the lows already reach the entry


def test_s13_nan_atr_is_degenerate_and_other_buffers_do_not_need_atr():
    cd = cd_of([FILL, FILL] + BASE[20:] + [C24, T25])  # t = 7: ATR14 is NaN
    assert np.isnan(atr(cd, 14)[7])
    run = run_strategy(cd, PRIMARY)
    assert run.intents == [] and run.counts == counts(impulses=1, blocks_seen=1, degenerate=1)
    for sb in (StopBuffer("pct", 0.001), StopBuffer("atr", 0.0)):
        (_, x), = run_strategy(cd, StrategyParams(stop_buffer=sb)).intents
        assert x.tag["atr"] is None


def test_candidate_search_clamps_at_the_first_candle():
    rows = [
        (100.5, 100.8, 99.5, 100.0),   # 0 bearish candidate
        (100.0, 101.0, 99.8, 100.6),   # 1 swing high (k = 1), level 101
        (100.6, 100.9, 100.2, 100.7),  # 2 confirms 1
        (100.7, 101.6, 100.5, 101.4),  # 3 impulse; N = 5 would reach index -2
    ]
    run = run_strategy(cd_of(rows), StrategyParams(swing_k=1, confirm_n=5, stop_buffer=StopBuffer("pct", 0.001)))
    (i, x), = run.intents
    assert i == 3 and x.tag["candidate_idx"] == 0 and x.tag["displacement_idx"] == 3


# --- protocol and call contract --------------------------------------------------------------


def test_order_block_strategy_is_a_strategy():
    s = OrderBlockStrategy(PRIMARY)
    assert isinstance(s, Strategy) and s.name == "order_block" and s.params is PRIMARY
    assert s.warmup_bars == 14 and OrderBlockStrategy(StrategyParams(trend_filter=True)).warmup_bars == 50 * 96
    assert s.skip_counts() == counts()
    s.skip_counts()["impulses"] = 99  # a copy
    assert s.skip_counts() == counts()


def test_on_candle_refuses_a_skipped_or_repeated_candle():
    cd = random_walk(100, seed=1, start_ms=T0_MS)
    view = build_view(cd, UTC, swing_k=2)
    s = OrderBlockStrategy(PRIMARY)
    s.on_candle(view, FLAT_ACCOUNT)
    with pytest.raises(ValueError, match="expected candle 1"):
        s.on_candle(view, FLAT_ACCOUNT)  # candle 0 again
    view.advance_to(2)
    with pytest.raises(ValueError, match="expected candle 1"):
        s.on_candle(view, FLAT_ACCOUNT)
