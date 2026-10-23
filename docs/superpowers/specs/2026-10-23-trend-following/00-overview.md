# Daily trend following on Binance USDT-M perpetuals — overview

Status: v1, 2026-10-23, all user decisions settled. Nothing has been implemented.

This is the second strategy in the repo. The first (the ICT order-block
strategy, `docs/superpowers/specs/2026-09-26-ob-backtest/`) is closed: no
edge, merged to `main` at d86805e. It is not modified or re-run; its
`configs/prereg.yaml`, `configs/prereg.lock`, tag `prereg-v1`, and
`runs/holdout/DONE` stay exactly as they are.

This overview holds everything that spans phases. Each phase file is the
contract for that phase and can be read on its own together with this
overview.

| File | Phase |
|---|---|
| `00-overview.md` | this file |
| `phase-0-data-universe.md` | spot archive support, the point-in-time universe, the trend data config, fetch |
| `phase-1-signals.md` | daily bars, the 12 ensemble members, vol forecast, target weights, causality tests |
| `phase-2-prereg-screen.md` | the trend pre-registration file, freeze, and the gross signal screen (go / stop) |
| `phase-3-execution.md` | the daily target-weight executor (reference loop and batched form), costs, funding, liquidation |
| `phase-4-statistics.md` | random-days baseline (T1), buy-and-hold test (T2), drawdown test (T3), verdict, diagnostics |
| `phase-5-experiments.md` | variants, runner, registry, code lock, spot holdout, forward runs |
| `phase-6-reporting.md` | figures, per-run report, summary |
| `phase-7-runs.md` | the run protocol, in order, including the forward reviews |

Phases 3–7 are built only if the Phase 2 screen says go.

---

## 1. Goal

An honest answer to: does a simple, pre-registered daily trend-following
rule, applied to a point-in-time basket of Binance USDT-M perpetuals, time
the market better than random **and** beat a vol-targeted buy-and-hold of
the same basket on Sharpe, after fees, slippage, and funding?

**Success looks like.** `runs/trend/summary.md` states plainly, with
p-values and CIs, whether the primary portfolio passes T1 (beats random
timing) and T2 (beats vol-targeted buy-and-hold on Sharpe), in-sample, on
the spot 2018–2019 holdout, and later on the forward period. When a test
fails, it says whether the strategy was shown to be worse or the test was
simply not able to tell (the CI includes zero).

**What is reused from the first strategy.** The data layer (bulk archive,
ccxt tail, stores, validation, manifests), the daily-bar builder, the
bootstrap and Sharpe code, DSR, the duplicate-key YAML loader and the
canonical-JSON hash, the registry's append-only log and file lock,
`version.code_version` (with a narrower scope, Phase 5 §5.4), and the Markdown/figure helpers. What is
new: a daily target-weight executor (the 15m bracket simulator does not fit
and is not touched), the random-days baseline, the trend pre-registration,
and spot data for the holdout.

## 2. Decisions

Settled with the user on 2026-10-23 unless marked *(builder's call)*; a
builder's call can be changed without going back to the user if a phase
finds a reason, and the change is recorded in the phase file.

| # | Question | Resolution |
|---|---|---|
| TD1 | Signal | Equal-weight ensemble of 12 members: N-day return sign, close above N-day SMA, and Donchian breakout, each at N ∈ {20, 40, 80, 160} days, on completed UTC daily closes. Long/flat exposure = share of members long, in steps of 1/12. Exact definitions in Phase 1. No weights are ever fitted. |
| TD2 | Direction | Long/flat is the primary. Long/short (members −1/0/+1) is computed as a labelled exploratory variant: reported, no say in the verdict. It can become a primary only in a later pre-registration, tested out of sample. |
| TD3 | Sizing | Volatility targeting per pair: target weight = exposure × min(0.30 / σ̂, 2.0), σ̂ = annualised std of the last 60 completed daily returns (funding included). Hard cap 2× notional per pair and 2× gross for the portfolio. |
| TD4 | Universe | Point-in-time basket of up to 10 USDT-M perps chosen by a mechanical rule from information available before trading starts, delisted perps included: candidates with daily archive data for all of 2020-01 – 2020-06, ranked by that period's quote volume; trading from 2020-07-01 (SOLUSDT, listed 2020-09, is therefore excluded). The primary is **one equal-weight portfolio** of the basket; per-pair results are secondary. Rule in Phase 0 §0.3. |
| TD5 | Rebalancing | Evaluate daily. A pair trades when its ensemble exposure differs from the exposure at its last trade, when its target is zero and it holds a position, or when its held weight is more than 20% (relative) away from target. Fill at the close of the first 15m candle of the next UTC day (00:15), taker fee plus slippage. Weekly rebalancing and fills at 01:00, 04:00, 08:00 UTC are exploratory robustness rows. |
| TD6a | T1 random paths | One shared circular-shift offset for all pairs per path, so the pairs' exposures keep moving together; only the timing is randomised (Phase 4 §4.1). |
| TD6 | Verdict | Edge iff **T1 and T2 both pass**, each one-sided at α = 0.05, unadjusted. Requiring every test to pass (an intersection–union test) already keeps the false-positive rate at α, so no Holm. T3 (smaller max drawdown at matched vol) is reported, not part of the verdict. |
| TD7 | Out-of-sample | All perp data through 2026-10-20 has been seen and is in-sample. Clean tests: (a) Binance **spot 2018–2019**, a one-shot holdout with its own `DONE` marker; (b) the **forward period** after the code lock, with a descriptive review at 12 months and a confirmatory T1/T2 test at 36 months. |
| TD8 | Signal screen | Before any execution code is built, and after the pre-registration is frozen: a gross-of-costs T1 on the in-sample basket with a simplified return model. Stop the project if `p ≥ 0.10`; otherwise continue with nothing changed. The screen can stop the project, never tune it. |
| TD9 | Funding | Perps: from the data, every event, longs pay positive rates. Spot holdout: an assumed 0.01% per 8 h charged to longs (Binance's base interest component), plus a zero-funding row. Funding drag is reported on its own. |
| TD10 | Costs | Taker 0.05% on every fill (all fills are market). Slippage 0.02% BTCUSDT/ETHUSDT, 0.05% every other pair, booked as a cost on traded notional. Re-priced at slippage {0, 0.02, 0.05, 0.10, 0.20}% × taker {0.04, 0.05}% by re-running the executor (cheap). *(builder's call on the grid values)* |
| TD11 | Code location | New subpackage `perpbt/trend/`; trend reports in `perpbt/report/trend/`. Shared modules (data layer, CLI) change only in backward-compatible ways; the order-block test suite stays green and is the regression guard. Trend outputs live under `runs/trend/`, configs under `configs/trend/`. |
| TD12 | Freeze | Two-stage lock: the pre-registration content is frozen before the screen (Phase 2); the code version is locked at the in-sample run (Phase 7). The holdout and forward runs refuse a different code version unless `--allow-code-change --reason` is given, as in the first strategy. |

## 3. Statistical power (estimated before committing)

These are analytic estimates, not results. No trend signal has been run on
any data.

- **In-sample length.** Trading from 2020-07-01 to 2026-10-20 is about
  6.3 years. A Sharpe ratio's t-statistic is roughly
  `SR_annual × √years ≈ SR × 2.5`.
- **Absolute Sharpe.** 80% power at one-sided 5% needs a true annual Sharpe
  of about **1.0**. Published crypto trend-following results sit near that,
  so this is plausible but marginal.
- **T2 (vs vol-targeted buy-and-hold).** The test is on the Sharpe
  difference. With the strategy and the benchmark correlated at ρ ≈ 0.6–0.7,
  the difference's standard error is about `√(2(1−ρ) / years) ≈ 0.33`, so
  80% power needs a true Sharpe advantage of about **0.8**. That is a large
  advantage; T2 is the binding test and is likely underpowered.
- **T1 (vs random timing).** Its power depends on how informative the
  timing is; the random-days distribution's spread will be measured, not
  guessed, and is reported next to the result.
- **The basket.** Crypto daily returns correlate at about 0.7–0.8 and trend
  positions somewhat less. Ten pairs give roughly 1.5–2 independent bets,
  about a 1.3–1.4× gain in power over one pair. Worth having, not a cure.
- **Spot holdout.** About 2 years: standard errors roughly 1.8× the
  in-sample ones. It can catch a large failure, not confirm a small edge.
- **Forward, 36 months.** Less power than the in-sample period. Its job is
  to catch an effect that was only in-sample, not to prove a small one.

**Consequence for the report.** A failed T2 most likely means "not
proven", not "worse". Every report shows the Sharpe-difference CI and says
which of the three it is: proven worse (CI entirely below 0), not proven
(CI contains 0), proven better (passes).

## 4. Out-of-sample plan

| Period | Data | Role | Touched |
|---|---|---|---|
| 2020-01-01 – 2020-06-30 | perps | warmup and universe ranking; no trading | Phase 0 |
| 2020-07-01 – 2026-10-20 | perps | in-sample: screen, primary, exploratory | Phases 2, 7 |
| 2018 – 2019 (per pair, from its warmup) | spot | holdout, once, primary rule only (plus its robustness rows) | Phase 7, once |
| code-lock day + 1 onward | perps | forward: review at 12 months (descriptive), confirm at 36 months (T1, T2) | Phase 7 |

The forward period is not live trading: it applies the frozen rule and
frozen code to data that did not exist when they were frozen, so it is
computed by the same runner over a later date range whenever it is run.
Nothing has to run daily.

## 5. Funding in bull trends

A long/flat trend portfolio is long mostly during uptrends, which is when
perp funding is highest (2021 saw stretches at 0.1% per 8 h, over 100% a
year). The executor charges every funding event in the data on the
position held at that instant. The report shows funding paid per year and
as a share of gross return, for the strategy and for buy-and-hold, and the
T1 baseline pays funding at its own (random) times, so a strategy that
only makes money before funding fails T1 honestly.

## 6. Architecture

```
perpbt/
  data/                 (shared; extended)
    bulk.py             + market "spot": data/spot/monthly/klines/... ; no funding files
    store.py            + store root per market: data/candles (perps, unchanged) and data/spot/candles
    fetch.py            + market argument; 15m-only fetch for the trend pairs
  indicators/daily.py   (shared, reused) daily_bars from 15m
  stats/                (shared, reused) bootstrap, sharpe, paired_sharpe_diff, percentile_ci, dsr, max_drawdown
  experiments/registry.py (shared, reused) Registry and file_lock; trend rows have their own builder
  version.py            (shared, reused) code_version, git_commit
  trend/
    config.py           TrendConfig, SignalConfig, SizingConfig, RebalanceConfig, CostConfig (frozen dataclasses)
    universe.py         point-in-time ranking for perps and spot
    daily.py            DayFrame: per-pair daily arrays (close, return, fill price, funding, day low) from 15m
    signals.py          the 12 members, ensemble exposure, vol forecast, target weights
    screen.py           the gross screen (simplified return model) and its random-days runs
    executor.py         reference daily loop (one path) and batched executor (many paths)
    baselines.py        circular-shift exposure paths for T1
    tests.py            T1, T2, T3, verdict (named verdict.py if "tests" confuses pytest discovery)
    diagnostics.py      turnover, time in market, funding drag, per-year table, cost re-pricing
    prereg.py           load / validate / hash / freeze / code-lock for configs/trend/prereg.yaml
    runner.py           run one variant, the exploratory set, baselines; writes runs/trend/...
    holdout.py          spot holdout, once
    forward.py          forward-period runs and the 12- and 36-month reviews
  report/trend/         figures, report, summary (outside code_version like report/)
  cli.py                + `perpbt trend ...` subcommands
configs/trend/          data.yaml, universe.json, prereg.yaml, prereg.lock
runs/trend/             registry.jsonl, results.parquet, <variant_id>/..., holdout/, forward/, summary.md
```

## 7. Conventions

Everything in the first strategy's overview §6 applies (UTC int64 ms,
numpy at the core, seeded randomness from `(master_seed, variant_id,
purpose)`, frozen dataclasses with canonical-JSON hashes, byte-identical
outputs on rerun, one test file per module, perturbation tests for every
causal computation). In addition:

- **The trading day.** Day `d` is a UTC calendar day. Its close `C_d` is
  the close of its last 15m candle. A decision made on day `d`'s close is
  filled on day `d+1` at the fill time (00:15 by default). Days are calendar
  days: a day with no 15m candle at all gets a forward-filled bar (Phase 1
  §1.1) and is counted.
- **Weights, not dollars.** Positions are held as quantities in one
  cross-margin account; targets and drift are measured as weights
  `w = notional / (equity / N_active)`. Returns are therefore independent
  of the starting equity.
- **Counting.** Every trade has a reason (`signal`, `exit`, `drift`,
  `delist`, `liquidation`); every non-trade that the band suppressed is
  counted as `band_skip`.

## 8. Data flow for one run

```
configs/trend/prereg.yaml ──► TrendVariant ──► CandleStore (perp or spot) + FundingStore
                                                   │
                                                   ▼
                     DayFrame per pair (Phase 1): C_d, r_d, fill price, funding events, day low
                                                   │
                                                   ▼
                     signals: 12 members ─► exposure e_d ─► target weight w*_d (vol targeting)
                                                   │
                                                   ▼
                     executor: fills on d+1, costs, funding, marks, liquidation check
                                                   │
                                                   ▼
                     daily (portfolio and per pair), fills, positions ─► T1 / T2 / T3, diagnostics
                                                   │
                                                   ▼
                     runs/trend/<variant_id>/*.parquet, stats.json, figures/ + registry row
```

## 9. Phase index and branches

| Phase | Delivers | Needs | Branch |
|---|---|---|---|
| 0 Data and universe | spot support, universe rule and result, trend data config, fetched data | — | `trend/0-data-universe` |
| 1 Signals | DayFrame, members, exposure, vol forecast, target weights | 0 | `trend/1-signals` |
| 2 Pre-registration and screen | `configs/trend/prereg.yaml` frozen, screen result, go/stop | 1 | `trend/2-prereg-screen` |
| 3 Execution | executor (loop and batched), equivalence tests | 2 = go | `trend/3-execution` |
| 4 Statistics | T1/T2/T3, verdict, diagnostics, re-pricing | 3 | `trend/4-statistics` |
| 5 Experiments | runner, registry, code lock, holdout, forward | 4 | `trend/5-experiments` |
| 6 Reporting | figures, reports, summary | 5 | `trend/6-reporting` |
| 7 Runs | in-sample, holdout, forward start; later the reviews | 6 | `trend/7-runs` |

Branch rules are the first strategy's overview §8.1 with these names: each
phase branches from `dev` and merges back with `--no-ff`; `dev` goes to
`main` once at the end of Phase 7's first pass (after the holdout). The
spec itself is on `strategy/trend-following-spec` and merges into `dev`
when approved.

## 10. Runtime (budgets, not promises)

| Step | Budget |
|---|---|
| Fetch 15m + funding for ~7 new perps, ~10 spot pairs | ~1 min per pair |
| Universe ranking (archive 1d klines) | a few minutes |
| One executor path, 10 pairs × ~2,300 days | well under 1 s (batched form) |
| T1 baseline, 5,000 paths, batched over paths | under 1 min |
| T2/T3 bootstraps, B = 10,000 | seconds |
| Screen (1,000 gross paths) | seconds |

The daily executor works on per-day arrays precomputed from 15m data once
per pair, so its cost does not depend on the 15m candle count.

## 11. Risks

1. **T2 is underpowered** (§3). Mitigated only by reporting "not proven"
   honestly; not by loosening the rule.
2. **The in-sample period has been lived through.** The rule was chosen
   from the literature, not from these charts, and has no fitted
   parameters; still, only the spot holdout and the forward period are
   clean.
3. **Survivorship.** The universe rule includes delisted perps and is
   fixed before trading starts; Phase 0 verifies archive coverage per pair.
4. **Funding in bull trends** (§5).
5. **Liquidity of 2020-era alt perps.** Slippage 0.05% may be low for some;
   the re-pricing table shows the sensitivity. Turnover is low, so the
   exposure to this is small.
6. **Spot is not perp.** The holdout uses spot prices and an assumed
   funding rate; basis and funding spikes are missing. Stated in the
   holdout report.
7. **The 2018–2019 holdout is mostly a bear market**, which flatters
   long/flat against buy-and-hold. The holdout report shows T1 next to T2
   for that reason, and the per-year table.
8. **Delistings and gaps.** Handled by rule (Phase 3 §3.6); counted.
9. **Shared-module edits.** Adding `perpbt/trend/` and any edit to the
   data layer change the first strategy's code version. That strategy is
   closed, so this matters only for its tests, which must stay green; one
   of them is edited on purpose (Phase 5 §5.8, the `allow_holdout=True`
   grep test). Do not run any first-strategy
   command (`perpbt run`, `grid`, `baselines`, `holdout`, `stats results`)
   after these edits; `stats results` would move its rows to
   `results_superseded.parquet`.
10. **Wrap-around in the circular shift.** One artificial seam per path;
    negligible over ~2,300 days, and the shift range excludes near-copies
    (Phase 4 §4.1).

## 12. Deferred

- Long/short as a primary (needs its own pre-registration and a clean
  test period).
- A dynamic universe (monthly re-ranking).
- Signal weighting, lookback selection, or any fitted parameter.
- Live trading.
