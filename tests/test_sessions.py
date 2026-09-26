"""SessionCalendar: DST-aware session windows on the 15m grid (perpbt.data.sessions)."""
from datetime import date, datetime, timezone

import numpy as np
import pytest

from perpbt.config import SessionSpec
from perpbt.data.sessions import SessionCalendar

STEP = 900_000
UTC = SessionSpec("utc", "UTC", "00:00", "24:00", (0, 1, 2, 3, 4, 5, 6))
NY = SessionSpec("ny", "America/New_York", "09:30", "16:00", (0, 1, 2, 3, 4))
LONDON = SessionSpec("london", "Europe/London", "08:00", "16:30", (0, 1, 2, 3, 4))


def utc_ms(y, m, d, hh=0, mm=0):
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp()) * 1000


def day_id(y, m, d):
    return date(y, m, d).toordinal()


def grid(start_ms, end_ms):
    return np.arange(start_ms, end_ms, STEP, dtype=np.int64)


@pytest.fixture(scope="module")
def march():
    """15m grid over 2024-03-01 .. 2024-04-07: US DST starts 03-10, UK BST starts 03-31."""
    return grid(utc_ms(2024, 3, 1), utc_ms(2024, 4, 8))


def test_ny_opens_1430_utc_before_dst_and_1330_after(march):
    sessions = SessionCalendar(NY, march).sessions()
    assert (day_id(2024, 3, 8), utc_ms(2024, 3, 8, 14, 30), utc_ms(2024, 3, 8, 21, 0)) in sessions
    assert (day_id(2024, 3, 11), utc_ms(2024, 3, 11, 13, 30), utc_ms(2024, 3, 11, 20, 0)) in sessions


def test_london_opens_0800_utc_before_bst_and_0700_after(march):
    sessions = SessionCalendar(LONDON, march).sessions()
    assert (day_id(2024, 3, 29), utc_ms(2024, 3, 29, 8, 0), utc_ms(2024, 3, 29, 16, 30)) in sessions
    assert (day_id(2024, 4, 1), utc_ms(2024, 4, 1, 7, 0), utc_ms(2024, 4, 1, 15, 30)) in sessions


def test_between_the_changes_ny_is_on_edt_while_london_is_on_gmt(march):
    ny = SessionCalendar(NY, march).sessions()
    london = SessionCalendar(LONDON, march).sessions()
    for d in range(11, 30):  # 2024-03-11 .. 2024-03-29
        if date(2024, 3, d).weekday() >= 5:
            continue
        assert (day_id(2024, 3, d), utc_ms(2024, 3, d, 13, 30), utc_ms(2024, 3, d, 20, 0)) in ny
        assert (day_id(2024, 3, d), utc_ms(2024, 3, d, 8, 0), utc_ms(2024, 3, d, 16, 30)) in london


def test_weekday_rule_drops_saturday_and_sunday_for_ny_and_london_only(march):
    for spec in (NY, LONDON):
        cal = SessionCalendar(spec, march)
        ids = [s[0] for s in cal.sessions()]
        assert all(date.fromordinal(i).weekday() < 5 for i in ids)
        assert len(ids) == 26  # 21 weekdays in March 2024 + Apr 1..5
        saturday = np.searchsorted(march, utc_ms(2024, 3, 9, 15, 0))
        assert cal.session_id[saturday] == -1 and not cal.in_window[saturday]
    utc = SessionCalendar(UTC, march)
    assert len(utc.sessions()) == 38  # every day 2024-03-01 .. 2024-04-07
    assert utc.in_window.all()


def test_every_window_boundary_is_on_the_15m_grid_and_ordered(march):
    for spec in (UTC, NY, LONDON):
        sessions = SessionCalendar(spec, march).sessions()
        for _, o, e in sessions:
            assert o % STEP == 0 and e % STEP == 0 and e > o
        opens = [o for _, o, _ in sessions]
        ends = [e for _, _, e in sessions]
        assert opens == sorted(opens)
        assert all(ends[i] <= opens[i + 1] for i in range(len(opens) - 1))


def test_per_candle_arrays_and_is_last(march):
    cal = SessionCalendar(NY, march)
    i = int(np.searchsorted(march, utc_ms(2024, 3, 8, 14, 30)))
    assert cal.in_window[i]
    assert cal.session_id[i] == day_id(2024, 3, 8)
    assert cal.open_ms[i] == utc_ms(2024, 3, 8, 14, 30) and cal.end_ms[i] == utc_ms(2024, 3, 8, 21, 0)
    assert not cal.in_window[i - 1]
    assert cal.session_id[i - 1] == -1 and cal.open_ms[i - 1] == -1 and cal.end_ms[i - 1] == -1
    j = int(np.searchsorted(march, utc_ms(2024, 3, 8, 20, 45)))
    assert cal.is_last[j] and cal.in_window[j]
    assert not cal.is_last[j - 1] and cal.in_window[j - 1]
    assert not cal.in_window[j + 1] and not cal.is_last[j + 1]
    assert int(cal.is_last.sum()) == len(cal.sessions())
    assert cal.session_id.dtype == np.int64 and cal.open_ms.dtype == np.int64 and cal.end_ms.dtype == np.int64
    assert cal.in_window.dtype == bool and cal.is_last.dtype == bool
    assert len(cal.session_id) == len(march)


def test_utc_session_id_increments_at_midnight(march):
    cal = SessionCalendar(UTC, march)
    k = int(np.searchsorted(march, utc_ms(2024, 3, 2)))
    assert cal.session_id[k] == cal.session_id[k - 1] + 1 == day_id(2024, 3, 2)
    assert cal.is_last[k - 1] and not cal.is_last[k]
    assert cal.open_ms[k] == utc_ms(2024, 3, 2) and cal.end_ms[k] == utc_ms(2024, 3, 3)
    assert (np.diff(np.unique(cal.session_id)) == 1).all()
    assert int(cal.is_last.sum()) == 38


def test_eligible_days_respects_the_weekday_rule_and_half_open_bounds(march):
    cal = SessionCalendar(NY, march)
    days = cal.eligible_days(utc_ms(2024, 3, 9), utc_ms(2024, 3, 18))  # Sat 9 .. Sun 17
    assert days.tolist() == [utc_ms(2024, 3, d, 13, 30) for d in (11, 12, 13, 14, 15)]
    assert days.dtype == np.int64
    assert cal.eligible_days(utc_ms(2024, 3, 11, 13, 30), utc_ms(2024, 3, 11, 13, 30)).tolist() == []
    assert cal.eligible_days(utc_ms(2024, 3, 11, 13, 30), utc_ms(2024, 3, 11, 13, 31)).tolist() == [utc_ms(2024, 3, 11, 13, 30)]
    utc = SessionCalendar(UTC, march)
    assert utc.eligible_days(utc_ms(2024, 3, 9), utc_ms(2024, 3, 12)).tolist() == [utc_ms(2024, 3, d) for d in (9, 10, 11)]


def test_close_off_the_15m_grid_raises(march):
    with pytest.raises(ValueError, match="grid"):
        SessionCalendar(SessionSpec("x", "UTC", "00:00", "16:20", (0, 1, 2, 3, 4)), march)
    with pytest.raises(ValueError, match="grid"):
        SessionCalendar(SessionSpec("x", "America/New_York", "09:35", "16:00", (0, 1, 2, 3, 4)), march)


def test_close_not_after_open_raises(march):
    with pytest.raises(ValueError, match="after"):
        SessionCalendar(SessionSpec("x", "UTC", "16:00", "09:30", (0, 1, 2, 3, 4)), march)


def test_candles_off_grid_or_unsorted_raise():
    with pytest.raises(ValueError, match="grid"):
        SessionCalendar(UTC, np.array([utc_ms(2024, 3, 1) + 1]))
    with pytest.raises(ValueError, match="increasing"):
        SessionCalendar(UTC, np.array([utc_ms(2024, 3, 1), utc_ms(2024, 3, 1)]))


def test_empty_and_weekend_only_series():
    cal = SessionCalendar(NY, np.zeros(0, dtype=np.int64))
    assert cal.sessions() == [] and len(cal.session_id) == 0
    assert cal.eligible_days(0, 10**13).tolist() == []
    saturday = grid(utc_ms(2024, 3, 9), utc_ms(2024, 3, 10))
    cal = SessionCalendar(NY, saturday)
    assert cal.sessions() == []
    assert (cal.session_id == -1).all() and not cal.in_window.any() and not cal.is_last.any()


def test_window_without_candles_is_still_listed():
    ts = np.concatenate([grid(utc_ms(2024, 3, 7), utc_ms(2024, 3, 8)), grid(utc_ms(2024, 3, 9), utc_ms(2024, 3, 10))])
    cal = SessionCalendar(NY, ts)  # Friday 03-08 has no candles at all
    assert [s[0] for s in cal.sessions()] == [day_id(2024, 3, 7), day_id(2024, 3, 8)]
    assert int(cal.is_last.sum()) == 1
