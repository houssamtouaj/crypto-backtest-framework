# Phase 4 — Statistics

Read with `00-overview.md`. Delivers the random-days baseline, the three
tests, the verdict and the diagnostics. Every function takes an explicit
`np.random.Generator` from `stats/seeds.py`, seeded by
`(master_seed, variant_id, purpose)`.

**Branch:** `etf/4-statistics`, from `dev` after Phase 3 is merged.

All tests use the **net daily excess returns** of the portfolio from the
executor (`excess_ret`), over the period's trading days.

## 4.1 Sharpe at √252

`etf/verdict.py`:

- `sharpe252(x) = √252 × mean(x) / std(x, ddof=1)`.
- `stats.bootstrap.paired_sharpe_diff` and `block_bootstrap_sharpe` are
  reused for the resampling; their Sharpe values and CI bounds are
  multiplied by `√(252/365)`. P-values are unchanged by the scale. A test
  checks `rescaled == sharpe252` on random data.

## 4.2 Random-days baseline (`etf/baselines.py`)

As in the crypto trend spec Phase 4 §4.1: for path `m`, draw one integer
`k` uniformly from `[60, n − 60]` (`n` the period's trading days) and shift
every ticker's exposure by it, `e'_{i,t} = e_{i,(t+k) mod n}`. All four
tickers share the calendar, so they move together exactly as in the
strategy. σ̂, prices, dividends and cash stay those of the actual days; the
executor re-derives weights and trades. `M = 5,000` paths, batched.

## 4.3 T1 — timing beats random

`S_obs = sharpe252(strategy excess)`; `S_m` the same for path `m`.
`p1 = (1 + #{S_m ≥ S_obs}) / (M + 1)`. Pass iff `p1 < α`. Report the
paths' mean, std, and 5/50/95 percentiles.

## 4.4 T2 — beats vol-targeted buy-and-hold

Paired moving-block bootstrap of the excess-Sharpe difference
(strategy − vol-targeted buy-and-hold), B = 10,000, block 20 days,
rescaled per §4.1. Pass iff `p2 < α`. Report the difference, its 95% CI and
the reading: proven worse (CI upper < 0), not proven (CI contains 0),
proven better (passes).

## 4.5 T3 — smaller drawdown at matched vol (reported only)

Scale the strategy's excess returns by `k = σ_BH / σ_S` (in-sample daily
std; holdout and forward reuse the in-sample `k`). Statistic:
`MDD(buy-and-hold) − MDD(scaled strategy)` on compounded excess returns,
`stats.diagnostics.max_drawdown`. Paired block bootstrap, same blocks;
one-sided p and 95% CI.

## 4.6 Verdict

`edge = (p1 < α) and (p2 < α)`, unadjusted. `stats.json` holds
`{"t1": {...}, "t2": {...}, "t3": {...}, "edge": bool, "reading": "..."}`
with `reading` one of `edge`, `timing_only` (T1 passes, T2 not),
`beats_bh_not_timing` (T2 passes, T1 not), `no_edge`. It also records
`strategies_tried_in_repo: 4`.

## 4.7 Diagnostics (`etf/diagnostics.py`)

- Annual return, vol, excess Sharpe with block-bootstrap CI, max drawdown,
  Calmar: strategy, vol-targeted buy-and-hold, 100% SPY, equal-weight
  buy-and-hold.
- Time in market (mean invested share, share of days with any position),
  turnover per year, trades per year by reason, band skips, cash-cap events.
- Costs per year (slippage, commission) and income per year (dividends,
  interest), in % of equity.
- Per-year table; per-ETF table (each ticker also run alone, against its
  own vol-targeted buy-and-hold).
- Up/down split: strategy return on days when SPY was up vs down, and
  during SPY drawdowns deeper than 20% (2007–2009, 2020, 2022).
- Cost re-pricing at every `reprice` cell: net excess Sharpe, p1 (against
  baseline paths re-run at the same costs), p2. Descriptive.
- DSR (`stats/dsr.py`) of the primary with the four exploratory rows as
  trials (N = 5); Sharpe inputs at daily scale.

## 4.8 Tests

- T1 formula; a strategy that is itself a shifted path gets a roughly
  uniform p over seeds.
- T2 alignment and reading; the √(252/365) rescaling.
- T3 on a constructed case with a known answer.
- Seed determinism: same seed, same `stats.json` bytes.

## 4.9 Exit criterion

Tests green; the `stats.json` schema documented in the module docstring.
