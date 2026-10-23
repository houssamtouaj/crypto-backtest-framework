# Phase 4 — Statistics

Read with `00-overview.md`. Delivers the random-days baseline, the three
tests, the verdict, and the diagnostics. Every function takes an explicit
`np.random.Generator` seeded from `(master_seed, variant_id, purpose)`
(`stats/seeds.py`).

**Branch:** `trend/4-statistics`, from `dev` after Phase 3 is merged.

All statistics use the **net daily portfolio returns** from the executor
over the period (in-sample: 2020-07-01 – 2026-10-20), zero-return days
included. Sharpe is `stats.bootstrap.sharpe` (√365 × mean / std).

## 4.1 Random-days baseline (`trend/baselines.py`)

**Open with the user: one shared offset or one offset per pair.** The
draft uses a shared offset (recommended): for path `m`, draw one integer
`k` uniformly from `[60, n − 60]`, `n` the number of days in the period,
and shift every pair by it: `e'_{i,t} = e_{i, (t + k) mod n_i}` (indices
within pair `i`'s `n_i` valid days). Pairs that span the whole period then
move together exactly as in the strategy, so the random paths keep the
cross-pair co-movement of the exposures; independent offsets would
decorrelate them, lower the random paths' portfolio vol, raise their
Sharpe, and make T1 stricter for a reason that has nothing to do with
timing. A pair with `n_i < 121` valid days (possible on the spot holdout)
still takes `k mod n_i`; it can land near its real timing, which makes
the test slightly conservative. *(builder's call for the short-pair
case)* The vol forecast,
costs, funding, and fill prices stay those of the actual days; the
executor re-derives weights and trades. This keeps each pair's time in
market, exposure levels, holding lengths, and number of exposure changes
exactly, and scrambles only the timing.

The margin of 60 days keeps near-copies of the real timing out of the
distribution. *(builder's call)*

`M = 5,000` paths, batched executor.

## 4.2 T1 — timing beats random

`S_obs` = net Sharpe of the strategy; `S_m` = net Sharpe of path `m`.
`p1 = (1 + #{S_m ≥ S_obs}) / (M + 1)`. Pass iff `p1 < α`. Report the
paths' mean, std, and 5/50/95 percentiles next to `S_obs`.

## 4.3 T2 — beats vol-targeted buy-and-hold

`stats.bootstrap.paired_sharpe_diff(strategy, buy_and_hold, B=10,000,
block_len=20)` on the aligned daily series. One-sided `p2` as defined
there. Pass iff `p2 < α`. Report the difference and its 95% CI and the
three-way reading: proven worse (CI upper < 0), not proven (CI contains
0), proven better (passes).

## 4.4 T3 — smaller drawdown at matched vol (reported only)

Scale the strategy's returns by `k = σ_BH / σ_S` (in-sample daily std;
the holdout and forward runs reuse the in-sample `k`). Statistic:
`MDD(buy-and-hold) − MDD(scaled strategy)`, with `MDD` from
`stats.diagnostics.max_drawdown`. Paired moving-block bootstrap on the
same blocks, `B = 10,000`, block 20; one-sided p and 95% CI.

## 4.5 Verdict

`edge = (p1 < α) and (p2 < α)`, unadjusted. Written into `stats.json` as
`{"t1": {...}, "t2": {...}, "t3": {...}, "edge": bool, "reading": "..."}`,
where `reading` is one of: `edge`; `timing_only` (T1 passes, T2 does not);
`beats_bh_not_timing` (T2 passes, T1 does not: exposure, not timing, did
the work); `no_edge`.

## 4.6 Diagnostics (`trend/diagnostics.py`)

- Annual return, vol, Sharpe with block-bootstrap CI, max drawdown, Calmar,
  for the strategy and buy-and-hold.
- Time in market (mean exposure, share of days with any position), turnover
  per year, trades per year by reason, band skips, late fills.
- Costs per year in % of equity: fees, slippage, funding; funding as a
  share of gross return, for the strategy and buy-and-hold (overview §5).
- Per-year table and per-pair table (each pair also run alone).
- Up / down market split: strategy return on days when buy-and-hold was
  up vs down, and in buy-and-hold drawdowns deeper than 20%.
- Cost re-pricing: the executor re-run at every `reprice` cell; net
  Sharpe, p1 (against baseline paths re-run at the same costs), and p2 per
  cell. Descriptive only.
- DSR (`stats/dsr.py`) of the primary with the exploratory variants as
  trials (N = 6), from the daily `dsr.sr` of each.

## 4.7 Tests

- T1 p-value formula and its behaviour on a constructed case (a strategy
  that is a shifted copy of the baseline gets a uniform p over seeds).
- T2 reuses tested code; test the alignment and the reading.
- T3 on a constructed pair where the answer is known.
- Seed determinism: same seed, same `stats.json` bytes.

## 4.8 Exit criterion

Tests green; `stats.json` schema documented in the module docstring.
