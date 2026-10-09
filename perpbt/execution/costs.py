"""Fees, slippage, funding and the cost coefficients in R (spec §4.5, §4.7, D8, D13).

Fees and slippage are charged on the exit *reference* price (before
slippage), so every cost is a rate times ``qty x price`` and net R is
linear in the three rates with the per-trade coefficients
``c_maker_entry``, ``c_maker_exit``, ``c_taker_exit`` and ``c_slip``.
Phase 5 re-prices the trade table with them without re-simulating.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from perpbt.config import ExecConfig


def entry_fee(qty: float, entry_price: float, cfg: ExecConfig) -> float:
    """Maker fee on a limit entry (USDT)."""
    return cfg.fee_maker * qty * entry_price


def exit_costs(qty: float, exit_ref: float, exit_role: str, cfg: ExecConfig) -> tuple[float, float, float]:
    """``(fee, slippage_usd, executed_price)`` of an exit at reference ``exit_ref``.

    A maker exit (target) pays the maker fee and no slippage; a taker exit
    (stop, time exit, data end) pays the taker fee and fills at
    ``exit_ref x (1 - slippage)``.
    """
    if exit_role == "maker":
        return cfg.fee_maker * qty * exit_ref, 0.0, exit_ref
    if exit_role == "taker":
        return cfg.fee_taker * qty * exit_ref, cfg.slippage * qty * exit_ref, exit_ref * (1.0 - cfg.slippage)
    raise ValueError(f"exit_role must be 'maker' or 'taker', got {exit_role!r}")


def funding_amount(rate: float, qty: float, price: float) -> float:
    """Funding paid by a long of ``qty`` at ``price`` (negative: received). Spec §4.5."""
    return rate * qty * price


class CostIdentityError(AssertionError):
    """``net_pnl != gross_pnl - fees - slippage_cost - funding`` for a trade."""


@dataclass(frozen=True)
class TradeCosts:
    """Per-trade P&L in USDT and in R, with the re-pricing coefficients of spec §4.7."""

    gross_pnl: float
    fees: float
    slippage_cost: float
    funding: float
    net_pnl: float
    gross_r: float
    net_r: float
    cost_r: float
    funding_r: float
    c_maker_entry: float
    c_maker_exit: float
    c_taker_exit: float
    c_slip: float


def trade_costs(
    *,
    qty: float,
    entry_price: float,
    exit_ref: float,
    stop_dist: float,
    risk_usd: float,
    exit_role: str,
    funding_usd: float,
    cfg: ExecConfig,
) -> TradeCosts:
    """Spec §4.7 for one closed trade; raises CostIdentityError if the USDT identity fails."""
    taker = exit_role == "taker"
    fee_exit, slip_usd, _ = exit_costs(qty, exit_ref, exit_role, cfg)
    fees = entry_fee(qty, entry_price, cfg) + fee_exit
    gross_pnl = qty * (exit_ref - entry_price)
    gross_r = gross_pnl / risk_usd
    c_maker_entry = entry_price / stop_dist
    c_maker_exit = 0.0 if taker else exit_ref / stop_dist
    c_taker_exit = exit_ref / stop_dist if taker else 0.0
    c_slip = c_taker_exit
    funding_r = funding_usd / risk_usd
    net_r = (
        gross_r
        - cfg.fee_maker * (c_maker_entry + c_maker_exit)
        - cfg.fee_taker * c_taker_exit
        - cfg.slippage * c_slip
        - funding_r
    )
    net_pnl = net_r * risk_usd
    direct = gross_pnl - fees - slip_usd - funding_usd
    if not math.isclose(net_pnl, direct, rel_tol=1e-12, abs_tol=1e-9):
        raise CostIdentityError(
            f"net_pnl {net_pnl!r} != gross_pnl - fees - slippage - funding = {direct!r} "
            f"(qty {qty}, entry {entry_price}, exit_ref {exit_ref}, stop_dist {stop_dist}, risk_usd {risk_usd})"
        )
    return TradeCosts(
        gross_pnl=gross_pnl, fees=fees, slippage_cost=slip_usd, funding=funding_usd, net_pnl=net_pnl,
        gross_r=gross_r, net_r=net_r, cost_r=gross_r - net_r, funding_r=funding_r,
        c_maker_entry=c_maker_entry, c_maker_exit=c_maker_exit, c_taker_exit=c_taker_exit, c_slip=c_slip,
    )
