"""In-memory stand-in for a ccxt exchange, shared by the ccxt_fetch and fetch tests.

Mimics ``ccxt.binanceusdm``: ``fetch_ohlcv`` returns up to ``limit`` candles
with open time >= ``since`` (including the candle that is still open, as the
real exchange does), ``fetch_funding_rate_history`` the same for events.
"""
from __future__ import annotations

_STEP = {"1m": 60_000, "15m": 900_000}


def ccxt_candles(start_ms: int, n: int, tf: str = "15m", *, price: float = 100.0) -> list[list]:
    """``n`` ccxt-style rows ``[ts, o, h, l, c, v]`` on the grid from ``start_ms``."""
    step = _STEP[tf]
    return [[start_ms + k * step, price + k, price + k + 1.0, price + k - 1.0, price + k + 0.5, 10.0 + k] for k in range(n)]


def ccxt_funding(start_ms: int, n: int, *, interval_h: int = 8, jitter_ms: int = 1) -> list[dict]:
    """``n`` ccxt-style funding events every ``interval_h`` hours, timestamps jittered."""
    return [
        {"timestamp": start_ms + k * interval_h * 3_600_000 + jitter_ms, "fundingRate": 1e-4 * (k + 1)}
        for k in range(n)
    ]


class FakeExchange:
    def __init__(self, candles: list[list] | None = None, funding: list[dict] | None = None, *, tf: str = "15m") -> None:
        self.candles = list(candles or [])
        self.funding = list(funding or [])
        self.tf = tf
        self.calls: list[tuple] = []

    def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None):
        self.calls.append(("ohlcv", symbol, timeframe, since, limit))
        if timeframe != self.tf:
            raise ValueError(f"fake exchange serves {self.tf}, asked for {timeframe}")
        rows = [r for r in self.candles if since is None or r[0] >= since]
        return [list(r) for r in rows[:limit]]

    def fetch_funding_rate_history(self, symbol=None, since=None, limit=None):
        self.calls.append(("funding", symbol, since, limit))
        rows = [e for e in self.funding if since is None or e["timestamp"] >= since]
        return [dict(e) for e in rows[:limit]]


class StuckExchange(FakeExchange):
    """Ignores ``since`` (always returns the first page), to exercise the no-progress guard."""

    def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None):
        self.calls.append(("ohlcv", symbol, timeframe, since, limit))
        return [list(r) for r in self.candles[:limit]]
