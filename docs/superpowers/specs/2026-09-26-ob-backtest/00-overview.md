# Crypto backtest framework for the ICT "first 15-min bullish order block" strategy — overview

Status: DRAFT v2 (after review), 2026-09-26. Nothing has been implemented.

v1 was a single document. v2 applies the review of 2026-09-26 and splits the
design into one file per build phase. This overview holds everything that
spans phases: goal, resolved decisions, architecture, conventions, grid,
risks, and the index of phase specs. Each phase file is the contract for
that phase and can be read on its own together with this overview.

| File | Phase |
|---|---|
| `00-overview.md` | this file |
| `phase-0-scaffold.md` | package skeleton, config types, hashing, test harness |
| `phase-1-data.md` | bulk download, stores, validation, session calendar, ccxt backfill/tail |
| `phase-2-indicators.md` | ATR, swing highs, daily SMA/ADX, MarketView look-ahead guard |
| `phase-3-strategy.md` | the order-block rule, mechanized |
| `phase-4-execution.md` | orders, fills, 1m resolver, costs, sizing, ledger, simulator loop |
| `phase-5-statistics.md` | bootstraps, evaluator, baselines, buy-and-hold, DSR, re-pricing, verdict |
| `phase-6-experiments.md` | pre-registration, registry, grid, runner, holdout guard |
| `phase-7-reporting.md` | figures, per-variant report, summary |
| `phase-8-runs.md` | the run protocol, in order |

---

## 1. Goal

An honest, statistically rigorous answer to: does this mechanized ICT
strategy have a measurable edge in Binance USDT-M perpetuals? The data,
execution, and statistics layers must be reusable for other strategies.

**From the brief.** Long-only. Three session variants, each a separate
strategy. 15m signals, optional 1m resolution. 2020-01-01 to present.
In-sample through 2025-12-31; holdout from 2026-01-01, touched once, primary
configuration only. Fees, slippage, funding, fixed-fractional sizing.
Bootstrap CIs, two random-timing baselines, buy-and-hold, robustness
heatmaps, alpha decay, deflated Sharpe, trade diagnostics. Pre-registered
primary. Every variant logged. Seeded randomness. Synthetic-data tests for
look-ahead. Clarity over speed.

**Success looks like.** A summary that states plainly, with p-values and CIs,
whether each primary cell beats random baseline A, random baseline B, and
vol-scaled buy-and-hold at the 5% level after multiplicity correction,
in-sample and on holdout, per pair and per session variant. Plus a strategy
interface where a second strategy drops in without touching data, execution,
or stats code.

**Assumptions.**
- Single Windows machine, no GPU, 20 CPUs for parallel variant runs.
- Nominal starting equity $10,000 per simulation; results reported in R
  units primarily, returns derived from 1% risk sizing.
- "Present" is frozen to the data download date recorded in the
  pre-registration file; the holdout period ends there.
- No exchange lot-size rounding; noted as a diagnostic.
- Data from Binance's public bulk archive; ccxt for the 2019 warmup backfill
  and the last day or two.

## 2. Decisions

All decisions are resolved with the recommendation unless marked *confirm*.
D11–D15 are new in v2 and come from the review.

| # | Question | Resolution |
|---|---|---|
| D1 | Session windows | UTC: 00:00→24:00 UTC. NY: 09:30→16:00 America/New_York. London: 08:00→16:30 Europe/London. Windows differ in length (24 h, 6.5 h, 8.5 h), so cross-session comparisons conflate opportunity count with edge; reports show sessions, blocks seen, and orders per variant next to every cross-session table. ICT "killzones" are narrower and are a different strategy. *Confirm.* |
| D2 | Must the broken swing high be unbroken? | Fresh break of a *live* level. Every live level a candle closes above retires on that candle, whether or not it was the reference. |
| D3 | Weekends | UTC: all 7 days. NY and London: Monday–Friday in session-local time. |
| D4 | Skipped block | Continue to the next block in the same session. The one-order-per-session cap counts intents, not blocks seen. |
| D5 | Overlapping trades within a variant | Allowed. Each trade risks 1% of mark-to-market equity at placement. Max concurrent positions is a diagnostic; the leverage cap applies to total open notional. |
| D6 | Baseline budget | 5,000 runs (A and B) for each of the 9 primary cells; 500 per grid cell. |
| D7 | Trend filter | 15m close above the SMA(50) of completed UTC daily closes. |
| D8 | Costs | Maker 0.02%, taker 0.05%. Slippage on stop and time exits: 0.02% BTC and ETH, 0.05% SOL. None on limit fills. Plus cost re-pricing (D13). |
| D9 | Git | Done. `main` and `dev`, remote `origin` configured. |
| D10 | Data location | `data/` under the project (about 1.2 GB), git-ignored. |
| D11 | What is "the primary"? | The family of 9 cells (3 pairs × 3 sessions) at the primary parameters. Within each benchmark family (A, B, buy-and-hold) the 9 one-sided p-values are Holm-adjusted at α = 0.05. Raw and adjusted p-values are reported; the verdict uses adjusted. *Confirm* (alternative: name one cell, e.g. BTCUSDT × NY, as the sole primary). |
| D12 | Displacement and mitigation | A block is valid only if some candle in `c+1..t` closes above `high[c]`. Mitigation is checked only on candles after that displacement candle. The order is placed only if `close[t] > entry`. Replaces v1 §6.5, which skipped nearly every block (see §3). |
| D13 | Cost sensitivity | The trade table is re-priced at slippage {0, 0.02, 0.05, 0.10}% × maker {0, 0.02}% without re-simulation. Mean net R, CIs, and baseline p-values are reported per cost cell. |
| D14 | Indicator warmup before 2020 | The futures bulk archive starts 2020-01 (verified: 2019-12 returns 404). 15m candles for 2019-11-01..2019-12-31 are fetched via ccxt for BTCUSDT and ETHUSDT so the daily SMA(50) and ADX(14) are valid on 2020-01-01. |
| D15 | Code version | SHA-256 of the `perpbt/` source tree, not the git commit, so docs and test commits do not invalidate the run cache. The commit hash is recorded alongside. |

## 3. Changes from v1

1. **Mitigation rule rewritten (D12).** v1 §6.5 tested whether any candle in
   `c+1..t` would have filled the entry. Candle `c+1` opens at `close[c]`,
   which is below `high[c]`, so the test fired on every block. The v2 rule
   requires displacement above the block first and checks mitigation only
   after it. The same rule excludes blocks whose entry sits above the impulse
   close, which v1 would have filled at the next open as a de facto market
   order while charging the maker fee.
2. **Primary is a family of nine (D11).** v1 called the primary "one trial".
   v2 applies a Holm correction per benchmark family and reports both raw
   and adjusted p-values.
3. **Cost sensitivity added (D13).** Costs enter net R linearly with
   per-trade coefficients, so the trade table and both baselines can be
   re-priced exactly without re-simulation.
4. **Baselines made cheap.** The single-trade evaluator is batched over
   trades. Baseline A precomputes the outcome of every trade at every
   candidate entry candle once; each run is then a table lookup. Baseline B
   stores per-run cost coefficients so re-pricing is a linear combination.
5. **Warmup backfill (D14).** v1 assumed bulk data from the listing date.
6. **Code version from the source tree (D15).**
7. **Smaller.** Planned and actual risk are both recorded per trade; the
   verdict rule is inlined in the pre-registration file so it is part of
   the hash; the liquidation check is a hard failure, not a log line; the
   deflated Sharpe section states that grid cells share most trades so the
   effective trial count is far below 36; every live level closed above
   retires, not just the reference; SOL slippage is 0.05%.

## 4. Key design choices

**4.1 Simulator style: candle loop with a strategy callback.** The strategy
receives a `MarketView` that refuses to return any candle after the current
one, so look-ahead is structurally impossible in strategy code and testable.
Slow in pure Python but acceptable. Vectorized signal generation is deferred
and, if added, must pass an equivalence test against the loop. Existing
frameworks (backtrader, vectorbt) are rejected: they hide fill logic and
fight the audit goal.

**4.2 Data: bulk archive first, ccxt for head and tail.** Monthly and daily
zips are complete and checksummed. ccxt fills the 2019 warmup (D14) and the
last one or two days not yet published.

**4.3 Session variants do not share an account.** Each (pair, session,
parameter set) is an isolated simulation with its own equity. The brief calls
each variant a separate strategy; the London and NY windows overlap for hours
and a shared account would need tie-break rules that are not part of the
strategy. The report states the fraction of trades shared between variants
(same candidate candle), since the three variants are not independent tests.
A combined-portfolio view is deferred.

**4.4 Overlapping trades within a variant are allowed (D5).** This keeps the
trade list a pure function of the rule, independent of prior outcomes, which
is what baseline A compares against.

**4.5 Baselines use a batched evaluator validated against the simulator.**
The evaluator computes the outcome of pre-specified trades with numpy and is
proven equivalent to the simulator by feeding the simulator's own trades
through it. It never generates signals.

**4.6 Reporting: matplotlib PNGs and Markdown.** Static, reproducible,
diffable.

## 5. Architecture

Package name `perpbt` (placeholder, rename freely).

```
perpbt/
  config.py            frozen dataclasses; YAML load/dump; canonical JSON and hash
  version.py           code_version() = SHA-256 of the perpbt source tree; git commit best effort
  data/
    bulk.py            download + checksum verify + parse data.binance.vision files
    ccxt_fetch.py      head backfill (2019 warmup) and tail fetch via ccxt
    store.py           CandleStore / FundingStore: Parquet cache, holdout guard
    validate.py        monotonic/unique timestamps, gap report, 1m→15m consistency
    sessions.py        SessionCalendar: per-candle session id, window flags, DST-aware
  indicators/
    atr.py             Wilder ATR(n) on 15m
    swings.py          swing highs with confirmation index
    daily.py           completed-day SMA / ADX aligned to the 15m index
  strategy/
    base.py            Strategy protocol, MarketView, AccountView, Intent types
    order_block.py     the ICT OB strategy
  execution/
    orders.py          Order, Position, SimEvent dataclasses, lifecycle enum
    fills.py           FillModel: 15m pessimistic rules + optional 1m resolver
    costs.py           fees by role, slippage, funding schedule, cost coefficients in R
    sizing.py          fixed-fractional sizing, leverage cap, liquidation assertion
    ledger.py          cash, positions, mark-to-market, daily marks
    simulator.py       the candle loop
    trades.py          trades table builder, MAE/MFE, hold time, regime tags
  stats/
    bootstrap.py       trade bootstrap; day-block bootstrap; moving-block Sharpe; paired Sharpe diff
    evaluator.py       batched single-trade outcome evaluator
    baselines.py       baseline A (precomputed table) and B (per-run draws), p-values
    repricing.py       cost re-pricing of trades and baselines (linear in cost rates)
    buyhold.py         buy-and-hold with funding, vol-scaled
    dsr.py             deflated Sharpe ratio, PSR
    multiplicity.py    Holm adjustment over the primary family
    rolling.py         rolling 6-month metrics, per-year table
    regimes.py         trend / vol regime labels
    diagnostics.py     win rate, exposure, drawdown, distributions, cost-in-R
  experiments/
    prereg.py          load/validate/hash the pre-registration file; lock file
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
  synthetic.py         candle builders, random-walk generator, scenario helpers, causality helper
  test_*.py            one file per module
configs/
  prereg.yaml          the pre-registered primary, grid, costs, and verdict rule
  prereg.lock          hash + commit recorded at freeze (Phase 8)
data/                  Parquet cache (git-ignored); manifest.json per pair/tf is committed
runs/                  per-variant outputs (git-ignored), registry.jsonl
```

## 6. Conventions that every phase follows

- **Time.** All timestamps are UTC `int64` milliseconds with an `_ms`
  suffix. Candle timestamps are open times. A 15m candle `i` has open time
  `τ_i` and close time `τ_i + 15m`. Decisions happen at the close.
- **Arrays at the core.** `Candles` holds numpy arrays; DataFrames appear
  only at the edges (Parquet I/O, reports).
- **Indicators** return arrays aligned to the 15m index, NaN before warmup,
  and document their confirmation lag. Nothing at index `i` may depend on
  candles after `i`.
- **Randomness.** The simulator has none. Every stats function takes an
  explicit `np.random.Generator` seeded from `(master_seed, variant_id,
  purpose)`.
- **Configuration** is frozen dataclasses with YAML round-trip; hashes are
  over canonical JSON (sorted keys, fixed float formatting).
- **Outputs** are Parquet tables plus `stats.json` per variant, byte-identical
  across reruns.
- **Tests.** pytest, one test file per module, synthetic data by default,
  real-data tests marked `@pytest.mark.slow`. Every causal computation has a
  perturbation test: changing candles after `cut` leaves outputs at `≤ cut`
  unchanged.
- **Counting.** Every reason a block does not become a trade is counted
  (`no_candidate`, `ineligible`, `no_displacement`, `mitigated`,
  `entry_above_price`, `degenerate`, `trend`, `session_used`, `leverage`)
  and reported next to `impulses` and `blocks_seen`.

## 7. Data flow for one variant

```
prereg.yaml ──► VariantConfig ──► CandleStore / FundingStore (holdout guard)
                                     │
                                     ▼
              indicators (ATR, swings, daily SMA/ADX)  +  SessionCalendar
                                     │
                                     ▼
              Simulator loop: for i in range(n): view = MarketView(i)
                  1. funding events at τ_i (positions open at the boundary)
                  2. pending orders and open positions against candle i
                     (fills, stops, targets, deadlines; 1m resolver if ambiguous)
                  3. mark-to-market; daily mark at the UTC day boundary
                  4. intents = strategy.on_candle(view, account)   ← candles ≤ i only
                  5. place/cancel per intents; sizing + leverage check at placement
                                     │
                                     ▼
              trades / daily / orders / fills / events / skips
                                     │
                                     ▼
              stats (bootstrap, baselines, re-pricing, B&H, DSR, Holm, rolling, regimes)
                                     │
                                     ▼
              runs/<variant_id>/*.parquet, stats.json, figures/  +  registry.jsonl row
```

Fills and exits for candle `i` are evaluated before the strategy sees candle
`i`'s close; an order placed at the close of `i` can only fill from `i+1`.

## 8. Phase index and dependencies

| Phase | Delivers | Needs |
|---|---|---|
| 0 Scaffold | package, config types, hashing, test harness, synthetic builders | — |
| 1 Data | candles and funding in Parquet, validation, session calendar, holdout guard | 0 |
| 2 Indicators | ATR, swings, daily SMA/ADX, MarketView guard | 0 |
| 3 Strategy | the OB rule as intents, with the full skip accounting | 1, 2 |
| 4 Execution | simulator, fills, costs, sizing, ledger, trades/daily tables | 3 |
| 5 Statistics | bootstraps, evaluator, baselines, re-pricing, B&H, DSR, Holm, verdict inputs | 4 |
| 6 Experiments | pre-registration, registry, grid, runner, holdout guard | 5 |
| 7 Reporting | figures, per-variant report, summary | 6 |
| 8 Runs | the actual runs, in the pre-registered order | 7 |

Each phase ends with its tests green before the next begins. Phases 1 and 2
can proceed in parallel after 0.

### 8.1 Branches: one per phase

`main` holds reviewed, merged work only. `dev` is the integration branch.
Every phase is built on its own branch, created from `dev` when the phase
starts and merged back into `dev` when the phase's exit criterion is met.
Branch names match the spec file names.

| Phase | Branch | Merges into |
|---|---|---|
| 0 Scaffold | `phase/0-scaffold` | `dev` |
| 1 Data | `phase/1-data` | `dev` |
| 2 Indicators | `phase/2-indicators` | `dev` |
| 3 Strategy | `phase/3-strategy` | `dev` |
| 4 Execution | `phase/4-execution` | `dev` |
| 5 Statistics | `phase/5-statistics` | `dev` |
| 6 Experiments | `phase/6-experiments` | `dev` |
| 7 Reporting | `phase/7-reporting` | `dev` |
| 8 Runs | `phase/8-runs` | `dev`, then `dev` into `main` |

Rules:

- **Start a phase.** Update `dev` (`git pull`), then
  `git switch -c phase/<n>-<name> dev`. Push the branch to `origin` on the
  first commit so the work is visible. Commit only that phase's work
  there, in small commits, each with tests.
- **Finish a phase.** All of the phase's tests are green, the spec and the
  code agree, and the exit criterion in the phase file is met. Merge into
  `dev` with `git merge --no-ff phase/<n>-<name>` (or a pull request from
  the phase branch into `dev`) so the phase boundary stays visible in the
  history. Push `dev`. Delete the phase branch locally and on `origin`.
- **Next phase** branches from the updated `dev`. Phases 1 and 2 may run
  at the same time on their two branches, each in its own worktree; Phase
  3 starts only after both are merged.
- **Fixes to an earlier phase** discovered later go on the current phase
  branch when small and covered by a test. Anything larger goes on a
  `fix/<topic>` branch from `dev`, is merged into `dev`, and `dev` is then
  merged into the open phase branch before work continues.
- **Spec changes** are made on the phase branch where the deviation was
  found, in the same commit as the code that deviates, so the spec and
  the code never disagree on `dev`.
- **`main`** receives `dev` once, at the end of Phase 8 (Phase 8 §8.2
  step 8). Nothing else merges into `main`.

## 9. Grid and runtime

### 9.1 Grid (per pair × session; other parameters at primary values)

| Axis | Values | Primary |
|---|---|---|
| r_target | 1, 1.5, 2, 3 | 2 |
| entry_level | top, mid | top |
| swing_k | 1, 2, 3 | 2 |
| confirm_n | 2, 3, 5 | 3 |
| stop_buffer | 0, 0.1, 0.25, 0.5 × ATR; 0.1%, 0.25% fixed | 0.1 × ATR |
| hold_rule | none, session_end, max_hold 24h, max_hold 72h | none |
| zone | full, body | full |
| pierce | 0, 0.05% | 0 |
| trend_filter | off, on | off |

Heatmap 1 (r_target × entry_level) 8 cells; heatmap 2 (swing_k × confirm_n)
9; heatmap 3 (ATR buffer × hold_rule) 16; singles: body zone, pierce, trend
on, two fixed-% buffers = 5. Total 38, minus two duplicate primaries =
**36 unique variants per pair × session, 324 in total.** Cost re-pricing
(D13) is not a grid axis: it does not change the trade list and is computed
from the trade table for every variant.

### 9.2 Data volume

| Item | Rows | On disk (Parquet) |
|---|---|---|
| 15m, 3 pairs, from 2019-11 / listing | ~0.7 M | ~30 MB |
| 1m, 3 pairs, from 2020-01 / listing | ~10.5 M | ~400 MB |
| Funding | ~22 k | < 1 MB |
| Bulk zips (deleted after conversion) | | ~400 MB transient |

### 9.3 Runtime (20-CPU laptop; budgets, not promises)

| Step | Budget |
|---|---|
| Download + convert + validate | 10–20 min, once |
| One variant run (~236k candles, Python loop) | 15–60 s |
| Per-variant stats (bootstraps, re-pricing) | ~5 s |
| Grid, 324 runs, 8 workers | 15–45 min |
| Baseline A, primary (precomputed table, 5,000 lookups) | seconds per cell |
| Baseline B, primary (5,000 batched runs × ~1,000 trades) | 2–10 min per cell |
| Baselines, grid cells (500 runs) | ~30 min total on 8 workers |
| Reports | ~5 min |

Trade counts are a guess (several hundred to ~1,000 per pair × session
in-sample; fill rate unknown). If the loop is slower than budgeted, the first
optimization is caching the signal stream per (k, N, zone, pierce, trend,
session), since only 16 of the 36 variants differ in those; the second is a
vectorized swing/impulse path validated against the loop.

## 10. Risks

1. **Cost drag dominates.** Cost in R is `(maker + taker + slippage) / stop
   distance`. A 0.3% stop costs ~0.25 R per trade; a 0.1% stop ~0.7 R. The
   report shows the cost-in-R distribution and the share of trades with cost
   above 1 R (guaranteed losers), plus the re-pricing table (D13).
2. **Tiny stops and high implied leverage.** Small bearish candles with a
   0.1 ATR buffer give stop distances of 0.05–0.2%, implying 5–20× leverage
   at 1% risk. The cap skips the extremes and counts them.
3. **Long-only in a bull-heavy sample.** Baseline B and buy-and-hold address
   this; the answer may be "the edge is beta".
4. **Bulk archive quirks.** Header/no-header, ms/µs timestamps, missing
   months, outage gaps. Mitigated by checksums, the manifest, gap reports,
   and the 1m→15m cross-check.
5. **Exchange rule changes.** Fee tiers, funding caps and intervals, leverage
   tiers. Funding intervals come from the data; fees are constants applied
   uniformly and stated as such.
6. **Look-ahead through indicators.** Swing highs reference future candles by
   definition; `confirmed_at` and the MarketView guard are the defence, the
   perturbation tests are the proof.
7. **Dependent trades.** Overlapping positions and one-per-day trades violate
   iid; hence the day-block secondary CI and the block bootstrap for Sharpe.
8. **Multiplicity.** Nine primary cells, 324 grid cells. Holm on the primary
   family; DSR with N = 36 and N = 324 on the grid, with the caveat that grid
   cells share most trades.
9. **Holdout contamination.** Loader guard, one-shot marker, and lock-file
   hash check are mechanical; the human safeguard is that the primary is not
   edited after in-sample results are seen.
10. **SOLUSDT short history.** In-sample starts 2020-09-14.
11. **Windows multiprocessing** uses `spawn`; runner functions must be
    importable; Parquet writes per process.
12. **Disk.** 8.7 GB free; the pipeline needs ~1.2 GB.
13. **Baseline design choices** (market entry with maker fee, uniform entry
    time) are judgment calls; stated in the report and configurable.

## 11. Deferred

- Vectorized signal path with equivalence test, if runtime matters.
- Portfolio view combining the three session variants.
- Short-side mirror (`PlaceBracketLimit` carries a side from day one).
- Additional pairs: add to `pairs` in the pre-registration file.
- Live-trading adapter: out of scope.
