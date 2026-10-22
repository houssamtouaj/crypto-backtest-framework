"""The strategy plug-in interface (spec §3.1) and the look-ahead guard (spec §2.4, overview §4.1).

``MarketView`` is built once per simulation over the full arrays and moved
forward with ``advance_to``; every accessor refuses an index after the
current candle ``i`` (or before 0) with ``LookaheadError``, so warmup NaNs
are the only signal of "not yet available". The guard targets accidental
look-ahead: underscored attributes and an array's ``.base`` are private and
strategy code must not touch them.

A strategy turns ``(MarketView, AccountView)`` into ``Intent`` objects and
hears back through ``SimEvent``. ``SimEvent`` lives here rather than in
``execution/`` so that strategy code never imports the simulator.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from perpbt.checks import as_int
from perpbt.config import HoldRule, StrategyParams
from perpbt.data.sessions import SessionCalendar
from perpbt.data.store import Candles
from perpbt.indicators.atr import atr as atr_of
from perpbt.indicators.daily import daily_adx_aligned, daily_sma_aligned
from perpbt.indicators.swings import Swings, swing_highs

# The indicator periods every MarketView is built with (spec §2.4); strategies read them as given.
VIEW_ATR_PERIOD = 14
VIEW_SMA_DAYS = 50
VIEW_ADX_PERIOD = 14


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


def build_market_view(
    candles: Candles,
    calendar: SessionCalendar,
    *,
    swing_k: int,
    daily_sma: np.ndarray | None = None,
    start_i: int = 0,
) -> MarketView:
    """The view the simulator hands a strategy: ATR14, completed-day SMA(50) and ADX(14), swing highs of ``swing_k``.

    ``daily_sma`` overrides the computed SMA (tests of the trend filter).
    """
    sma = daily_sma_aligned(candles, VIEW_SMA_DAYS) if daily_sma is None else np.asarray(daily_sma, dtype=np.float64)
    return MarketView(
        candles,
        atr=atr_of(candles, VIEW_ATR_PERIOD),
        daily_sma=sma,
        daily_adx=daily_adx_aligned(candles, VIEW_ADX_PERIOD),
        swings=swing_highs(candles, swing_k),
        calendar=calendar,
        start_i=start_i,
    )


# --- intents, events, the strategy protocol (spec §3.1) ---------------------------------

SIDES = ("long", "short")
EVENT_KINDS = ("filled", "cancelled", "closed", "skipped_leverage")


@dataclass(frozen=True)
class PlaceBracketLimit:
    """Place an entry limit with an attached stop and target (spec §3.1).

    ``expires_ms``: cancel if unfilled by this instant (the window end).
    ``hold_rule``: the simulator derives the exit deadline at the fill.
    ``tag``: audit metadata of plain ``int``/``float``/``str``/``None``
    values, copied to orders and trades. Validated on construction so the
    simulator never sees NaN prices or a stop on the wrong side. Not
    hashable (``tag`` is a dict).
    """

    side: str
    price: float
    stop: float
    target: float
    expires_ms: int
    hold_rule: HoldRule
    tag: dict

    def __post_init__(self) -> None:
        if self.side not in SIDES:
            raise ValueError(f"PlaceBracketLimit.side must be one of {SIDES}, got {self.side!r}")
        for name in ("price", "stop", "target"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"PlaceBracketLimit.{name} must be finite, got {getattr(self, name)!r}")
        if self.side == "long":
            ordered = self.stop < self.price < self.target
        else:
            ordered = self.target < self.price < self.stop
        if not ordered:
            raise ValueError(
                f"PlaceBracketLimit: stop {self.stop}, price {self.price}, target {self.target} "
                f"are out of order for a {self.side}"
            )
        # A numpy integer (e.g. straight from a calendar array) is accepted and stored as int.
        object.__setattr__(self, "expires_ms", as_int(self.expires_ms, "PlaceBracketLimit.expires_ms (UTC ms)"))
        if not isinstance(self.hold_rule, HoldRule):
            raise TypeError(f"PlaceBracketLimit.hold_rule must be a HoldRule, got {type(self.hold_rule).__name__}")
        if not isinstance(self.tag, dict):
            raise TypeError(f"PlaceBracketLimit.tag must be a dict, got {type(self.tag).__name__}")


@dataclass(frozen=True)
class CancelOrder:
    """Cancel a pending entry order."""

    order_id: int


@dataclass(frozen=True)
class ClosePosition:
    """Close an open position at the current close (a time exit)."""

    position_id: int
    reason: str


Intent = PlaceBracketLimit | CancelOrder | ClosePosition


@dataclass(frozen=True)
class SimEvent:
    """What the simulator tells the strategy after acting (spec §3.1; built by Phase 4).

    ``kind``: ``filled`` (an entry order filled; ``order_id``, ``position_id``),
    ``cancelled`` (a pending order cancelled; ``order_id``, ``reason`` =
    ``expired``/``strategy``/``leverage_cap``/``data_end``), ``closed`` (a
    position exited; ``position_id``, ``reason`` = the exit reason),
    ``skipped_leverage`` (a ``PlaceBracketLimit`` refused by the leverage
    cap at placement; no ids). ``idx`` is the candle it happened on. Phase 4
    may add fields, with defaults.
    """

    kind: str
    idx: int
    order_id: int | None = None
    position_id: int | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"SimEvent.kind must be one of {EVENT_KINDS}, got {self.kind!r}")


@runtime_checkable
class Strategy(Protocol):
    """The plug-in interface every strategy implements (spec §3.1).

    Call contract: the simulator calls ``on_candle`` once per candle, in
    order, without gaps, from the first decision candle on; intents are
    placed at that candle's close. ``on_event`` delivers what happened to
    earlier intents. ``warmup_bars`` is the number of candles before the
    first decision candle the strategy's indicators need.
    """

    name: str
    params: StrategyParams
    warmup_bars: int

    def on_candle(self, view: MarketView, account: AccountView) -> list[Intent]: ...

    def on_event(self, event: SimEvent) -> None: ...

    def skip_counts(self) -> dict[str, int]: ...
