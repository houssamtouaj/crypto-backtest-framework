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
        period_start_ms: int, period_end_ms: int) -> SimResult

@dataclass
class SimResult:
    orders: pd.DataFrame; fills: pd.DataFrame; trades: pd.DataFrame
    daily: pd.DataFrame; events: pd.DataFrame; skips: dict[str, int]
    summary: dict
```

For each candle `i` from the first decision candle (`period_start`) to
`period_end` (the strategy's `warmup_bars` before `period_start` are
available through the view but produce no intents):

1. **Funding** at every funding timestamp `f` with `τ_i ≤ f < τ_i + 15m`
   is charged to positions open at `f` (rule in 4.5).
2. **Pending orders and open positions** are evaluated against candle `i`
   in `order_id` order using 4.3 (and 4.4 when 1m is available). Fills,
   stops, targets, expiries, and time exits produce events and update the
   ledger.
3. **Mark-to-market** at `close[i]`. If `τ_i + 15m` is 00:00 UTC, record
   the daily mark.
4. `intents = strategy.on_candle(view_at(i), account_view)`.
5. **Placement**: for each `PlaceBracketLimit`, size (4.6), check the
   leverage cap, assert liquidation below the stop, and register the order
   with `placed_idx = i`. Orders placed here are first evaluated at `i+1`.

The first `on_candle` call is at `period_start`: the strategy rebuilds its
state from the earlier candles through the view (Phase 3 §3.1), so the
loop does not run over the warmup and skip counts cover the period only.

The simulator has no randomness. Running twice yields byte-identical
tables.

## 4.2 Orders, positions, events (`execution/orders.py`)

Order kinds: `entry_limit`, `stop`, `target_limit`, `time_exit`. Statuses:
`pending`, `filled`, `cancelled`. Cancel reasons: `expired`, `strategy`,
`leverage_cap`, `data_end`. A filled `entry_limit` creates a `Position`
with its stop and target as attached orders and a hold deadline computed
from the hold rule at fill time.

## 4.3 Fills and exits on 15m candles (pessimistic)

For each candle `j` after placement (`j > placed_idx`), in this order:

1. **Entry** (pending, `expires_ms > τ_j`): if `open[j] ≤ entry − pierce_abs`
   → fill at `open[j]`, resolution `open_gap`; else if
   `low[j] ≤ entry − pierce_abs` → fill at `entry`. The fill price is the
   limit price: pierce models queue position, not price improvement. Maker
   fee. A pending order whose `expires_ms ≤ τ_j` is cancelled (`expired`)
   before this check; an order expiring at `τ_j + 15m` can still fill on
   `j`.
2. **Stop** (open position, including one filled on this same candle): if
   `low[j] ≤ stop` → exit reference `min(stop, open[j])`, exit price
   `reference × (1 − slippage)`, taker fee.
3. **Target** (open position, *not* one filled on this same candle): if
   `high[j] ≥ target + pierce_abs` → exit at `max(target, open[j])`, maker
   fee, no slippage.
4. **Time exit** (open position whose deadline equals `τ_j + 15m`): exit
   reference `close[j]`, exit price `close[j] × (1 − slippage)`, taker fee.

So on one 15m candle: fill and stop → stopped; fill and target → no target
that candle; stop and target → stop. The `resolution` column records which
rule decided: `15m_unambiguous` (only one event touched),
`15m_pessimistic` (two or more touched, no 1m), `1m` (resolved by 4.4),
`1m_pessimistic` (a single 1m candle touched two events),
`15m_pessimistic_missing_1m`, or `open_gap`.

## 4.4 1m resolution (`use_1m`)

For a 15m candle where two or more of {entry, stop, target} are touched
for the same order/position, the resolver walks the 15 one-minute candles
inside it, applying 4.3 per 1m candle (entry then stop then target, same
same-candle rules). Time exits are never resolved by 1m; they happen at the
15m close. If any of the 15 one-minute candles is missing, the 15m
pessimistic rule applies and the case is logged. The 1m path is only
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

## 4.6 Sizing, leverage, liquidation (`execution/sizing.py`)

At placement (close of `t`):

- `risk_usd = risk_per_trade × equity_mtm`
- `qty = risk_usd / stop_dist` (fractional, no lot rounding)
- `notional = qty × entry`
- If `notional / equity_mtm > max_leverage` **or**
  `(open_notional + notional) / equity_mtm > max_leverage` the order is
  not registered, the event `skipped_leverage` is emitted to the strategy
  (which consumes the session, Phase 3 §3.5), and the skip is counted.
- Liquidation assertion (cross margin, single-position approximation):
  `liq = entry × (1 − equity_mtm / notional + mmr)`. The simulator raises
  `LiquidationAboveStopError` if `liq ≥ stop`. This is a hard failure of
  the run, not a log line; with 1% risk it can only trigger when
  `stop_dist / entry < mmr / 99`, so a trigger means a bug or a degenerate
  stop.
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
the equity at the close of the candle ending at 00:00 UTC. Daily return =
equity ratio − 1; days with no position and no cash movement give exactly
0. `n_open` and `exposure_notional` are recorded at the mark.

## 4.9 Data end

Positions still open at the last candle of the period are closed at its
close as a time exit with `exit_reason = data_end`. They are included in
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
| candidate_ms, impulse_ms, displacement_ms, placed_ms | int64 | audit trail |
| entry_ms, entry_price | | actual fill |
| planned_entry, stop_price, target_price, stop_dist | float64 | fixed at placement |
| exit_ms, exit_ref_price, exit_price | | |
| exit_reason | str | `target`, `stop`, `session_end`, `max_hold`, `data_end` |
| exit_role | str | `maker`/`taker` |
| qty, notional | float64 | |
| equity_at_entry, risk_usd, actual_risk_usd | float64 | |
| implied_leverage, max_iso_leverage | float64 | |
| gross_pnl, fees, funding, slippage_cost, net_pnl | float64 | USDT |
| gross_r, net_r, cost_r, funding_r | float64 | |
| c_maker_entry, c_maker_exit, c_taker_exit, c_slip | float64 | re-pricing coefficients |
| mae_r, mfe_r | float64 | from extremes between fill and exit (1m when available) |
| hold_minutes | int64 | |
| atr_at_entry, stop_dist_atr | float64 | `stop_dist / atr_at_entry`; used by baselines |
| regime_trend, regime_vol | str | labels as of the entry day (Phase 5) |
| dow, entry_hour_utc | int8 | |
| fill_resolution, exit_resolution | str | |

### `daily`

| column | type | notes |
|---|---|---|
| variant_id, pair, session_variant | | |
| date | date | UTC |
| equity | float64 | mark-to-market at 00:00 UTC |
| ret | float64 | equity / previous equity − 1 |
| n_open | int8 | |
| exposure_notional | float64 | |
| funding_paid | float64 | |

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
  gives `actual_risk_usd > risk_usd`.
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
  with candle index `≤ cut`); two runs yield byte-identical Parquet.
- **4.7 Real-data smoke run (slow).**
  Primary config on BTCUSDT 2024, UTC session: no exceptions; every trade
  has an exit; `net_r ≥ −1 − cost_r` and `net_r ≤ r_target + 1e-9` for
  non-`open_gap` trades; fill rate, skip counts, and resolution counts
  logged and eyeballed.

Exit criterion: all tests green; the smoke run's trade list is spot-checked
by hand against the chart for three trades. Then merge into `dev` and
delete the branch.
