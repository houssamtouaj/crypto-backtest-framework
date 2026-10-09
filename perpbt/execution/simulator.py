"""The candle loop (spec §4.1): one isolated variant, deterministic, no randomness.

For each 15m candle ``i`` from ``period_start`` to the last candle before
``period_end`` (exclusive):

1. funding events up to the candle's close are charged to the positions
   open at them (spec §4.5; events must lie on the 15m grid);
2. pending entries and open positions are evaluated against candle ``i``
   in ``order_id`` order (``fills.resolve_candle``: §4.3, and §4.4 with
   1m candles), then time exits at the close;
3. mark-to-market at ``close[i]`` gives the strategy's ``AccountView``;
4. ``strategy.on_candle`` (events from step 2 are delivered just before);
5. intents are applied: placement with sizing, the leverage cap and the
   liquidation assertion (§4.6); cancels; closes.

On the period's last candle every open position is then closed at its
close (``data_end``) and every pending order cancelled (§4.9). The daily
mark is written after the whole candle, at the last candle of each UTC day
and at the period's last candle (§4.8).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from perpbt.config import ExecConfig
from perpbt.data.sessions import SessionCalendar
from perpbt.data.store import DAY_MS, Candles, Funding
from perpbt.execution import trades as tables
from perpbt.execution.costs import entry_fee, exit_costs, funding_amount, trade_costs
from perpbt.execution.fills import CANDLE_15M_MS, MINUTE_MS, MinuteIndex, levels, resolve_candle
from perpbt.execution.ledger import Ledger
from perpbt.execution.orders import Entry, Order, Position
from perpbt.execution.sizing import check_liquidation, leverage_ok, size_order
from perpbt.strategy.base import (
    AccountView,
    CancelOrder,
    ClosePosition,
    OrderView,
    PlaceBracketLimit,
    PositionView,
    SimEvent,
    Strategy,
    build_market_view,
)

log = logging.getLogger(__name__)

TABLES = {
    "orders": tables.ORDERS_SCHEMA, "fills": tables.FILLS_SCHEMA, "trades": tables.TRADES_SCHEMA,
    "daily": tables.DAILY_SCHEMA, "events": tables.EVENTS_SCHEMA,
}


@dataclass
class SimResult:
    orders: pd.DataFrame
    fills: pd.DataFrame
    trades: pd.DataFrame
    daily: pd.DataFrame
    events: pd.DataFrame
    skips: dict[str, int]
    summary: dict

    def to_parquet(self, out_dir: str | Path) -> None:
        """Write the five tables as ``<name>.parquet`` (byte-identical across reruns)."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        for name, schema in TABLES.items():
            tables.write_table(getattr(self, name), schema, out / f"{name}.parquet")


def run(
    candles15: Candles,
    candles1m: Candles | None,
    funding: Funding,
    calendar: SessionCalendar,
    strategy: Strategy,
    exec_cfg: ExecConfig,
    period_start_ms: int,
    period_end_ms: int,
    *,
    variant_id: str = "",
) -> SimResult:
    """Simulate ``strategy`` on candles ``period_start_ms <= τ < period_end_ms`` (spec §4.1).

    ``candles15`` must start early enough for the strategy's warmup (the
    view exposes every candle up to the current one). With
    ``exec_cfg.use_1m`` the 1m candles resolve ambiguous 15m candles and
    MAE/MFE; ``use_1m`` without ``candles1m`` raises ValueError.
    """
    return _Simulator(
        candles15, candles1m, funding, calendar, strategy, exec_cfg, period_start_ms, period_end_ms, variant_id,
    ).run()


class _Simulator:
    def __init__(
        self, candles15: Candles, candles1m: Candles | None, funding: Funding, calendar: SessionCalendar,
        strategy: Strategy, cfg: ExecConfig, period_start_ms: int, period_end_ms: int, variant_id: str,
    ) -> None:
        if candles15.tf != "15m":
            raise ValueError(f"run: candles15 must be 15m candles, got {candles15.tf!r}")
        if cfg.use_1m and candles1m is None:
            raise ValueError("run: exec_cfg.use_1m is set but no 1m candles were given")
        for name, other in (("candles1m", candles1m), ("funding", funding)):
            if other is not None and other.pair != candles15.pair:
                raise ValueError(f"run: {name} are for {other.pair}, candles15 for {candles15.pair}")
        self.cd = candles15
        self.cfg = cfg
        self.strategy = strategy
        self.calendar = calendar
        self.minutes = MinuteIndex(candles1m) if cfg.use_1m else None
        self.i0 = candles15.index_at(period_start_ms)
        self.i_last = candles15.index_at(period_end_ms) - 1
        if self.i_last < self.i0:
            raise ValueError(f"run: no 15m candle in [{period_start_ms}, {period_end_ms})")
        self.period = (int(period_start_ms), int(period_end_ms))
        first_ms, last_close_ms = int(candles15.ts[self.i0]), int(candles15.ts[self.i_last]) + CANDLE_15M_MS
        if first_ms > self.period[0] or last_close_ms < self.period[1]:
            log.warning("%s: the 15m candles cover %d..%d ms of the requested period %d..%d ms",
                        candles15.pair, first_ms, last_close_ms, *self.period)
        if self.i0 < strategy.warmup_bars:
            log.warning("%s: only %d candles before the first decision; %s wants %d for warmup",
                        candles15.pair, self.i0, strategy.name, strategy.warmup_bars)
        self.coverage = {"first_candle_ms": first_ms, "last_close_ms": last_close_ms, "warmup_candles": self.i0}
        self.ids = {"variant_id": variant_id, "pair": candles15.pair, "session_variant": calendar.spec.name}
        self.view = build_market_view(candles15, calendar, swing_k=strategy.params.swing_k, start_i=self.i0)
        self.pierce = float(strategy.params.pierce)
        self.ts = candles15.ts.tolist()
        self.o = candles15.o.tolist()
        self.h = candles15.h.tolist()
        self.l = candles15.l.tolist()  # noqa: E741
        self.c = candles15.c.tolist()
        self.sid = calendar.session_id
        self.sopen = calendar.open_ms
        self.send = calendar.end_ms

        # Funding events up to the last candle's close, from the first candle's open.
        first, stop = int(self.ts[self.i0]), int(self.ts[self.i_last]) + CANDLE_15M_MS
        sl = slice(int(funding.ts.searchsorted(first)), int(funding.ts.searchsorted(stop)))
        self.f_ts = funding.ts[sl].tolist()
        self.f_rate = funding.rate[sl].tolist()
        off = [f for f in self.f_ts if f % CANDLE_15M_MS]
        if off:
            raise ValueError(f"run: funding events off the 15m grid are not supported, first at {off[0]} ms")
        self.f_ptr = 0

        self.ledger = Ledger(cfg.start_equity)
        self.next_order_id = 1
        self.next_trade_id = 1
        self.orders: list[Order] = []
        self.active: dict[int, Entry | Position] = {}  # by entry order_id; insertion order == order_id order
        self.open: dict[int, Position] = {}  # by trade_id
        self.fill_rows: list[dict] = []
        self.trade_rows: list[dict] = []
        self.event_rows: list[dict] = []
        self.queue: list[SimEvent] = []
        self.n_leverage = 0
        self.n_missing_1m = 0
        self.max_concurrent = 0

    # --- the loop --------------------------------------------------------------------------

    def run(self) -> SimResult:
        ts = self.ts
        for i in range(self.i0, self.i_last + 1):
            self.view.advance_to(i)
            self._funding(i)
            if self.active:
                self._evaluate(i)
            account = self._account(i)
            self._deliver()
            for intent in self.strategy.on_candle(self.view, account):
                self._apply(intent, i)
            if i == self.i_last:
                self._data_end(i)
            self._deliver()
            self.max_concurrent = max(self.max_concurrent, len(self.open))
            if i == self.i_last or ts[i + 1] // DAY_MS != ts[i] // DAY_MS:
                self._mark_day(i)
        return self._result()

    def _deliver(self) -> None:
        for ev in self.queue:
            self.strategy.on_event(ev)
        self.queue.clear()

    def _event(self, i: int, kind: str, *, order_id: int | None = None, trade_id: int | None = None,
               reason: str | None = None, price: float | None = None, qty: float | None = None,
               amount: float | None = None) -> None:
        self.event_rows.append({
            "event_id": len(self.event_rows) + 1, "idx": i, "ts_ms": self.ts[i], "kind": kind,
            "order_id": order_id, "trade_id": trade_id, "reason": reason, "price": price, "qty": qty,
            "amount": amount,
        })

    def _new_order(self, kind: str, side: str, price: float, qty: float, status: str, placed_ms: int,
                   placed_idx: int, session_id: int, tag_json: str, **kw) -> Order:
        o = Order(self.next_order_id, kind, side, price, qty, status, placed_ms, placed_idx, session_id, tag_json, **kw)
        self.next_order_id += 1
        self.orders.append(o)
        return o

    # --- step 1: funding (spec §4.5) -------------------------------------------------------

    def _funding(self, i: int) -> None:
        end = self.ts[i] + CANDLE_15M_MS
        while self.f_ptr < len(self.f_ts) and self.f_ts[self.f_ptr] < end:
            rate = self.f_rate[self.f_ptr]
            self.f_ptr += 1
            if not self.open:
                continue
            price = self.c[i - 1]  # the close of the candle ending at f (or the last close before it)
            for p in self.open.values():
                amount = funding_amount(rate, p.qty, price)
                p.funding_usd += amount
                self.ledger.debit(amount, funding=True)
                self._event(i, "funding", trade_id=p.trade_id, price=price, qty=p.qty, amount=amount)

    # --- step 2: fills and exits (spec §4.3, §4.4) ----------------------------------------

    def _evaluate(self, i: int) -> None:
        tau = self.ts[i]
        close_ms = tau + CANDLE_15M_MS
        o, h, l = self.o[i], self.h[i], self.l[i]  # noqa: E741
        minutes = self.minutes.window(tau) if self.minutes is not None else None
        for key in list(self.active):
            item = self.active[key]
            if isinstance(item, Entry):
                order = item.order
                if order.expires_ms <= tau:
                    self._cancel(item, i, "expired", order.expires_ms)
                    continue
                out = resolve_candle(True, o, h, l, item.lv, minutes, first_look=i == order.placed_idx + 1)
                self._note_missing(out.resolution, i, key)
                if not out.filled:
                    if order.expires_ms <= close_ms:
                        self._cancel(item, i, "expired", order.expires_ms)
                    continue
                pos = self._fill(item, i, out)
            else:
                pos = item
                out = resolve_candle(False, o, h, l, pos.entry.lv, minutes)
                self._note_missing(out.resolution, i, key)
            pos.label_idx, pos.label = i, out.resolution
            if out.exit is not None:
                minute = out.exit_minute
                ts_ms = tau if minute < 0 else tau + minute * MINUTE_MS
                end = close_ms if minute < 0 else ts_ms + MINUTE_MS
                role = "maker" if out.exit == "target" else "taker"
                self._close(pos, i, out.exit, out.exit_ref, role, ts_ms, end, out.resolution)
            elif pos.deadline_ms is not None and pos.deadline_ms <= close_ms:
                self._time_exit(pos, i, pos.entry.hold_rule.kind, out.resolution)

    def _note_missing(self, resolution: str, i: int, order_id: int) -> None:
        if resolution == "15m_pessimistic_missing_1m":
            self.n_missing_1m += 1
            self._event(i, "missing_1m", order_id=order_id)
            log.warning("%s: 1m candles missing inside the ambiguous 15m candle at %d ms (order %d); "
                        "15m pessimistic rule applied", self.cd.pair, self.ts[i], order_id)

    def _fill(self, e: Entry, i: int, out) -> Position:
        tau = self.ts[i]
        order = e.order
        fill_ms = tau if out.fill_minute < 0 else tau + out.fill_minute * MINUTE_MS
        price = out.fill_price
        trade_id = self.next_trade_id
        self.next_trade_id += 1
        order.status, order.filled_ms, order.fill_price, order.trade_id = "filled", fill_ms, price, trade_id
        fee = entry_fee(order.qty, price, self.cfg)
        self.ledger.debit(fee)
        resolution = "open_gap" if out.fill_gap else out.resolution
        self._fill_row(order.order_id, trade_id, fill_ms, price, price, order.qty, fee, "maker", 0.0, resolution)
        hold = e.hold_rule
        if hold.kind == "none":
            deadline = None
        elif hold.kind == "session_end":
            deadline = e.session_end_ms
        else:
            deadline = tau + CANDLE_15M_MS + round(hold.hours * 3_600_000)
        common = dict(placed_ms=fill_ms, placed_idx=i, session_id=order.session_id, tag_json=order.tag_json,
                      trade_id=trade_id)
        stop_o = self._new_order("stop", "sell", e.stop, order.qty, "pending", **common)
        target_o = self._new_order("target_limit", "sell", e.target, order.qty, "pending", **common)
        pos = Position(trade_id, e, order.qty, price, i, fill_ms, resolution, deadline, stop_o, target_o)
        self.active[order.order_id] = pos
        self.open[trade_id] = pos
        self._event(i, "filled", order_id=order.order_id, trade_id=trade_id, price=price, qty=order.qty)
        self.queue.append(SimEvent("filled", i, order_id=order.order_id, position_id=trade_id))
        return pos

    def _fill_row(self, order_id, trade_id, ts_ms, ref, price, qty, fee, role, slip, resolution) -> None:
        self.fill_rows.append({
            "fill_id": len(self.fill_rows) + 1, "order_id": order_id, "trade_id": trade_id, "ts_ms": ts_ms,
            "ref_price": ref, "price": price, "qty": qty, "fee": fee, "fee_role": role, "slippage": slip,
            "resolution": resolution,
        })

    def _time_exit(self, p: Position, i: int, reason: str, resolution: str) -> None:
        """Exit ``p`` at ``close[i]`` (taker): deadlines, strategy closes, data end.

        Stamped at the candle's open like every 15m fill, but never before the
        entry: a position the 1m walk filled at minute ``m`` of this candle
        exits at that minute's stamp, so ``hold_minutes`` is 0, not ``-m``.
        """
        ts_ms = max(self.ts[i], p.fill_ms)
        self._close(p, i, reason, self.c[i], "taker", ts_ms, self.ts[i] + CANDLE_15M_MS, resolution)

    def _close(self, p: Position, i: int, reason: str, exit_ref: float, role: str, ts_ms: int, window_end_ms: int,
               resolution: str) -> None:
        """Exit ``p`` at reference ``exit_ref``: ``reason`` is the exit reason (stop, target, a hold kind, ...).

        ``ts_ms`` stamps the exit fill (and the exit order's placement and fill);
        ``window_end_ms`` ends the MAE/MFE window.
        """
        fee, slip, price = exit_costs(p.qty, exit_ref, role, self.cfg)
        self.ledger.credit(p.qty * (price - p.entry_price) - fee)
        if reason == "stop":
            filled, siblings = p.stop_order, (p.target_order,)
        elif reason == "target":
            filled, siblings = p.target_order, (p.stop_order,)
        else:
            o = p.entry.order
            filled = self._new_order("time_exit", "sell", exit_ref, p.qty, "pending", ts_ms, i,
                                     o.session_id, o.tag_json, trade_id=p.trade_id)
            siblings = (p.stop_order, p.target_order)
        filled.status, filled.filled_ms, filled.fill_price = "filled", ts_ms, price
        for s in siblings:
            s.status, s.cancelled_ms, s.cancel_reason = "cancelled", ts_ms, "oco"
        self._fill_row(filled.order_id, p.trade_id, ts_ms, exit_ref, price, p.qty, fee, role, slip, resolution)
        p.exit_idx, p.exit_ms, p.exit_ref, p.exit_price = i, ts_ms, exit_ref, price
        p.exit_reason, p.exit_role, p.exit_resolution, p.exit_window_end_ms = reason, role, resolution, window_end_ms
        del self.active[p.entry.order.order_id]
        del self.open[p.trade_id]
        e = p.entry
        tc = trade_costs(qty=p.qty, entry_price=p.entry_price, exit_ref=exit_ref, stop_dist=e.stop_dist,
                         risk_usd=e.risk_usd, exit_role=role, funding_usd=p.funding_usd, cfg=self.cfg)
        atr_prev = self.view.atr(p.fill_idx - 1) if p.fill_idx > 0 else float("nan")
        self.trade_rows.append(tables.trade_row(
            p, tc, self.ids, candles15=self.cd, session_open_ms=int(self.sopen[e.order.placed_idx]),
            atr_at_entry=atr_prev, minutes=self.minutes,
        ))
        self._event(i, "closed", order_id=filled.order_id, trade_id=p.trade_id, reason=reason, price=price, qty=p.qty)
        self.queue.append(SimEvent("closed", i, position_id=p.trade_id, reason=reason))

    def _cancel(self, e: Entry, i: int, reason: str, at_ms: int) -> None:
        o = e.order
        o.status, o.cancelled_ms, o.cancel_reason = "cancelled", at_ms, reason
        del self.active[o.order_id]
        self._event(i, "cancelled", order_id=o.order_id, reason=reason)
        self.queue.append(SimEvent("cancelled", i, order_id=o.order_id, reason=reason))

    # --- steps 3-5 ---------------------------------------------------------------------------

    def _equity(self, i: int) -> float:
        return self.ledger.equity(self.open.values(), self.c[i])

    def _account(self, i: int) -> AccountView:
        positions = tuple(
            PositionView(p.trade_id, "long", p.entry_price, p.entry.stop, p.entry.target, p.qty, p.fill_idx)
            for p in self.open.values()
        )
        pending = tuple(
            OrderView(e.order.order_id, "long", e.order.price, e.stop, e.target, e.order.expires_ms, e.order.placed_idx)
            for e in self.active.values() if isinstance(e, Entry)
        )
        return AccountView(self._equity(i), positions, pending)

    def _apply(self, intent, i: int) -> None:
        if isinstance(intent, PlaceBracketLimit):
            self._place(intent, i)
        elif isinstance(intent, CancelOrder):
            e = self.active.get(intent.order_id)
            if not isinstance(e, Entry):
                raise ValueError(f"CancelOrder: order {intent.order_id} is not a pending entry")
            self._cancel(e, i, "strategy", self.ts[i] + CANDLE_15M_MS)
        elif isinstance(intent, ClosePosition):
            p = self.open.get(intent.position_id)
            if p is None:
                raise ValueError(f"ClosePosition: position {intent.position_id} is not open")
            label = p.label if p.label_idx == i else "15m_unambiguous"  # the label of the candle it happens on
            self._time_exit(p, i, "strategy", label)
        else:
            raise TypeError(f"unknown intent {type(intent).__name__}")

    def _place(self, intent: PlaceBracketLimit, i: int) -> None:
        if intent.side != "long":
            raise NotImplementedError("short brackets are deferred (overview §11)")
        equity = self._equity(i)
        sz = size_order(equity, intent.price, intent.stop, self.cfg)
        placed_ms = self.ts[i] + CANDLE_15M_MS
        session_id = int(self.sid[i])
        tag_json = json.dumps(intent.tag, allow_nan=False)
        order = self._new_order("entry_limit", "buy", intent.price, sz.qty, "pending", placed_ms, i, session_id,
                                tag_json, expires_ms=intent.expires_ms)
        open_notional = sum(p.qty * self.c[i] for p in self.open.values())
        if not leverage_ok(sz, open_notional, equity, self.cfg):
            order.status, order.cancelled_ms, order.cancel_reason = "cancelled", placed_ms, "leverage_cap"
            self.n_leverage += 1
            self._event(i, "skipped_leverage", order_id=order.order_id, price=intent.price, qty=sz.qty)
            self.queue.append(SimEvent("skipped_leverage", i, order_id=order.order_id, reason="leverage_cap"))
            return
        check_liquidation(intent.price, intent.stop, equity, sz.notional, self.cfg.mmr)
        session_end = int(self.send[i])
        if intent.hold_rule.kind == "session_end" and session_end < 0:
            raise ValueError(f"a session_end hold rule needs a session: candle {i} is outside every window")
        pierce_abs = self.pierce * intent.price  # exactly as Phase 3 computes it
        self.active[order.order_id] = Entry(
            order=order, lv=levels(intent.price, intent.stop, intent.target, pierce_abs),
            stop=intent.stop, target=intent.target, pierce_abs=pierce_abs, hold_rule=intent.hold_rule,
            session_end_ms=session_end, stop_dist=sz.stop_dist, risk_usd=sz.risk_usd, notional=sz.notional,
            equity_at_entry=equity, implied_leverage=sz.implied_leverage, max_iso_leverage=sz.max_iso_leverage,
            tag=intent.tag,
        )
        self._event(i, "placed", order_id=order.order_id, price=intent.price, qty=sz.qty)

    def _data_end(self, i: int) -> None:
        for key in list(self.active):
            item = self.active[key]
            if isinstance(item, Entry):
                self._cancel(item, i, "data_end", self.ts[i] + CANDLE_15M_MS)
            else:
                self._time_exit(item, i, "data_end", "15m_unambiguous")

    def _mark_day(self, i: int) -> None:
        close = self.c[i]
        exposure = sum(p.qty * close for p in self.open.values())
        day = self.ts[i] - self.ts[i] % DAY_MS
        self.ledger.mark_day(day, self._equity(i), len(self.open), exposure)

    # --- result --------------------------------------------------------------------------

    def _result(self) -> SimResult:
        ids = self.ids
        orders = tables.frame([tables.order_row(o, ids) for o in self.orders], tables.ORDERS_SCHEMA)
        fills = tables.frame(self.fill_rows, tables.FILLS_SCHEMA)
        trades = tables.frame(self.trade_rows, tables.TRADES_SCHEMA).sort_values("trade_id", kind="stable")
        trades = trades.reset_index(drop=True)
        daily_rows = [
            {**ids, "date": tables.utc_date(r["date_ms"]), **{k: v for k, v in r.items() if k != "date_ms"}}
            for r in self.ledger.rows
        ]
        daily = tables.frame(daily_rows, tables.DAILY_SCHEMA)
        events = tables.frame(self.event_rows, tables.EVENTS_SCHEMA)
        skips = {**self.strategy.skip_counts(), "leverage": self.n_leverage}
        entries = orders[orders["kind"] == "entry_limit"]
        n_orders = int((entries["cancel_reason"] != "leverage_cap").sum())
        n_filled = int((entries["status"] == "filled").sum())
        n_data_end = int((trades["exit_reason"] == "data_end").sum())
        summary = {
            **ids,
            "period_start_ms": self.period[0], "period_end_ms": self.period[1],
            "first_idx": self.i0, "last_idx": self.i_last, "n_candles": self.i_last - self.i0 + 1,
            **self.coverage,
            "start_equity": self.ledger.start_equity,
            "final_equity": self._equity(self.i_last),
            "n_intents": len(entries), "n_orders": n_orders, "n_filled": n_filled,
            "fill_rate": n_filled / n_orders if n_orders else None,
            "n_trades": len(trades), "n_data_end": n_data_end, "n_trades_r": len(trades) - n_data_end,
            "n_leverage_skips": self.n_leverage,
            "n_missing_1m": self.n_missing_1m,  # ambiguous (candle, order) pairs resolved without full 1m
            "max_concurrent": self.max_concurrent,
            "funding_total": float(np.sum(trades["funding"])) if len(trades) else 0.0,
            "resolution_counts": {k: int(v) for k, v in sorted(fills["resolution"].value_counts().items())},
        }
        return SimResult(orders, fills, trades, daily, events, skips, summary)
