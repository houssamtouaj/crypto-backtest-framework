"""Drive a strategy over candles the way the Phase 4 simulator will, minus execution.

Builds the ``MarketView`` from the candles (ATR14, swing highs of the
params' ``k``, completed-day SMA(50) unless an explicit array is given,
ADX(14), the session calendar), advances it one candle at a time, calls
``on_candle`` with a flat account and records every intent with its
decision index. Events given for candle ``i`` are delivered right after
``on_candle(i)``, where the simulator's placement step would emit them.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from perpbt.config import SessionSpec, StrategyParams
from perpbt.data.sessions import SessionCalendar
from perpbt.data.store import Candles
from perpbt.strategy.base import AccountView, Intent, MarketView, SimEvent, build_market_view
from perpbt.strategy.order_block import COUNT_KEYS, SKIP_REASONS, OrderBlockStrategy

UTC = SessionSpec(name="utc", tz="UTC", open="00:00", close="24:00", days=(0, 1, 2, 3, 4, 5, 6))
NY = SessionSpec(name="ny", tz="America/New_York", open="09:30", close="16:00", days=(0, 1, 2, 3, 4))
LONDON = SessionSpec(name="london", tz="Europe/London", open="08:00", close="16:30", days=(0, 1, 2, 3, 4))

FLAT_ACCOUNT = AccountView(equity_mtm=10_000.0, open_positions=(), pending_orders=())


@dataclass
class Run:
    intents: list[tuple[int, Intent]]  # (decision index, intent)
    counts: dict[str, int]  # skip_counts() after the last candle run
    counts_at: dict[int, dict[str, int]] = field(default_factory=dict)  # skip_counts() after candle i


def assert_counts_consistent(c: dict[str, int]) -> None:
    """Spec §3.11: every impulse and every block has exactly one outcome."""
    assert list(c) == list(COUNT_KEYS), list(c)
    assert c["impulses"] == c["no_candidate"] + c["blocks_seen"], c
    assert c["blocks_seen"] == sum(c[r] for r in SKIP_REASONS if r != "no_candidate") + c["intents"], c


def build_view(
    candles: Candles, spec: SessionSpec, *, swing_k: int,
    daily_sma: np.ndarray | None = None, start_i: int = 0,
) -> MarketView:
    return build_market_view(
        candles, SessionCalendar(spec, candles.ts), swing_k=swing_k, daily_sma=daily_sma, start_i=start_i,
    )


def run_strategy(
    candles: Candles, params: StrategyParams, spec: SessionSpec = UTC, *,
    daily_sma: np.ndarray | None = None, start_i: int = 0, stop_at: int | None = None,
    events: Mapping[int, Sequence[SimEvent]] | None = None, counts_at: Sequence[int] = (),
) -> Run:
    """Run ``OrderBlockStrategy(params)`` on candles ``start_i .. stop_at`` (default: to the end)."""
    strategy = OrderBlockStrategy(params)
    view = build_view(candles, spec, swing_k=params.swing_k, daily_sma=daily_sma, start_i=start_i)
    last = len(candles) - 1 if stop_at is None else stop_at
    events = events or {}
    snap = set(counts_at)
    run = Run([], {})
    for i in range(start_i, last + 1):
        view.advance_to(i)
        for intent in strategy.on_candle(view, FLAT_ACCOUNT):
            run.intents.append((i, intent))
        for ev in events.get(i, ()):
            strategy.on_event(ev)
        if i in snap:
            run.counts_at[i] = strategy.skip_counts()
    run.counts = strategy.skip_counts()
    return run
