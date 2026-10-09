"""Cost re-pricing (spec §5.6, D13): net R is linear in the three cost rates.

Every trade (and every baseline run mean) is stored as components: gross R,
the fee and slippage coefficients in R (Phase 4 §4.7) and funding in R.
``reprice`` evaluates the one formula, in the same float order as
``execution.costs.trade_costs``, so the simulator's trades re-priced at
their own costs give its ``net_r`` bit for bit.
"""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np

REQUIRED = ("gross_r", "c_maker_entry", "c_maker_exit", "c_taker_exit", "c_slip", "funding_r")


def _col(components, name: str) -> np.ndarray:
    return np.asarray(components[name], dtype=np.float64)


def _has(components, name: str) -> bool:
    if isinstance(components, Mapping):
        return name in components
    return name in getattr(components, "columns", ())


def reprice(components, fee_maker: float, fee_taker: float, slippage: float) -> np.ndarray:
    """Net R per row of ``components`` (a mapping or DataFrame of the component columns).

    ``gross_r − fm × (c_maker_entry + c_maker_exit) − ft × (c_taker_entry + c_taker_exit) − s × c_slip − funding_r``;
    without a ``c_taker_entry`` column (the simulator's trades, whose entries are all maker) the
    taker term is ``ft × c_taker_exit``.
    """
    missing = [k for k in REQUIRED if not _has(components, k)]
    if missing:
        raise KeyError(f"reprice: missing component columns {missing}")
    taker = _col(components, "c_taker_exit")
    if _has(components, "c_taker_entry"):
        taker = _col(components, "c_taker_entry") + taker
    return (
        _col(components, "gross_r")
        - fee_maker * (_col(components, "c_maker_entry") + _col(components, "c_maker_exit"))
        - fee_taker * taker
        - slippage * _col(components, "c_slip")
        - _col(components, "funding_r")
    )
