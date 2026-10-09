"""The simulator's output tables (spec §4.10): typed frames, the trade row, MAE/MFE.

Every table has a fixed column order and dtype, also when empty, so two
runs write byte-identical Parquet. Nullable integers and floats use the
pandas ``Int64`` / ``Float64`` dtypes; strings are ``object`` columns that
may hold None.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from perpbt.data.store import DAY_MS, Candles
from perpbt.execution.costs import TradeCosts
from perpbt.execution.fills import CANDLE_15M_MS, MINUTE_MS, MinuteIndex
from perpbt.execution.orders import Order, Position

STR = "object"

ORDERS_SCHEMA: dict[str, str] = {
    "order_id": "int64", "variant_id": STR, "pair": STR, "session_variant": STR, "session_id": "int64",
    "kind": STR, "side": STR, "price": "float64", "qty": "float64", "status": STR,
    "placed_ms": "int64", "placed_idx": "int64", "expires_ms": "Int64", "filled_ms": "Int64",
    "fill_price": "Float64", "cancelled_ms": "Int64", "cancel_reason": STR, "trade_id": "Int64", "tag": STR,
}

FILLS_SCHEMA: dict[str, str] = {
    "fill_id": "int64", "order_id": "int64", "trade_id": "int64", "ts_ms": "int64",
    "ref_price": "float64", "price": "float64", "qty": "float64", "fee": "float64",
    "fee_role": STR, "slippage": "float64", "resolution": STR,
}

TRADES_SCHEMA: dict[str, str] = {
    "trade_id": "int64", "variant_id": STR, "pair": STR, "session_variant": STR, "session_id": "int64",
    "session_open_ms": "Int64",
    "candidate_ms": "Int64", "impulse_ms": "Int64", "displacement_ms": "Int64", "placed_ms": "int64",
    "entry_idx": "int64", "entry_ms": "int64", "entry_price": "float64",
    "planned_entry": "float64", "stop_price": "float64", "target_price": "float64", "stop_dist": "float64",
    "pierce_abs": "float64", "deadline_ms": "Int64",
    "exit_idx": "int64", "exit_ms": "int64", "exit_ref_price": "float64", "exit_price": "float64",
    "exit_reason": STR, "exit_role": STR,
    "qty": "float64", "notional": "float64",
    "equity_at_entry": "float64", "risk_usd": "float64", "actual_risk_usd": "float64",
    "implied_leverage": "float64", "max_iso_leverage": "float64",
    "gross_pnl": "float64", "fees": "float64", "funding": "float64", "slippage_cost": "float64", "net_pnl": "float64",
    "gross_r": "float64", "net_r": "float64", "cost_r": "float64", "funding_r": "float64",
    "c_maker_entry": "float64", "c_maker_exit": "float64", "c_taker_exit": "float64", "c_slip": "float64",
    "mae_r": "float64", "mfe_r": "float64", "hold_minutes": "int64",
    "atr_at_entry": "float64", "stop_dist_atr": "float64",
    "regime_trend": STR, "regime_vol": STR,
    "dow": "int8", "entry_hour_utc": "int8",
    "fill_resolution": STR, "exit_resolution": STR,
}

DAILY_SCHEMA: dict[str, str] = {
    "variant_id": STR, "pair": STR, "session_variant": STR, "date": STR, "equity": "float64", "ret": "float64",
    "n_open": "int16", "exposure_notional": "float64", "funding_paid": "float64",
}

EVENTS_SCHEMA: dict[str, str] = {
    "event_id": "int64", "idx": "int64", "ts_ms": "int64", "kind": STR, "order_id": "Int64", "trade_id": "Int64",
    "reason": STR, "price": "Float64", "qty": "Float64", "amount": "Float64",
}

EVENT_KINDS = ("placed", "skipped_leverage", "filled", "cancelled", "closed", "funding", "missing_1m")


def frame(rows: list[dict], schema: dict[str, str]) -> pd.DataFrame:
    """A DataFrame with exactly ``schema``'s columns and dtypes; every row must have every column."""
    data = {}
    for col, dtype in schema.items():
        values = [r[col] for r in rows]
        if dtype == STR:
            data[col] = pd.Series(values, dtype=object)
        elif dtype in ("Int64", "Float64"):
            data[col] = pd.array([pd.NA if v is None else v for v in values], dtype=dtype)
        else:
            data[col] = np.asarray(values, dtype=dtype) if values else np.zeros(0, dtype=dtype)
    return pd.DataFrame(data, columns=list(schema))


def order_row(o: Order, ids: dict) -> dict:
    return {
        "order_id": o.order_id, **ids, "session_id": o.session_id, "kind": o.kind, "side": o.side,
        "price": o.price, "qty": o.qty, "status": o.status, "placed_ms": o.placed_ms, "placed_idx": o.placed_idx,
        "expires_ms": o.expires_ms, "filled_ms": o.filled_ms, "fill_price": o.fill_price,
        "cancelled_ms": o.cancelled_ms, "cancel_reason": o.cancel_reason, "trade_id": o.trade_id, "tag": o.tag_json,
    }


def utc_date(ms: int) -> date:
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).date()


def mae_mfe(
    entry_price: float, stop_dist: float, a_ms: int, b_ms: int, candles15: Candles, minutes: MinuteIndex | None,
) -> tuple[float, float]:
    """Spec §4.10 ``mae_r``, ``mfe_r`` over ``[a_ms, b_ms)``: 1m candles when they cover it, else 15m.

    ``mae_r = (min low - entry_price) / stop_dist`` (<= 0 for a fill),
    ``mfe_r = (max high - entry_price) / stop_dist``.
    """
    hl = minutes.span(a_ms, b_ms) if minutes is not None else None
    if hl is None or len(hl[0]) == 0:
        a15 = a_ms - a_ms % CANDLE_15M_MS
        i, j = candles15.index_at(a15), candles15.index_at(b_ms)
        hl = candles15.h[i:j], candles15.l[i:j]
    highs, lows = hl
    return (float(lows.min()) - entry_price) / stop_dist, (float(highs.max()) - entry_price) / stop_dist


def _ms_of(tag: dict, key: str, ts: np.ndarray) -> int | None:
    j = tag.get(key)
    if isinstance(j, (int, np.integer)) and not isinstance(j, bool) and 0 <= j < len(ts):
        return int(ts[j])
    return None


def trade_row(
    p: Position, tc: TradeCosts, ids: dict, *, candles15: Candles, session_open_ms: int,
    atr_at_entry: float, minutes: MinuteIndex | None,
) -> dict:
    """One row of the ``trades`` table for the closed position ``p``."""
    e = p.entry
    o = e.order
    mae, mfe = mae_mfe(p.entry_price, e.stop_dist, p.fill_ms, p.exit_ms + p.exit_span_ms, candles15, minutes)
    return {
        "trade_id": p.trade_id, **ids, "session_id": o.session_id,
        "session_open_ms": None if session_open_ms < 0 else session_open_ms,
        "candidate_ms": _ms_of(e.tag, "candidate_idx", candles15.ts),
        "impulse_ms": _ms_of(e.tag, "impulse_idx", candles15.ts),
        "displacement_ms": _ms_of(e.tag, "displacement_idx", candles15.ts),
        "placed_ms": o.placed_ms,
        "entry_idx": p.fill_idx, "entry_ms": p.fill_ms, "entry_price": p.entry_price,
        "planned_entry": o.price, "stop_price": e.stop, "target_price": e.target, "stop_dist": e.stop_dist,
        "pierce_abs": e.pierce_abs, "deadline_ms": p.deadline_ms,
        "exit_idx": p.exit_idx, "exit_ms": p.exit_ms, "exit_ref_price": p.exit_ref, "exit_price": p.exit_price,
        "exit_reason": p.exit_reason, "exit_role": p.exit_role,
        "qty": p.qty, "notional": e.notional,
        "equity_at_entry": e.equity_at_entry, "risk_usd": e.risk_usd,
        "actual_risk_usd": p.qty * (p.entry_price - e.stop),
        "implied_leverage": e.implied_leverage, "max_iso_leverage": e.max_iso_leverage,
        "gross_pnl": tc.gross_pnl, "fees": tc.fees, "funding": tc.funding,
        "slippage_cost": tc.slippage_cost, "net_pnl": tc.net_pnl,
        "gross_r": tc.gross_r, "net_r": tc.net_r, "cost_r": tc.cost_r, "funding_r": tc.funding_r,
        "c_maker_entry": tc.c_maker_entry, "c_maker_exit": tc.c_maker_exit,
        "c_taker_exit": tc.c_taker_exit, "c_slip": tc.c_slip,
        "mae_r": mae, "mfe_r": mfe, "hold_minutes": (p.exit_ms - p.fill_ms) // MINUTE_MS,
        "atr_at_entry": atr_at_entry,
        "stop_dist_atr": e.stop_dist / atr_at_entry if atr_at_entry > 0 else float("nan"),  # NaN ATR: NaN
        "regime_trend": None, "regime_vol": None,  # Phase 5 (stats/regimes.py) owns the labels
        "dow": (p.fill_ms // DAY_MS + 3) % 7,  # 1970-01-01 was a Thursday; Monday = 0
        "entry_hour_utc": (p.fill_ms % DAY_MS) // 3_600_000,
        "fill_resolution": p.fill_resolution, "exit_resolution": p.exit_resolution,
    }
