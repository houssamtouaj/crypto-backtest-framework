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
