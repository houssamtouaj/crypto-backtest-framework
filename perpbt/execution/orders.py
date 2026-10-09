"""Orders, positions and the simulator's vocabularies (spec §4.2).

An entry order (``entry_limit``) carries its bracket: the stop and target
become attached ``stop`` / ``target_limit`` orders when the entry fills,
and a ``time_exit`` order is created when a deadline, the data end or the
strategy closes the position. When one exit fills, its siblings are
cancelled with reason ``oco``. ``SimEvent`` (what the strategy hears) lives
in ``strategy/base.py``; these records are the simulator's own state.
"""
from __future__ import annotations

from dataclasses import dataclass

from perpbt.config import HoldRule
from perpbt.execution.fills import Levels

ORDER_KINDS = ("entry_limit", "stop", "target_limit", "time_exit")
ORDER_STATUSES = ("pending", "filled", "cancelled")
CANCEL_REASONS = ("expired", "strategy", "leverage_cap", "data_end", "oco")
EXIT_REASONS = ("target", "stop", "session_end", "max_hold", "data_end", "strategy")
RESOLUTIONS = (
    "15m_unambiguous", "15m_pessimistic", "1m", "1m_pessimistic", "15m_pessimistic_missing_1m", "open_gap",
)
FEE_ROLES = ("maker", "taker")


@dataclass(slots=True)
class Order:
    """One row of the ``orders`` table, updated in place as the order lives."""

    order_id: int
    kind: str
    side: str  # "buy" | "sell"
    price: float  # limit or stop level; the reference close for a time exit
    qty: float
    status: str
    placed_ms: int  # close time of the decision candle (or of the candle that created an exit order)
    placed_idx: int
    session_id: int
    tag_json: str
    expires_ms: int | None = None
    filled_ms: int | None = None
    fill_price: float | None = None
    cancelled_ms: int | None = None
    cancel_reason: str | None = None
    trade_id: int | None = None


@dataclass(slots=True)
class Entry:
    """A registered entry order and everything fixed at placement (spec §4.6)."""

    order: Order
    lv: Levels  # entry, stop, target and the pierce thresholds
    stop: float
    target: float
    pierce_abs: float
    hold_rule: HoldRule
    session_end_ms: int  # end of the placement candle's window; -1 outside a window
    stop_dist: float
    risk_usd: float
    notional: float  # qty x planned entry
    equity_at_entry: float  # equity_mtm used for sizing
    implied_leverage: float
    max_iso_leverage: float
    tag: dict


@dataclass(slots=True)
class Position:
    """An open (later closed) position; ``trade_id`` doubles as the strategy's ``position_id``."""

    trade_id: int
    entry: Entry
    qty: float
    entry_price: float
    fill_idx: int
    fill_ms: int
    fill_resolution: str
    deadline_ms: int | None
    stop_order: Order
    target_order: Order
    funding_usd: float = 0.0
    exit_idx: int = -1
    exit_ms: int = -1
    exit_ref: float = float("nan")
    exit_price: float = float("nan")
    exit_reason: str = ""
    exit_role: str = ""
    exit_resolution: str = ""
    exit_span_ms: int = 0  # 60_000 when 1m resolved the exit, else 900_000 (MAE/MFE window)
