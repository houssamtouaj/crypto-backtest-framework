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
