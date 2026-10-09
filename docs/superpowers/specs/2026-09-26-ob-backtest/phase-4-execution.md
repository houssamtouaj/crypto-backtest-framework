# Phase 4 — Execution

Read with `00-overview.md`. Delivers the simulator: order lifecycle,
pessimistic 15m fills with optional 1m resolution, costs, funding, sizing,
leverage cap, liquidation assertion, ledger, daily marks, and the trades
table that every statistic consumes. Depends on Phase 3.

**Branch:** `phase/4-execution`, created from `dev` after Phase 3 is
merged; merged into `dev` with `--no-ff` when the exit criterion below is
met (overview §8.1).

## 4.1 Simulator loop (`execution/simulator.py`)

```python
def run(candles15: Candles, candles1m: Candles | None, funding: Funding,
        calendar: SessionCalendar, strategy: Strategy, exec_cfg: ExecConfig,
        period_start_ms: int, period_end_ms: int, *, variant_id: str = "") -> SimResult

@dataclass
class SimResult:
    orders: pd.DataFrame; fills: pd.DataFrame; trades: pd.DataFrame
    daily: pd.DataFrame; events: pd.DataFrame; skips: dict[str, int]
    summary: dict
    def to_parquet(self, out_dir) -> None   # the five tables, byte-identical across reruns
```

`period_end_ms` is exclusive (`date_ms(period_end) + 1 day`); `pair` comes
from `candles15.pair`, `session_variant` from `calendar.spec.name`. The
simulator builds the strategy's view with `strategy.base.build_market_view`
(ATR14, completed-day SMA(50) and ADX(14), swing highs of
`params.swing_k`), the same builder the Phase 3 harness uses. `skips` is
`strategy.skip_counts()` plus `leverage`; `summary` holds the counts
(`n_intents`, `n_orders`, `n_filled`, `fill_rate`, `n_trades`,
`n_data_end`, `n_trades_r`, `n_leverage_skips`, `n_missing_1m`,
`max_concurrent`), the start and final equity, total funding and the
resolution counts over fills.

For each candle `i` from the first decision candle (`period_start`) to
`period_end` (the strategy's `warmup_bars` before `period_start` are
available through the view but produce no intents):

1. **Funding** at every funding timestamp `f` with `τ_i ≤ f < τ_i + 15m`
   is charged to positions open at `f` (rule in 4.5).
2. **Pending orders and open positions** are evaluated against candle `i`
   in `order_id` order using 4.3 (and 4.4 when 1m is available). Fills,
   stops, targets, expiries, and time exits produce events and update the
   ledger.
3. **Mark-to-market** at `close[i]`: the equity of the `AccountView` the
   strategy sees.
4. `intents = strategy.on_candle(view_at(i), account_view)`.
5. **Placement**: for each `PlaceBracketLimit`, size (4.6), check the
   leverage cap, assert liquidation below the stop, and register the order
   with `placed_idx = i`. Orders placed here are first evaluated at `i+1`.

Events of step 2 are delivered to `strategy.on_event` just before step 4,
those of step 5 just after it. On the period's last candle the data-end
close (4.9) follows step 5. The **daily mark** is written after the whole
candle, at every candle whose next candle opens on a later UTC day and at
the period's last candle. Without gaps this is "`τ_i + 15m` is 00:00 UTC"
plus a final mark, so the daily returns compound exactly to the final
equity, and a strategy close or the data-end close on a day's last candle
is in that day's mark.

The first `on_candle` call is at `period_start`: the strategy rebuilds its
state from the earlier candles through the view (Phase 3 §3.1), so the
loop does not run over the warmup and skip counts cover the period only.

The simulator has no randomness. Running twice yields byte-identical
tables.

## 4.2 Orders, positions, events (`execution/orders.py`)

Order kinds: `entry_limit`, `stop`, `target_limit`, `time_exit`. Statuses:
`pending`, `filled`, `cancelled`. Cancel reasons: `expired`, `strategy`,
`leverage_cap`, `data_end`, `oco`. A filled `entry_limit` creates a
`Position` with its stop and target as attached orders (rows created at the
fill, `placed_ms` = the fill's `ts_ms`) and a hold deadline computed from
the hold rule at fill time: none; `session_end` → the end of the window of
the placement candle; `max_hold` → fill candle close + `hours`. When one
exit fills, its siblings are cancelled with `oco`; a time exit (deadline,
data end, a strategy `ClosePosition`) is a `time_exit` order created and
filled at that close. A leverage-capped intent still gets an `entry_limit`
row (status `cancelled`, reason `leverage_cap`, never evaluated) so every
intent is auditable; `n_orders` counts only the others. `CancelOrder`
cancels a pending entry with `strategy`; `ClosePosition` exits at
`close[i]` as a taker time exit with `exit_reason = strategy` (the
order-block strategy emits neither). A `short` bracket raises
`NotImplementedError` (overview §11).

## 4.3 Fills and exits on 15m candles (pessimistic)

For each candle `j` after placement (`j > placed_idx`), in this order:

1. **Entry** (pending, `expires_ms > τ_j`): if `open[j] ≤ entry − pierce_abs`
   → fill at `open[j]`, resolution `open_gap`; else if
   `low[j] ≤ entry − pierce_abs` → fill at `entry`. The fill price is the
   limit price: pierce models queue position, not price improvement. Maker
   fee. A pending order whose `expires_ms ≤ τ_j` is cancelled (`expired`)
   before this check; an order expiring at `τ_j + 15m` can still fill on
   `j`, and if it does not it is cancelled after the check, so it is not
   pending in the `AccountView` of that close (`cancelled_ms = expires_ms`).
2. **Stop** (open position, including one filled on this same candle): if
   `low[j] ≤ stop` → exit reference `min(stop, open[j])`, exit price
   `reference × (1 − slippage)`, taker fee.
3. **Target** (open position, *not* one filled on this same candle): if
   `high[j] ≥ target + pierce_abs` → exit at `target`, maker fee, no
   slippage. A resting sell limit fills at its price, never better (user
   ruling 2026-10-09, replacing `max(target, open[j])`): on these 24/7
   markets an open above the target only follows a candle whose target
   touch the same-candle rule ignored, and paying that open made 17 of
   about 9,600 real trades (2020–2025, three pairs, three sessions) exit
   above their target, about +10 R in total, all in the strategy's favour.
4. **Time exit** (open position whose deadline is `≤ τ_j + 15m`, equality
   on the grid): exit reference `close[j]`, exit price
   `close[j] × (1 − slippage)`, taker fee. A `session_end` position filled
   on the window's last candle exits at that close.

So on one 15m candle: fill and stop → stopped; fill and target → no target
that candle; stop and target → stop. The `resolution` column records which
rule decided: `15m_unambiguous` (only one event touched),
`15m_pessimistic` (two or more touched, no 1m), `1m` (resolved by 4.4),
`1m_pessimistic` (a single 1m candle touched two events),
`15m_pessimistic_missing_1m`, or `open_gap`. Every fill of one order on one
candle carries that candle's label, except an entry filled at an open
price, which is `open_gap`. A time exit carries the label of the candle it
happens on; a data-end exit is `15m_unambiguous`.

## 4.4 1m resolution (`use_1m`)

For a 15m candle where two or more of {entry, stop, target} are touched
for the same order/position, the resolver walks the 15 one-minute candles
inside it, applying 4.3 per 1m candle (entry then stop then target, same
same-candle rules). Time exits are never resolved by 1m; they happen at the
15m close. If any of the 15 one-minute candles is missing, the 15m
pessimistic rule applies and the case is logged (`logging.warning`, an
`events` row of kind `missing_1m`, `summary["n_missing_1m"]`). The 1m
walk's result is trusted even where the 1m data do not aggregate to the
15m bar: an entry the 15m bar says filled may stay pending. `use_1m`
without 1m candles raises `ValueError`; with `use_1m` off, 1m candles are
ignored (resolver and MAE/MFE). The 1m path is only
consulted when the 15m path is ambiguous, so it can never make an
unambiguous 15m outcome worse or better.

## 4.5 Funding

Charged at every funding timestamp `f` with
`fill_candle_close ≤ f ≤ exit_candle_open`, using the actual timestamps
and intervals from the funding table. Amount `rate × qty × close_price_at_f`
where `close_price_at_f` is the close of the candle ending at `f`; longs
pay positive rates. Boundary cases: fill candle close equals `f` → charged;
exit candle open equals `f` → charged; exit candle close equals `f` → not
charged.

Funding events must lie on the 15m grid (all real events do: none of
19,033 is off-grid, checked 2026-10-09); an off-grid event raises
`ValueError` rather than guessing a "close of the candle ending at `f`".
An event that falls in a gap between candles is charged at the next
candle's step 1 at the last close before it, which is the predicate above
applied to gapped data. Each charge is an `events` row of kind `funding`.

## 4.6 Sizing, leverage, liquidation (`execution/sizing.py`)

At placement (close of `t`):

- `risk_usd = risk_per_trade × equity_mtm`
- `qty = risk_usd / stop_dist` (fractional, no lot rounding)
- `notional = qty × entry`
- If `notional / equity_mtm > max_leverage` **or**
  `(open_notional + notional) / equity_mtm > max_leverage` (with
  `open_notional = Σ qty × close[t]` over open positions, marked like
  `equity_mtm`; pending orders do not count) the order is
  not registered, the event `skipped_leverage` is emitted to the strategy
  (which consumes the session, Phase 3 §3.5), and the skip is counted.
- Liquidation assertion (cross margin, single-position approximation):
  `liq = entry × (1 − equity_mtm / notional + mmr)`. The simulator raises
  `LiquidationAboveStopError` if `liq ≥ stop`. This is a hard failure of
  the run, not a log line; with 1% risk it can only trigger when
  `stop_dist / entry < mmr / 99`, so a trigger means a bug or a degenerate
  stop.
- Sizing a non-positive or non-finite `equity_mtm` raises `ValueError`
  (impossible at 1% risk; a strategy or config that drives equity to zero
  must not size a negative order).
- Recorded per trade: `implied_leverage = notional / equity_mtm` and
  `max_iso_leverage = 1 / (stop_dist / entry + mmr)`, the largest isolated
  leverage setting that keeps liquidation below the stop.

## 4.7 Cost accounting in R (`execution/costs.py`)

Every cost is stored both in USDT and as an R coefficient so the trade
table can be re-priced exactly (Phase 5 §5.6). With `stop_dist` fixed at
placement and `risk_usd = qty × stop_dist`:

| quantity | definition |
|---|---|
| `gross_r` | `qty × (exit_ref − entry_price) / risk_usd` (exit reference price before slippage) |
| `c_maker_entry` | `entry_price / stop_dist` |
| `c_maker_exit` | `exit_ref / stop_dist` if the exit was a target, else 0 |
| `c_taker_exit` | `exit_ref / stop_dist` if the exit was a stop, time exit, or data end, else 0 |
| `c_slip` | same as `c_taker_exit` (slippage applies to taker exits only) |
| `funding_r` | `funding_usd / risk_usd` |
| `net_r` | `gross_r − fee_maker × (c_maker_entry + c_maker_exit) − fee_taker × c_taker_exit − slippage × c_slip − funding_r` |
| `cost_r` | `gross_r − net_r` |

`net_pnl` in USDT is `net_r × risk_usd`. The identity
`net_pnl == gross_pnl − fees − slippage_cost − funding` is asserted per
trade to 1e-9. `actual_risk_usd = qty × (entry_price − stop)` is recorded
separately; it differs from `risk_usd` only on `open_gap` fills.

## 4.8 Ledger, mark-to-market, daily marks

Equity = cash + Σ `qty × (close[i] − entry_price)` over open positions.
Fees and funding are debited from cash when they occur. The daily mark is
the equity at the close of the candle ending at 00:00 UTC (timing in 4.1).
Daily return = equity ratio − 1, the first against the start equity; days
with no position and no cash movement give exactly 0. `n_open` and
`exposure_notional` (`Σ qty × close`) are recorded at the mark.

## 4.9 Data end

Positions still open at the last candle of the period are closed at its
close as a time exit with `exit_reason = data_end`, after the last
candle's steps 1–5 (so the strategy's last intents are registered and then
cancelled). Positions and pending orders are handled in `order_id` order.
They are included in
the equity curve and excluded from R statistics; their count is reported.
Pending orders are cancelled with reason `data_end`. The holdout simulation
starts flat at its `period_start` with warmup-only access to earlier
candles.

## 4.10 Tables

### `orders`

| column | type | notes |
|---|---|---|
| order_id | int64 | |
| variant_id, pair, session_variant | str | |
| session_id | int64 | |
| kind | str | `entry_limit`, `stop`, `target_limit`, `time_exit` |
| side | str | `buy`/`sell` |
| price | float64 | limit or stop level |
| qty | float64 | |
| status | str | `pending`, `filled`, `cancelled` |
| placed_ms, placed_idx | int64 | close time and index of the decision candle |
| expires_ms | int64 | nullable |
| filled_ms, fill_price | | nullable |
| cancelled_ms, cancel_reason | | nullable |
| trade_id | int64 | nullable |
| tag | str (json) | from the intent |

`stop` and `target_limit` rows are created at the fill (`placed_ms` = the
fill's `ts_ms`, `placed_idx` = the fill candle); a `time_exit` row is
created and filled at its close (`placed_ms` = that close).

### `fills`

| column | type | notes |
|---|---|---|
| fill_id, order_id, trade_id | int64 | |
| ts_ms | int64 | open time of the fill candle (15m), or of the 1m candle when resolved by 1m |
| ref_price | float64 | before slippage |
| price | float64 | executed, after slippage |
| qty | float64 | |
| fee | float64 | USDT |
| fee_role | str | `maker`/`taker` |
| slippage | float64 | USDT |
| resolution | str | see 4.3 |

### `trades` (one row per closed position; the unit of all statistics)

| column | type | notes |
|---|---|---|
| trade_id, variant_id, pair, session_variant, session_id | | |
| session_open_ms | int64 | |
| candidate_ms, impulse_ms, displacement_ms, placed_ms | int64 | audit trail; the first three from the tag's indices, null when a strategy's tag lacks them |
| entry_idx, entry_ms, entry_price | | actual fill; `entry_idx` is the 15m fill candle |
| planned_entry, stop_price, target_price, stop_dist | float64 | fixed at placement |
| pierce_abs | float64 | `pierce × planned_entry` (Phase 5 evaluator input) |
| deadline_ms | int64 | nullable; the time-exit deadline |
| exit_idx, exit_ms, exit_ref_price, exit_price | | `exit_idx` is the 15m exit candle |
| exit_reason | str | `target`, `stop`, `session_end`, `max_hold`, `data_end`, `strategy` |
| exit_role | str | `maker`/`taker` |
| qty, notional | float64 | |
| equity_at_entry, risk_usd, actual_risk_usd | float64 | `equity_at_entry` is the `equity_mtm` used for sizing (at placement) |
| implied_leverage, max_iso_leverage | float64 | |
| gross_pnl, fees, funding, slippage_cost, net_pnl | float64 | USDT |
| gross_r, net_r, cost_r, funding_r | float64 | |
| c_maker_entry, c_maker_exit, c_taker_exit, c_slip | float64 | re-pricing coefficients |
| mae_r, mfe_r | float64 | `(min low − entry_price) / stop_dist`, `(max high − entry_price) / stop_dist` over `[entry_ms, exit_ms + span)`, span 1m or 15m by how the exit was resolved; 1m candles when `use_1m` and they cover the window, else the 15m candles |
| hold_minutes | int64 | `(exit_ms − entry_ms) / 1 min`; `entry_ms`/`exit_ms` are the fills' `ts_ms`, so a 24 h `max_hold` gives 1,440 |
| atr_at_entry, stop_dist_atr | float64 | ATR14 at `entry_idx − 1` (as baseline A uses `ATR14[e−1]`); `stop_dist / atr_at_entry`, NaN while ATR is NaN; used by baselines |
| regime_trend, regime_vol | str | null here; Phase 5 `stats/regimes.py` fills the labels as of the entry day |
| dow, entry_hour_utc | int8 | |
| fill_resolution, exit_resolution | str | |

### `daily`

| column | type | notes |
|---|---|---|
| variant_id, pair, session_variant | | |
| date | date | UTC |
| equity | float64 | mark-to-market at 00:00 UTC |
| ret | float64 | equity / previous equity − 1 |
| n_open | int16 | (int16, not int8: overlapping `none`-hold positions are unbounded in principle) |
| exposure_notional | float64 | |
| funding_paid | float64 | |

`date` is the UTC date of the marked day (stored as a Parquet date).

### `events`

| column | type | notes |
|---|---|---|
| event_id, idx, ts_ms | int64 | `idx`, `ts_ms`: the 15m candle it happened on |
| kind | str | `placed`, `skipped_leverage`, `filled`, `cancelled` (entry orders), `closed`, `funding`, `missing_1m` |
| order_id, trade_id | int64 | nullable |
| reason | str | cancel or exit reason |
| price, qty, amount | float64 | nullable; `amount` is the funding paid |

Every table has a fixed column order and dtype, also when empty.

## 4.11 Tasks and tests

- **4.1 Order lifecycle and simulator loop, no costs.**
  E1 fill when `low == entry` with `pierce = 0`, not with `pierce > 0`;
  fill when `low ≤ entry − pierce_abs`. E2 open-gap fill at the open.
  E3 fill and stop on one candle → stop, `−1 R` gross. E4 fill and target
  on one candle → no target that candle. E5 stop and target on one candle
  → stop. E6 cancel at expiry; an order expiring at the candle's close
  can still fill on that candle. E7 `session_end` and `max_hold` exits on
  the right candle, deadline from the fill. E8 no order placed at the
  close of `t` fills on `t`. E9 an order placed above the market (should
  not happen after Phase 3) fills at the next open with `open_gap`.
  E10 a leverage skip emits the event and the session is consumed.
- **4.2 1m resolver.**
  1m sequences where target precedes stop → target, and vice versa; fill
  then target within one 15m candle → target when the 1m order shows it;
  a single 1m candle touching both → `1m_pessimistic`; one missing minute
  → `15m_pessimistic_missing_1m` and logged; an unambiguous 15m candle is
  never sent to the resolver.
- **4.3 Cost model.**
  Hand-made trade with known prices → fees, slippage, funding, `net_r`,
  and the four coefficients to the cent; the R identity holds; the three
  funding boundary cases; a 4-hour interval honoured; an `open_gap` fill
  gives `actual_risk_usd < risk_usd` (a long's gap fill is below the
  planned entry, so less is at risk; this line said `>` before
  2026-10-09).
- **4.4 Sizing, leverage, ledger, daily marks.**
  Equity 10,000, entry 100, stop 99 → qty 100, notional 10,000, leverage
  1×; per-trade and total-notional caps each skip and count; the
  liquidation assertion raises on a crafted case with a huge `mmr`; daily
  returns compound exactly to the final equity; zero-position days return
  0; equity after a stop-out equals the hand computation.
- **4.5 Trades table, MAE/MFE, hold time, data end.**
  MAE/MFE on a synthetic path with and without 1m; `hold_minutes`;
  positions open at the period end get `data_end` and are excluded from
  the R subset; pending orders cancelled with `data_end`.
- **4.6 Simulator-level look-ahead and determinism.**
  As Phase 3 §3.3 but comparing the full event log (orders, fills, exits
  with candle index `≤ cut`, minus the data-end rows of the truncated
  run), the closed trades and the daily marks before the cut's day; 1m
  candles come from a 1m random walk aggregated to 15m, and funding rates
  after the cut are perturbed too. Besides fixed cuts, cuts are placed on
  candles with a placement, a fill and an exit, where a one-candle peek
  shows. Two runs yield byte-identical Parquet.
- **4.7 Real-data smoke run (slow).**
  Primary config on BTCUSDT 2024, UTC session: no exceptions; every trade
  has an exit; `net_r ≥ −1 − cost_r` and `net_r ≤ r_target + 1e-9` for
  non-`open_gap` trades; fill rate, skip counts, and resolution counts
  logged and eyeballed.

Exit criterion: all tests green; the smoke run's trade list is spot-checked
by hand against the chart for three trades. Then merge into `dev` and
delete the branch.
