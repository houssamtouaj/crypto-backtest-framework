"""Fixed-fractional sizing, the leverage cap and the liquidation assertion (spec §4.6, D5).

Sizing happens at placement (the close of the decision candle) on the
mark-to-market equity: ``risk_usd = risk_per_trade x equity``,
``qty = risk_usd / stop_dist`` (fractional, no lot rounding).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from perpbt.config import ExecConfig


class LiquidationAboveStopError(RuntimeError):
    """The estimated liquidation price is at or above the stop: a bug or a degenerate stop (spec §4.6)."""


@dataclass(frozen=True)
class Sizing:
    risk_usd: float
    stop_dist: float
    qty: float
    notional: float  # qty x entry
    implied_leverage: float  # notional / equity
    max_iso_leverage: float  # 1 / (stop_dist / entry + mmr)


def size_order(equity_mtm: float, entry: float, stop: float, cfg: ExecConfig) -> Sizing:
    """Size a long limit at ``entry`` with ``stop``; ValueError on non-positive equity or ``stop >= entry``."""
    if not (math.isfinite(equity_mtm) and equity_mtm > 0):
        raise ValueError(f"cannot size an order: equity_mtm is {equity_mtm!r}")
    stop_dist = entry - stop
    if not stop_dist > 0:
        raise ValueError(f"cannot size an order: stop {stop!r} is not below entry {entry!r}")
    risk_usd = cfg.risk_per_trade * equity_mtm
    qty = risk_usd / stop_dist
    notional = qty * entry
    return Sizing(
        risk_usd=risk_usd, stop_dist=stop_dist, qty=qty, notional=notional,
        implied_leverage=notional / equity_mtm, max_iso_leverage=1.0 / (stop_dist / entry + cfg.mmr),
    )


def leverage_ok(sizing: Sizing, open_notional: float, equity_mtm: float, cfg: ExecConfig) -> bool:
    """False when the order alone, or the order plus the open notional, exceeds ``max_leverage``."""
    return not (
        sizing.notional / equity_mtm > cfg.max_leverage
        or (open_notional + sizing.notional) / equity_mtm > cfg.max_leverage
    )


def liquidation_price(entry: float, equity_mtm: float, notional: float, mmr: float) -> float:
    """Cross margin, single-position approximation: ``entry x (1 - equity / notional + mmr)``."""
    return entry * (1.0 - equity_mtm / notional + mmr)


def check_liquidation(entry: float, stop: float, equity_mtm: float, notional: float, mmr: float) -> float:
    """The liquidation price; raises LiquidationAboveStopError if it is at or above ``stop``."""
    liq = liquidation_price(entry, equity_mtm, notional, mmr)
    if liq >= stop:
        raise LiquidationAboveStopError(
            f"liquidation {liq!r} >= stop {stop!r} (entry {entry}, equity {equity_mtm}, notional {notional}, mmr {mmr})"
        )
    return liq
