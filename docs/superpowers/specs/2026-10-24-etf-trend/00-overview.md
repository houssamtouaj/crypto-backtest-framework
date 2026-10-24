# Daily trend following on gold and US index ETFs — overview

Status: v1, 2026-10-24. Every decision marked *(my call)* was taken by the
builder on the user's standing instruction to go with the recommended
option; the user can overturn any of them before Phase 0 starts. A fresh
builder check ran; its findings (the full `ETF_CODE_PATHS` import chain,
the `no_vol_target` branch, the Stooq basis, dividend reinvestment, an IWM
split in the warmup) are folded into the phase files. Nothing
has been implemented.

This is the fourth strategy in the repo, and the second test of the trend
rule. The first three stay exactly as they are and are not re-run:

- the ICT order-block strategy (tag `prereg-v1`): no edge;
- daily trend following on crypto perps (tag `trend-prereg-v1`): stopped at
  its screen, p = 0.1558;
- funding carry (code lock `7ce43816…`, forward period from 2026-10-25):
  positive but below the hurdle, not proven. **Its forward reviews are
  still to come, so nothing in this project may change a file in
  `CARRY_CODE_PATHS`** (§7).

| File | Phase |
|---|---|
| `00-overview.md` | this file |
| `phase-0-data.md` | ETF prices, dividends and splits from Yahoo, T-bill rates from FRED, the cross-check, the store, the holdout guard |
| `phase-1-signals.md` | the ETF day frame (trading days, total return, next-open fills), the 12 members reused, vol forecast, target weights |
| `phase-2-prereg-screen.md` | the ETF pre-registration, freeze, and the gross screen (go / stop) |
| `phase-3-execution.md` | the cash-account executor (reference loop and batched form), costs, cash yield |
| `phase-4-statistics.md` | T1 (beats random timing), T2 (beats vol-targeted buy-and-hold), T3 (drawdown, reported), verdict, diagnostics |
| `phase-5-experiments.md` | variants, runner, registry, code lock, holdout, forward runs |
| `phase-6-reporting.md` | figures, per-run report, summary |
| `phase-7-runs.md` | the run protocol, in order |

Phases 3–7 are built only if the Phase 2 screen says go.

---

## 1. Goal

An honest answer to: does the **same** pre-registered daily trend rule that
was frozen for crypto (12-member ensemble, volatility targeting, long/flat),
applied without leverage to gold and US stock index ETFs, time the market
better than random **and** beat a vol-targeted buy-and-hold of the same
ETFs on Sharpe, after costs, with idle cash earning the T-bill rate?

**Why this one.** The user asked whether the three strategies could be
tested on gold and US stocks. Trend following is the only one that carries
over:

- the order-block setup made about +0.01 R per trade **before** costs on
  crypto, so cheaper costs cannot rescue it;
- funding carry needs perpetual funding, which gold and stocks do not have,
  and a gold cash-and-carry is priced to pay about the T-bill rate.

Trend following has its longest public record on exactly these markets.
The crypto test stopped at its screen, but that says little about other
asset classes.

**Success looks like.** `runs/etf/summary.md` states plainly, with p-values
and CIs, whether the primary portfolio passes T1 and T2 in-sample
(2005-08 to 2017), on the 2018–2026 holdout, and later on the forward period.
When a test fails, it says whether the strategy was shown to be worse or
the test could not tell (the CI contains zero). It also shows, as plain
context for a stock investor, how the strategy compared with simply holding
SPY.

**Non-goals.** Live trading or broker integration; individual stocks;
leverage; short selling as a primary; fitting any parameter; bonds,
commodities other than gold, or non-US markets (§12).

**Anti-goals.** Retuning the rule after seeing a result; widening the basket
or changing the lookbacks to rescue a failed screen; a holdout run more
than once.

**What is reused.** From the crypto trend code: the 12 member functions and
`target_weight` in `trend/signals.py` (pure array functions), the prereg
pattern (`trend/prereg.py`: exact keys, duplicate-key loader, canonical
hash, JSON lock), the screen's design. From the shared code: the bootstrap
helpers, DSR, drawdown, seeds, the registry log, `version.files_code_version`
with a scoped file list, and the Markdown/figure helpers. All of these are
**imported, never edited**. New: a daily-bar data source (Yahoo and FRED,
not Binance), a trading-day frame with total returns and next-open fills,
an unleveraged cash-account executor with T-bill interest on idle cash, and
annualisation at 252 trading days.

## 2. Decisions

All *(my call)* unless noted; a call can be changed by a later phase if it
finds a reason, and the change is recorded in that phase file.

| # | Question | Resolution |
|---|---|---|
| ED1 | Rule | The crypto rule, unchanged in form: 12 members (N-day return sign, close above N-day SMA, Donchian breakout with the half exit, N ∈ {20, 40, 80, 160}), equal weight, long/flat exposure in steps of 1/12, **on completed daily closes**. Lookbacks count **bars**, and a bar is now a US trading day (160 bars ≈ 7.5 months). No weights are fitted. |
| ED2 | Basket | **SPY** (S&P 500), **QQQ** (Nasdaq-100), **IWM** (Russell 2000), **GLD** (gold). Fixed list: the user asked for gold and US stocks, and these are the largest, oldest ETFs for each. The common history starts with GLD on 2004-11-18. The primary is **one equal-weight portfolio** of the four; each ETF alone is a secondary table. |
| ED3 | Sizing | Vol targeting per ETF, **no leverage**: target weight = exposure × min(0.15 / σ̂, 1.0), σ̂ = std of the last 60 daily total returns × √252. Each ETF holds at most its 1/N share of equity, so the account never exceeds 100% invested. (Crypto used 30% and a 2× cap; a cash account cannot borrow, and 15% sits near the ETFs' own vol.) |
| ED4 | Cash | Uninvested cash earns the 3-month T-bill rate (FRED `DTB3`), accrued per calendar day. All Sharpe ratios are on returns **in excess of** that rate, for the strategy, the random paths and the benchmarks alike. |
| ED5 | Execution | Decide at day `d`'s close; fill at the **open of the next trading day** (`d+1`), market orders. A position trades when its exposure changes, when it must exit, or when its held weight is more than 20% (relative) away from target, as in crypto. Exploratory rows: fill at the next day's close, weekly rebalancing. |
| ED6 | Costs | Commission 0 (US zero-commission brokers). Slippage per side: 0.02% SPY and QQQ, 0.03% IWM and GLD. Re-priced at slippage {0, 0.02, 0.05, 0.10, 0.20}% × commission {0, 0.05}% (the 0.05% row stands for a non-US broker). Regulatory sell fees are ignored (under 0.003%). |
| ED7 | Data | Daily OHLC, dividends and splits from **Yahoo Finance** via the `yfinance` package (new dependency); total returns are computed in the repo from raw closes and cash dividends, not taken from Yahoo's adjusted close. A second free source (Stooq) cross-checks closes. T-bill rates from FRED (`DTB3`, CSV, no key). Phase 0 §0.2. |
| ED8 | Periods | Warmup from 2004-11-18; **in-sample 2005-08-01 – 2017-12-31** (about 12.4 years; the first month after the 160-bar warmup); **holdout 2018-01-01 – the last full trading day before the download**, one shot, with a caveat (§4); **forward** from the day after the code lock, reviewed at 12 months (descriptive) and 36 months (T1, T2). |
| ED9 | Verdict | Edge iff **T1 and T2 both pass**, each one-sided at α = 0.05, unadjusted (intersection–union, as in crypto trend). T3 (smaller max drawdown at matched vol) is reported only. The summary states the number of strategies tried in this repo (four) next to the verdict. |
| ED10 | Screen | After the freeze, before any execution code: a gross-of-costs T1 on the in-sample basket with a simplified return model (Phase 2 §2.3). Stop if `p ≥ 0.10`; otherwise continue with nothing changed. |
| ED11 | Code | New subpackage `perpbt/etf/`, reports in `perpbt/report/etf/`, configs under `configs/etf/`, data under `data/etf/`, outputs under `runs/etf/`. No file in `CARRY_CODE_PATHS` is edited; `perpbt/trend/` is not edited either. `cli.py` gains a `perpbt etf …` group. |
| ED12 | Freeze | Two-stage lock as in carry: the pre-registration is frozen before the screen (Phase 2); the code version, scoped to `ETF_CODE_PATHS`, is locked at the in-sample run (Phase 7). Holdout and forward runs refuse a different code version unless `--allow-code-change --reason` is given. |
| ED13 | Exploratory | Reported, no say in the verdict, DSR trials: `weekly`, `fill_close`, `no_vol_target` (weight = exposure, cap 1), `no_band` (drift band 0). Long/short is **not** run: a cash account cannot short, and the crypto spec only reported it. |

## 3. Statistical power (estimated before committing)

Analytic estimates, not results. No signal has been computed on ETF data in
this repo.

- **In-sample length.** 2005-08 to 2017-12 is about 12.4 years, so a
  Sharpe's t-statistic is roughly `SR × √12.5 ≈ SR × 3.5`. 80% power at
  one-sided 5% needs a true excess Sharpe of about **0.7**. Published
  long/flat trend results on equity indices and gold sit around 0.4–0.7, so
  absolute significance is plausible but not assured.
- **T2.** On the Sharpe difference against vol-targeted buy-and-hold. With
  correlation ρ ≈ 0.6 between the two, the difference's standard error is
  about `√(2(1−ρ)/12.5) ≈ 0.25`, so 80% power needs a true advantage of
  about **0.6**. Long/flat trend on equities historically adds about 0.1–0.3
  Sharpe over buy-and-hold, mostly by sitting out 2008. **T2 is
  underpowered**; a failure will most likely read "not proven".
- **T1.** Depends on how informative the timing is; the random paths'
  spread is measured and reported next to the result.
- **The basket.** SPY, QQQ and IWM correlate at about 0.85–0.9; gold is
  nearly uncorrelated with them. Four ETFs give roughly two independent
  bets.
- **Holdout.** About 8.8 years: standard errors about 1.2× the in-sample
  ones. Much stronger than the crypto holdouts.
- **Forward, 36 months.** Weak. Its job is to catch an effect that was only
  in-sample.

**Consequence.** Every report shows the Sharpe-difference CI and the
three-way reading: proven worse (CI entirely below 0), not proven (CI
contains 0), proven better (passes).

## 4. Out-of-sample plan

| Period | Role | Touched |
|---|---|---|
| 2004-11-18 – 2005-07-31 | warmup only, no trading | Phase 1 |
| 2005-08-01 – 2017-12-31 | in-sample: screen, primary, exploratory | Phases 2, 7 |
| 2018-01-01 – holdout end | holdout, once, primary and exploratory rows | Phase 7, once |
| code-lock day + 1 onward | forward: 12-month review (descriptive), 36-month (T1, T2) | Phase 7 and later |

**Caveat: the holdout is not unseen.** No trend signal has been computed on
ETF data in this repo, but 2018–2026 is well-known market history (the 2020
crash and V-shaped recovery, the 2022 bear market, the 2024–2025 gold rally),
and the rule is from published research that covers part of it. The
forward period is the only fully unseen test. Every holdout report says so.

**Holdout end.** Set in Phase 0 to the last full US trading day before the
download day, and recorded in `configs/etf/data.yaml`. The loader refuses
any date on or after 2018-01-01 without `allow_holdout=True`, whatever
config it is given (Phase 0 §0.5).

The forward period is not live trading: the runner applies the frozen rule
and code to data that did not exist when they were frozen, whenever it is
run.

## 5. Cash and the benchmark

A long/flat portfolio spends long stretches partly in cash. In 2005–2007 and
2023–2026 cash paid 4–5% a year, so ignoring it would understate the
strategy, and counting it without subtracting it would inflate every
Sharpe. Both strategy and benchmarks therefore earn T-bill interest on
uninvested cash, and every Sharpe is computed on the excess over T-bills.

The T2 benchmark is a **vol-targeted buy-and-hold** of the same four ETFs:
exposure fixed at 1, sized by ED3, rebalanced by the same executor and band.
It isolates timing from sizing. Two plain benchmarks are reported for
context only: **100% SPY** and an **equal-weight 25% each** buy-and-hold,
rebalanced monthly.

## 6. Architecture

```
perpbt/
  etf/
    config.py        EtfDataConfig, EtfSizingConfig, EtfRebalanceConfig, EtfCostConfig (frozen dataclasses)
    sources.py       Yahoo (yfinance) and Stooq downloads, FRED DTB3; network only here
    store.py         data/etf/<TICKER>/{prices,actions}.parquet, data/etf/rates/dtb3.parquet, manifests
    validate.py      calendar, gaps, splits, dividends, cross-check against Stooq
    frame.py         EtfFrame: per-ETF trading-day arrays (close, open, total return, cash rate)
    signals.py       wraps trend/signals.py members and target_weight; σ̂ with √252
    screen.py        the gross screen and its random paths
    executor.py      reference loop (one path) and batched executor (many paths)
    baselines.py     shared-offset circular-shift paths for T1
    verdict.py       T1, T2, T3, verdict; Sharpe at √252
    diagnostics.py   time in market, turnover, costs, per-year and per-ETF tables, re-pricing, DSR
    prereg.py        load / validate / hash / freeze / code-lock for configs/etf/prereg.yaml; ETF_CODE_PATHS
    runner.py        run one variant, the exploratory set, baselines; writes runs/etf/...
    holdout.py       the holdout, once
    forward.py       forward runs and the 12- and 36-month reviews
  report/etf/        figures, report, summary (outside ETF_CODE_PATHS)
  cli.py             + `perpbt etf ...`
configs/etf/         data.yaml, prereg.yaml, prereg.lock
data/etf/            raw downloads and manifests (gitignored, as data/ is)
runs/etf/            registry.jsonl, results.parquet, <variant_id>/..., holdout/, forward/, summary.md
```

## 7. Conventions and constraints

Everything in the crypto trend overview §7 applies (UTC int64 ms for day
keys, numpy at the core, seeded randomness from `(master_seed, variant_id,
purpose)`, frozen dataclasses with canonical-JSON hashes, byte-identical
outputs on rerun, one test file per module, perturbation tests for every
causal computation). Changes for ETFs:

- **The trading day.** Day `d` is a NYSE trading day, keyed by its date at
  00:00 UTC. Only days with prices exist; weekends and holidays are not
  rows. Cash interest still accrues over them (ED4).
- **Annualisation is √252**, not √365. `stats/bootstrap.py` (`sharpe`,
  `paired_sharpe_diff`) multiplies by √365 and is in `CARRY_CODE_PATHS`, so
  it is **not edited**. `etf/verdict.py` has its own `sharpe252` and
  rescales the shared functions' Sharpe values and CIs by `√(252/365)`.
  P-values are unaffected by the scale. A test pins the rescaling against a
  direct computation.
- **Hands off locked files.** No edit to any path in
  `perpbt/carry/prereg.py`'s `CARRY_CODE_PATHS` (that would change the
  carry code version and void its forward reviews) and none to
  `perpbt/trend/`. A test in `tests/etf/` computes
  `carry_code_version()` and asserts it still equals the locked
  `7ce43816…`. If a shared module truly needs a change, the ETF code
  copies the function instead, with a comment saying why.
- **Weights.** As in crypto: `w = position value / (equity / N_active)`.
  Results do not depend on the starting equity.
- **Counting.** Every trade has a reason (`signal`, `exit`, `drift`);
  every trade the band suppressed is counted as `band_skip`.

## 8. Data flow for one run

```
configs/etf/prereg.yaml ──► EtfVariant ──► EtfStore (prices, actions, DTB3)
                                                │
                                                ▼
                  EtfFrame per ETF (Phase 1): close, open, total return, fill price, cash rate
                                                │
                                                ▼
                  signals: 12 members ─► exposure e_d ─► target weight w*_d (vol targeting, cap 1)
                                                │
                                                ▼
                  executor: fills at the open of d+1, slippage, dividends, cash interest
                                                │
                                                ▼
                  daily (portfolio and per ETF), fills ─► T1 / T2 / T3, diagnostics
                                                │
                                                ▼
                  runs/etf/<variant_id>/*.parquet, stats.json, figures/ + registry row
```

## 9. Phase index and branches

| Phase | Delivers | Needs | Branch |
|---|---|---|---|
| 0 Data | sources, store, validation, cross-check, data config, holdout guard, fetched data | — | `etf/0-data` |
| 1 Signals | EtfFrame, members, exposure, σ̂, target weights | 0 | `etf/1-signals` |
| 2 Pre-registration and screen | `configs/etf/prereg.yaml` frozen, screen, go/stop | 1 | `etf/2-prereg-screen` |
| 3 Execution | executor (loop and batched), equivalence tests | 2 = go | `etf/3-execution` |
| 4 Statistics | T1/T2/T3, verdict, diagnostics, re-pricing | 3 | `etf/4-statistics` |
| 5 Experiments | runner, registry, code lock, holdout, forward | 4 | `etf/5-experiments` |
| 6 Reporting | figures, reports, summary | 5 | `etf/6-reporting` |
| 7 Runs | in-sample, holdout, forward start | 6 | `etf/7-runs` |

Each phase branches from `dev` and merges back with `--no-ff` after the
user's go. Phase 0 branches from `strategy/etf-trend-spec`, so the spec
reaches `dev` with it. `dev` goes to `main` after Phase 7's holdout (or
after the screen, if it stops the project).

**Stops for the user:** before the freeze; after the screen; before the
holdout; before every merge.

## 10. Runtime (budgets, not promises)

| Step | Budget |
|---|---|
| Download 4 ETFs (daily, ~5,500 rows each), DTB3, Stooq cross-check | under 1 min |
| One executor path, 4 ETFs × ~5,500 days | well under 1 s (batched) |
| T1 baseline, 5,000 paths | under 1 min |
| Bootstraps, B = 10,000 | seconds |
| Screen, 1,000 paths | seconds |

## 11. Risks

1. **T2 is underpowered** (§3). Reported honestly as "not proven"; not
   fixed by loosening the rule.
2. **The holdout is not unseen** (§4). The forward period is the clean test.
3. **Yahoo data quality.** Unofficial API, revised history, occasional bad
   prints or missing dividends. Mitigated by computing total return in the
   repo, the Stooq cross-check, validation rules and a frozen snapshot with
   hashes (Phase 0). If Yahoo is unreachable, Phase 0 stops and reports
   rather than switching source silently.
4. **Correlated basket.** Three of four ETFs are US equities (§3). The
   per-ETF table shows whether gold carries the result.
5. **Overnight gaps.** Fills at the next open take the overnight gap; the
   `fill_close` row shows the sensitivity.
6. **Multiple testing across strategies.** This is the fourth strategy and
   the second trend test. The verdict is unadjusted, as in the earlier
   three; the summary states the count so a reader can discount a marginal
   pass.
7. **Locked shared code** (§7). A careless edit to a shared module would
   change the carry code version; a test guards it.
8. **Dividend timing.** A dividend is credited to the holder at the
   ex-date's open (it is in the close-to-close total return); a position
   bought at that open does not receive it. Phase 1 §1.2 defines this
   exactly.

## 12. Deferred

- A broader futures-style basket (bonds, oil, currencies, non-US indices).
- Long/short; leverage.
- Individual stocks and cross-sectional momentum (a different strategy).
- Live or paper trading.
