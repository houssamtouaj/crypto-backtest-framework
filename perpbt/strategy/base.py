"""Strategy-facing views: the look-ahead guard (spec §2.4, overview §4.1).

``MarketView`` is built once per simulation over the full arrays and moved
forward with ``advance_to``; every accessor refuses an index after the
current candle ``i`` (or before 0) with ``LookaheadError``, so warmup NaNs
are the only signal of "not yet available". The guard targets accidental
look-ahead: underscored attributes and an array's ``.base`` are private and
strategy code must not touch them. The Phase 3 strategy types (intents, the
``Strategy`` protocol) join this module in Phase 3.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from perpbt.checks import as_int
from perpbt.data.sessions import SessionCalendar
from perpbt.data.store import Candles
from perpbt.indicators.swings import Swings


class LookaheadError(RuntimeError):
    """A strategy asked for a candle after the current one (or a negative index)."""


@dataclass(frozen=True)
class SessionInfo:
    """The session of the current candle; ``-1``/``False`` everywhere outside a window."""

    id: int
    open_ms: int
    end_ms: int
    in_window: bool
    is_last: bool


@dataclass(frozen=True)
class OrderView:
    """A pending entry order as the strategy sees it (built by the simulator, Phase 4)."""

    order_id: int
    side: str
    price: float
    stop: float
    target: float
    expires_ms: int
    placed_idx: int


@dataclass(frozen=True)
class PositionView:
    """An open position as the strategy sees it (built by the simulator, Phase 4)."""

    position_id: int
    side: str
    entry: float
    stop: float
    target: float
    qty: float
    fill_idx: int


@dataclass(frozen=True)
class AccountView:
    """Read-only account state of this variant at the close of the current candle."""

    equity_mtm: float
    open_positions: tuple[PositionView, ...]
    pending_orders: tuple[OrderView, ...]


def _read_only(arr: np.ndarray, dtype: type) -> np.ndarray:
    """A read-only view of ``arr`` as ``dtype``.

    ``np.asarray`` copies only when ``arr`` has another dtype; otherwise the
    view shares memory with the caller's array, whose own flag is untouched.
    """
    view = np.asarray(arr, dtype=dtype).view()
    view.flags.writeable = False
    return view


class MarketView:
    """Window ending at candle ``i``. Any access beyond ``i`` raises LookaheadError.

    The view never reveals how many candles follow ``i``.
    """

    __slots__ = (
        "_i", "_n", "_ts", "_o", "_h", "_l", "_c", "_atr", "_sma", "_adx",
        "_sw_idx", "_sw_level", "_sw_conf", "_sid", "_sopen", "_send", "_sin", "_slast",
        "_session_cache",
    )

    def __init__(
        self,
        candles: Candles,
        *,
        atr: np.ndarray,
        daily_sma: np.ndarray,
        daily_adx: np.ndarray,
        swings: Swings,
        calendar: SessionCalendar,
        start_i: int = 0,
    ) -> None:
        n = len(candles)
        for name, arr in (("atr", atr), ("daily_sma", daily_sma), ("daily_adx", daily_adx)):
            if np.shape(arr) != (n,):
                raise ValueError(f"MarketView: {name} has shape {np.shape(arr)}, expected ({n},)")
        if not np.array_equal(calendar.ts, candles.ts):
            raise ValueError("MarketView: the session calendar is not built on these candles' timestamps")
        self._n = n
        self._ts = _read_only(candles.ts, np.int64)
        self._o = _read_only(candles.o, np.float64)
        self._h = _read_only(candles.h, np.float64)
        self._l = _read_only(candles.l, np.float64)
        self._c = _read_only(candles.c, np.float64)
        self._atr = _read_only(atr, np.float64)
        self._sma = _read_only(daily_sma, np.float64)
        self._adx = _read_only(daily_adx, np.float64)
        self._sw_idx = _read_only(swings.idx, np.int64)
        self._sw_level = _read_only(swings.level, np.float64)
        self._sw_conf = _read_only(swings.confirmed_at, np.int64)
        self._sid = calendar.session_id
        self._sopen = calendar.open_ms
        self._send = calendar.end_ms
        self._sin = calendar.in_window
        self._slast = calendar.is_last
        self._session_cache: SessionInfo | None = None
        start_i = as_int(start_i, "start_i")
        if not 0 <= start_i < n:
            raise ValueError(f"MarketView: start_i must be in [0, {n}), got {start_i}")
        self._i = start_i

    # --- cursor ------------------------------------------------------------------------

    @property
    def i(self) -> int:
        """The current candle: decisions happen at its close."""
        return self._i

    def advance_to(self, i: int) -> None:
        """Move the cursor forward to ``i`` (simulator only). Backwards or past the end raises ValueError."""
        i = as_int(i, "candle index")
        if i < self._i or i >= self._n:
            raise ValueError(f"MarketView.advance_to: cannot move from {self._i} to {i}")
        if i != self._i:
            self._i = i
            self._session_cache = None

    # --- guards ------------------------------------------------------------------------

    def _j(self, j: object) -> int:
        j = as_int(j, "candle index")
        if j < 0 or j > self._i:
            raise LookaheadError(f"candle {j} is not visible at i={self._i}")
        return j

    def _ab(self, a: object, b: object) -> slice:
        a, b = as_int(a, "candle index"), as_int(b, "candle index")
        if not 0 <= a <= b <= self._i:
            raise LookaheadError(f"range [{a}, {b}] is not visible at i={self._i} (need 0 <= a <= b <= i)")
        return slice(a, b + 1)

    # --- scalars -----------------------------------------------------------------------

    def ts(self, j: int) -> int:
        """Open time of candle ``j`` in UTC ms."""
        return int(self._ts[self._j(j)])

    def open(self, j: int) -> float:
        return float(self._o[self._j(j)])

    def high(self, j: int) -> float:
        return float(self._h[self._j(j)])

    def low(self, j: int) -> float:
        return float(self._l[self._j(j)])

    def close(self, j: int) -> float:
        return float(self._c[self._j(j)])

    def atr(self, j: int) -> float:
        return float(self._atr[self._j(j)])

    def daily_sma(self, j: int) -> float:
        return float(self._sma[self._j(j)])

    def daily_adx(self, j: int) -> float:
        return float(self._adx[self._j(j)])

    def is_bearish(self, j: int) -> bool:
        """``close[j] < open[j]``."""
        j = self._j(j)
        return bool(self._c[j] < self._o[j])

    # --- ranges (read-only views, inclusive of b) -------------------------------------

    def opens(self, a: int, b: int) -> np.ndarray:
        return self._o[self._ab(a, b)]

    def highs(self, a: int, b: int) -> np.ndarray:
        return self._h[self._ab(a, b)]

    def lows(self, a: int, b: int) -> np.ndarray:
        return self._l[self._ab(a, b)]

    def closes(self, a: int, b: int) -> np.ndarray:
        return self._c[self._ab(a, b)]

    # --- swings and session ------------------------------------------------------------

    def swings_confirmed_by(self, j: int) -> Swings:
        """Swing highs with ``confirmed_at <= j`` (``j <= i``), as read-only views."""
        j = self._j(j)
        m = int(self._sw_conf.searchsorted(j, side="right"))
        return Swings._unchecked(self._sw_idx[:m], self._sw_level[:m], self._sw_conf[:m])

    @property
    def session(self) -> SessionInfo:
        """The session of candle ``i``."""
        if self._session_cache is None:
            i = self._i
            self._session_cache = SessionInfo(
                id=int(self._sid[i]),
                open_ms=int(self._sopen[i]),
                end_ms=int(self._send[i]),
                in_window=bool(self._sin[i]),
                is_last=bool(self._slast[i]),
            )
        return self._session_cache
