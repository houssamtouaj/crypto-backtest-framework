# Phase 5 — Statistics

Read with `00-overview.md`. Delivers everything that turns a `SimResult`
into evidence: bootstrap CIs, the batched trade evaluator, baselines A and
B, cost re-pricing, buy-and-hold, deflated Sharpe, the Holm adjustment,
alpha decay, regimes, and diagnostics. Depends on Phase 4. Every function
takes an explicit `rng: np.random.Generator`; seeds derive from
`(master_seed, variant_id, purpose)`.

**Branch:** `phase/5-statistics`, created from `dev` after Phase 4 is
merged; merged into `dev` with `--no-ff` when the exit criterion below is
met (overview §8.1).

All trade statistics use the R subset: trades with
`exit_reason != data_end`.

## 5.1 Confidence intervals (`stats/bootstrap.py`)

- **Mean net R, trade bootstrap.** Resample trades with replacement,
  `B = 10,000`, percentile 95% CI. Primary CI, as specified in the brief.
- **Mean net R, day-block bootstrap.** Group trades by entry UTC day,
  resample days with replacement, concatenate; same `B`. Secondary CI,
  because overlapping trades (D5) and one-per-day trades in autocorrelated
  markets are not independent. Both are reported.
- **Daily Sharpe.** `sqrt(365) × mean / std` of daily returns, zero-return
  days included. Moving-block bootstrap, block length 10 days, `B = 10,000`,
  percentile 95% CI.
- **Paired Sharpe difference.** For two aligned daily series, resample the
  same blocks from both and compute `Sharpe_a − Sharpe_b` per replicate;
  95% CI and one-sided `p = (1 + #{replicates ≤ 0}) / (B + 1)`.

## 5.2 Batched trade evaluator (`stats/evaluator.py`)

Computes the outcome of pre-specified trades with numpy. It never
generates signals. It is the only vectorized execution code and is proven
equivalent to the simulator (test 5.3).

```python
@dataclass
class TradeSpec:                  # arrays of length m (a scalar is broadcast)
    entry_idx: np.ndarray         # 15m index of the entry candle
    entry_kind: np.ndarray        # "limit": filled at entry_price on entry_idx, same-candle rules apply
                                  # "market_open": fill at open[entry_idx]
    entry_price: np.ndarray
    stop: np.ndarray
    target: np.ndarray
    deadline_idx: np.ndarray      # candle at whose close the time exit happens; -1 for none
    pierce_abs: np.ndarray
    entry_role: np.ndarray        # "maker" | "taker" (fee on entry)
    entry_minute: np.ndarray | None = None  # 1m candle (0-14) of the fill in the entry candle; default 0
    stop_dist: np.ndarray | None = None     # the R unit; default entry price − stop

@dataclass
class Outcome:                    # arrays of length m
    exit_idx, exit_minute, exit_ref, exit_reason, exit_role
    funding_r, gross_r, c_maker_entry, c_maker_exit, c_taker_entry, c_taker_exit, c_slip

def evaluate(spec: TradeSpec, candles15, candles1m | None, funding, exec_cfg, *,
             last_idx=None, min_batch=32, max_steps=512) -> Outcome
def net_r(outcome: Outcome, fee_maker, fee_taker, slippage) -> np.ndarray
def specs_from_trades(trades, candles15) -> TradeSpec   # the simulator's trades as limit specs
```

`exit_reason` is `stop`, `target`, `time` (a deadline; the evaluator does
not know the hold kind) or `data_end`; `exit_role` is `maker` for targets,
else `taker`; `exit_minute` is the 1m candle of a 1m-resolved exit, −1
otherwise. `c_taker_entry` is `entry / stop_dist` for a taker entry (fee
only: slippage is charged on exits, D8), else 0. The simulator's trades
carry `stop_dist = planned_entry − stop`, which differs from
`entry_price − stop` on `open_gap` fills, and `entry_minute =
(entry_ms − τ_entry) / 1 min`.

Algorithm: a pending mask over all `m` trades. Step `k = 0` is the entry
candle: the bracket is already filled, so the stop may exit (reference
`min(stop, open)`) and the target may not. When that candle touched two or
more of {entry, stop, target} and 1m data are in use, its minutes are
walked *from the fill minute* by Phase 4's `walk_minutes` with the entry
level set marketable (`fill_at = +inf`, first look): stop allowed and
target not on the fill minute, both after it, which is the post-fill part
of the simulator's own walk (the simulator can fill and then hit the
target inside one 15m candle, so "target not on the entry candle" holds
only without 1m; changed 2026-10-09 to match Phase 4). For
`k = 1, 2, …` gather `idx = entry_idx + k` for pending trades, test stop
and target (with pierce) in one vectorized pass, resolve with the
pessimistic precedence, and, when 1m data is present and both stop and
target are touched, hand those trades to the 1m resolver of Phase 4
(`resolve_candle`, shared code, not a copy); a walk that finds no exit
(1m data disagreeing with the 15m bar) leaves the trade open. A trade with
no stop or target exit on its deadline candle exits at that close; one
still open after the checks of `last_idx` (default the last candle given)
exits at its close as `data_end` (taker, slippage), as simulator §4.9.
`deadline_idx` from a deadline in ms is the first candle whose close is at
or after it, clamped to `≥ entry_idx`. When fewer than `min_batch` (32)
trades remain pending, or after `max_steps` (512) steps, the stragglers
are finished with per-trade windowed scans (`np.argmax` on the boolean hit
arrays), so one long-lived trade does not force thousands of vectorized
steps. Funding is summed per trade from the funding table over events
`τ_entry + 15m ≤ f < τ_exit + 15m` (on the grid: `[fill close, exit
open]`), each at the close of the candle before the one containing `f`
(the simulator's price); off-grid events raise, as in the simulator.

## 5.3 Baseline A — timing skill (`stats/baselines.py`)

Question answered: given the days and sessions on which the strategy got
filled, and its stop geometry, does *when* it entered matter?

For each real trade (R subset), the candidate entry set is every in-window
15m candle `e` of the trade's own session. For each `e`:
`entry = open[e]` (market at the open), `stop = open[e] − stop_dist_atr ×
ATR14[e−1]` (the real trade's ATR multiple, ATR as of the previous candle),
`target = open[e] + r_target × (open[e] − stop)`, deadline from the
variant's hold rule (`session_end` → the session's last candle;
`max_hold` → `e + 4 × hours`; `none` → −1), same pierce, maker fee on entry
(to isolate timing), exits as real, actual funding.

`precompute_table_a(trades, …) -> TableA`: the `Outcome` components for
every (trade, slot), shape `m × max_slots`, NaN-padded. Computed once per
variant with one batched `evaluate` call.

One run = draw one slot per trade uniformly over that trade's slots;
statistic = mean `net_r` under the cost setting in use. `M` runs give the
null distribution. Observed = the real trades' mean `net_r` under the same
cost setting. `p_A = (1 + #{runs ≥ observed}) / (M + 1)`, one-sided;
`z_A = (observed − mean_runs) / std_runs`. Because the table holds
components, `p_A` is available for every re-pricing cell at no extra cost.

## 5.4 Baseline B — day selection plus timing

Question answered: does the strategy beat a random long of the same shape
on random days?

One run: for each real trade, draw a session uniformly from the variant's
eligible sessions in the period (`SessionCalendar.eligible_days`,
respecting the weekday rule and the pair's listing date), a slot uniformly
inside that session's window, and a `stop_dist_atr` with replacement from
the real trades' pool. Build one `TradeSpec` and call `evaluate` once.
Store per run the mean of each `Outcome` component, so the run's mean
`net_r` under any cost setting is a linear combination. `M` runs.
`p_B`, `z_B` as for A.

Both baselines are recomputed on the holdout with the holdout's own days.

## 5.5 Buy-and-hold (`stats/buyhold.py`)

Daily return of a constant 1× long in the perp:
`ret_d = close_d / close_{d−1} − 1 − Σ rate_f` over funding events in day
`d` (longs pay positive rates). Vol-scaled series:
`ret_d × (σ_strategy / σ_bh)` using the two in-sample daily vols; Sharpe is
scale-invariant, the scaling matters for return and drawdown. Reported:
Sharpe with block-bootstrap CI, vol-scaled return and max drawdown, and
the paired Sharpe difference (5.1) with its CI and one-sided `p_BH`.

## 5.6 Cost re-pricing (`stats/repricing.py`, D13)

```python
def reprice(components, fee_maker, fee_taker, slippage) -> np.ndarray   # net_r per trade or per run
def reprice_grid(trades, table_a, runs_b, cfg) -> pd.DataFrame
```

Grid: `slippage ∈ {0, 0.02, 0.05, 0.10}%` × `maker ∈ {0, 0.02}%`, taker
fixed at 0.05%. For each cell: mean `net_r`, trade-bootstrap CI, win rate,
`p_A`, `p_B`. Exact, because net R is linear in the three rates with
per-trade coefficients (Phase 4 §4.7). Also reported: the share of trades
with `cost_r > 1` under the primary costs, and the stop-distance
distribution in percent and in ATR.

## 5.7 Deflated Sharpe ratio (`stats/dsr.py`)

Bailey and López de Prado (2014). With `N` trials and the variance `V` of
the trials' (non-annualized daily) Sharpe ratios:
`SR* = sqrt(V) × ((1 − γ) Φ⁻¹(1 − 1/N) + γ Φ⁻¹(1 − 1/(N·e)))`, `γ` the
Euler–Mascheroni constant. Then
`DSR = PSR(SR*) = Φ((SR − SR*) sqrt(T − 1) / sqrt(1 − γ₃ SR + (γ₄ − 1)/4 SR²))`
with `γ₃, γ₄` the skew and kurtosis of the daily returns and `T` the
number of days. Reported for each primary cell with `N = 36` (`V` over
that pair × session's grid) and `N = 324` (`V` over all grid cells), and
for the best grid cell. The report states that grid cells share most of
their trades, so the effective number of independent trials is far below
`N` and the DSR is conservative.

## 5.8 Multiplicity (`stats/multiplicity.py`, D11)

Holm step-down over the family of 9 primary cells, per benchmark (A, B,
buy-and-hold), separately in-sample and on holdout. With sorted p-values
`p_(1) ≤ … ≤ p_(9)`: `p̃_(i) = max_{j ≤ i} min(1, (9 − j + 1) · p_(j))`.
Raw and adjusted are both stored. Bonferroni thresholds are also shown for
reference.

## 5.9 Alpha decay, regimes, diagnostics

- **Rolling** (`stats/rolling.py`): 6-month windows stepped monthly by
  entry date: `n`, mean net R, win rate; rolling daily Sharpe over
  182-day windows; per-year table (`n`, win rate, mean net R, Sharpe, max
  DD, fill rate).
- **Regimes** (`stats/regimes.py`): trend = daily ADX(14) > 25 as of the
  day before entry (the Phase 2 aligned array at the entry candle); vol =
  30-day realized vol of daily log returns, as of the day before entry,
  above or below the pair's in-sample median, fixed and applied as-is to
  holdout.
- **Diagnostics** (`stats/diagnostics.py`): win rate, mean and median net
  R, profit factor, exposure (fraction of candles with an open position,
  mean notional), max drawdown on daily equity, max concurrent positions,
  fill rate (`filled / placed`), skip counts, cost-in-R distribution,
  implied-leverage distribution, hold-time distribution, MAE/MFE,
  breakdowns by regime, day of week, entry hour, year; the fraction of
  trades shared with the other two session variants (same candidate
  candle), computed at report time from the three trade tables.

## 5.10 `stats.json` (per variant)

Every number the report or the summary prints comes from this file:
counts and skip counts; mean net R with both CIs; win rate; Sharpe with
CI; max DD; exposure; `p_A, z_A, p_B, z_B` with `n_runs`; buy-and-hold
block; DSR at both `N`; re-pricing grid; rolling and per-year tables;
regime and breakdown tables; the R-subset size and the `data_end` count.
Holm-adjusted p-values are added by the experiments layer once all nine
primary cells exist.

## 5.11 Tasks and tests

- **5.1 Trade and day-block bootstrap.** Constant trades → degenerate CI;
  on N(0,1) samples across 500 seeded draws, 95% CI coverage within
  93–97%; the day-block CI on trades with a strong within-day common
  shock is wider than the trade CI.
- **5.2 Block bootstrap Sharpe and paired difference.** iid coverage as
  above; on AR(1) daily returns the block CI is wider than the iid CI;
  the paired difference of a series with itself is exactly zero with a
  zero-width CI.
- **5.3 Evaluator equivalence.** Feed the simulator's own trades from a
  random-walk run (entry candle, fill price, stop, target, deadline,
  pierce) as `limit` specs: identical `exit_idx`, `exit_ref`,
  `exit_reason`, funding, and `net_r` for every trade, with and without
  1m data, including `open_gap` fills and `data_end`; the straggler path
  and the vectorized path agree on the same inputs.
- **5.4 Baselines and p-values.** With slots forced to the real entry
  candles and `market_open` replaced by the real fill, baseline A
  reproduces the real mean R; on random-walk data with a coin-flip
  strategy across 20 seeds, `p_A` and `p_B` are not concentrated below
  0.05 (at most 3 of 20); `p` is never 0; day-of-week and listing-date
  rules respected in B; the per-run component means re-price to the same
  `net_r` as direct evaluation under two cost settings.
- **5.5 Buy-and-hold.** 3-day hand example with funding; scaling yields
  equal vol; paired Sharpe difference sign matches a hand case.
- **5.6 Re-pricing.** `reprice` at the primary costs reproduces the
  simulator's `net_r` to 1e-12 for every trade; the grid has 8 cells;
  `p_A` at the primary cell equals the direct computation.
- **5.7 DSR.** Decreases in `N`; with `N = 1` equals the PSR against 0;
  the skew and kurtosis terms match the published formula on a worked
  example.
- **5.8 Holm.** Matches `scipy`/hand values on a 9-vector; monotone;
  bounded by 1.
- **5.9 Rolling, regimes, diagnostics.** Hand-made trade lists; regime
  labels use the day before entry; the shared-trade fraction on three
  synthetic trade tables.

Exit criterion: all tests green; `stats.json` from the Phase 4 smoke run
is produced and read back without loss. Then merge into `dev` and delete
the branch.
