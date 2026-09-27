"""SessionCalendar: per-candle session ids and window flags, DST-aware (spec §1.3, D1, D3).

For every session-local calendar day whose weekday is in ``spec.days`` the
window opens and closes at the local wall-clock times of the spec, converted
to UTC with ``zoneinfo``. ``"24:00"`` is 00:00 of the next local day. Every
boundary must land on the 15m grid. Local times that do not exist on a DST
change day (the skipped hour) are resolved by ``zoneinfo`` with ``fold=0``;
the three sessions of this project never touch that hour.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

from perpbt.config import SessionSpec

STEP_15M_MS = 900_000


def _local_ms(d: date, hhmm: str, tz: ZoneInfo) -> int:
    """UTC ms of local wall-clock ``hhmm`` on local day ``d`` (``24:00`` → next day 00:00)."""
    hh, mm = (int(x) for x in hhmm.split(":"))
    if hh == 24:
        d = d + timedelta(days=1)
        hh = 0
    local = datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz)
    utc = local.astimezone(timezone.utc)
    return calendar.timegm(utc.timetuple()) * 1000


def _windows(spec: SessionSpec, ts: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(ids, opens, ends) of every window intersecting ``[ts[0], ts[-1] + 15m)``."""
    empty = np.zeros(0, dtype=np.int64)
    if len(ts) == 0:
        return empty, empty, empty
    tz = ZoneInfo(spec.tz)
    lo, hi = int(ts[0]), int(ts[-1]) + STEP_15M_MS
    first = datetime.fromtimestamp(lo / 1000, tz).date() - timedelta(days=1)
    last = datetime.fromtimestamp(int(ts[-1]) / 1000, tz).date() + timedelta(days=1)
    ids: list[int] = []
    opens: list[int] = []
    ends: list[int] = []
    d = first
    while d <= last:
        if d.weekday() in spec.days:
            o = _local_ms(d, spec.open, tz)
            e = _local_ms(d, spec.close, tz)
            if e <= o:
                raise ValueError(
                    f"session {spec.name!r}: close {spec.close} is not after open {spec.open} on {d}"
                )
            if o % STEP_15M_MS or e % STEP_15M_MS:
                raise ValueError(
                    f"session {spec.name!r}: window on {d} ({o}..{e} ms) is not on the 15m grid"
                )
            if e > lo and o < hi:
                ids.append(d.toordinal())
                opens.append(o)
                ends.append(e)
        d += timedelta(days=1)
    for i in range(len(opens) - 1):
        if opens[i + 1] < ends[i]:
            raise ValueError(f"session {spec.name!r}: windows overlap around {opens[i + 1]} ms")
    return (
        np.asarray(ids, dtype=np.int64),
        np.asarray(opens, dtype=np.int64),
        np.asarray(ends, dtype=np.int64),
    )


class SessionCalendar:
    """Session windows of ``spec`` over the 15m open times ``ts``.

    ``session_id`` is the ordinal of the session-local calendar day (−1
    outside any window); ``open_ms``/``end_ms`` are the window bounds of the
    candle's session (−1 outside); ``in_window`` is ``open_ms <= ts < end_ms``;
    ``is_last`` is ``ts + 15m == end_ms``.
    """

    def __init__(self, spec: SessionSpec, ts: np.ndarray) -> None:
        ts = np.asarray(ts)
        if ts.ndim != 1 or not np.issubdtype(ts.dtype, np.integer):
            raise TypeError("SessionCalendar: ts must be a 1-D integer array of UTC ms")
        ts = ts.astype(np.int64, copy=False)
        if len(ts) > 1 and not np.all(np.diff(ts) > 0):
            raise ValueError("SessionCalendar: ts must be strictly increasing")
        if np.any(ts % STEP_15M_MS != 0):
            raise ValueError("SessionCalendar: ts must lie on the 15m grid")
        self.spec = spec
        self.ts = ts
        self._ids, self._opens, self._ends = _windows(spec, ts)
        n = len(ts)
        if len(self._opens):
            k = np.searchsorted(self._opens, ts, side="right") - 1
            k = np.clip(k, 0, len(self._opens) - 1)
            inside = (ts >= self._opens[k]) & (ts < self._ends[k])
        else:
            k = np.zeros(n, dtype=np.int64)
            inside = np.zeros(n, dtype=bool)
        minus_one = np.full(n, -1, dtype=np.int64)
        self.in_window: np.ndarray = inside
        self.session_id: np.ndarray = np.where(inside, self._ids[k] if len(self._ids) else minus_one, minus_one)
        self.open_ms: np.ndarray = np.where(inside, self._opens[k] if len(self._opens) else minus_one, minus_one)
        self.end_ms: np.ndarray = np.where(inside, self._ends[k] if len(self._ends) else minus_one, minus_one)
        self.is_last: np.ndarray = inside & (ts + STEP_15M_MS == self.end_ms)

    def sessions(self) -> list[tuple[int, int, int]]:
        """``(session_id, open_ms, end_ms)`` of every window, in time order."""
        return list(zip(self._ids.tolist(), self._opens.tolist(), self._ends.tolist(), strict=True))

    def eligible_days(self, start_ms: int, end_ms: int) -> np.ndarray:
        """Session open instants with ``start_ms <= open < end_ms`` (int64)."""
        keep = (self._opens >= start_ms) & (self._opens < end_ms)
        return self._opens[keep]
