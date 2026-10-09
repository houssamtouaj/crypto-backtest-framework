# Phase 4 — Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the simulator: a deterministic candle loop that turns a strategy's intents into orders, fills, exits, costs, funding, a cash ledger with daily marks, and the `orders` / `fills` / `trades` / `daily` / `events` tables every Phase 5 statistic consumes.

**Architecture:** Pure, scalar fill rules live in `execution/fills.py` (one function applies spec §4.3 to one candle of any timeframe; a second walks the 15 one-minute candles of an ambiguous 15m candle), so the Phase 5 batched evaluator can call the same code. `costs.py` and `sizing.py` are pure functions of numbers. `orders.py` holds the mutable `Order` / `Position` records and the string vocabularies. `ledger.py` keeps cash, the open-position set, mark-to-market and the daily marks. `simulator.py` is the loop of §4.1 and owns the bookkeeping (ids, events, the strategy's `AccountView`); `trades.py` turns the records into typed DataFrames, computes MAE/MFE and writes Parquet. The `MarketView` builder moves to `strategy/base.py` so the strategy harness and the simulator build the same view.

**Tech Stack:** Python 3.11, numpy 2.x, pandas 2.x, pyarrow (Parquet), pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-ob-backtest/phase-4-execution.md`, read with `00-overview.md` (§4.4, §6, §7, D5, D8) and `phase-3-strategy.md` (§3.1 interface and call contract, §3.5 one intent per session, §3.6 pierce, §3.10 hold rules). Consumer: `phase-5-statistics.md` §5.2 (the evaluator reuses the 1m resolver) and §5.3 (`stop_dist_atr`, `atr_at_entry`). Code this builds on: `perpbt/strategy/base.py`, `perpbt/strategy/order_block.py`, `perpbt/data/store.py` (`Candles`, `Funding`), `perpbt/data/sessions.py`, `perpbt/config.py` (`ExecConfig`, `HoldRule`), `tests/synthetic.py`, `tests/strategy_harness.py`.

**Execution:** Native (user preference, memory `workflow-end-to-end-phases`): this plan is the working document; code is written straight into the files task by task with its tests, then one fresh whole-branch review. Code blocks below give the interfaces and the non-obvious logic; the tests named in each task are the acceptance list.

## Global Constraints

- Branch `phase/4-execution` from `dev` at `68448b5`; push on the first commit; small commits with tests; merge into `dev` with `--no-ff` only after the user says so; delete the branch locally and on `origin` (overview §8.1). Spec amendments go in the same commit as the code that deviates.
- Time: UTC `int64` ms, candle open times; 15m candle `j` spans `[τ_j, τ_j + 15m)`; decisions at the close.
- The simulator has no randomness; two runs give byte-identical Parquet.
- Fill threshold `fill_at = price − pierce_abs` with `pierce_abs = strategy.params.pierce * intent.price`, the same float operations as Phase 3 §3.6/§3.7. Target threshold `target + pierce_abs`.
- Fees on the reference price: maker `fee_maker × qty × entry_price` on every entry; target exits maker on `exit_ref`; stop, time and data-end exits taker on `exit_ref` plus `slippage × qty × exit_ref`. Executed exit price `exit_ref × (1 − slippage)` for taker exits.
- R coefficients exactly as the §4.7 table; `net_pnl = net_r × risk_usd`; identity `net_pnl == gross_pnl − fees − slippage_cost − funding` asserted per trade (`math.isclose(rel_tol=1e-12, abs_tol=1e-9)`).
- Long only: a `short` `PlaceBracketLimit` raises `NotImplementedError` (overview §11 defers shorts).
- Tests: pytest, one file per module (`test_fills.py`, `test_costs.py`, `test_sizing.py`, `test_ledger.py`, `test_trades.py`, `test_simulator.py`), synthetic data; the real-data smoke run is `@pytest.mark.slow` in `tests/test_real_data.py`.
- Files are LF; edit with the Edit tool. Fast suite: `python -m pytest -q -m "not slow"` from the repo root.

## Decisions where the spec is silent (recorded in the spec in the commit that implements them)

1. **Funding off the 15m grid is refused.** All real funding timestamps are on the 15m grid (checked 2026-10-09: BTC 6,576, ETH 6,576, SOL 5,881 events, 0 off-grid). The simulator raises `ValueError` on an off-grid event instead of guessing a "close of the candle ending at `f`". A funding event that falls in a candle gap is charged at the next candle's step 1 at the last close before it; this is the §4.5 predicate (`fill close ≤ f ≤ exit open`) applied to gapped data.
2. **Expiry is checked at both ends of the candle.** Before the entry check (`expires_ms ≤ τ_j`, the spec rule, which catches gaps) and after it (`expires_ms ≤ τ_j + 15m`), so an order expiring at the close of `j` can still fill on `j` (E6) but does not show as pending in the `AccountView` of that close. `cancelled_ms = expires_ms`.
3. **Leverage-capped intents get an `orders` row** (`entry_limit`, status `cancelled`, reason `leverage_cap`, `cancelled_ms = placed_ms`, never evaluated), so every intent is auditable; `n_orders` in the summary counts only registered orders. Spec §4.6 "not registered" means never pending.
4. **Exit siblings are cancelled with reason `oco`.** When the stop fills, the target order is cancelled (and vice versa; both on a time exit). `oco` joins the cancel reasons.
5. **Total open notional** for the cap is `Σ qty × close[i]` over open positions (marked, like `equity_mtm`); pending orders do not count. `exposure_notional` in `daily` uses the same mark.
6. **Daily mark after the whole candle.** The daily row is written at the end of the candle's processing (after placement and, on the last candle, after the data-end close), at every candle whose next candle opens on a later UTC day, and at the last candle of the period. Without gaps this is exactly "`τ_i + 15m` is 00:00 UTC" plus a final mark, and the daily returns compound exactly to the final equity. `date` = UTC date of `τ_i`.
7. **Data end on the last candle:** steps 1–5 run as usual (the strategy's last intents are registered), then every open position is closed at `close[last]` (`time_exit` order, `exit_reason = data_end`, taker, slippage) and every pending order is cancelled with `data_end`.
8. **Resolution labels per fill.** Every fill of one order on one candle carries the candle's label (`15m_unambiguous`, `15m_pessimistic`, `15m_pessimistic_missing_1m`, `1m`, `1m_pessimistic`); an entry filled at an open price is `open_gap` instead. Time exits on an ambiguous candle carry that candle's label; time exits otherwise and data-end exits are `15m_unambiguous`.
9. **Time exits:** deadline `None` (`none`), `calendar.end_ms[placed_idx]` (`session_end`), or `τ_fill15 + 15m + round(hours × 3.6e6)` (`max_hold`); the exit happens at the first evaluated candle with `τ_j + 15m ≥ deadline` (equality on the grid). A `session_end` position filled on the window's last candle exits at that close.
10. **Trade timestamps:** `entry_ms` / `exit_ms` are the fills' `ts_ms` (open time of the 15m candle, or of the 1m candle when 1m resolved it); `hold_minutes = (exit_ms − entry_ms) // 60_000`. A 24 h `max_hold` gives exactly 1,440.
11. **`equity_at_entry`** is the `equity_mtm` used for sizing (at placement); **`atr_at_entry`** is ATR14 at `fill_idx − 1` (the last close before the entry candle, as baseline A uses `ATR14[e−1]`); `stop_dist_atr = stop_dist / atr_at_entry` (NaN when ATR is NaN).
12. **`regime_trend`, `regime_vol`** are written as nulls; Phase 5 `stats/regimes.py` owns the labels (the vol label needs the pair's in-sample median).
13. **MAE/MFE window** `[fill_ts, exit_ts + span)` where `span` is 1m or 15m by how the exit was resolved; 1m candles when `use_1m` and the window is fully covered, else the 15m candles of the fill through exit candles. `mae_r = (min low − entry_price) / stop_dist` (≤ 0), `mfe_r = (max high − entry_price) / stop_dist` (≥ 0).
14. **`n_open` is `int16`** (spec said `int8`; overlapping `none`-hold positions are unbounded in principle).
15. **`ClosePosition`** closes at `close[i]` as a taker time exit with `exit_reason = "strategy"`; `CancelOrder` cancels with `strategy`. The order-block strategy emits neither; both are tested with a scripted strategy.
16. **`run` gains `variant_id: str = ""` (keyword-only)**; `pair` comes from `candles15.pair`, `session_variant` from `calendar.spec.name`. `period_end_ms` is exclusive (`date_ms(period_end) + 1 day`).
17. **`use_1m` and `candles1m`:** `use_1m=True` with `candles1m=None` raises `ValueError`; `use_1m=False` ignores any 1m candles (resolver and MAE/MFE both).
18. **Missing 1m minutes are logged** as an `events` row (`kind = missing_1m`) and a `logging.warning`, and counted in `summary["n_missing_1m"]`.

## Review Focus

1. **The 1m walk disagreeing with the 15m candle** (the ~20 real candles per pair whose 1m data do not aggregate to the 15m bar). The walk's result is trusted: an order the 15m bar said filled may stay pending after the walk; the code must not assume a fill or an exit happened. Pinned in Task 2 by `test_walk_that_never_fills_leaves_the_order_pending`.
2. **An entry, its stop and a deadline on the same candle** (a `session_end` order filled on the window's last candle, or a gap fill below the stop). Stop wins, then the time exit only if still open; a fill on the last window candle with no stop exits at that close. Pinned in Task 4 by `test_e7_session_end_fill_on_last_window_candle_exits_at_its_close` and `test_open_gap_below_stop_exits_at_the_open`.
3. **Funding at the period's first candle and at the data end.** No position can be open at `period_start`; a position closed by `data_end` at the last candle is charged funding at `f = τ_last` (exit candle open equals `f`). Pinned in Task 5 by `test_funding_boundaries` (all three boundary cases) and `test_data_end_position_pays_funding_at_last_open`.
4. **Several positions open at once (D5)** with their own stops, targets and funding, processed in `order_id` order; the leverage cap counts their marked notional. Pinned in Task 6 by `test_total_notional_cap_counts_open_positions` and in Task 7 by the random-walk property test (equity identity with overlapping trades).
5. **NaN or zero equity / degenerate sizing** cannot occur with 1 % risk, but a strategy (or a config with `risk_per_trade = 1`) could drive equity to ≤ 0; placement must raise rather than size a negative order. Pinned in Task 6 by `test_placement_with_non_positive_equity_raises`.

---

### Task 1: Vocabulary, records, shared `MarketView` builder, Phase 3 pointer

**Files:**
- Create: `perpbt/execution/orders.py`
- Modify: `perpbt/strategy/base.py` (`build_market_view`, `VIEW_*` constants; `PlaceBracketLimit.expires_ms` accepts numpy ints via `as_int`)
- Modify: `perpbt/strategy/order_block.py` (`ATR_PERIOD = VIEW_ATR_PERIOD`, `TREND_SMA_DAYS = VIEW_SMA_DAYS`)
- Modify: `tests/strategy_harness.py` (`build_view` calls `build_market_view`)
- Test: `tests/test_strategy_base.py` (append)

**Interfaces (produces):**

```python
# perpbt/strategy/base.py
VIEW_ATR_PERIOD = 14; VIEW_SMA_DAYS = 50; VIEW_ADX_PERIOD = 14
def build_market_view(candles: Candles, calendar: SessionCalendar, *, swing_k: int,
                      daily_sma: np.ndarray | None = None, start_i: int = 0) -> MarketView

# perpbt/execution/orders.py
ORDER_KINDS = ("entry_limit", "stop", "target_limit", "time_exit")
ORDER_STATUSES = ("pending", "filled", "cancelled")
CANCEL_REASONS = ("expired", "strategy", "leverage_cap", "data_end", "oco")
EXIT_REASONS = ("target", "stop", "session_end", "max_hold", "data_end", "strategy")
RESOLUTIONS = ("15m_unambiguous", "15m_pessimistic", "1m", "1m_pessimistic",
               "15m_pessimistic_missing_1m", "open_gap")
@dataclass(slots=True) class Order: order_id, kind, side, price, qty, status, placed_ms, placed_idx,
    session_id, tag_json, expires_ms=None, filled_ms=None, fill_price=None, cancelled_ms=None,
    cancel_reason=None, trade_id=None
@dataclass(slots=True) class Entry: order: Order; stop; target; pierce_abs; fill_at; target_at;
    hold_rule; deadline_session_end_ms; stop_dist; risk_usd; notional; equity_at_entry; tag (dict)
@dataclass(slots=True) class Position: trade_id; entry: Entry; qty; entry_price; fill_idx; fill_ms;
    fill_resolution; deadline_ms; stop_order: Order; target_order: Order; funding_usd = 0.0;
    exit_* fields filled at close
```

Tests: `test_bracket_limit_accepts_numpy_int_expiry` (normalised to `int`), `test_build_market_view_matches_the_harness` (same arrays as the Phase 3 harness built by hand). The existing 477 tests stay green.

### Task 2: Fill rules and the 1m resolver — `execution/fills.py`

**Interfaces (produces; Phase 5 evaluator reuses `apply_rules` and `walk_minutes`):**

```python
@dataclass(frozen=True)
class Levels:            # thresholds of one bracket
    entry: float; stop: float; target: float; pierce_abs: float
    fill_at: float       # entry - pierce_abs
    target_at: float     # target + pierce_abs
def levels(entry, stop, target, pierce_abs) -> Levels

@dataclass(frozen=True)
class Step:              # one candle under §4.3
    touched: int         # how many of {entry, stop, target} were touched
    filled: bool; fill_price: float; fill_gap: bool
    exit: str | None     # "stop" | "target"
    exit_ref: float
def apply_rules(pending: bool, o: float, h: float, l: float, lv: Levels) -> Step

@dataclass(frozen=True)
class CandleOutcome:
    filled: bool; fill_price: float; fill_gap: bool; fill_minute: int      # -1 on the 15m path
    exit: str | None; exit_ref: float; exit_minute: int
    resolution: str      # candle label (decision 8)
def walk_minutes(pending, o1, h1, l1, lv) -> CandleOutcome
def resolve_candle(pending, o, h, l, lv, minutes) -> CandleOutcome
    # minutes: None (no 1m in use), MISSING (1m in use, a minute absent), or (o1, h1, l1)

class MinuteIndex:       # 1m candles; window(τ15) -> (o1, h1, l1) of 15 minutes or MISSING
```

`apply_rules`: if pending: `o ≤ fill_at` → fill at `o` (gap) else `l ≤ fill_at` → fill at `entry` else nothing touched; then `stop_hit = l ≤ stop`, `target_hit = h ≥ target_at`; stop → `exit_ref = min(stop, o)`; else target and not filled on this candle → `exit_ref = max(target, o)`. `touched` counts entry (if pending and filled), stop_hit, target_hit.

`resolve_candle`: `apply_rules` on the 15m bar; `touched ≤ 1` → that result, `15m_unambiguous`; else with `minutes is None` → that result, `15m_pessimistic`; `MISSING` → that result, `15m_pessimistic_missing_1m`; else `walk_minutes` (label `1m`, or `1m_pessimistic` if any minute touched two events).

Tests (`tests/test_fills.py`): E1 trio at the rule level (`low == entry`, pierce 0 fills; pierce > 0 does not; `low == entry − pierce_abs` fills); E2 open gap; fill+stop → stop at `stop` (ref `min(stop, open)`); fill+target → no target; stop+target → stop; target gap above → `max(target, open)`; stop gap below → `open`; 4.2 resolver list: target precedes stop → target, stop precedes target → stop, fill then target in later minute → target, single minute touching both → `1m_pessimistic`, missing minute → `15m_pessimistic_missing_1m`, unambiguous 15m never walks (spy on `walk_minutes` via monkeypatch), walk that never fills leaves the order pending (Review Focus 1); `MinuteIndex.window` returns MISSING for one absent minute and at series edges.

### Task 3: Costs and sizing — `execution/costs.py`, `execution/sizing.py`

```python
# costs.py
@dataclass(frozen=True)
class TradeCosts:
    gross_pnl; fees; slippage_cost; funding; net_pnl
    gross_r; net_r; cost_r; funding_r; c_maker_entry; c_maker_exit; c_taker_exit; c_slip
def trade_costs(*, qty, entry_price, exit_ref, stop_dist, risk_usd, exit_role, funding_usd,
                cfg: ExecConfig) -> TradeCosts            # asserts the identity
def entry_fee(qty, entry_price, cfg) -> float
def exit_fee_and_slippage(qty, exit_ref, exit_role, cfg) -> tuple[float, float, float]  # fee, slip, price
def funding_amount(rate, qty, price) -> float           # positive = paid by a long

# sizing.py
class LiquidationAboveStopError(RuntimeError)
@dataclass(frozen=True) class Sizing: risk_usd; stop_dist; qty; notional; implied_leverage; max_iso_leverage
def size_order(equity_mtm, entry, stop, cfg) -> Sizing         # ValueError if equity <= 0 or not finite
def leverage_ok(sizing, open_notional, equity_mtm, cfg) -> bool
def liquidation_price(entry, equity_mtm, notional, mmr) -> float
def check_liquidation(entry, stop, equity_mtm, notional, mmr) -> float  # raises if liq >= stop
```

Tests: hand trade to the cent (entry 100, stop 99, qty 100, target exit at 102: fees 2.00 + 2.04, net_r = 2 − 0.0002×(100+102) = 1.9596; stop exit at 99 with slippage 0.0002: fees 2.00 + 4.95, slippage 1.98, net_r = −1 − 0.0002×100 − 0.0005×99 − 0.0002×99 = −1.0893); identity holds; time-exit coefficients equal stop coefficients; `open_gap` (entry 99.5 below a planned 100) gives `actual_risk_usd > risk_usd`. Sizing: equity 10,000, entry 100, stop 99 → qty 100, notional 10,000, leverage 1×, `max_iso_leverage = 1/(0.01 + mmr)`; per-order cap and total cap each refuse; liquidation raises with `mmr = 0.2`, entry 100, stop 99.9; non-positive or NaN equity raises.

### Task 4: Ledger and the simulator loop without funding — `execution/ledger.py`, `execution/simulator.py`

```python
# ledger.py
class Ledger:
    def __init__(self, start_equity: float)
    cash: float
    def debit(self, amount: float, *, funding: bool = False) -> None
    def equity(self, positions, close: float) -> float    # cash + Σ qty × (close − entry_price)
    def mark_day(self, date_ms: int, equity: float, n_open: int, exposure: float) -> None
    rows: list[dict]                                        # daily rows (ret vs previous equity)

# simulator.py
@dataclass
class SimResult:
    orders: pd.DataFrame; fills: pd.DataFrame; trades: pd.DataFrame
    daily: pd.DataFrame; events: pd.DataFrame; skips: dict[str, int]; summary: dict
    def to_parquet(self, out_dir: Path) -> None

def run(candles15, candles1m, funding, calendar, strategy, exec_cfg,
        period_start_ms, period_end_ms, *, variant_id: str = "") -> SimResult
```

Loop per candle `i` in `[i0, i_last]`: (1) funding (Task 5), (2) evaluate every active entry / position in `order_id` order with `resolve_candle`, then time exits, (3) `equity_mtm`, `AccountView`, (4) `on_candle`, deliver queued `SimEvent`s from step 2 before the call and from step 5 after it, (5) intents in order: `PlaceBracketLimit` → size, cap, liquidation, register; `CancelOrder`; `ClosePosition`; then on the last candle the data-end close; then the daily mark (decision 6).

Tests (`tests/test_simulator.py`, a `ScriptedStrategy` in `tests/sim_harness.py` that returns given intents at given candles and records events): E1–E9 end to end with zero costs; E10 with `OrderBlockStrategy` and a tiny `max_leverage`; overlapping positions processed in order_id order; `CancelOrder` / `ClosePosition`; short intent raises; `use_1m` without 1m raises; Review Focus 2 tests.

### Task 5: Funding

Step 1 of candle `i`: every funding event with `f < τ_i + 15m` not yet processed and `f ≥ τ_i0` (off-grid → `ValueError`) is charged to every open position at `close[i − 1]`: `amount = rate × qty × close`, debited from cash, added to the position's `funding_usd` and the day's `funding_paid`, logged as a `funding` event.

Tests: the three §4.5 boundary cases (fill candle close = `f` charged; exit candle open = `f` charged; exit candle close = `f` not charged); a 4-hour schedule is honoured (6 charges per day); negative rate pays the long; `funding_r` and `net_r` of a hand trade; off-grid event raises; Review Focus 3.

### Task 6: Sizing, leverage cap and liquidation in the loop; ledger and daily marks

Tests: E10 counts (`skips["leverage"]`, event `skipped_leverage`, orders row `leverage_cap`); total-notional cap with an open position (Review Focus 4); liquidation raises from `run`; non-positive equity raises (Review Focus 5); daily returns compound exactly to the final equity; zero-position days give exactly 0.0; equity after a stop-out equals the hand computation; `n_open` / `exposure_notional` at the mark.

### Task 7: Tables, MAE/MFE, data end, look-ahead and determinism — `execution/trades.py`

```python
ORDERS_SCHEMA, FILLS_SCHEMA, TRADES_SCHEMA, DAILY_SCHEMA, EVENTS_SCHEMA: dict[str, dtype]
def orders_frame(orders, ...) -> pd.DataFrame        # and fills_frame, trades_frame, daily_frame, events_frame
def mae_mfe(entry_price, stop_dist, lo_ms, hi_ms, candles15, minutes) -> tuple[float, float]
```

Tests: column lists and dtypes equal the spec tables (plus decision-12 nulls); MAE/MFE on a synthetic path with and without 1m; `hold_minutes`; `data_end` positions and cancelled pending orders; simulator-level look-ahead (20 seeds × 3 cuts, UTC and NY, 1m from a 1m random walk aggregated to 15m, funding perturbed after the cut): events with `idx ≤ cut` (minus `data_end` rows) and daily rows before the cut's day are identical across full, truncated and perturbed runs; two runs write byte-identical Parquet; random-walk properties: Σ `net_pnl` = final equity − start, every trade's identity, `orders`/`fills`/`trades` cross-references consistent.

### Task 8: Real-data smoke run, spec amendments, review

`test_simulator_smoke_btc_2024` (slow): primary config, BTCUSDT, UTC session, 2024-01-01..2024-12-31, 1m on: no exception; every trade has an exit; for non-`open_gap` trades `net_r ≥ −1 − cost_r − 1e-9` and `net_r ≤ r_target + 1e-9`; fill rate, skip counts and resolution counts printed. Then three trades spot-checked against the raw candles, the spec amended (decisions 1–18), the whole-branch review, and the merge question to the user.

## Execution notes

- **User ruling 2026-10-09 (target pricing).** The smoke run found a target exit above its target: the same-candle rule ignores a target touched on the fill candle (or minute), and the next open above the target was then paid by `max(target, open)`. On all real data, 17 of ~9,600 trades, ~+10 R, all favourable. Ruling: a target exit is always at the target price. `apply_rules` changed; spec §4.3 amended in the same commit.
- **Spec test 4.3 corrected:** an `open_gap` fill gives `actual_risk_usd < risk_usd` for a long (the spec said `>`).
- **Look-ahead test strengthened:** a planted one-candle peek (equity from `close[i+1]`, or `low[i+1]` in the fill check) passed the fixed-cut test; cuts on candles with a placement, a fill and an exit catch both.
- **Real-data speed:** about 2 s per variant over 2020–2025 with 1m resolution (all three pairs, three sessions).
- **Spot check (exit criterion):** BTCUSDT 2024 UTC trades 25 (target), 45 (stop) and 41 (1m-resolved) recomputed by hand from the raw 15m and 1m candles: stop, target, qty, fees, slippage, funding and net R match; no earlier candle touches the entry, stop or target.
