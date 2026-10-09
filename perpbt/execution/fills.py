"""Fill and exit rules on one candle, and the 1m resolver (spec §4.3, §4.4).

``apply_rules`` is spec §4.3 on a single candle of any timeframe, for one
long bracket: entry (if pending), then stop (also on the fill candle),
then target (not on the fill candle). ``resolve_candle`` applies it to a
15m candle and, when two or more of {entry, stop, target} were touched and
1m candles are in use, hands the candle to ``walk_minutes``, which applies
the same rules to each of its 15 minutes. Time exits are not handled here:
they happen at the 15m close (the simulator checks them afterwards). The
Phase 5 batched evaluator reuses these functions.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from perpbt.data.store import Candles

MINUTE_MS = 60_000
CANDLE_15M_MS = 900_000
MINUTES_PER_15M = 15

NAN = float("nan")


class _Missing:
    """Sentinel: 1m candles are in use but a minute of this 15m candle is absent."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()


@dataclass(frozen=True)
class Levels:
    """Price thresholds of one long bracket; ``fill_at`` and ``target_at`` include the pierce."""

    entry: float
    stop: float
    target: float
    pierce_abs: float
    fill_at: float  # entry - pierce_abs
    target_at: float  # target + pierce_abs


def levels(entry: float, stop: float, target: float, pierce_abs: float) -> Levels:
    """``Levels`` with the thresholds computed exactly as Phase 3 §3.6/§3.7 does (``entry - pierce_abs``)."""
    return Levels(entry, stop, target, pierce_abs, entry - pierce_abs, target + pierce_abs)


@dataclass(frozen=True)
class Step:
    """Spec §4.3 on one candle. ``touched`` counts the events of {entry, stop, target} the candle reached."""

    touched: int
    filled: bool
    fill_price: float
    fill_gap: bool  # filled at the open (the open was already through the threshold)
    exit: str | None  # "stop" | "target"
    exit_ref: float  # exit reference price, before slippage


def apply_rules(pending: bool, o: float, h: float, l: float, lv: Levels) -> Step:  # noqa: E741
    """One candle for one bracket: ``pending`` is True for an unfilled entry, False for an open position."""
    touched = 0
    filled = False
    fill_price = NAN
    gap = False
    if pending:
        if o <= lv.fill_at:
            filled, fill_price, gap = True, o, True
        elif l <= lv.fill_at:
            filled, fill_price = True, lv.entry
        else:
            return Step(0, False, NAN, False, None, NAN)
        touched = 1
    stop_hit = l <= lv.stop
    target_hit = h >= lv.target_at
    touched += stop_hit + target_hit
    if stop_hit:
        return Step(touched, filled, fill_price, gap, "stop", min(lv.stop, o))
    if target_hit and not filled:
        return Step(touched, filled, fill_price, gap, "target", max(lv.target, o))
    return Step(touched, filled, fill_price, gap, None, NAN)


@dataclass(frozen=True)
class CandleOutcome:
    """What happened to one bracket on one 15m candle; minutes are 0..14, or -1 on the 15m path."""

    filled: bool
    fill_price: float
    fill_gap: bool
    fill_minute: int
    exit: str | None
    exit_ref: float
    exit_minute: int
    resolution: str


def _from_step(s: Step, resolution: str) -> CandleOutcome:
    return CandleOutcome(s.filled, s.fill_price, s.fill_gap, -1, s.exit, s.exit_ref, -1, resolution)


def walk_minutes(pending: bool, o1: np.ndarray, h1: np.ndarray, l1: np.ndarray, lv: Levels) -> CandleOutcome:
    """Spec §4.4: ``apply_rules`` minute by minute until an exit; ``1m_pessimistic`` if one minute touched two events."""
    filled = False
    fill_price = NAN
    gap = False
    fill_minute = -1
    pessimistic = False
    for m in range(len(o1)):
        s = apply_rules(pending, float(o1[m]), float(h1[m]), float(l1[m]), lv)
        if s.touched >= 2:
            pessimistic = True
        if s.filled:
            filled, fill_price, gap, fill_minute = True, s.fill_price, s.fill_gap, m
            pending = False
        if s.exit is not None:
            label = "1m_pessimistic" if pessimistic else "1m"
            return CandleOutcome(filled, fill_price, gap, fill_minute, s.exit, s.exit_ref, m, label)
    return CandleOutcome(filled, fill_price, gap, fill_minute, None, NAN, -1, "1m_pessimistic" if pessimistic else "1m")


def resolve_candle(
    pending: bool, o: float, h: float, l: float, lv: Levels,  # noqa: E741
    minutes: tuple[np.ndarray, np.ndarray, np.ndarray] | _Missing | None,
) -> CandleOutcome:
    """Spec §4.3 on a 15m candle, with the 1m resolver for ambiguous candles.

    ``minutes`` is None when 1m candles are not in use, ``MISSING`` when
    they are but one of this candle's minutes is absent, else the
    ``(open, high, low)`` arrays of its 15 minutes. The minutes are only
    consulted when the 15m candle touched two or more events.
    """
    s = apply_rules(pending, o, h, l, lv)
    if s.touched <= 1:
        return _from_step(s, "15m_unambiguous")
    if minutes is None:
        return _from_step(s, "15m_pessimistic")
    if minutes is MISSING:
        return _from_step(s, "15m_pessimistic_missing_1m")
    o1, h1, l1 = minutes
    return walk_minutes(pending, o1, h1, l1, lv)


class MinuteIndex:
    """Lookup of the 15 one-minute candles inside a 15m candle."""

    def __init__(self, candles1m: Candles) -> None:
        if candles1m.tf != "1m":
            raise ValueError(f"MinuteIndex needs 1m candles, got {candles1m.tf!r}")
        self.candles = candles1m
        self._ts = candles1m.ts

    def window(self, open_ms: int) -> tuple[np.ndarray, np.ndarray, np.ndarray] | _Missing:
        """``(o, h, l)`` of the minutes in ``[open_ms, open_ms + 15m)``, or ``MISSING`` unless all 15 exist."""
        k = int(self._ts.searchsorted(open_ms))
        e = k + MINUTES_PER_15M
        if e > len(self._ts) or self._ts[k] != open_ms or self._ts[e - 1] != open_ms + (MINUTES_PER_15M - 1) * MINUTE_MS:
            return MISSING
        c = self.candles
        return c.o[k:e], c.h[k:e], c.l[k:e]

    def span(self, a_ms: int, b_ms: int) -> tuple[np.ndarray, np.ndarray] | None:
        """``(highs, lows)`` of the minutes in ``[a_ms, b_ms)``, or None unless every minute exists."""
        k = int(self._ts.searchsorted(a_ms))
        e = int(self._ts.searchsorted(b_ms))
        if e - k != (b_ms - a_ms) // MINUTE_MS:
            return None
        return self.candles.h[k:e], self.candles.l[k:e]
