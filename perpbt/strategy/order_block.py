"""The ICT order-block strategy, mechanized (spec phase-3 §3.2–§3.11).

At the close of every candle ``t`` the strategy updates its live levels
(§3.3); if ``t`` is an impulse it looks for the order-block candidate
(§3.4) and runs the checks of §3.5–§3.8 in spec order. The first check a
block fails is its skip reason (§3.11); a block that passes them all
becomes one ``PlaceBracketLimit`` unless the session already has one.
Every read goes through ``MarketView``, so nothing after ``t`` is visible.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

from perpbt.config import StrategyParams
from perpbt.strategy.base import AccountView, Intent, MarketView, PlaceBracketLimit, SimEvent

ATR_PERIOD = 14  # the simulator builds the view's ATR with this period
TREND_SMA_DAYS = 50  # and the daily SMA with this one (D7)
CANDLES_PER_DAY = 96

SKIP_REASONS = (
    "no_candidate", "ineligible", "degenerate", "no_displacement",
    "mitigated", "entry_above_price", "trend", "session_used",
)
COUNT_KEYS = ("impulses", "blocks_seen", *SKIP_REASONS, "intents")
TAG_KEYS = (
    "candidate_idx", "impulse_idx", "displacement_idx", "swing_idx", "swing_level",
    "zone_low", "zone_high", "entry_kind", "atr", "stop_dist", "session_id",
)


class Level(NamedTuple):
    """A confirmed swing high: candle ``swing_idx``, price ``level``, confirmed at ``confirmed_at``."""

    swing_idx: int
    level: float
    confirmed_at: int


class LiveLevels:
    """The live levels of spec §3.3 (D2): confirmed swing highs not yet closed above.

    ``step`` runs steps 1–4 for one candle and returns the reference (the
    live level with the greatest ``confirmed_at``, taken after adding and
    before retiring). With ``fresh=False`` (``structure_break = "literal"``)
    nothing retires, so the reference is the most recently confirmed swing
    high regardless of breaks.
    """

    def __init__(self, fresh: bool = True) -> None:
        self.fresh = fresh
        self._live: list[Level] = []  # in confirmation order
        self._min = math.inf  # lowest live level: nothing retires unless a close exceeds it

    @property
    def live(self) -> tuple[Level, ...]:
        return tuple(self._live)

    def step(self, new: Sequence[Level], close: float) -> Level | None:
        """Add ``new`` (confirmed on this candle), take the reference, retire every level below ``close``."""
        for lv in new:
            self._live.append(lv)
            self._min = min(self._min, lv.level)
        ref = self._live[-1] if self._live else None
        if self.fresh and close > self._min:
            self._live = [lv for lv in self._live if not lv.level < close]
            self._min = min((lv.level for lv in self._live), default=math.inf)
        return ref


@dataclass(frozen=True)
class BlockPrices:
    """Zone, entry, stop and target of one candidate (spec §3.6)."""

    zone_low: float
    zone_high: float
    entry: float
    stop: float
    stop_dist: float
    target: float
    pierce_abs: float


def block_prices(o: float, h: float, l: float, c: float, atr_t: float, params: StrategyParams) -> BlockPrices:  # noqa: E741
    """Spec §3.6 for a candidate with prices ``o, h, l, c`` and ``ATR14[t] = atr_t``.

    ``stop_dist`` is not checked here: the caller rejects ``not stop_dist > 0``
    (which includes NaN) as ``degenerate``. An ATR buffer of value 0 is 0
    even when ``atr_t`` is NaN.
    """
    zone_low, zone_high = (l, h) if params.zone == "full" else (c, o)
    entry = zone_high if params.entry_level == "top" else (zone_low + zone_high) / 2
    sb = params.stop_buffer
    if sb.kind == "atr":
        buffer = 0.0 if sb.value == 0 else sb.value * atr_t
    else:
        buffer = sb.value * entry
    stop = l - buffer  # the candle's true low in both zone modes
    stop_dist = entry - stop
    return BlockPrices(
        zone_low=zone_low, zone_high=zone_high, entry=entry, stop=stop, stop_dist=stop_dist,
        target=entry + params.r_target * stop_dist, pierce_abs=params.pierce * entry,
    )


def _finite_or_none(x: float) -> float | None:
    return x if math.isfinite(x) else None


class OrderBlockStrategy:
    """The first-bullish-order-block rule as a ``Strategy`` (spec §3.1–§3.11).

    ``on_candle`` must be called once per candle, in order, without gaps
    (a gap would skip a retirement, so it raises ValueError). On the first
    call, at candle ``i0``, the live levels are rebuilt from candles
    ``0..i0-1`` without counting or emitting anything, so the output from
    ``i0`` on does not depend on where the loop starts. The used-session
    state is not rebuilt, so a first call inside a window (other than at
    its open, or at candle 0) raises ValueError.
    """

    name = "order_block"

    def __init__(self, params: StrategyParams) -> None:
        self.params = params
        self.warmup_bars = TREND_SMA_DAYS * CANDLES_PER_DAY if params.trend_filter else ATR_PERIOD
        self._levels = LiveLevels(fresh=params.structure_break == "fresh")
        self._seen = 0  # swings already handed to the live levels
        self._next_i: int | None = None
        self._used_session: int | None = None
        self._counts = dict.fromkeys(COUNT_KEYS, 0)

    # --- Strategy protocol -------------------------------------------------------------

    def on_candle(self, view: MarketView, account: AccountView) -> list[Intent]:
        t = view.i
        if self._next_i is None:
            s = view.session
            if t > 0 and s.in_window and view.ts(t) != s.open_ms:
                raise ValueError(
                    f"OrderBlockStrategy: first call at candle {t} is inside a session window opened at "
                    f"{s.open_ms} ms; the used-session state is not rebuilt, so start at a session boundary"
                )
            self._catch_up(view, t)
        elif t != self._next_i:
            raise ValueError(f"OrderBlockStrategy.on_candle: expected candle {self._next_i}, got {t}")
        self._next_i = t + 1
        close_t = view.close(t)
        ref = self._levels.step(self._new_levels(view, t), close_t)
        if ref is None or not close_t > ref.level:
            return []
        self._counts["impulses"] += 1
        intent = self._evaluate(view, t, ref, close_t)
        return [] if intent is None else [intent]

    def on_event(self, event: SimEvent) -> None:
        """Nothing to do: the session is consumed when the intent is emitted (§3.5)."""
        return None

    def skip_counts(self) -> dict[str, int]:
        """``impulses``, ``blocks_seen``, every skip reason and ``intents``, in ``COUNT_KEYS`` order (§3.11)."""
        return dict(self._counts)

    # --- live levels ---------------------------------------------------------------------

    def _catch_up(self, view: MarketView, i0: int) -> None:
        if i0 == 0:
            return
        sw = view.swings_confirmed_by(i0 - 1)
        closes = view.closes(0, i0 - 1)
        p = 0
        for j in range(i0):
            new = []
            while p < len(sw) and sw.confirmed_at[p] == j:
                new.append(Level(int(sw.idx[p]), float(sw.level[p]), j))
                p += 1
            self._levels.step(new, float(closes[j]))
        self._seen = len(sw)

    def _new_levels(self, view: MarketView, t: int) -> list[Level]:
        sw = view.swings_confirmed_by(t)
        new = [
            Level(int(sw.idx[j]), float(sw.level[j]), int(sw.confirmed_at[j]))
            for j in range(self._seen, len(sw))
        ]
        self._seen = len(sw)
        return new

    # --- the block ---------------------------------------------------------------------

    def _skip(self, reason: str) -> None:
        self._counts[reason] += 1

    def _candidate(self, view: MarketView, t: int) -> int | None:
        """§3.4: the most recent bearish candle in ``max(t - N, 0) .. t - 1``."""
        for j in range(t - 1, max(t - self.params.confirm_n, 0) - 1, -1):
            if view.is_bearish(j):
                return j
        return None

    def _displacement(self, view: MarketView, c: int, t: int) -> int | None:
        """§3.7: the first candle in ``c + 1 .. t`` closing above ``high[c]``."""
        high_c = view.high(c)
        for j, close in enumerate(view.closes(c + 1, t), start=c + 1):
            if close > high_c:
                return j
        return None

    def _evaluate(self, view: MarketView, t: int, ref: Level, close_t: float) -> PlaceBracketLimit | None:
        p = self.params
        c = self._candidate(view, t)
        if c is None:
            return self._skip("no_candidate")
        self._counts["blocks_seen"] += 1
        s = view.session
        if not (s.in_window and not s.is_last and view.ts(c) >= s.open_ms):  # §3.5
            return self._skip("ineligible")
        bp = block_prices(view.open(c), view.high(c), view.low(c), view.close(c), view.atr(t), p)
        if not bp.stop_dist > 0:  # §3.6; NaN (ATR warmup) fails too
            return self._skip("degenerate")
        d = self._displacement(view, c, t)
        if d is None:
            return self._skip("no_displacement")
        if d < t and float(view.lows(d + 1, t).min()) <= bp.entry - bp.pierce_abs:
            if p.skip_mitigated == "stop":
                self._used_session = s.id
            return self._skip("mitigated")
        if not close_t > bp.entry:
            return self._skip("entry_above_price")
        if p.trend_filter and not close_t > view.daily_sma(t):  # §3.8; NaN SMA rejects
            return self._skip("trend")
        if s.id == self._used_session:  # §3.9: at most one intent per session
            return self._skip("session_used")
        self._used_session = s.id
        self._counts["intents"] += 1
        tag = {
            "candidate_idx": c,
            "impulse_idx": t,
            "displacement_idx": d,
            "swing_idx": ref.swing_idx,
            "swing_level": ref.level,
            "zone_low": bp.zone_low,
            "zone_high": bp.zone_high,
            "entry_kind": p.entry_level,
            "atr": _finite_or_none(view.atr(t)),
            "stop_dist": bp.stop_dist,
            "session_id": s.id,
        }
        return PlaceBracketLimit(
            side="long", price=bp.entry, stop=bp.stop, target=bp.target,
            expires_ms=s.end_ms, hold_rule=p.hold_rule, tag=tag,
        )
