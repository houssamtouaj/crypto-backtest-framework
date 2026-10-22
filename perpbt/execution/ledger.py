"""Cash, mark-to-market equity and the daily marks (spec §4.8).

Equity = cash + Σ ``qty x (close - entry_price)`` over open positions.
Entry fees, exit fees, slippage and funding move cash when they occur; an
exit also realises ``qty x (exit_price - entry_price)``. The daily mark is
the equity at the close of a UTC day's last candle; ``ret`` is the ratio to
the previous mark (the start equity for the first) minus one, so a day
without positions or cash movement returns exactly 0.
"""
from __future__ import annotations

from collections.abc import Iterable

from perpbt.execution.orders import Position


class Ledger:
    def __init__(self, start_equity: float) -> None:
        self.start_equity = float(start_equity)
        self.cash = float(start_equity)
        self.rows: list[dict] = []
        self._prev_equity = float(start_equity)
        self._day_funding = 0.0

    def debit(self, amount: float, *, funding: bool = False) -> None:
        """Take ``amount`` (USDT; negative pays in) from cash; ``funding=True`` also books it to the day."""
        self.cash -= amount
        if funding:
            self._day_funding += amount

    def credit(self, amount: float) -> None:
        self.cash += amount

    @staticmethod
    def unrealized(positions: Iterable[Position], close: float) -> float:
        return sum(p.qty * (close - p.entry_price) for p in positions)

    def equity(self, positions: Iterable[Position], close: float) -> float:
        return self.cash + self.unrealized(positions, close)

    def mark_day(self, date_ms: int, equity: float, n_open: int, exposure_notional: float) -> None:
        """Record the day ending now: ``date_ms`` is the UTC midnight that starts the marked day."""
        self.rows.append({
            "date_ms": int(date_ms),
            "equity": equity,
            "ret": equity / self._prev_equity - 1.0,
            "n_open": int(n_open),
            "exposure_notional": exposure_notional,
            "funding_paid": self._day_funding,
        })
        self._prev_equity = equity
        self._day_funding = 0.0
