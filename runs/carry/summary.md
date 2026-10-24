# Funding carry — summary

**Question.** Does a simple, pre-registered, delta-neutral funding carry (long spot and short the USDT-M perpetual of the same coin, held only while recent funding is high) on a point-in-time basket earn a net return that is significantly positive and significantly above the yield of simply holding stablecoins, after fees, slippage, basis moves and margin? (overview §1)

**Rule.** Per pair of the basket (BTCUSDT, ETHUSDT, BCHUSDT, LINKUSDT, ETCUSDT, LTCUSDT, XRPUSDT, EOSUSDT, TRXUSDT, XLMUSDT), long spot and short the perpetual in equal quantity while the 7-day trailing funding says on (on above 10%/yr, off below 0%/yr), equal sleeves, perp leverage 2. Costs: perp taker 0.05%, spot taker 0.10%, slippage per leg (default 0.05%, BTCUSDT 0.02%, ETHUSDT 0.02%). **Edge iff V1 (net Sharpe above 0) and V2 (annualised net return above the 5%/yr hurdle) both pass**, each one-sided at α = 0.05, unadjusted; moving-block bootstrap, B = 10000, blocks of 30 days. V2 reads three ways: proven above the hurdle, not proven (the CI contains 0), proven below (the CI lies below 0).

**Screen (Phase 2).** The simplified net screen on the in-sample basket gave p = 0.0045 (V2 against the 5%/yr hurdle; stop at 0.1 or above): **go**. It could stop the project, never tune it; its model has no fill delay, margin, rebalancing or liquidation.

**Code lock.** Carry code version `7ce43816563f41f4f2aa7318d46e382e027e7a547652ac4d6b748454fa80f240` locked on 2026-10-24 (git `ab9592190a0dfa67322dde14a3928b2a1beb6244`); the forward period starts 2026-10-25.

## Verdict per period

| Period | Dates | Status | Verdict | p (V1) | p (V2) | Excess over the hurdle | 95% CI (excess) | V2 reading | Annualised net return | Report |
|---|---|---|---|---|---|---|---|---|---|---|
| in-sample | 2020-07-01 to 2023-12-31 | run | positive below the hurdle | 0.0001 | 0.0571 | 2.97% | -0.64% to 6.94% | not proven: the CI contains 0 | 7.85% | [report](b3116cf36f604d135e7bea2ecc11ba600ba293f4ae750987a932ce8e7b1af9b6/report.md) |
| holdout | 2024-01-01 to 2026-10-20 | not run yet | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| forward, 12-month review | from the forward start | not run yet | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| forward, 36-month review | from the forward start | not run yet | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

- **Verdict (in-sample): positive below the hurdle.** V1 (net Sharpe above 0) passes, p = 0.0001; V2 (annualised net return above the 5%/yr hurdle) fails, p = 0.0571. Annualised net return 7.85% (95% CI 4.24% to 11.82%); excess over the hurdle 2.97% (95% CI -0.64% to 6.94%): not proven: the CI contains 0.

The 36-month forward review is the only fully unseen test; the holdout is not fully unseen (overview §4).

## Exploratory rows (descriptive)

The six pre-registered exploratory rows on the portfolio: reported, no say in the verdict (the DSR trials of the primary's report). Each pair alone is a table in its row's report.

**in-sample**

| Row | Would read | p (V1) | p (V2) | Excess over the hurdle | 95% CI (excess) | V2 reading | Annualised net return | Report |
|---|---|---|---|---|---|---|---|---|
| always_on | positive below the hurdle | 0.0004 | 0.407 | 0.48% | -2.65% to 4.23% | not proven: the CI contains 0 | 5.36% | [report](a276d3c88a9bd82f9555693bb266b10b233707b1542ba96faa26541be61bfeb1/report.md) |
| entry_5 | edge | 0.0001 | 0.0427 | 3.32% | -0.47% to 7.38% | proven above the hurdle (V2 passes) | 8.20% | [report](000210a66b6275f7a82fcb9340a6031c2466316fd51194ee742f0677701586f1/report.md) |
| entry_20 | positive below the hurdle | 0.0001 | 0.183 | 1.82% | -1.72% to 5.58% | not proven: the CI contains 0 | 6.70% | [report](db43d752fd03b2a8cdeaf09eae85957718e2999ea62f26575c11cd3447adc50e/report.md) |
| lookback_3d | positive below the hurdle | 0.0001 | 0.2 | 1.36% | -1.57% to 4.31% | not proven: the CI contains 0 | 6.24% | [report](f71895edacf6943afa4fafcf926aab765bc9e187ce822e9dcd0a3dfcc065ad8d/report.md) |
| lookback_14d | positive below the hurdle | 0.0001 | 0.0645 | 2.86% | -0.70% to 6.82% | not proven: the CI contains 0 | 7.74% | [report](596af74623742cc809dbb4e4dbe2cb21f300c9e5052fa89548bf2d1663a82bd9/report.md) |
| lev_1 | positive below the hurdle | 0.0001 | 0.0572 | 2.86% | -0.57% to 6.78% | not proven: the CI contains 0 | 7.74% | [report](0c86ff24e59b56257aec91ff68b96badfe6e012cdfc72f57c9d747c2191e0bb3/report.md) |

## Unmodelled risks

A backtest cannot show the main risk of a carry trade: **exchange and counterparty risk** (an exchange failure, a frozen withdrawal), which is not modelled (overview §5 item 5). Also not modelled: auto-deleveraging, rule changes such as funding caps, and the yield idle USDT could earn (an off sleeve earns 0; the hurdle stands for it). Funding-interval changes are in the data. Costs are Binance VIP 0 without the BNB discount; the re-pricing table shows the sensitivity.

## Notes

- Output-neutral code changes recorded in the registry: 0.
- Numbers in the tables are `stats.json` values; each report says where its figures come from.
