# Phase 4 — Statistics

Read with `00-overview.md`. Delivers V1, V2, the verdict, and the
diagnostics, on the portfolio's net daily returns from the Phase 3
executor.

**Branch:** `carry/4-statistics`, from `dev` after Phase 3 is merged.

## 4.1 V1 — net Sharpe above zero

`stats.bootstrap.block_bootstrap_sharpe(ret, B = 10,000, block_len = 30)`
on the in-period daily net returns; `p1 = (1 + #{replicates ≤ 0}) / (B' +
1)` over the finite replicates (the convention of
`paired_sharpe_diff`). Pass iff `p1 < α`. Report the Sharpe and its 95%
percentile CI.

## 4.2 V2 — net return above the hurdle

Daily excess return `e_t = ret_t − h_d` with `h_d = (1 + h)^(1/365) − 1` and `h = hurdle.annual` (the daily rate that compounds to `h` over a year; the equity figure's hurdle line uses the same rate).
Moving-block resamples of `e` (same `block_indices`, block 30, B =
10,000); replicate statistic = `365 × mean(e*)` (an annualised excess over the hurdle; its sign is the test). `p2 = (1 + #{replicates
≤ 0}) / (B + 1)`. Pass iff `p2 < α`. Report the annualised excess, its 95%
CI, and the three-way reading: proven below the hurdle (CI upper < 0), not
proven (CI contains 0), proven above (passes). The same statistic at each
`hurdle.descriptive` value is reported, descriptively.

New function `stats.bootstrap.block_bootstrap_mean(x, B, rng, block_len)`
returning the replicate means (shared module, backward compatible).

## 4.3 Verdict

`edge = (p1 < α) and (p2 < α)`, unadjusted. `stats.json`:
`{"v1": {...}, "v2": {...}, "edge": bool, "reading": ...}` with `reading`
one of `edge`; `positive_below_hurdle` (V1 passes, V2 does not);
`no_edge`.

## 4.4 Diagnostics (`carry/diagnostics.py`), descriptive

- Annual return, vol, Sharpe with CI, max drawdown, worst day and worst
  month, for the primary and `always_on`.
- **Filter value:** paired Sharpe difference primary − always-on
  (`paired_sharpe_diff`, block 30), with CI.
- **Random timing:** `stats.random_timing_runs` paths, one shared
  circular-shift offset per path on `[60, n − 60]` applied to every pair's
  on/off series within its live days (trend Phase 4 §4.1), executor re-run;
  the primary's rank among them. Shows whether *when* to be on matters
  beyond how often.
- Decomposition per year and per pair: funding received, basis P&L, fees,
  slippage, rebalance and liquidation costs; days on; entries per year.
- Rebalances and liquidations: counts, dates, cost.
- Cost re-pricing over the `reprice` grid: annualised net return and p2
  per cell.
- DSR of the primary with the six exploratory rows as trials (N = 7).

## 4.5 Tests

- V1 and V2 on constructed series with known answers (a constant 10%/yr
  excess with small noise passes V2 at h = 5%; 4%/yr does not).
- `block_bootstrap_mean` against a hand case and its seed determinism.
- The reading for each combination.
- Same seed, same `stats.json` bytes.

## 4.6 Exit criterion

Tests green; `stats.json` schema documented in the module docstring.

## 4.7 Implementation notes (Phase 4 build, 2026-10-24)

Builder's calls taken while building this phase. None was tuned on a
return: no return, Sharpe, p-value, P&L or funding total was computed on
real data (the real-data tests run only the engines: offsets, shifted
states, `run_batch`, `run_path`, the re-pricing `ExecSpec`s). The calls
are also in the module docstrings of `perpbt/carry/verdict.py` and
`perpbt/carry/diagnostics.py`.

- **V1 and V2 are the screen's functions** *(my call)*. `verdict.py`
  imports `v1_stats`, `v2_stats` and `daily_hurdle` from `screen.py`: one
  implementation, and the screen file stays as frozen (its
  `screen.json` still reproduces byte for byte).
- **Reading when V2 passes and V1 fails** *(my call)*: `no_edge`. §4.3
  defines `positive_below_hurdle` as V1 passing without V2, and an edge
  needs both.
- **Seeds** *(my call)*. Every generator is `rng_for(stats.master_seed,
  prereg_hash, "<period>/…")` (`verdict.Seeds`); `<period>` is one of
  `insample`, `holdout`, `forward_12m`, `forward_36m`. Purposes:
  - `<period>/<row>/v1`: V1. The annual table's Sharpe CI is V1's, not
    drawn again.
  - `<period>/<row>/v2`: V2 at `hurdle.annual` and at every
    `hurdle.descriptive` value, a fresh generator each. They resample the
    same blocks, so the 5% row of the hurdle table is the verdict's V2.
  - `<period>/<row>/reprice/v2`: V2 of every re-pricing cell, one stream
    for all cells (common random numbers: cells differ only by costs).
  - `<period>/filter_value`: the paired Sharpe difference.
  - `<period>/random_timing`: the shift offsets.

  None collides with the screen's purposes (`screen…`). A test pins the
  purposes one primary `stats.json` draws.
- **`stats.json`** (`stats_document`, `stats_json`): `{"schema":
  "carry-stats/1", "prereg_hash", "period": {name, start, end}, "row",
  "n_days", "v1", "v2", "edge", "reading", "hurdles", "diagnostics"}`;
  `indent=2`, keys in that order, non-finite numbers as `null`. The
  `diagnostics` blocks are `annual`, `decomposition`, `margin`, `reprice`
  (every row; `row_diagnostics`) and `filter_value`, `random_timing`,
  `dsr` (the primary; `primary_diagnostics`, which also returns the
  per-path random-timing table for Phase 5's `random_timing.parquet`).
- **Random timing** *(my call where the spec is silent)*:
  - `n` is the number of return days; `k ~ U{m, …, n − m}`, both ends
    included, `m = stats.shift_margin_days`; it refuses `n < 2m`.
  - A pair's window is its live decision days in the period (the
    executor's liveness: the frame has `d` and `d + 1`). Pair `i` takes
    `on'[j0 + t] = on[j0 + (t + k) mod n_i]`; rows outside the window are
    off (the executor never reads them).
  - Statistics per path: the annualised net return (`365 × mean`, V2's
    statistic) and the Sharpe. For each, the paths' mean, std and 5/50/95
    percentiles, the primary's `rank` (`1 + #{paths > primary}`) and
    `share_at_least` (`(1 + #{paths ≥ primary}) / (M' + 1)` over the `M'`
    finite paths).
  - A path's daily returns are recomputed from `run_batch`'s equity
    (`E_t / E_{t−1} − 1`, the executor's own expression): the carry AST
    guard forbids reading a `.ret` attribute in `perpbt/carry/`.
- **Decomposition** *(my call)*. Each day's money is divided by the
  previous day's equity (the starting equity first) and summed, so
  `funding − fees − slippage + basis` equals the sum of the daily returns
  per year and per pair. `rebalance_cost` and `liquidation_cost` are the
  fees and slippage of fills with that reason, a part of `fees +
  slippage`, not in addition to them. A liquidation's loss to its price
  is in `basis`. "Days on" are pair-days hedged at the close; entries are
  perp entry fills. Each year also has `ret_compounded` and `share_on`.
- **Per-pair columns** *(my call)*. `daily_pair` gains `fees`,
  `slippage`, `basis_pnl` (appended after `funding`; backward
  compatible): the per-pair amounts the executor already summed for
  `daily`.
- **Liquidation cost per event** *(my call)*: the pair's fills of reason
  `liquidation` from its date until its next liquidation, i.e. the
  buy-back with its fee and the later spot sale.
- **Annual stats** *(my call)*: `ann_return` (`365 × mean`), `cagr`,
  `ann_vol` (`√365 × std`, ddof 1), Sharpe and CI (V1's), max drawdown
  (`stats.diagnostics.max_drawdown`), worst day, worst calendar month
  (compounded), each with its date. Computed for every row, not only the
  primary and `always_on`.
- **Re-pricing cell** *(my call)*. `costs = replace(costs, spot_taker=t,
  slippage={"default": s})`: the cell's slippage applies to every pair;
  `perp_taker`, margin and the rest are the row's. One `run_path` per
  cell. Reported: annualised net return, Sharpe, the V2 block, the cell's
  fees and slippage, and the number of entries.
- **Filter value**: `paired_sharpe_diff(primary, always_on, B, rng,
  block_len)` on the same dates (it refuses others), with its CI and `p`.
- **DSR**: `dsr_from_trials(sr_moments(primary), [daily SR of the seven
  rows])`, the primary among its own trials (N = 7). It refuses unless
  exactly the pre-registered rows (`row_names(prereg)`) are given. `N`
  counts the rows whose Sharpe is defined.
- **Data.** The new modules load nothing; the caller passes the
  PairFrames (Phase 5: `load_pairframe`, in-sample without
  `allow_holdout`). The AST guards of `tests/carry/test_frames.py` are
  unchanged and green.

**Real data (in-sample, 2026-10-24; structure only).** Every pair's live
window is the 1,279 decision days from 2020-06-30. The 1,000 shifted
primaries keep each pair's days on; `run_batch` runs them in about 5 s;
the eight re-pricing runs take under a second.
