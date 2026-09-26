# Design: crypto backtest framework for the ICT "first 15-min bullish order block" strategy

Status: DRAFT v1 for review. Nothing has been implemented. Date: 2026-09-26.

This document is the plan requested in the project brief. Section 0 lists the
decisions that need your answer. Sections 1–8 are the design. Section 9 is the
build sequence with tests. Section 10 is the full ambiguity list. Sections 11–12
are the grid/runtime estimate and risks. Section 13 is the draft
pre-registration file.

---

## 0. Decisions I need from you

Reply with the numbers you want changed. Anything you do not mention, I will
take as accepting the recommendation. Full reasoning for each is in Section 10.

| # | Question | Recommendation | Alternative |
|---|----------|----------------|-------------|
| D1 | What is each session's window (search window for the first block, order-cancel deadline, and "close at session end")? | UTC: 00:00→24:00 (the UTC day). NY: 09:30→16:00 ET. London: 08:00→16:30 Europe/London. | Uniform 8-hour window from each open, so the variants differ only in the open time. |
| D2 | Must the swing high broken by the impulse be *unbroken* (a fresh break of structure), or does the literal rule apply even when price already closed above the level earlier? | Fresh break: keep a set of confirmed swing highs not yet closed above ("live"); confirmation = close above the most recent live level; the level retires once closed above. | Literal: close above the most recently confirmed swing high, broken or not. Produces degenerate confirmations whenever price is already above the level. |
| D3 | Do NY and London sessions run on weekends? | UTC: all 7 days. NY and London: Monday–Friday only (the session premise is the equity/FX open). | All 7 days for all variants, with a weekday/weekend split in diagnostics. |
| D4 | If the first block is skipped because price already traded into the zone, do we take the next block in that session? | Yes, continue to the next confirmed block in the same session. The "one trade per session" cap applies to orders placed, not blocks seen. | No trade that session. |
| D5 | Within one variant, may a new session's trade open while a previous trade is still open (relevant for hold rule "no limit")? | Yes. Trades are independent, each sized at 1% of mark-to-market equity at placement. Max concurrent positions is reported as a diagnostic. | One position at a time per pair; sessions are skipped while a trade is open. |
| D6 | Baseline budget | 5,000 baseline runs (A and B) for the 9 primary-parameter runs (3 pairs × 3 sessions); 500 runs per grid cell. | Baselines for the primary only; grid cells get bootstrap CIs. |
| D7 | Trend-filter definition | 15m close above the SMA(50) of completed UTC daily closes. | SMA(200). Costs SOL its first 200 days. |
| D8 | Cost defaults | Maker 0.02%, taker 0.05% (Binance USDT-M regular tier), slippage 0.02% of price on stop and time exits, none on limit fills. | Higher slippage for SOL (0.05%). |
| D9 | Initialise a git repo here? | Yes. The holdout guard and the variant registry record a code version; that needs git. | Hash the source tree instead. |
| D10 | Data location | `D:\Users\khali\projects\trading_backtest\data\` (~1.2 GB, zips deleted after conversion). D: has 8.7 GB free. | Another drive. |

---

## 1. What we are building (write-back of the brief)

**Goal.** An honest, statistically rigorous answer to "does this mechanized ICT
strategy have a measurable edge in Binance USDT-M perpetuals?", with the
data, execution, and statistics layers reusable for other strategies.

**What you said.** Long-only, three session variants each run as a separate
strategy, 15m signals, optional 1m resolution, 2020-01-01 to present, in-sample
through 2025-12-31, holdout from 2026-01-01 touched once on the primary
configuration only. Fees, slippage, funding, fixed-fractional sizing.
Bootstrap CIs, two random-timing baselines, buy-and-hold, robustness heatmaps,
alpha decay, deflated Sharpe, trade diagnostics. Pre-registered primary. Every
variant logged. Seeded randomness. Synthetic-data tests for look-ahead.
Clarity over speed.

**Success looks like.** A summary that states plainly, with p-values and CIs,
whether the primary configuration beats random baseline A, random baseline B,
and vol-scaled buy-and-hold at the 5% level, in-sample and on holdout, per pair
and per session variant. Plus a strategy interface where a second strategy can
be dropped in without touching data, execution, or stats code.

**Assumptions I am making (correct me).**
- Single Windows machine, no GPU, 20 CPUs available for parallel variant runs.
- Nominal starting equity $10,000 per simulation; results are reported in R
  units primarily, with returns derived from 1% risk sizing.
- "Present" is frozen to the data download date recorded in the
  pre-registration file; the holdout period ends there.
- No exchange lot-size rounding (fractional quantities); noted as a diagnostic.
- Data from Binance's public bulk archive; ccxt only as a fallback for the last
  day or two. Verified reachable from your network (bulk host and futures API).

## 2. Environment findings (verified today)

- Project directory is empty and not a git repository.
- Python 3.11.2 with pandas 2.2.3, numpy 2.2.4, pyarrow 20.0, ccxt 4.5.64,
  matplotlib 3.10.1, scipy 1.15.2, pytest 9.1.1 installed. No plotly. `zoneinfo`
  and `tzdata` are available for time zones (no extra dependency).
- `data.binance.vision` serves monthly and daily zips for USDT-M klines (1m, 15m)
  and monthly funding-rate files, each with a `.CHECKSUM` file. Sample files
  inspected: 2020 kline files have no header row, 2026 files do; timestamps are
  milliseconds; funding files have columns `calc_time, funding_interval_hours,
  last_funding_rate` with timestamps jittered by 1–2 ms (must be rounded).
- Perpetual listing dates: BTCUSDT 2019-09-08, ETHUSDT 2019-11-27,
  SOLUSDT 2020-09-14. SOL's in-sample period therefore starts 2020-09-14.
- Disk: 8.7 GB free on D:.

## 3. Key design decisions

### 3.1 Simulator style: event-driven candle loop with a strategy callback

Options considered:
1. **Candle-by-candle loop, strategy as a callback receiving a read-only window
   of the past** (chosen). The `MarketView` object refuses to return any candle
   after the current one, so look-ahead is structurally impossible in strategy
   code, and testable. Slow-ish in pure Python (estimated 5–15 s per variant per
   pair), which is acceptable.
2. Vectorized signal generation + loop-based execution. Faster, but every
   vectorized signal must be proven causal separately. Kept as a *later*
   optimization: any vectorized signal path must pass an equivalence test
   against the loop implementation on random data.
3. An existing framework (backtrader, vectorbt). Rejected: hides fill logic,
   adds a large dependency, and fights the audit-ability goal.

### 3.2 Data source: bulk archive first, ccxt for the tail

Bulk monthly/daily files are complete, checksummed, and fast (~120 MB zipped
per pair for 1m). ccxt would need ~2,300 paginated requests per pair for 1m
data. ccxt is used only to fill the last 1–2 days not yet published as daily
files, and is optional.

### 3.3 Session variants do not share an account (decision requested in the brief)

Each (pair, session variant, parameter set) is an isolated simulation with its
own equity. Justification:
- The brief says each session variant is "a separate strategy"; the question is
  whether *each* has an edge, not whether a portfolio of the three does.
- The London window (08:00–16:30 local, roughly 07:00–15:30 UTC) and the NY
  window (13:30–20:00 UTC) overlap for hours. With a shared account, the same
  order block could be claimed by two variants, forcing tie-break rules that
  are not part of the strategy and would contaminate both variants' trade lists.
- Isolation keeps each variant's trade list a pure function of the rule, which
  is what the baselines compare against.
- A combined-portfolio view can be produced later by aggregating the three
  daily-return series with their empirical correlation. The report will state
  the fraction of trades shared between variants (same candidate candle), since
  the three variants are not independent tests.

### 3.4 Overlapping trades within a variant are allowed (D5)

With hold rule "no limit", a trade can span several sessions. Allowing the next
session's trade to open keeps the trade list independent of prior outcomes and
identical in composition for baseline A. Each trade risks 1% of mark-to-market
equity at order placement. Diagnostics report the max concurrent positions
and the notional exposure; the leverage cap (Section 6.6) still applies to the
sum of open notional.

### 3.5 Baselines are computed by a vectorized evaluator validated against the simulator

Thousands of baseline runs over ~1,000 trades each cannot go through the full
Python loop. A separate `evaluator` computes the outcome of a single
pre-specified trade (entry candle, entry price, stop, target, deadline) using
numpy slices. It is proven equivalent to the simulator by a test that feeds the
simulator's own real trades into the evaluator and requires identical exits and
net R. This is the one place with vectorized code, and it never generates
signals.

### 3.6 Reporting: matplotlib PNGs + Markdown/HTML

Static, reproducible, diffable. Plotly is not installed and adds nothing the
report needs.

## 4. Architecture

Package name placeholder: `perpbt` (rename freely).

```
perpbt/
  config.py            frozen dataclasses: DataConfig, SessionSpec, StrategyParams,
                       ExecConfig, StatsConfig, VariantConfig; YAML load/dump; canonical hash
  data/
    bulk.py            download + checksum verify + parse data.binance.vision files
    ccxt_tail.py       optional: fetch the last days via ccxt, merge without duplicates
    store.py           CandleStore / FundingStore: Parquet cache, holdout guard
    validate.py        monotonic/unique timestamps, gap report, 1m→15m consistency
    sessions.py        SessionCalendar: per-candle session id, window flags (DST-aware)
  indicators/
    atr.py             Wilder ATR(n) on 15m
    swings.py          swing highs with confirmation index
    daily.py           completed-day SMA / ADX aligned to the 15m index (causal)
  strategy/
    base.py            Strategy protocol, MarketView, AccountView, Intent types
    order_block.py     the ICT OB strategy
  execution/
    orders.py          Order, Position, SimEvent dataclasses, lifecycle enum
    fills.py           FillModel: 15m pessimistic rules + optional 1m resolver
    costs.py           fees by role, slippage, funding schedule
    sizing.py          fixed-fractional sizing, leverage and liquidation checks
    ledger.py          cash, positions, mark-to-market, daily marks
    simulator.py       the candle loop
    trades.py          trades table builder, MAE/MFE, hold time, regime tags
  stats/
    bootstrap.py       trade bootstrap; moving-block bootstrap of daily Sharpe; paired Sharpe diff
    evaluator.py       vectorized single-trade outcome evaluator (see 3.5)
    baselines.py       baseline A and B generators, p-values
    buyhold.py         buy-and-hold with funding, vol-scaled
    dsr.py             deflated Sharpe ratio, PSR
    rolling.py         rolling 6-month metrics, per-year table
    regimes.py         trend / vol regime labels
    diagnostics.py     win rate, exposure, drawdown, distributions, cost-in-R
  experiments/
    prereg.py          load/validate/hash the pre-registration file
    registry.py        append-only run log (jsonl) + results table (parquet)
    grid.py            enumerate variants from the pre-registration grid
    runner.py          run one variant end to end; multiprocessing driver; resume
    holdout.py         one-shot holdout runner with guard
  report/
    figures.py         all charts
    report.py          per pair × session report
    summary.py         the plain-language verdict
  cli.py               perpbt data fetch | validate | run | grid | baselines | report | holdout
tests/
  synthetic.py         candle builders, random-walk generator, scenario helpers
  test_*.py            one file per module
configs/
  prereg.yaml          the pre-registered primary, grid, and decision rules
data/                  Parquet cache (git-ignored)
runs/                  per-variant outputs (git-ignored), registry.jsonl
```

### 4.1 Interfaces between layers

**Data → everything.** Arrays, not DataFrames, at the core, for speed and to
make index arithmetic explicit.

```python
@dataclass(frozen=True)
class Candles:            # one pair, one timeframe, UTC, sorted, unique, gap-checked
    pair: str; tf: str
    ts: np.ndarray        # int64 ms, open time
    o, h, l, c, v: np.ndarray   # float64
    def index_at(self, ts_ms) -> int          # searchsorted
    def slice(self, start_ms, end_ms) -> Candles

class CandleStore:
    def load(self, pair, tf, start, end, *, allow_holdout=False) -> Candles
    # raises HoldoutAccessError if end > insample_end and not allow_holdout

class FundingStore:
    def load(self, pair, start, end) -> Funding   # ts (rounded to minute), rate, interval_h
    def events_between(self, a_ms, b_ms) -> slice  # funding timestamps in [a, b]

class SessionCalendar:
    def __init__(self, spec: SessionSpec, ts: np.ndarray)   # precomputes per candle:
    session_id: np.ndarray     # -1 outside any window
    open_ts, end_ts: np.ndarray
    in_window: np.ndarray[bool]
```

**Indicators → strategy.** Each returns an array aligned to the 15m index with
NaN before warmup, and documents its confirmation lag. Swing highs return
`(level, confirmed_at_index)` pairs, so consumers cannot use a swing before
`confirmed_at_index`.

**Strategy plug-in.**

```python
class MarketView:               # window ending at candle i; anything beyond i raises LookaheadError
    i: int
    def ts(self, j) / open(self, j) / high(self, j) / low(self, j) / close(self, j)
    def atr(self, j); def daily_sma(self, j)          # indicators, causal
    def swings_confirmed_by(self, j) -> list[Swing]   # swings with confirmed_at <= j
    session: SessionInfo        # id, open_ts, end_ts, in_window, is_last_candle

class AccountView:              # read-only: equity_mtm, open positions, pending orders (this variant)

class Strategy(Protocol):
    name: str
    params: StrategyParams
    warmup_bars: int
    def on_candle(self, view: MarketView, account: AccountView) -> list[Intent]
    def on_event(self, event: SimEvent) -> None     # fills/cancels/closes, for bookkeeping

Intent = PlaceBracketLimit(price, stop, target, expires_at_ms, hold_deadline, tag: dict)
       | CancelOrder(order_id)
       | ClosePosition(position_id, reason)
```

`tag` carries strategy metadata (candidate index, confirm index, zone, ATR,
swing level) into the orders and trades tables so every trade is auditable.

**Simulator.**

```python
def run(candles15, candles1m | None, funding, calendar, strategy, exec_cfg, seed) -> SimResult
SimResult: orders, fills, trades, daily, events, skips (DataFrames) + summary dict
```

**Stats.** Consume `trades`, `daily`, and `Candles` (for baselines). Every
function takes an explicit `rng: np.random.Generator`.

**Experiments.** `VariantConfig` = (pair, session variant, StrategyParams,
ExecConfig, period). `variant_id` = SHA-256 of canonical JSON of the config
plus the code version. The registry is append-only.

### 4.2 Data flow for one variant

```
prereg.yaml ──► VariantConfig ──► CandleStore/FundingStore (holdout guard)
                                     │
                                     ▼
              indicators (ATR, swings, daily SMA/ADX)  +  SessionCalendar
                                     │
                                     ▼
              Simulator loop: for i in range(n): view=MarketView(i)
                  1. process funding events at ts[i]  (positions open at the boundary)
                  2. evaluate pending orders and open positions against candle i
                     (fills, stops, targets, deadlines; 1m resolver if ambiguous)
                  3. mark-to-market; daily mark at UTC day boundary
                  4. intents = strategy.on_candle(view, account)  ← sees candles ≤ i only
                  5. place/cancel per intents; sizing + leverage check at placement
                                     │
                                     ▼
              trades / daily / orders / fills / events / skips
                                     │
                                     ▼
              stats (bootstrap, baselines, B&H, DSR, rolling, regimes, diagnostics)
                                     │
                                     ▼
              runs/<variant_id>/*.parquet, stats.json, figures/  +  registry.jsonl row
```

Order of operations inside a candle matters: fills and exits for candle i are
evaluated *before* the strategy sees candle i's close, and new orders placed at
the close of i can only fill from candle i+1.

## 5. Data schema

All timestamps are UTC, stored as int64 milliseconds (`_ms` suffix) in Parquet
and converted to `datetime64[ms, UTC]` only for display. Prices `float64`.

### 5.1 `candles` (Parquet, partitioned `pair/tf/year`)

| column | type | notes |
|---|---|---|
| pair | str | e.g. BTCUSDT |
| tf | str | `1m`, `15m` |
| open_ms | int64 | primary key with pair, tf |
| open, high, low, close | float64 | |
| volume | float64 | base asset |
| quote_volume | float64 | |
| trades | int64 | trade count |
| taker_buy_volume | float64 | |
| source | str | `bulk_monthly`, `bulk_daily`, `ccxt` |

A `manifest.json` per pair/tf records file names, checksums, download date,
first/last timestamp, and the gap report.

### 5.2 `funding`

| column | type | notes |
|---|---|---|
| pair | str | |
| funding_ms | int64 | rounded to the minute (raw values carry ±2 ms jitter) |
| rate | float64 | signed; longs pay when positive |
| interval_h | int8 | from the file; do not assume 8 |
| mark_price | float64 | from API when available, else NaN |

### 5.3 `orders` (per variant run)

| column | type | notes |
|---|---|---|
| order_id | int64 | |
| variant_id | str | |
| pair, session_variant | str | |
| session_id | int64 | |
| kind | str | `entry_limit`, `stop`, `target_limit`, `time_exit` |
| side | str | `buy`/`sell` |
| price | float64 | limit/stop level |
| qty | float64 | |
| status | str | `pending`, `filled`, `cancelled` |
| placed_ms | int64 | close time of the decision candle |
| placed_idx | int64 | decision candle index |
| expires_ms | int64 | nullable |
| filled_ms, fill_price | | nullable |
| cancelled_ms, cancel_reason | | nullable: `session_end`, `strategy`, `leverage_cap`, `data_end` |
| trade_id | int64 | nullable |
| tag | str (json) | candidate_idx, confirm_idx, zone_low/high, atr, swing_level, entry_level_kind |

### 5.4 `fills`

| column | type | notes |
|---|---|---|
| fill_id, order_id, trade_id | int64 | |
| ts_ms | int64 | candle open time of the fill candle |
| price | float64 | executed price incl. slippage |
| qty | float64 | |
| fee | float64 | USDT |
| fee_role | str | `maker`/`taker` |
| slippage | float64 | USDT |
| resolution | str | `15m_pessimistic`, `15m_unambiguous`, `1m`, `open_gap` |

### 5.5 `trades` (one row per closed position; the unit of all statistics)

| column | type | notes |
|---|---|---|
| trade_id, variant_id, pair, session_variant, session_id | | |
| session_open_ms | int64 | |
| candidate_ms, confirm_ms, placed_ms | int64 | strategy audit trail |
| entry_ms, entry_price | | actual fill |
| planned_entry, stop_price, target_price | float64 | fixed at placement |
| exit_ms, exit_price | | |
| exit_reason | str | `target`, `stop`, `session_end`, `max_hold`, `data_end` |
| qty, notional | float64 | |
| equity_at_entry, risk_usd | float64 | |
| implied_leverage | float64 | notional / equity |
| gross_pnl, fees, funding, slippage_cost, net_pnl | float64 | USDT |
| gross_r, net_r | float64 | divided by risk_usd |
| cost_r | float64 | (fees + funding + slippage) / risk_usd |
| mae_r, mfe_r | float64 | from candle extremes between entry and exit |
| hold_minutes | int64 | |
| atr_at_entry, stop_dist_atr | float64 | used by baseline A |
| regime_trend, regime_vol | str | labels as of the entry day |
| dow, entry_hour_utc | int8 | |
| fill_resolution, exit_resolution | str | |

### 5.6 `daily`

| column | type | notes |
|---|---|---|
| variant_id, pair, session_variant | | |
| date | date | UTC |
| equity | float64 | mark-to-market at 00:00 UTC |
| ret | float64 | equity / previous equity − 1 |
| n_open | int8 | positions open at the mark |
| exposure_notional | float64 | |
| funding_paid | float64 | |

### 5.7 `results` (registry; one row per variant run)

| column | notes |
|---|---|
| variant_id, run_ms, code_version, seed | |
| params_json | full VariantConfig |
| pair, session_variant, period_start, period_end, is_holdout | |
| n_sessions, n_orders, n_filled, fill_rate, n_trades | |
| n_skipped_mitigated, n_skipped_leverage, n_skipped_trend | |
| win_rate, mean_gross_r, mean_net_r, mean_net_r_ci_lo/hi | trade bootstrap |
| mean_net_r_ci_block_lo/hi | day-block bootstrap (secondary) |
| sharpe_ann, sharpe_ci_lo/hi | daily, block bootstrap |
| max_dd, exposure_time, avg_hold_min, avg_cost_r, max_concurrent | |
| p_a, p_b, z_a, z_b, n_baseline_runs | nullable |
| bh_sharpe, sharpe_diff_ci_lo/hi | vs buy-and-hold |
| dsr_local, dsr_global | deflated Sharpe with N = variants for this pair×session / all |
| runtime_s, artifacts_path | |

## 6. Strategy mechanics as they will be implemented

This section is the mechanized rule with the ambiguity resolutions of Section 0
applied. Please read it as the contract; if anything differs from your intent,
that is an ambiguity I should have caught.

Notation: 15m candles indexed `i`, open time `τ_i`, close time `τ_i + 15m`.
Decision time for candle `i` is its close. "As of `i`" means using candles
`≤ i` only.

**6.1 Swing high (k).** Candle `s` is a swing high iff `high[s] > high[s±j]`
for all `j = 1..k` (strict on both sides; equal highs do not qualify). It is
confirmed as of `i = s + k`. Its level is `high[s]`.

**6.2 Live levels and confirmation (D2).** A confirmed swing high is *live*
until the first candle that closes above its level. (No candle between `s` and
`s + k` can close above `high[s]`, so "live from confirmation" and "unbroken
since formation" coincide.) At candle `t`, the reference level is the most
recently confirmed live swing high as of `t`. Candle `t` is an **impulse** iff
`close[t] > reference level`. After an impulse, the broken level retires.

**6.3 Candidate and order block (N).** For an impulse at `t`, the order block
candidate is the most recent bearish candle `c` (`close[c] < open[c]`) with
`t − N ≤ c ≤ t − 1`. If there is none, the impulse produces no block. One
impulse yields at most one block, and it is the last bearish candle before the
impulse, which is the standard OB definition.

**6.4 Session eligibility.** For session variant `v` with window `[open, end)`
(D1) and day-of-week rule (D3): the block is eligible iff `τ_c ≥ open` and
`τ_t + 15m ≤ end` (the decision happens inside the window). Blocks are
processed in impulse order. The first eligible block that also passes 6.5 and
6.9 produces an order. After an order is placed for a session, no further
orders are placed in that session, whatever happens to that order (D4).

**6.5 Mitigation check.** Skip the block (and continue searching, D4) if any
candle in `c+1 .. t` would already have filled the entry, i.e.
`min(low[c+1..t]) ≤ entry_level − pierce`. This uses exactly the fill
predicate of 6.8, so "traded back into the zone" and "would have filled" are
the same test.

**6.6 Zone, entry, stop, target.**
- Zone: `full` → `[low[c], high[c]]`; `body` → `[close[c], open[c]]`.
- Entry level: `top` → zone top; `mid` → zone midpoint.
- Stop: `low[c] − buffer`. The stop always anchors at the candle's true low
  regardless of zone mode ("bottom of the block = the low"). Buffer is
  `b × ATR14[t]` (Wilder, 15m, as of the confirming candle) or `p% × entry`.
- Target: `entry + R × (entry − stop)`.
- Sizing: `risk = 1% × equity_mtm[t]`, `qty = risk / (entry − stop)`,
  `notional = qty × entry`. If `notional / equity > max_leverage` (default 25×)
  the order is not placed and counted as `skipped_leverage`. The
  cross-margin liquidation price `entry × (1 − equity/notional + MMR)` is
  asserted below the stop (with 1% risk this holds unless the stop distance is
  below about MMR/99 ≈ 0.004%). The maximum isolated leverage setting that
  keeps liquidation below the stop, `1 / (stop_dist_pct + MMR)`, is recorded
  per trade.
- Order placed at the close of `t`; expires at the session window end.

**6.7 Hold rules.** `none`; `session_end` (deadline = end of the window in
which the order was placed); `max_hold_h` (deadline = fill candle close +
H hours). A time exit executes at the close of the candle whose close time
equals the deadline, as a taker with slippage.

**6.8 Fills and exits on 15m candles (pessimistic).** For each candle `j`
after placement, in this order:
1. Entry (pending): if `open[j] ≤ entry − pierce` → fill at `open[j]`; else if
   `low[j] ≤ entry − pierce` → fill at `entry`. Fill price is the limit price
   (pierce models queue position, not price improvement). Maker fee.
2. Stop (open position, including one filled on this same candle): if
   `low[j] ≤ stop` → exit at `min(stop, open[j]) − slippage`, taker fee.
3. Target (open position, *not* one filled on this same candle): if
   `high[j] ≥ target + pierce` → exit at `max(target, open[j])`, maker fee.
4. Time exit at the deadline candle's close, taker fee + slippage.

So: fill and stop on the same candle → stopped (−1R before costs); fill and
target on the same candle → no target that candle; stop and target on the same
candle → stop. Which of these happened is recorded in `resolution`.

**6.9 1m resolution (when available).** For a 15m candle where two or more of
{fill, stop, target} are touched, the resolver walks the 1m candles inside it
applying 6.8 per 1m candle. A single 1m candle touching two events falls back
to the pessimistic rule. Missing 1m data → pessimistic, logged.

**6.10 Funding.** Charged at every funding timestamp `f` with
`fill_candle_close ≤ f ≤ exit_candle_open` (open at the boundary, using the
actual timestamps and intervals from the funding table). Amount
`rate × qty × close_price_at_f`; longs pay positive rates.

**6.11 Trend filter (toggle, D7).** When on, an order is placed only if
`close[t] > SMA50` of the last 50 *completed* UTC daily closes as of `t`.
Skipped blocks count as `skipped_trend` and the search continues.

**6.12 Mark-to-market and daily returns.** Equity = cash + unrealized P&L of
open positions at the 15m close. Daily mark at 00:00 UTC. Daily return =
equity ratio − 1; days with no position contribute 0.

**6.13 Data end.** Positions still open at the end of the period are closed at
the last close with `exit_reason = data_end` and counted; they are included in
the equity curve and excluded from R statistics (their count is reported). The
holdout simulation starts flat on 2026-01-01 with warmup-only access to earlier
candles.

## 7. Execution model details

- **Fees.** Maker 0.02%, taker 0.05% of notional (D8), configurable per pair.
  Entry limit and target limit are maker; stop and time exits are taker.
- **Slippage.** Fixed fraction of price on stop and time exits (default 0.02%).
- **Sizing.** Fixed fractional 1% of mark-to-market equity at placement.
- **Leverage.** Cap 25× per trade and on total open notional; skips counted.
- **Maintenance margin.** Configurable per pair (defaults: BTC 0.4%, ETH 0.5%,
  SOL 1.0%); only used for the liquidation assertion.
- **Determinism.** The simulator has no randomness. All stats take a seeded
  `np.random.Generator` derived from `(master_seed, variant_id, purpose)`.

## 8. Statistical layer details

- **Mean net R CI.** Trade bootstrap, B = 10,000, percentile 95% CI (as
  specified). Secondary: block bootstrap by entry day, because overlapping trades
  (D5) are not independent. Both reported.
- **Daily Sharpe CI.** `sqrt(365) × mean/std` of daily returns (crypto trades 7
  days). Moving-block bootstrap, block length 10 days, B = 10,000.
- **Baseline A (timing skill).** For each real trade: same date, same session
  window, entry at the open of a uniformly random 15m candle inside the window,
  stop at `open − stop_dist_atr × ATR14` (the real trade's ATR multiple, ATR as
  of the previous candle), target at the same R, same hold rule, same fees
  (maker on entry, to isolate timing), same slippage, actual funding. One run =
  one redraw of every trade's entry time; statistic = mean net R. p-value =
  `(1 + #{runs ≥ observed}) / (M + 1)`, one-sided. Also reported: z-score.
- **Baseline B (day selection + timing).** As A, but the date is drawn uniformly
  from the eligible days of the in-sample period (respecting the variant's
  day-of-week rule), and the stop/target geometry is resampled from the real
  trades' ATR multiples.
- **Buy-and-hold.** Daily returns of a constant long in the perp, including
  funding paid. Scaled to the strategy's realized daily vol. Reported: Sharpe,
  vol-scaled return and max drawdown, and a paired moving-block bootstrap 95%
  CI of `Sharpe_strategy − Sharpe_B&H` on the aligned daily series.
- **Deflated Sharpe ratio.** Bailey & López de Prado (2014): the expected
  maximum Sharpe under `N` trials given the variance of trial Sharpes, then the
  probabilistic Sharpe ratio of the observed Sharpe against that benchmark,
  using daily return skew, kurtosis, and T. Reported for the primary with
  `N = 36` (variants for that pair × session) and `N = 324` (all), and for the
  best grid cell. Baseline p-values also shown next to a Bonferroni threshold.
- **Alpha decay.** Rolling 6-month mean net R (by entry date) and rolling daily
  Sharpe, stepped monthly; per-year table (n, win rate, mean net R, Sharpe, max
  DD, fill rate).
- **Regimes.** Trend: daily ADX(14) > 25 as of the day before entry. Vol:
  30-day realized vol above/below its in-sample median (fixed, applied as-is to
  holdout).
- **Diagnostics.** MAE/MFE (R), win rate, average hold, exposure (time and
  notional), max drawdown, max concurrent positions, fill rate, skip counts,
  cost-in-R distribution, breakdowns by session variant, regime, day of week,
  entry hour, and by year.
- **Verdict rule (pre-registered).** The primary configuration "beats" baseline
  A/B if the one-sided p-value for mean net R is below 0.05; "beats
  buy-and-hold" if the paired bootstrap CI of the Sharpe difference excludes
  zero and the point difference is positive. The summary states these four
  booleans (A, B, B&H in-sample; the same on holdout) per pair and session
  variant, plus the CIs, and notes holdout power.

## 9. Build sequence with tests

Each task is small enough to finish and verify in one sitting. Tests are
`pytest`; synthetic candle builders live in `tests/synthetic.py`. Slow tests
that touch real data are marked `@pytest.mark.slow`.

### Phase 0 — Scaffold
- **0.1 Package skeleton, `pyproject.toml`, pytest config, `configs/prereg.yaml` draft, `.gitignore`.**
  Test: `pytest` collects; `VariantConfig` round-trips through YAML and its hash is stable across key order.

### Phase 1 — Data
- **1.1 Bulk downloader/parser** (monthly + daily, checksum verify, header/no-header, ms or µs timestamps, zip cleanup).
  Tests: fixture CSVs in both header styles and both timestamp units parse to identical frames; a bad checksum raises; re-running is idempotent (no re-download, no duplicate rows).
- **1.2 CandleStore with validation and holdout guard.**
  Tests: unsorted/duplicate input rejected; gap report on a synthetic series with a 45-minute hole; `load(end > 2025-12-31)` raises without `allow_holdout=True`.
- **1.3 1m→15m consistency check.**
  Tests: synthetic 1m aggregates exactly to 15m (open of first, max high, min low, close of last, volume sum). Slow test: one real month agrees within float tolerance.
- **1.4 FundingStore.**
  Tests: jittered timestamps round to the minute; `interval_h` preserved; `events_between` boundary inclusive on both ends.
- **1.5 SessionCalendar.**
  Tests: NY session opens at 14:30 UTC on 2024-03-08 and 13:30 UTC on 2024-03-11; London opens 08:00 UTC on 2024-03-29 and 07:00 UTC on 2024-04-01; during 2024-03-10..03-30 NY is on EDT while London is still on GMT; weekday rule drops Saturday/Sunday for NY/London only; every window start/end lands on a 15m boundary; UTC session id increments at 00:00.
- **1.6 ccxt tail fetch (optional).**
  Test: mocked ccxt pages merge with the bulk frame without duplicates or gaps.

### Phase 2 — Indicators (all causal)
- **2.1 Wilder ATR(14).** Tests: matches a hand-computed 20-candle example; **causality**: perturbing candles after index `cut` leaves `atr[:cut+1]` unchanged.
- **2.2 Swing highs.** Tests: synthetic series with known swing highs; equal highs excluded; `confirmed_at == s + k` exactly; k=1 and k=3 cases; **causality** on the confirmed-at index.
- **2.3 Daily SMA / ADX aligned to 15m.** Tests: the value on any 15m candle equals the SMA of the previous 50 *completed* days; changing today's candles never changes today's filter value; **causality**.
- **2.4 MarketView guard.** Test: `view.close(view.i + 1)` raises `LookaheadError`; `view.swings_confirmed_by(i)` never returns a swing with `confirmed_at > i`.

### Phase 3 — Strategy
- **3.1 Live-level tracking + impulse detection.** Tests: level retires on first close above; the most recent live level is the reference when an older higher level exists; a level confirmed on candle `i` cannot be broken on `i`.
- **3.2 Block selection.** Tests (synthetic, known answers):
  S1 basic block: order at `high[c]`, stop `low[c] − 0.1 ATR`, target at 2R.
  S2 impulse at `c + N + 1` → no block.
  S3 two bearish candles before the impulse → the later one is the block.
  S4 level already broken before the candidate → no confirmation (D2).
  S5 candidate before session open, impulse after → not eligible; next block is.
  S6 mitigated block skipped, next block taken (D4); with `pierce > 0` a touch exactly at the level is not mitigation.
  S7 one trade per session: later blocks ignored after an order is placed, even after cancel or stop-out.
  S8 body-zone entry vs full-zone entry levels; `mid` entry.
  S9 trend filter on/off flips a known block.
  S10 order expiry timestamp equals the window end.
- **3.3 Strategy-level look-ahead test.** Test: on a seeded random walk (n = 5,000), the list of intents with decision index `≤ cut` is identical between the full series, the series truncated at `cut`, and the series with candles after `cut` replaced by a different random path (continuity preserved so the perturbation is not detectable from the past). Run for 20 seeds and 3 cuts each.

### Phase 4 — Execution
- **4.1 Order lifecycle and simulator loop, no costs.** Tests: E1 fill when `low == entry` with `pierce = 0`, not with `pierce > 0`, fill when `low ≤ entry − pierce`; E2 open-gap fill at open; E3 fill+stop same candle → stop; E4 fill+target same candle → no target; E5 stop+target same candle → stop; E6 cancel at expiry; E7 `session_end` and `max_hold` exits at the right candle; E8 no order placed at close of `t` can fill on `t`.
- **4.2 1m resolver.** Tests: 1m sequences where target precedes stop (→ target) and vice versa; fill then target within the same 15m candle (→ target when the 1m order shows it); a single 1m candle touching both → pessimistic; missing 1m → pessimistic and logged.
- **4.3 Cost model.** Tests: hand-made trade with known prices → fees, slippage, funding and net R to the cent; funding boundary cases (fill candle close == funding time → charged; exit candle open == funding → charged; exit candle close == funding → not charged); a 4-hour funding interval is honoured.
- **4.4 Sizing, leverage, ledger, daily marks.** Tests: equity 10,000, entry 100, stop 99 → qty 100, notional 10,000, leverage 1×; a case above the cap is skipped and counted; liquidation assertion holds; daily returns compound exactly to the final equity; zero-position days return 0.
- **4.5 Trades table + MAE/MFE + regime tags.** Tests: MAE/MFE on a synthetic path; hold minutes; `data_end` handling.
- **4.6 Simulator-level look-ahead and determinism tests.** Tests: as 3.3 but comparing the full event log (orders placed, fills, exits with candle index `≤ cut`); running twice yields byte-identical Parquet outputs.
- **4.7 Real-data smoke run (slow).** Primary config on BTCUSDT 2024: no exceptions; every trade has an exit; `net_r ≥ −1 − cost_r − slippage_r` and `net_r ≤ R_target + tolerance`; fill rate and counts logged.

### Phase 5 — Statistics
- **5.1 Trade bootstrap.** Tests: constant trades → degenerate CI; on N(0,1) samples across 500 seeded draws, 95% CI coverage within 93–97%.
- **5.2 Block bootstrap Sharpe.** Tests: iid coverage as above; on AR(1) daily returns the block CI is wider than the iid CI.
- **5.3 Vectorized evaluator + equivalence test.** Test: feed the simulator's own real trades (entry candle, fill price, stop, target, deadline) from a random-walk run into the evaluator; require identical exit time, exit price, funding, and net R for every trade, with and without 1m data.
- **5.4 Baselines A and B + p-values.** Tests: with entry times forced to the real ones, baseline A reproduces the real mean R; on random-walk data with a coin-flip strategy across 20 seeds, p-values are not concentrated below 0.05; day-of-week rule respected in B.
- **5.5 Buy-and-hold with funding, vol-scaled, paired Sharpe difference.** Tests: 3-day hand example; scaling yields equal vol.
- **5.6 Deflated Sharpe.** Tests: DSR decreases in N; with `N = 2` and zero trial variance DSR equals the PSR against 0; skew/kurtosis terms match the published formula on a worked example.
- **5.7 Rolling metrics, per-year table, regimes, diagnostics.** Tests: hand-made trade lists.

### Phase 6 — Experiments
- **6.1 Pre-registration loader + hash; variant ids; registry.** Tests: same config → same id regardless of key order; registry row count equals runs; a run with a changed code version gets a new id.
- **6.2 Grid enumeration + multiprocessing runner + resume.** Tests: dry run enumerates exactly 36 unique variants per pair × session (324 total); interrupted run resumes without re-running finished variants.
- **6.3 Holdout runner with one-shot guard.** Tests: second invocation refuses; pre-registration hash mismatch refuses; only the primary configuration is accepted.

### Phase 7 — Reporting
- **7.1 Figures.** Test: every figure renders from a synthetic `SimResult` without error.
- **7.2 Per-report and summary with the verdict rule.** Test: table-driven verdict logic (each combination of p-values and CIs yields the expected sentence).

### Phase 8 — Runs (after all tests pass)
1. Fetch and validate data; commit the manifest.
2. Freeze `prereg.yaml` (commit hash recorded).
3. In-sample primary on 3 pairs × 3 sessions; baselines at 5,000 runs.
4. Grid (324 runs) with 500-run baselines; reports.
5. You review. Then holdout, once, primary only, and the final summary.

## 10. Ambiguities found in the spec, with recommended resolutions

Grouped by area. Items marked **D#** are the ones in Section 0; the rest I
propose to resolve as stated unless you object.

**Signals**
1. Equal highs ("exceeds"): strict inequality on both sides; ties are not swing highs.
2. **D2** Whether the broken swing high must be unbroken: fresh break of a live level.
3. Several bearish candles before an impulse: the last one within N is the block; one impulse → at most one block.
4. Whether the reference level is fixed at the candidate or evaluated at each candle: evaluated at the impulse candle (decision time), per "already confirmed at decision time".
5. Candidate inside the window but decision after the window end: not eligible (the order would be cancelled immediately anyway).
6. **D4** Skipped (mitigated) block: continue to the next block.
7. "Traded back into the zone" with a `mid` entry: measured against the entry level, using the fill predicate (including pierce).
8. Body-only zone: entry uses the body top; stop still anchors at the candle's true low.
9. ATR definition and timing: Wilder RMA(14) on 15m as of the confirming candle.
10. Fixed-percentage stop buffer: percent of the entry price.
11. **D7** Trend filter MA: SMA(50) of completed daily closes, compared with the 15m close.
12. `max_hold` measured from the fill, not from placement.

**Sessions**
13. **D1** Window length per variant.
14. **D3** Weekends for NY/London.
15. Session end for the UTC variant with hold rule `session_end` = 00:00 UTC next day.
16. Exchange holidays are ignored (the crypto market is open); a diagnostic will show them.

**Execution**
17. Fill price when a candle opens through the limit: fill at the open (better price); rare in a continuous market.
18. Pierce default in the primary: 0 (the primary is the literal rule); pierce is a grid toggle. Pierce applies symmetrically to the target limit.
19. Target exit fee: maker (a resting limit).
20. Fill candle also touching the target: no target that candle (pessimistic); 1m resolves.
21. Time exits execute at the deadline candle's close as a taker with slippage.
22. Funding at exact boundaries: charged if `fill close ≤ f ≤ exit open`; funding interval taken from the data, not assumed 8h.
23. Funding notional: `qty × close at f`.
24. Sizing equity: mark-to-market equity at placement (includes unrealized P&L).
25. Leverage cap 25× with skip-and-count; liquidation assertion under cross margin.
26. **D5** Overlapping positions within a variant.
27. Cross-variant concurrency: isolated simulations (Section 3.3).
28. **D8** Fee and slippage defaults.
29. No lot-size rounding; min-notional ignored; noted in the report.

**Data and periods**
30. Warmup: candles are fetched from each listing date so indicators are valid from 2020-01-01 (SOL from 2020-09-14 + warmup).
31. "Present": frozen at the data download date recorded in `prereg.yaml`.
32. Trades open at 2025-12-31: closed at data end, excluded from R stats, included in equity, counted.
33. 1m gaps: pessimistic fallback with logging.
34. **D10** Data location.

**Statistics**
35. Baseline entry mechanics: market entry at a random candle open, charged the maker fee to isolate timing.
36. Baseline A day set: days with a *filled* real trade (unfilled orders are not trades).
37. Baseline B geometry: resampled from real trades' ATR multiples.
38. **D6** Baseline budget.
39. Mean-R bootstrap dependence: trade bootstrap as specified plus day-block bootstrap as a secondary CI.
40. Sharpe annualisation: `sqrt(365)`; zero-return days included.
41. Buy-and-hold comparison metric: paired block-bootstrap CI of the Sharpe difference plus vol-scaled return/drawdown.
42. Deflated Sharpe trial count: reported at N = 36 and N = 324; the pre-registered primary is technically one trial, which the report will also say.
43. Regime definitions: ADX(14) > 25 daily for trend; 30-day realized vol vs in-sample median.
44. MAE/MFE from 15m extremes (1m when available).
45. Verdict rule as in Section 8.

**Process**
46. **D9** Git repository for code-version hashing.
47. Package name `perpbt` is a placeholder.

## 11. Grid size and runtime estimate

### 11.1 Grid (per pair × session; other parameters at primary values)

| Axis | Values | Primary |
|---|---|---|
| R_target | 1, 1.5, 2, 3 | 2 |
| entry_level | top, mid | top |
| swing_k | 1, 2, 3 | 2 |
| confirm_N | 2, 3, 5 | 3 |
| stop_buffer | 0, 0.1, 0.25, 0.5 × ATR; 0.1%, 0.25% fixed | 0.1 × ATR |
| hold_rule | none, session_end, max_hold 24h, max_hold 72h | none |
| zone | full, body | full |
| pierce | 0, 0.05% | 0 |
| trend_filter | off, on | off |

Cells: heatmap 1 (R_target × entry) 8; heatmap 2 (k × N) 9; heatmap 3 (ATR
buffer × hold) 16; singles: body zone, pierce, trend on, two fixed-% buffers = 5.
Total 38, minus two duplicate primaries = **36 unique variants per pair ×
session, 324 in total**. This is the number of trials logged for the deflated
Sharpe. Heatmap cells show mean net R, Sharpe, and n with the primary marked.

### 11.2 Data volume

| Item | Rows | On disk (Parquet) |
|---|---|---|
| 15m, 3 pairs, from listing | ~0.7 M | ~30 MB |
| 1m, 3 pairs, from listing | ~10.5 M | ~400 MB |
| Funding | ~22 k | < 1 MB |
| Bulk zips (deleted after conversion) | | ~400 MB transient |

### 11.3 Runtime (20-CPU laptop; conservative)

| Step | Estimate |
|---|---|
| Download + convert + validate | 10–20 min, once |
| One variant run (236k candles, Python loop) | 5–15 s |
| Per-variant stats (bootstraps) | ~2 s |
| Grid, 324 runs, 8 workers | 5–15 min |
| Baselines, primary runs (9 × 2 × 5,000 runs × ~1,000 trades) | ~30–60 min on 8 workers |
| Baselines, grid cells (324 × 2 × 500 runs) | ~30–60 min on 8 workers |
| Reports | ~5 min |

Trade counts are a guess (around 1,000 per pair × session in-sample; fill rate
unknown). If the loop is slower than estimated, the first optimization is
caching the signal stream per (k, N, session, zone, pierce, trend) since only
16 of the 36 variants differ in those; the second is a vectorized swing/impulse
path validated against the loop.

## 12. Risks

1. **Cost drag dominates.** Costs in R equal `(maker + taker + slippage) /
   stop distance`. A 0.3% stop distance costs ~0.25R per trade; a 0.1% stop
   costs ~0.7R. A 2R-target strategy then needs a win rate well above 40% just
   to break even. The report will show the cost-in-R distribution prominently,
   because this alone can decide the answer.
2. **Tiny stops and high implied leverage.** Small bearish candles with a
   0.1 ATR buffer give stop distances of 0.05–0.2%, implying 5–20× leverage at
   1% risk. The cap skips the extremes and counts them; the distribution is
   reported.
3. **Long-only in a bull-heavy sample.** 2020–2021 and 2023–2025 were strongly
   up. Baseline B and buy-and-hold address this, but the answer may be
   "the edge is beta".
4. **Bulk archive quirks.** Header/no-header, timestamp units, occasional
   missing months, exchange outages producing gaps. Mitigated by checksums, the
   manifest, gap reports, and the 1m→15m cross-check.
5. **Exchange rule changes over time.** Fee tiers, funding caps and intervals
   (some symbols moved to 4h funding), max leverage tiers. Funding intervals
   come from the data; fees are constants applied uniformly and stated as such.
6. **Look-ahead through indicators.** Swing highs reference future candles by
   definition; the `confirmed_at` index and the MarketView guard are the
   defence, and the perturbation tests in 3.3/4.6 are the proof.
7. **Dependent trades.** Overlapping positions and one-per-day trades in
   autocorrelated markets violate the iid assumption of the trade bootstrap;
   hence the day-block secondary CI and the block bootstrap for Sharpe.
8. **Holdout contamination.** The loader guard, the one-shot marker, and the
   pre-registration hash check are mechanical safeguards; the human one is
   that the primary is not edited after in-sample results are seen.
9. **SOLUSDT short history.** In-sample starts 2020-09-14; fewer trades, wider CIs.
10. **Windows multiprocessing** uses `spawn`; runner functions must be
    importable, and Parquet writes must be per-process.
11. **Disk.** 8.7 GB free; the pipeline needs ~1.2 GB. Zips are deleted after
    conversion.
12. **Baseline design choices** (market entry with maker fee, uniform entry
    time) are judgment calls; both are stated in the report and configurable.

## 13. Draft pre-registration file (`configs/prereg.yaml`)

```yaml
registered_on: 2026-09-26
data_download_date: null          # set when data is fetched; defines "present"
insample: {start: 2020-01-01, end: 2025-12-31}
holdout:  {start: 2026-01-01, end: null}   # end = data_download_date
pairs: [BTCUSDT, ETHUSDT, SOLUSDT]
sessions:
  utc:    {tz: UTC,              open: "00:00", close: "24:00", days: [0,1,2,3,4,5,6]}
  ny:     {tz: America/New_York, open: "09:30", close: "16:00", days: [0,1,2,3,4]}
  london: {tz: Europe/London,    open: "08:00", close: "16:30", days: [0,1,2,3,4]}
primary:
  swing_k: 2
  confirm_n: 3
  zone: full
  entry_level: top
  stop_buffer: {kind: atr, value: 0.1}
  r_target: 2.0
  hold_rule: {kind: none}
  trend_filter: off
  pierce: 0.0
  structure_break: fresh          # D2
  skip_mitigated: continue        # D4
execution:
  fee_maker: 0.0002
  fee_taker: 0.0005
  slippage: 0.0002
  risk_per_trade: 0.01
  max_leverage: 25
  start_equity: 10000
  use_1m: true
grid:                              # other axes held at primary values
  heatmaps:
    - {axes: [r_target, entry_level], r_target: [1, 1.5, 2, 3], entry_level: [top, mid]}
    - {axes: [swing_k, confirm_n], swing_k: [1, 2, 3], confirm_n: [2, 3, 5]}
    - {axes: [stop_buffer, hold_rule],
       stop_buffer: [{kind: atr, value: 0}, {kind: atr, value: 0.1}, {kind: atr, value: 0.25}, {kind: atr, value: 0.5}],
       hold_rule: [{kind: none}, {kind: session_end}, {kind: max_hold, hours: 24}, {kind: max_hold, hours: 72}]}
  singles:
    - {zone: body}
    - {pierce: 0.0005}
    - {trend_filter: on}
    - {stop_buffer: {kind: pct, value: 0.001}}
    - {stop_buffer: {kind: pct, value: 0.0025}}
stats:
  bootstrap_n: 10000
  block_len_days: 10
  baseline_runs_primary: 5000
  baseline_runs_grid: 500
  alpha: 0.05
  master_seed: 20260926
verdict_rule: see design section 8
```

## 14. Deferred / later

- Vectorized signal path (with equivalence test) if runtime matters.
- Portfolio view combining the three session variants.
- Short-side mirror of the strategy (the framework will not assume long-only;
  `PlaceBracketLimit` carries a side).
- Additional pairs: add to `pairs` in the pre-registration file; the data layer
  handles listing dates automatically.
- Live-trading adapter: out of scope.
