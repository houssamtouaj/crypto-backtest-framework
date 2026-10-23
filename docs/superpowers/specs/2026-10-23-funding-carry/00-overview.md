# Funding-rate carry on Binance spot and USDT-M perpetuals — overview

Status: v1, 2026-10-23. Every decision marked *(my call)* was taken by the
builder on the user's standing instruction to go with the recommended
option; the user can overturn any of them before Phase 0 starts. Nothing
has been implemented.

This is the third strategy in the repo. The first two are closed with no
edge: the ICT order-block strategy (`docs/superpowers/specs/2026-09-26-ob-backtest/`,
tag `prereg-v1`) and daily trend following
(`docs/superpowers/specs/2026-10-23-trend-following/`, tag `trend-prereg-v1`;
its Phase 2 screen stopped the project, p = 0.1558). Neither is modified or
re-run; their pre-registrations, locks, tags and run outputs stay as they
are.

| File | Phase |
|---|---|
| `00-overview.md` | this file |
| `phase-0-data.md` | spot 15m for the basket from 2020, the spot guard split, the carry data config, fetch |
| `phase-1-signals.md` | the paired daily frame (perp + spot), trailing funding, the on/off state, causality tests |
| `phase-2-prereg-screen.md` | the carry pre-registration, freeze, and the simplified net screen (go / stop) |
| `phase-3-execution.md` | the two-leg executor (reference loop and batched form), margin, liquidation, costs |
| `phase-4-statistics.md` | V1 (net Sharpe > 0), V2 (net return > hurdle), verdict, diagnostics |
| `phase-5-experiments.md` | variants, runner, registry, code lock, holdout, forward runs |
| `phase-6-reporting.md` | figures, per-run report, summary |
| `phase-7-runs.md` | the run protocol, in order |

Phases 3–7 are built only if the Phase 2 screen says go.

---

## 1. Goal

An honest answer to: does a simple, pre-registered, delta-neutral funding
carry — long spot and short the USDT-M perpetual of the same coin, held
only while recent funding is high — on a point-in-time basket earn a net
return that is **significantly positive** and **significantly above the
yield of simply holding stablecoins**, after fees, slippage, basis moves
and margin?

**Why this one.** The first two strategies bet on price direction, and all
perp price data through 2026-10-20 has now been examined for that. Carry is
a different source of return: perp longs pay shorts a premium when
leverage demand is high. It is the best documented return source in
crypto derivatives. Nobody in this repo has computed a carry return on any
of this data.

**Success looks like.** `runs/carry/summary.md` states plainly, with
p-values and CIs, whether the primary passes V1 and V2 in-sample, on the
2024–2026 holdout, and later on the forward period; when V2 fails, whether
the carry was shown to be below the hurdle or the test could not tell.

**What is reused.** The data layer (archive, stores, validation,
manifests), the trend Phase 1 `DayFrame` (`trend/daily.py`) for both legs,
the trend universe (`configs/trend/universe.json`, perp members), the
pre-registration machinery pattern (`trend/prereg.py`: exact keys,
duplicate-key loader, canonical hash, JSON lock), the screen pattern, the
bootstrap and Sharpe code, DSR, the registry, `version.code_version` with a
scoped file list. New: spot data from 2020, a split spot guard, the paired
frame, the on/off signal, a two-leg executor with margin, and the V2 hurdle
test.

## 2. Decisions

All *(my call)* unless marked otherwise; a builder's call made later in a
phase is recorded in that phase file.

| # | Question | Resolution |
|---|---|---|
| CD1 | Trade | Per pair, long spot and short perp in **equal base quantity** (delta-neutral), on or off. No reverse carry (shorting spot needs margin borrowing; its cost is not in the data). |
| CD2 | Signal | Trailing funding `f̄_d` = mean rate of the pair's funding events on days `d−6..d` (7 days, 21 events at 8 h), annualised as `mean rate × events per day × 365`. Turn **on** when `f̄_d > 10%`/yr, **off** when `f̄_d < 0`; otherwise keep the state (hysteresis). Decided at day `d`'s close, filled at 00:15 of `d+1` on both legs. Realised rates only, never the exchange's predicted next rate. |
| CD3 | Sizing | Equal capital sleeves: each live pair's sleeve is `E / N_live`. An "on" sleeve holds spot notional `X = S × L / (L + 1)` and a short perp of the same quantity backed by margin `X / L`, with perp leverage **L = 2** (spot notional = 2/3 of the sleeve). Separate spot and futures wallets; spot is not used as perp collateral (conservative). An "off" sleeve is cash (USDT, earning 0). |
| CD4 | Universe | The trend strategy's point-in-time perp basket, unchanged (`configs/trend/universe.json` `um`: BTC ETH BCH LINK ETC LTC XRP EOS TRX XLM), with the spot pair of the same symbol. All ten have Binance spot klines in the archive from before 2020 (checked 2026-10-23; EOSUSDT spot ends 2025-05, as its perp). Trading from 2020-07-01. Reusing it adds no selection freedom. |
| CD5 | Periods | In-sample **2020-07-01 – 2023-12-31** (3.5 years). Holdout **2024-01-01 – 2026-10-20**, one shot, with its own `DONE` marker (2.8 years). Forward after the code lock: descriptive review at 12 months, confirmatory V1/V2 at 36 months. |
| CD6 | Costs | Perp taker 0.05%, spot taker 0.10% (Binance VIP 0, no BNB discount), on every fill. Slippage per leg 0.02% BTC/ETH, 0.05% the others, on traded notional. Funding from the data on the perp leg (shorts receive positive rates). Re-pricing grid: slippage {0, 0.02, 0.05, 0.10}% × spot fee {0.075, 0.10}%. |
| CD7 | Margin | **Liquidation** is checked against the day **high** (adverse for a short); **rebalancing** is checked at the day **close** *(my call, after the builder check: a high-based trigger would rebalance on intraday spikes that reverse)*. If at the close the perp wallet's equity is below 50% of the margin it would need at L = 2, the sleeve is rebalanced back to target at the next fill (sell spot, reduce the short equally, move cash), with costs. If the day high reaches the liquidation price (equity ≤ maintenance margin, `mmr` 0.4% BTC, 0.5% ETH, 1% others), the short is liquidated at that price with a 1% liquidation fee on notional, and the spot leg is sold at the next fill; the event is counted. |
| CD8 | Verdict | Edge iff **V1 and V2 both pass**, each one-sided at α = 0.05, unadjusted (intersection–union). V1: portfolio net Sharpe > 0 (moving-block bootstrap). V2: annualised net return minus a **5%/yr hurdle** > 0 (block bootstrap of the daily excess return). Block length 30 days. |
| CD9 | Hurdle | **5% a year**: the opportunity cost of holding the same USDT in a savings or lending product. Stablecoin yields on Binance and in DeFi ranged from about 2% to well over 10% over 2020–2026; 5% is a round middle, not fitted to anything. |
| CD10 | Screen | After the freeze and before any executor code: the simplified net model of Phase 2 on the in-sample basket. **Stop if V2's p ≥ 0.10**; otherwise continue with nothing changed. It can stop the project, never tune it. |
| CD11 | Exploratory | Reported, no say in the verdict, DSR trials: `always_on` (no filter), `entry_5` and `entry_20` (on threshold 5% / 20%), `lookback_3d` and `lookback_14d`, `lev_1` (L = 1). |
| CD12 | Code location | New subpackage `perpbt/carry/`; reports in `perpbt/report/carry/`; configs `configs/carry/`; outputs `runs/carry/`. Shared modules change only backward compatibly; both earlier strategies' test suites stay green. |
| CD13 | Freeze | Two-stage, as trend: the pre-registration is frozen before the screen (Phase 2); the code version is locked at the in-sample primary run (Phase 7). Holdout and forward refuse another code version unless `--allow-code-change --reason`; an output-neutral change passes, as in trend Phase 5 §5.4. |

## 3. Statistical power (estimated before committing)

Analytic estimates from general knowledge of the market, not results; no
carry return has been computed on any data.

- **Return.** Binance BTC/ETH funding averaged very roughly 20–30%/yr in
  2021, near zero in 2022, and 5–10% in 2023. With the filter on part of
  the time and 2/3 of each sleeve in the trade, a gross carry of perhaps
  5–10%/yr on capital is plausible in-sample, before about 0.3–0.5% per
  round trip of costs.
- **Risk.** A hedged sleeve moves with the basis (perp vs spot price),
  typically a few basis points a day, with blow-outs in crashes (May 2021,
  November 2022). A guess of 1.5–3% annualised vol for the basket.
- **V1** (Sharpe > 0) is then very likely to pass if the carry is real; it
  mostly guards against costs and basis eating everything.
- **V2** (return > 5%) is the binding test. Over 3.5 years the standard
  error of the annual mean is about `vol / √3.5 ≈ 0.8–1.6%`, more with
  autocorrelation (funding regimes last months; hence the 30-day blocks).
  80% power needs the true net return about 2.5 standard errors above the
  hurdle: roughly **7–9%/yr net**. Below that, a failed V2 most likely
  means "not proven", not "worse".
- **Holdout** (2.8 years) and **forward 36 months** have similar power.

**Consequence for the report.** Every V2 result shows the excess-return CI
and says which it is: proven below the hurdle (CI entirely below 0), not
proven (CI contains 0), proven above (passes).

## 4. Out-of-sample plan

| Period | Data | Role | Touched |
|---|---|---|---|
| 2020-01-01 – 2020-06-30 | perps, spot | warmup and the universe ranking (trend Phase 0); no trading | done |
| 2020-07-01 – 2023-12-31 | perps, spot | in-sample: screen, primary, exploratory | Phases 2, 7 |
| 2024-01-01 – 2026-10-20 | perps, spot | holdout, once: the primary (verdict) and the six exploratory rows (descriptive), fixed now | Phase 7, once |
| code-lock day + 1 onward | perps, spot | forward: review at 12 months, confirm at 36 | Phase 7 |

**How clean the holdout is** *(my call, after the builder check)*. No
carry return, basis series or per-pair funding statistic has been computed
on 2024-01-01 – 2026-10-20 in this repo. It is not unseen, though:

- The trend screen loaded these perp prices, as price-direction signals.
- The first strategy charged BTCUSDT and ETHUSDT funding over the whole
  window (its in-sample through 2025-12-31, its holdout 2026). Its
  committed reports show mean funding per trade.
- General market knowledge covers 2024 too (§3 quotes funding levels from
  it, such as the high funding early that year).

The holdout keeps its one-shot verdict role. Every report states this
caveat next to its verdict and treats the 36-month forward test as the
only fully unseen one.

## 5. What can go wrong in a carry trade (and how it is modelled)

1. **Funding turns negative** while on: the short pays. The filter exits
   at the next decision after the 7-day mean drops below 0; the drag until
   then is charged event by event.
2. **Basis blow-out**: in a crash the perp can trade below spot for
   hours. Both legs are marked at their own 15m closes, so the basis P&L is
   in the returns; exits fill at the actual 00:15 prices of both legs.
3. **Short squeeze on the perp leg**: the margin rule (CD7) with the day
   high; liquidations are counted and reported, never hidden.
4. **Delisting** of the perp or the spot pair: the sleeve is closed at the
   last common candle, as in trend Phase 3 §3.6.
5. **Exchange and counterparty risk** (an exchange failure, a frozen
   withdrawal) is not modelled. The report states it as the main risk a
   backtest cannot show.

## 6. Architecture

```
perpbt/
  data/store.py         spot guard split: loads before 2020-01-01 (the trend spot holdout window) need
                        allow_holdout; later spot loads follow the DataConfig date guard like perps
  trend/daily.py        (reused) DayFrame for each leg
  carry/
    config.py           CarrySignalConfig, SleeveConfig, CostConfig, MarginConfig (frozen dataclasses)
    frames.py           PairFrame: perp and spot DayFrames aligned by day; common last day; basis
    signals.py          trailing funding, on/off state machine
    screen.py           the simplified net screen
    executor.py         reference two-leg loop and batched form
    baselines.py        random on/off timing paths (descriptive)
    verdict.py          V1, V2, verdict; diagnostics.py
    prereg.py           load / validate / hash / freeze / code-lock for configs/carry/prereg.yaml
    runner.py, holdout.py, forward.py
  report/carry/         figures, report, summary
  cli.py                + `perpbt carry ...`
configs/carry/          data.yaml, prereg.yaml, prereg.lock
runs/carry/             registry.jsonl, results.parquet, <variant_id>/..., holdout/, forward/, summary.md
```

## 7. Conventions

Everything in the first strategy's overview §6 and trend overview §7
applies (UTC int64 ms, numpy at the core, seeds from `(master_seed,
variant_id, purpose)`, frozen dataclasses with canonical hashes,
byte-identical outputs on rerun, perturbation tests for every causal
computation, calendar days, decisions on day `d`'s close filled on `d+1`).
In addition:

- **Sleeves, not weights.** Returns are per sleeve and per portfolio, as
  fractions of equity; the starting equity is only a scale.
- **Counting.** Every trade has a reason (`enter`, `exit`, `rebalance`,
  `liquidation`, `delist`); every liquidation and rebalance is counted.

## 8. Phase index and branches

| Phase | Delivers | Needs | Branch |
|---|---|---|---|
| 0 Data | spot 15m 2020+, spot guard split, carry data config | — | `carry/0-data` |
| 1 Signals | PairFrame, trailing funding, on/off state | 0 | `carry/1-signals` |
| 2 Pre-registration and screen | prereg frozen, screen result, go/stop | 1 | `carry/2-prereg-screen` |
| 3 Execution | two-leg executor, equivalence tests | 2 = go | `carry/3-execution` |
| 4 Statistics | V1, V2, verdict, diagnostics | 3 | `carry/4-statistics` |
| 5 Experiments | runner, registry, code lock, holdout, forward | 4 | `carry/5-experiments` |
| 6 Reporting | figures, reports, summary | 5 | `carry/6-reporting` |
| 7 Runs | in-sample, holdout, forward start | 6 | `carry/7-runs` |

Each phase branches from `dev` and merges back with `--no-ff`. The spec is
on `strategy/funding-carry-spec` and reaches `dev` with the Phase 0 merge.

## 9. Runtime (budgets, not promises)

| Step | Budget |
|---|---|
| Spot 15m fetch, 10 pairs × 2020–2026 | ~1–3 min per pair |
| One executor path, 10 pairs × ~1,280 in-sample days | well under 1 s |
| Random-timing paths (1,000), bootstraps (B = 10,000) | under a minute |

## 10. Risks

1. **V2 is underpowered** (§3). Mitigated only by honest reporting.
2. **The hurdle is a choice.** 5% is defensible, not unique; the report
   shows the result against 0%, 3%, 5% and 8% descriptively so a reader can
   apply their own.
3. **Funding regimes.** 2021 dominates any in-sample carry; the per-year
   table shows how much.
4. **Costs at VIP 0.** Real traders at size pay less; the re-pricing grid
   shows the sensitivity. The verdict uses VIP 0.
5. **Unmodelled risks**: exchange failure, withdrawal freezes, auto-
   deleveraging, rule changes (funding caps, interval changes to 4 h).
   Funding intervals are taken from the data, so interval changes are in.
6. **Shared-module edits** (the spot guard) change the earlier strategies'
   code versions. Both are closed; their tests must stay green. Do not run
   any first-strategy command (`perpbt run`, `grid`, `baselines`,
   `holdout`, `stats`) or any trend run command after these edits.

## 11. Deferred

- Reverse carry (short spot when funding is negative).
- Cross-exchange carry, quarterly-futures basis, and portfolio margin.
- Using the exchange's predicted next funding rate.
- Dynamic universe, fitted thresholds, live trading.
