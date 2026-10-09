"""A strategy with no edge by construction, for the evaluator and baseline tests.

At each in-window candle of a session it has not traded yet (not the
window's last candle), a seeded coin with probability ``p`` decides to
buy at the candle's close, which on a random walk (``open[t+1] ==
close[t]``) fills at the next open on the first look (``open_gap``) — a
market entry at a random time, the same shape as baselines A and B. The
stop is a random multiple of ATR14 below, the target ``r_target`` R above.
"""
from __future__ import annotations

import numpy as np

from perpbt.config import StrategyParams
from perpbt.strategy.base import AccountView, Intent, MarketView, PlaceBracketLimit, SimEvent


class CoinFlipStrategy:
    name = "coinflip"
    warmup_bars = 15

    def __init__(self, params: StrategyParams, seed: int, *, p: float = 1 / 48,
                 atr_mult: tuple[float, float] = (0.5, 2.0)) -> None:
        self.params = params
        self.rng = np.random.default_rng(seed)
        self.p = p
        self.atr_mult = atr_mult
        self.used: set[int] = set()

    def on_candle(self, view: MarketView, account: AccountView) -> list[Intent]:
        s = view.session
        if not s.in_window or s.is_last or s.id in self.used:
            return []
        if self.rng.random() >= self.p:
            return []
        atr = view.atr(view.i)
        if not atr > 0:
            return []
        self.used.add(s.id)
        price = view.close(view.i)
        stop = price - self.rng.uniform(*self.atr_mult) * atr
        target = price + self.params.r_target * (price - stop)
        return [PlaceBracketLimit("long", price, stop, target, s.end_ms, self.params.hold_rule, {})]

    def on_event(self, event: SimEvent) -> None:
        pass

    def skip_counts(self) -> dict[str, int]:
        return {}
