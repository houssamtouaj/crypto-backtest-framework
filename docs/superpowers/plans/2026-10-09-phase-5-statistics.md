# Phase 5 — Statistics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a `SimResult` into evidence: bootstrap CIs, the batched trade evaluator proven equal to the simulator, baselines A and B with p-values, exact cost re-pricing, buy-and-hold, deflated Sharpe, Holm, rolling/regime/diagnostic tables, and a lossless per-variant `stats.json`.

**Architecture:** Pure numpy functions, one module per concern under `perpbt/stats/`. Every random function takes an explicit `np.random.Generator`; `stats/seeds.py` derives generators from `(master_seed, variant_id, purpose)`. The evaluator (`stats/evaluator.py`) vectorizes the §4.3 rules over many pre-specified trades and hands every 1m case to the Phase 4 functions `fills.walk_minutes` / `fills.resolve_candle` (shared code). Every outcome is stored as *components* (gross R plus the cost coefficients in R and funding R), so net R under any cost setting is the one linear formula in `stats/repricing.py`; baselines store per-run component means, so `p_A`, `p_B` exist for every re-pricing cell at no extra cost. `stats/variant.py` assembles `stats.json`.

**Tech Stack:** Python 3.11, numpy 2.x, pandas 2.x, scipy (normal quantiles, skew/kurtosis), pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-ob-backtest/phase-5-statistics.md`, read with `00-overview.md` (§6 conventions, D5, D6, D11, D13) and `phase-4-execution.md` (§4.3 fill rules, §4.4 1m resolver, §4.5 funding, §4.7 cost coefficients, §4.10 tables). Consumers: `phase-6-experiments.md` §6.3 (`results` columns), §6.5 (baselines pass writes `baseline_a.parquet`, `baseline_b.parquet`, updates `stats.json`), `phase-7-reporting.md` (every number from `stats.json`).

**Execution:** Native (user preference, memory `workflow-end-to-end-phases`): this plan is the working document; code goes straight into the files task by task with its tests, then one fresh whole-branch review. Code blocks give the interfaces and the non-obvious logic; the tests named per task are the acceptance list.

## Global Constraints

- Branch `phase/5-statistics` from `dev` at `f66a96d`; push on the first commit; spec amendments in the same commit as the deviating code; merge into `dev` with `--no-ff` only after the user says so (overview §8.1).
- All trade statistics use the R subset `exit_reason != "data_end"`.
- `B = 10,000` bootstrap replicates, block length 10 days, percentile 95 % CIs; `p = (1 + #{runs ≥ observed}) / (M + 1)` for baselines; `p = (1 + #{replicates ≤ 0}) / (B + 1)` for the paired Sharpe difference.
- Daily Sharpe `sqrt(365) × mean / std` (ddof 1) of the `daily.ret` series, zero-return days included.
- Re-pricing grid `slippage ∈ {0, 0.0002, 0.0005, 0.001}` × `maker ∈ {0, 0.0002}` (from `StatsConfig`), taker = `exec.fee_taker`.
- Net R formula, in this float order (identical to `costs.trade_costs`): `gross_r − fm × (c_maker_entry + c_maker_exit) − ft × (c_taker_entry + c_taker_exit) − s × c_slip − funding_r`; a table without `c_taker_entry` (the simulator's trades) uses `− ft × c_taker_exit`, so re-pricing at the primary costs gives the simulator's `net_r` bit for bit.
- `stats.json`: sorted keys, non-finite floats stored as `null` (the in-memory dict is sanitised first, so `read == written`), byte-identical across reruns.
- Fast suite `python -m pytest -q -m "not slow"`; the real-data stats smoke run is `@pytest.mark.slow`.

## Decisions where the spec is silent or conflicts (recorded in the spec in the implementing commit)

1. **Evaluator step k = 0 (memory: Phase 4 §5.2 conflict).** The entry candle applies `fills.apply_rules` to an already-filled bracket: stop allowed (reference `min(stop, open)`), target not. When the candle touched two or more of {entry, stop, target} and 1m is in use, the 15 minutes are walked *from the fill minute* by `fills.walk_minutes` with a "marketable at the open" level (`fill_at = +inf`, `first_look=True`), which is exactly the post-fill part of the simulator's walk (stop allowed and target not on the fill minute, both after it). `TradeSpec` gains `entry_minute` (0..14; 0 for `market_open`; the simulator's trades give `(entry_ms − τ_entry) / 1 min`).
2. **`TradeSpec.stop_dist`** (the R unit) is optional and defaults to `entry_price − stop`. The simulator's trades carry `stop_dist = planned_entry − stop`, which differs from `entry_price − stop` on `open_gap` fills.
3. **`Outcome.c_taker_entry`** exists for `entry_role = "taker"` (fee only; slippage is charged on exits only, D8). Baselines use maker entries, so it is 0 there.
4. **`Outcome.exit_reason`** is one of `stop`, `target`, `time`, `data_end` (the evaluator does not know the hold kind; the equivalence test maps `session_end`/`max_hold` to `time`). `exit_role` is `maker` for targets, else `taker`.
5. **Series end.** `evaluate(..., last_idx=None)` takes the period's last candle (default the last candle given); a trade still open after that candle's stop/target/deadline checks exits at its close as `data_end` (taker, slippage), as simulator §4.9.
6. **Deadline candle.** `deadline_idx` = first candle whose close is `≥` the deadline (`searchsorted(ts + 15m, deadline_ms)`), clamped to `≥ entry_idx`; time exits only when no stop/target exit happened on that candle (simulator order).
7. **Funding in the evaluator.** Events with `τ_entry + 15m ≤ f < τ_exit + 15m` (on the grid this is `[fill close, exit open]`), each at the close of the candle before the one containing `f` (the simulator's `close[i−1]`), `funding_r = Σ rate × price / stop_dist`. Off-grid events raise, as in the simulator.
8. **Baseline slots.** Baseline A's candidate entries are the in-window candles `e` of the trade's own session with `first_idx ≤ e ≤ last_idx` and finite, positive `ATR14[e−1]`; baseline B's sessions are those of `calendar.eligible_days(max(period_start, listing), period_end)` with at least one such candle. A real trade with NaN `stop_dist_atr` is left out of both baselines (counted as `n_excluded`), and the observed statistic is the mean over the trades the baseline covers.
9. **Baseline trades that reach the data end** are left out of their run's mean (table A: the slot is dropped; B: the run mean is over the completed trades), mirroring the R subset of the real trades. A real trade whose slots all reach the data end is left out of A (counted).
10. **Baseline B is evaluated in chunks of runs** (one `evaluate` call per chunk of concatenated per-run specs); each run's draws come from the generator in run order, so results do not depend on the chunk size.
11. **Both baselines store per-run component means** (`M × 7`, columns `gross_r, c_maker_entry, c_maker_exit, c_taker_entry, c_taker_exit, c_slip, funding_r` plus `n`); A's runs are slot draws over the precomputed table. `z = (observed − mean) / std` (ddof 1).
12. **Buy-and-hold day boundaries.** Day `d` = the UTC days with candles in the period (the same dates as `daily`); `close_d` = close of the day's last candle; the first day's previous close is the close of the candle before the period (the open of the first candle when there is none); funding events with `f // 1 day == d` are charged to day `d` (the simulator's assignment). Scaling uses in-sample σ (ddof 1); a holdout passes the in-sample σ's in.
13. **DSR in `stats.json`.** The per-variant file stores the inputs (`sr_daily`, `T`, `skew`, `kurt`); the two DSR values need the grid's Sharpe variances and are written by the experiments layer (like Holm), via `dsr.dsr_from_trials`. `dsr` with `N = 1` uses `SR* = 0` (the formula's `Φ⁻¹(0)` is −∞).
14. **Regimes.** `regime_trend` = `"trend"` if daily ADX(14) at `entry_idx` (`daily_adx_aligned`, completed days before the entry day) `> 25`, `"no_trend"` if `≤ 25`, null if NaN. `regime_vol` = `"high"` if the 30-day std (ddof 1) of daily log returns of the completed days before the entry day is above the pair's in-sample median, `"low"` otherwise, null if NaN; the median is over the as-of values of the in-sample period's days, stored in `stats.json` (`insample_ref.vol_median`) and passed to holdout runs.
15. **Rolling.** 6-month windows `[month m, month m + 6)` for every month start from the period's first month whose window ends at or before the period end; rolling Sharpe over the 182 days ending at each month end with ≥ 182 days of history. Per-year fill rate = filled / placed entry orders (excluding `leverage_cap`) by placement year.
16. **Exposure.** `exposure_time` = fraction of period candles in `[entry_idx, exit_idx]` of some trade (all trades, including `data_end`); `exposure_notional` = mean of `daily.exposure_notional / daily.equity`.

## Review Focus

1. **An evaluator trade that opens and ends on the same candle under 1m** (fill at minute 7 then target at minute 9, or stop on the fill minute): the walk must start at the fill minute, not minute 0, and the fill minute must not allow a target. Pinned in Task 2 by `test_entry_candle_walk_starts_at_the_fill_minute` and the equivalence test with 1m.
2. **1m data that disagree with the 15m bar** (an ambiguous 15m candle whose walk finds no exit): the trade stays open and the deadline/data-end checks still run on that candle. Pinned in Task 2 by `test_walk_with_no_exit_falls_through_to_deadline`.
3. **Degenerate statistics inputs:** zero or one trade, constant net R, all-zero daily returns (std 0). CIs must be finite or `None`, never an exception; `stats.json` must still round-trip. Pinned in Task 1 (`test_degenerate_inputs`) and Task 8 (`test_stats_json_with_no_trades`).
4. **Baseline draws respecting the calendar:** NY/London sessions only on local weekdays, nothing before the listing date, nothing outside `[first_idx, last_idx]`. Pinned in Task 4 by `test_baseline_b_respects_weekdays_listing_and_period`.
5. **Very long-lived trades** (`hold_rule = none`, wide stops) that outlive the vectorized steps: the straggler scan must give the same result as the vectorized path and finish without scanning the whole series per step. Pinned in Task 2 by `test_straggler_and_vectorized_paths_agree` (forcing each path).

---

### Task 1: Seeds and bootstraps — `stats/seeds.py`, `stats/bootstrap.py`

```python
def rng_for(master_seed: int, variant_id: str, purpose: str) -> np.random.Generator   # SeedSequence(sha256 entropy)
def percentile_ci(reps, level=0.95) -> tuple[float|None, float|None]                 # NaN-safe; None when empty
def trade_bootstrap(x, B, rng) -> np.ndarray                                         # replicate means, chunked
def day_block_bootstrap(x, day, B, rng) -> np.ndarray                                # resample days, Σsum/Σcount
def sharpe(ret) -> float                                                             # sqrt(365) mean/std(ddof 1); NaN if std 0 or < 2
def block_indices(T, B, rng, block_len) -> Iterator[np.ndarray]                      # moving blocks, chunks of rows
def block_bootstrap_sharpe(ret, B, rng, block_len=10) -> np.ndarray
def paired_sharpe_diff(a, b, B, rng, block_len=10) -> dict  # {diff, ci_lo, ci_hi, p}
```

Tests (`tests/test_bootstrap.py`): constant trades → degenerate CI; N(0,1) coverage over 500 seeded draws in 93–97 %; day-block CI wider than trade CI under a common within-day shock; iid Sharpe coverage; AR(1) block CI wider than iid (block length 1) CI; paired difference of a series with itself is exactly 0 with zero-width CI and `p = 1`; `rng_for` deterministic and purpose-sensitive; degenerate inputs (Review Focus 3).

### Task 2: Batched evaluator — `stats/evaluator.py`

```python
COMPONENTS = ("gross_r", "c_maker_entry", "c_maker_exit", "c_taker_entry", "c_taker_exit", "c_slip", "funding_r")
@dataclass class TradeSpec: entry_idx, entry_kind, entry_price, stop, target, deadline_idx, pierce_abs, entry_role,
                            entry_minute=None, stop_dist=None   # arrays of length m
@dataclass class Outcome: exit_idx, exit_minute, exit_ref, exit_reason, exit_role, funding_r, gross_r,
                          c_maker_entry, c_maker_exit, c_taker_entry, c_taker_exit, c_slip
def evaluate(spec, candles15, candles1m, funding, exec_cfg, *, last_idx=None, min_batch=32, max_steps=512) -> Outcome
def net_r(outcome, fee_maker, fee_taker, slippage) -> np.ndarray
def specs_from_trades(trades: pd.DataFrame, candles15) -> TradeSpec    # the simulator's trades as `limit` specs
def deadline_index(ts, deadline_ms) -> int
```

Steps: validate; k = 0 for all (vectorized 15m rule; per-trade walk for ambiguous with 1m); then vectorized steps `idx = entry_idx + k` while ≥ `min_batch` pending and `k ≤ max_steps`; stragglers by windowed `argmax` scans (window 1,024, doubling). Every candle-level resolution: stop and target both → 1m `resolve_candle(False, …)` when in use, else stop; stop → `min(stop, open)`; target → `target`; else deadline candle → time at close; else last candle → data end.

Tests (`tests/test_evaluator.py`): hand cases (fill-candle stop, fill-candle target ignored, stop before target, gap below stop, deadline, data end, pierce on the target); equivalence with the simulator on random walks (UTC and NY, primary, `max_hold` + pierce, `session_end`; with and without 1m; includes `open_gap` and `data_end` trades): identical `exit_idx`, `exit_ref`, `exit_reason` (mapped), funding and `net_r` (1e-12); straggler-only and vectorized-only runs agree (Review Focus 5); Review Focus 1 and 2 tests.

### Task 3: Re-pricing core — `stats/repricing.py`

```python
def reprice(components: Mapping | pd.DataFrame, fee_maker, fee_taker, slippage) -> np.ndarray
```
`evaluator.net_r` calls it. Tests: `reprice` of the simulator's trades at its own costs equals `net_r` bit for bit (random walks, with funding); linearity (two cost settings).

### Task 4: Baselines A and B — `stats/baselines.py`

```python
@dataclass(frozen=True) class Setup: candles15, candles1m, funding, calendar, exec_cfg, params, first_idx, last_idx, listing_ms=None; atr (computed)
def make_setup(...) -> Setup
@dataclass class TableA: trade_ids, n_slots, entry_idx (m,S), comps (m,S,7), n_excluded; to_frame()
def slot_specs_a(trades_r, setup) -> tuple[TradeSpec, owner]
def precompute_table_a(trades, setup) -> TableA
def table_from_outcome(owner, entry_idx, outcome, trade_ids, *, n_excluded=0) -> TableA
def runs_a(table, M, rng) -> pd.DataFrame            # per-run component means
def b_sessions(setup) -> (first, count, slots)       # eligible sessions and their valid slots
def b_spec(setup, sessions, pool, m, rng) -> TradeSpec
def runs_b(trades, setup, M, rng, *, chunk_trades=200_000) -> pd.DataFrame
def p_value(observed, run_values) -> tuple[float, float]   # (p, z)
def observed_components(trades) -> pd.DataFrame       # the R subset's components (c_taker_entry = 0)
```

Tests (`tests/test_baselines.py`, coin-flip strategy in `tests/coinflip.py`): forced slots (the simulator's own trades as one-slot table) reproduce the real mean R exactly; coin-flip on 20 random-walk seeds → at most 3 of 20 `p_A < 0.05` and at most 3 of 20 `p_B < 0.05`, `p > 0` always; B respects weekdays, listing and period (Review Focus 4); B per-run component means re-price to the direct `net_r` mean under two cost settings; A's table slots cover exactly the in-window candles of the trade's session; results independent of B's chunk size.

### Task 5: Re-pricing grid — `stats/repricing.py`

```python
def reprice_grid(trades, runs_a, runs_b, obs_a, obs_b, exec_cfg, stats_cfg, rng_seed_fn) -> pd.DataFrame
def cost_summary(trades_r) -> dict   # share cost_r > 1, cost_r / stop_dist % / ATR quantiles
```
Tests: 8 cells; the primary cell's mean equals the simulator's mean `net_r`, its CI equals the headline CI (same generator purpose), its `p_A` equals the direct computation; columns present when baselines are absent (`None`).

### Task 6: Buy-and-hold, DSR, Holm — `stats/buyhold.py`, `stats/dsr.py`, `stats/multiplicity.py`

```python
def bh_daily(candles15, funding, first_idx, last_idx) -> pd.DataFrame       # date, close, funding, ret
def buy_and_hold(strategy_daily, bh, B, rngs, block_len, *, sigma_strategy=None, sigma_bh=None) -> dict
def expected_max_sr(n_trials, var) -> float; def psr(sr, sr_ref, T, skew, kurt) -> float
def dsr(sr, T, skew, kurt, n_trials, var) -> float; def sr_moments(ret) -> dict; def dsr_from_trials(ret, trial_srs) -> dict
def holm(p) -> np.ndarray; def bonferroni(p) -> np.ndarray
```
Tests: 3-day hand example with funding; scaling equalises vol; paired difference sign on a hand case; DSR decreasing in N, `N = 1` equals PSR(0), the Bailey–López de Prado worked example (SR 2.5/√250, T 1250, γ₃ −3, γ₄ 10, N 100, V ½/250 → DSR ≈ 0.90); Holm on a hand 9-vector, monotone, ≤ 1.

### Task 7: Rolling, regimes, diagnostics — `stats/rolling.py`, `stats/regimes.py`, `stats/diagnostics.py`

```python
def rolling_windows(trades_r, period_start_ms, period_end_ms, months=6) -> list[dict]
def rolling_sharpe(daily, window_days=182) -> list[dict]
def per_year(trades_r, daily, orders) -> list[dict]
def daily_vol_asof(candles15, n=30) -> np.ndarray        # aligned to the 15m index
def vol_median(candles15, start_ms, end_ms, n=30) -> float
def label_regimes(trades, candles15, vol_median) -> pd.DataFrame
def headline(trades_r) -> dict; def max_drawdown(equity, start) -> float
def exposure(trades, daily, first_idx, last_idx) -> dict; def quantiles(x) -> dict | None
def breakdown(trades_r, key) -> list[dict]; def shared_fraction(tables: Mapping[str, DataFrame]) -> dict
```
Tests: hand-made trade lists for windows, per-year and breakdowns; regime labels use the day before entry (a jump on the entry day does not change the label); shared fraction on three synthetic tables.

### Task 8: `stats.json` — `stats/variant.py`; smoke run; spec amendments; review

```python
@dataclass(frozen=True) class Market: candles15, candles1m, funding, calendar, period_start_ms, period_end_ms
@dataclass class Baselines: table_a, runs_a, runs_b, n_runs
def run_baselines(result, market, cfg, *, variant_id, n_runs=None, listing_ms=None) -> Baselines
def compute_stats(result, market, cfg, *, variant_id, baselines=None, insample_ref=None, listing_ms=None) -> dict
def write_stats(stats, path); def read_stats(path) -> dict; def sanitize(obj) -> obj
```
Tests: random-walk variant → every §5.10 block present, write/read equal, two writes byte-identical; no trades (Review Focus 3); baselines absent → p fields null. Slow: BTCUSDT 2024 UTC smoke run → `stats.json` written and read back without loss (exit criterion), numbers printed.

## Execution notes

- Task 3 folded into Task 2: `evaluator.net_r` calls `repricing.reprice`; its bit-for-bit test lives in `test_evaluator.py`.
- The random-walk helpers (`walk`, `run_walk`) moved from `test_simulator.py` into `tests/sim_harness.py`; `tests/coinflip.py` is a no-edge strategy whose random-walk entries are market entries at the open (the baselines' shape).
- The coin-flip null test cannot see slot-geometry errors (zero drift): a planted `r_target → 1` passed it, so `test_slot_at_a_coin_flip_entry_is_that_trade` pins the geometry against real trades.
- Coverage of the bootstrap CIs on the fixed seeds: trade 93.8 %, block Sharpe 94.0 %.
- Real-data smoke (BTCUSDT 2024 UTC primary, 500 baseline runs): n 315, mean net R −0.229 [−0.400, −0.057], p_A 0.124, p_B 0.279, Sharpe −2.60 vs B&H 1.53 (p_BH 0.998); baselines 1 s, stats 1.5 s. Reviewer at full scale (BTC 2020–2025): baselines 67 s with 5,000 runs, `compute_stats` 5 s.
- Whole-branch review (fresh reviewer): no critical. Fixed: `n_sessions` and `std_net_r` added to `stats.json` (Phase 6/7 read them); the evaluator accepted only `stop < entry`, which rejected a simulator `open_gap` fill below the stop. Spec reconciled: baseline B's NaN-multiple rule, `reprice_grid` signature.
