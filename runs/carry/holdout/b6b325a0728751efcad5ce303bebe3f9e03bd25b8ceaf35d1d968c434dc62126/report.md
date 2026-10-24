# Funding carry — exploratory row always_on, holdout

- Row: `always_on` (exploratory: reported, no say in the verdict (a DSR trial))
- Signal: always on (no filter); 7-day trailing funding reported
- Sleeve: perp leverage 2 (equal sleeves, spot notional L / (L + 1) of a sleeve)
- Pairs: BTCUSDT, ETHUSDT, BCHUSDT, LINKUSDT, ETCUSDT, LTCUSDT, XRPUSDT, EOSUSDT, TRXUSDT, XLMUSDT
- Period: 2024-01-01 to 2026-10-20 (1024 return days)
- Variant id: `b6b325a0728751efcad5ce303bebe3f9e03bd25b8ceaf35d1d968c434dc62126`
- Pre-registration hash: `a44eee47990843ff13c81da0dd0db23d82a0811fdb4896366482818a6e14b242`
- Code version: `7ce43816563f41f4f2aa7318d46e382e027e7a547652ac4d6b748454fa80f240` (git `e079ff023e2615af99dcc3efc96dbc5c3433d803`)

## Headline

**Descriptive (holdout; exploratory row `always_on`, no say in the verdict): it would read positive below the hurdle.** V1 (net Sharpe above 0) passes, p = 0.0001; V2 (annualised net return above the 5%/yr hurdle) fails, p = 0.592. Annualised net return 4.56% (95% CI 1.72% to 8.87%); excess over the hurdle -0.32% (95% CI -3.16% to 3.99%): not proven: the CI contains 0.

**Caveat: this holdout is not fully unseen** (overview §4). No carry return, basis series or per-pair funding statistic had been computed on it in this repo, but the trend screen loaded these perp prices, the first strategy charged BTCUSDT and ETHUSDT funding over the whole window (its reports show mean funding per trade), and general market knowledge covers it. The 36-month forward test is the only fully unseen one.

| Headline | Value |
|---|---|
| V1: net Sharpe (annualised) | 1.55 |
| V1: 95% CI | 1.12 to 8.74 |
| V1: p (one-sided) | 0.0001 |
| V1 passes | yes |
| V2: annualised net return | 4.56% |
| V2: annualised net return, 95% CI | 1.72% to 8.87% |
| V2: hurdle | 5.0% |
| V2: excess over the hurdle | -0.32% |
| V2: excess, 95% CI | -3.16% to 3.99% |
| V2: p (one-sided) | 0.592 |
| V2 passes | no |
| V2 reading | not proven: the CI contains 0 |
| Reading | positive below the hurdle |
| Return days | 1024 |

## Rule

Edge iff V1 and V2 both pass, each one-sided at α = 0.05, unadjusted (overview CD8). V1: the portfolio's net daily Sharpe above 0; V2: the annualised net return (365 × mean daily return) above the 5%/yr hurdle (CD9), on the daily excess over its daily rate. Both by moving-block bootstrap, B = 10000, blocks of 30 days; 95% percentile CIs. V2 reads three ways (overview §3): proven above the hurdle (passes), not proven (the CI contains 0), proven below (the CI lies below 0).

Costs on every fill: perp taker 0.05%, spot taker 0.10%, slippage per leg (default 0.05%, BTCUSDT 0.02%, ETHUSDT 0.02%); funding from the data on the perp leg; liquidation fee 1.0%.

## Equity

| Statistic | always_on |
|---|---|
| annualised return (365 × mean) | 4.56% |
| CAGR | 4.62% |
| annualised vol | 2.95% |
| Sharpe (annualised) | 1.55 |
| Sharpe, 95% CI | 1.12 to 8.74 |
| max drawdown | 0.55% |
| worst day | -0.55% |
| worst day: date | 2024-12-02 |
| worst month | -0.01% |
| worst month: month | 2026-02 |

![equity](figures/equity.png)

## Hurdles

V2 against other hurdles, descriptive (overview §10.2): the same bootstrap draws as the verdict's V2, so the 5.0% row is the verdict's.

| Hurdle | Excess | 95% CI | p (one-sided) | Passes | Reading |
|---|---|---|---|---|---|
| 0.0% | 4.56% | 1.72% to 8.87% | 0.0001 | yes | proven above the hurdle (V2 passes) |
| 3.0% | 1.60% | -1.23% to 5.91% | 0.194 | no | not proven: the CI contains 0 |
| 5.0% | -0.32% | -3.16% to 3.99% | 0.592 | no | not proven: the CI contains 0 |
| 8.0% | -3.14% | -5.97% to 1.17% | 0.936 | no | not proven: the CI contains 0 |

![V2 bootstrap](figures/v2_bootstrap.png)

## Filter value and random timing

Computed for the portfolio primary only (Phase 5 §5.9).

## Return decomposition

Fractions of equity summed over days (each day's money over the previous day's equity), so funding + basis − fees − slippage is the net. Basis is the two legs' price P&L (a liquidation's loss to its price included); the rebalance and liquidation costs are a part of fees and slippage, not added to them (Phase 4 §4.7).

| Total | Value |
|---|---|
| funding | 8.29% |
| basis | 5.21% |
| fees | 0.60% |
| slippage | 0.12% |
| of which rebalances | 0.09% |
| of which liquidations | 0.46% |
| net (sum of daily returns) | 12.79% |
| compounded | 13.50% |
| share of live pair-days on | 68.7% |
| pair-days on | 6680 |
| pair-days live | 9723 |
| entries | 10 |

| Year | Days | Funding | Basis | Fees | Slippage | of which rebalances | of which liquidations | Net | Compounded | Share on | Entries |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2024 | 366 | 6.59% | 5.17% | 0.59% | 0.11% | 0.08% | 0.46% | 11.06% | 11.57% | 88.8% | 10 |
| 2025 | 365 | 1.34% | 0.03% | 0.01% | 0.00% | 0.01% | 0.00% | 1.36% | 1.37% | 57.4% | 0 |
| 2026 | 293 | 0.36% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.37% | 0.37% | 55.6% | 0 |

![decomposition per year](figures/decomposition.png)

| Pair | Funding | Basis | Fees | Slippage | Net | Days on | Days live | Entries |
|---|---|---|---|---|---|---|---|---|
| BCHUSDT | 0.12% | 0.38% | 0.12% | 0.01% | 0.36% | 61 | 1024 | 1 |
| BTCUSDT | 1.24% | 0.00% | 0.02% | 0.01% | 1.22% | 1024 | 1024 | 1 |
| EOSUSDT | 0.76% | 0.04% | 0.02% | 0.01% | 0.76% | 506 | 507 | 1 |
| ETCUSDT | 1.02% | 0.00% | 0.02% | 0.01% | 0.99% | 1024 | 1024 | 1 |
| ETHUSDT | 1.16% | 0.00% | 0.02% | 0.00% | 1.15% | 1024 | 1024 | 1 |
| LINKUSDT | 1.13% | 0.00% | 0.01% | 0.01% | 1.11% | 1024 | 1024 | 1 |
| LTCUSDT | 1.26% | 0.00% | 0.01% | 0.01% | 1.24% | 1024 | 1024 | 1 |
| TRXUSDT | 0.17% | 4.50% | 0.12% | 0.02% | 4.53% | 337 | 1024 | 1 |
| XLMUSDT | 0.61% | 0.74% | 0.12% | 0.01% | 1.22% | 320 | 1024 | 1 |
| XRPUSDT | 0.81% | -0.46% | 0.13% | 0.02% | 0.20% | 336 | 1024 | 1 |

## Funding and basis per pair

From the run folder's `signals.parquet`: the trailing funding the rule decides on and the basis at the close, per pair, with the days the pair was hedged at the close shaded.

![trailing funding per pair](figures/funding.png)

![basis per pair](figures/basis.png)

## Margin events

Rebalance: at the close the perp wallet held less than half the margin at the target leverage; both legs were cut back at the next fill. Liquidation: the day high reached the liquidation price; the short was bought back there with the liquidation fee and the spot leg sold at the next fill (CD7).

| Event | Count | Cost (USDT) | Cost (% of equity) |
|---|---|---|---|
| rebalances | 24 | 9.29 | 0.089% |
| liquidations | 4 | 48.25 | 0.458% |

| Event | Date | Pair | Liquidation price / qty cut | Cost (USDT) | Cost (% of equity) |
|---|---|---|---|---|---|
| liquidation | 2024-03-02 | BCHUSDT | 476.515822 | 11.56 | 0.114% |
| liquidation | 2024-11-16 | XLMUSDT | 0.204230 | 12.21 | 0.115% |
| liquidation | 2024-12-02 | XRPUSDT | 2.868206 | 12.22 | 0.114% |
| liquidation | 2024-12-03 | TRXUSDT | 0.287154 | 12.27 | 0.114% |
| rebalance | 2024-01-12 | ETCUSDT | 7.183983 | 0.52 | 0.005% |
| rebalance | 2024-02-06 | LINKUSDT | 9.163918 | 0.44 | 0.004% |
| rebalance | 2024-02-15 | BTCUSDT | 0.002705 | 0.27 | 0.003% |
| rebalance | 2024-02-16 | ETHUSDT | 0.051964 | 0.28 | 0.003% |
| rebalance | 2024-02-16 | TRXUSDT | 1048.123873 | 0.34 | 0.003% |
| rebalance | 2024-03-02 | BCHUSDT | 0.465360 | 0.37 | 0.004% |
| rebalance | 2024-03-02 | ETHUSDT | 0.040596 | 0.26 | 0.003% |
| rebalance | 2024-03-03 | EOSUSDT | 155.310896 | 0.42 | 0.004% |
| rebalance | 2024-03-03 | LTCUSDT | 1.769263 | 0.41 | 0.004% |
| rebalance | 2024-03-04 | BTCUSDT | 0.002240 | 0.27 | 0.003% |
| rebalance | 2024-03-05 | ETCUSDT | 4.091891 | 0.37 | 0.004% |
| rebalance | 2024-08-21 | TRXUSDT | 885.244177 | 0.36 | 0.003% |
| rebalance | 2024-11-11 | BTCUSDT | 0.001866 | 0.29 | 0.003% |
| rebalance | 2024-11-16 | TRXUSDT | 718.093898 | 0.34 | 0.003% |
| rebalance | 2024-11-16 | XRPUSDT | 270.142059 | 0.60 | 0.006% |
| rebalance | 2024-11-17 | XRPUSDT | 185.756013 | 0.53 | 0.005% |
| rebalance | 2024-11-22 | BTCUSDT | 0.001583 | 0.30 | 0.003% |
| rebalance | 2024-11-23 | XRPUSDT | 153.526673 | 0.59 | 0.005% |
| rebalance | 2024-12-01 | XRPUSDT | 97.458053 | 0.47 | 0.004% |
| rebalance | 2024-12-02 | LTCUSDT | 1.471835 | 0.45 | 0.004% |
| rebalance | 2024-12-03 | LINKUSDT | 6.323610 | 0.39 | 0.004% |
| rebalance | 2024-12-07 | EOSUSDT | 110.085205 | 0.38 | 0.003% |
| rebalance | 2025-08-13 | ETHUSDT | 0.037374 | 0.33 | 0.003% |
| rebalance | 2025-08-14 | BTCUSDT | 0.001315 | 0.31 | 0.003% |

## Cost re-pricing

The row re-run at other costs, descriptive (overview §10.4); the verdict uses VIP 0. **No cell equals the verdict's costs**: the pre-registered slippage is per pair (default 0.05%, BTCUSDT 0.02%, ETHUSDT 0.02%), while a cell's slippage applies to every pair.

| Slippage | Spot taker | Perp taker | Annualised net return | Sharpe | Excess over the hurdle | 95% CI | p (V2) | V2 reading | Fees | Slippage cost | Entries |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.00% | 0.075% | 0.050% | 4.61% | 1.56 | -0.27% | -3.14% to 3.90% | 0.597 | not proven: the CI contains 0 | 0.56% | 0.00% | 10 |
| 0.00% | 0.100% | 0.050% | 4.60% | 1.56 | -0.28% | -3.14% to 3.89% | 0.598 | not proven: the CI contains 0 | 0.60% | 0.00% | 10 |
| 0.02% | 0.075% | 0.050% | 4.59% | 1.56 | -0.29% | -3.14% to 3.88% | 0.599 | not proven: the CI contains 0 | 0.56% | 0.05% | 10 |
| 0.02% | 0.100% | 0.050% | 4.58% | 1.55 | -0.30% | -3.14% to 3.86% | 0.6 | not proven: the CI contains 0 | 0.60% | 0.05% | 10 |
| 0.05% | 0.075% | 0.050% | 4.57% | 1.55 | -0.31% | -3.15% to 3.85% | 0.601 | not proven: the CI contains 0 | 0.56% | 0.13% | 10 |
| 0.05% | 0.100% | 0.050% | 4.55% | 1.54 | -0.33% | -3.15% to 3.84% | 0.602 | not proven: the CI contains 0 | 0.60% | 0.13% | 10 |
| 0.10% | 0.075% | 0.050% | 4.52% | 1.53 | -0.36% | -3.16% to 3.81% | 0.605 | not proven: the CI contains 0 | 0.56% | 0.26% | 10 |
| 0.10% | 0.100% | 0.050% | 4.51% | 1.53 | -0.37% | -3.16% to 3.79% | 0.607 | not proven: the CI contains 0 | 0.60% | 0.26% | 10 |

## Per-pair rows

Each pair alone as a one-pair portfolio under the `always_on` row, from its own folder's `stats.json`; descriptive, no say in the verdict. A pair whose data ends before the period end (a delisting) is not run alone; the portfolio closes it by the delisting rule.

| Pair | Annualised net return | Excess over the hurdle | 95% CI (excess) | p (V2) | V2 reading | Sharpe | p (V1) | Reading | Share on |
|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | 4.39% | -0.49% | -1.98% to 1.31% | 0.744 | not proven: the CI contains 0 | 10.81 | 0.0001 | positive below the hurdle | 100.0% |
| ETHUSDT | 4.11% | -0.77% | -2.31% to 1.10% | 0.83 | not proven: the CI contains 0 | 10.07 | 0.0001 | positive below the hurdle | 100.0% |
| BCHUSDT | 1.32% | -3.56% | -4.88% to -0.53% | 0.982 | proven below the hurdle: the CI lies entirely below 0 | 0.48 | 0.0266 | positive below the hurdle | 6.0% |
| LINKUSDT | 4.00% | -0.88% | -2.45% to 1.19% | 0.821 | not proven: the CI contains 0 | 6.84 | 0.0001 | positive below the hurdle | 100.0% |
| ETCUSDT | 3.56% | -1.32% | -3.04% to 0.94% | 0.898 | not proven: the CI contains 0 | 6.43 | 0.0001 | positive below the hurdle | 100.0% |
| LTCUSDT | 4.44% | -0.44% | -2.07% to 1.63% | 0.7 | not proven: the CI contains 0 | 8.97 | 0.0001 | positive below the hurdle | 100.0% |
| XRPUSDT | 0.73% | -4.15% | -9.11% to -0.68% | 0.991 | proven below the hurdle: the CI lies entirely below 0 | 0.21 | 0.359 | no edge | 32.8% |
| EOSUSDT | n/a | n/a | n/a | n/a | not run alone in this period (no stats.json) | n/a | n/a | n/a | n/a |
| TRXUSDT | 17.29% | 12.41% | -5.53% to 47.19% | 0.35 | not proven: the CI contains 0 | 0.58 | 0.124 | no edge | 32.9% |
| XLMUSDT | 4.35% | -0.52% | -4.05% to 5.21% | 0.621 | not proven: the CI contains 0 | 1.01 | 0.0002 | positive below the hurdle | 31.2% |

## Deflated Sharpe ratio

Computed for the portfolio primary only, with the seven pre-registered rows as trials.

## Unmodelled risks

A backtest cannot show the main risk of a carry trade: **exchange and counterparty risk** (an exchange failure, a frozen withdrawal), which is not modelled (overview §5 item 5). Also not modelled: auto-deleveraging, rule changes such as funding caps, and the yield idle USDT could earn (an off sleeve earns 0; the hurdle stands for it). Funding-interval changes are in the data. Costs are Binance VIP 0 without the BNB discount; the re-pricing table shows the sensitivity.

## Notes

- The V2 bootstrap figure redraws the verdict's replicates at report time with its own seed (`holdout/always_on/v2`); their p and CI equal `stats.json`'s.
- Holdout `DONE`: status ok; failed 0, skipped 7 variants; output-neutral code change no.
- Numbers in the tables are `stats.json` values; the figures read the run folder's tables.
