# Funding carry — exploratory row entry_5, holdout

- Row: `entry_5` (exploratory: reported, no say in the verdict (a DSR trial))
- Signal: 7-day trailing funding (annualised); on above 5%/yr, off below 0%/yr, otherwise the previous state
- Sleeve: perp leverage 2 (equal sleeves, spot notional L / (L + 1) of a sleeve)
- Pairs: BTCUSDT, ETHUSDT, BCHUSDT, LINKUSDT, ETCUSDT, LTCUSDT, XRPUSDT, EOSUSDT, TRXUSDT, XLMUSDT
- Period: 2024-01-01 to 2026-10-20 (1024 return days)
- Variant id: `b0d714d8476b9152c37b25b0191ffa2e086688818162a2d7a71f509a8621c367`
- Pre-registration hash: `a44eee47990843ff13c81da0dd0db23d82a0811fdb4896366482818a6e14b242`
- Code version: `7ce43816563f41f4f2aa7318d46e382e027e7a547652ac4d6b748454fa80f240` (git `e079ff023e2615af99dcc3efc96dbc5c3433d803`)

## Headline

**Descriptive (holdout; exploratory row `entry_5`, no say in the verdict): it would read positive below the hurdle.** V1 (net Sharpe above 0) passes, p = 0.0001; V2 (annualised net return above the 5%/yr hurdle) fails, p = 0.568. Annualised net return 4.70% (95% CI 1.34% to 9.39%); excess over the hurdle -0.18% (95% CI -3.54% to 4.51%): not proven: the CI contains 0.

**Caveat: this holdout is not fully unseen** (overview §4). No carry return, basis series or per-pair funding statistic had been computed on it in this repo, but the trend screen loaded these perp prices, the first strategy charged BTCUSDT and ETHUSDT funding over the whole window (its reports show mean funding per trade), and general market knowledge covers it. The 36-month forward test is the only fully unseen one.

| Headline | Value |
|---|---|
| V1: net Sharpe (annualised) | 1.58 |
| V1: 95% CI | 1.09 to 5.14 |
| V1: p (one-sided) | 0.0001 |
| V1 passes | yes |
| V2: annualised net return | 4.70% |
| V2: annualised net return, 95% CI | 1.34% to 9.39% |
| V2: hurdle | 5.0% |
| V2: excess over the hurdle | -0.18% |
| V2: excess, 95% CI | -3.54% to 4.51% |
| V2: p (one-sided) | 0.568 |
| V2 passes | no |
| V2 reading | not proven: the CI contains 0 |
| Reading | positive below the hurdle |
| Return days | 1024 |

## Rule

Edge iff V1 and V2 both pass, each one-sided at α = 0.05, unadjusted (overview CD8). V1: the portfolio's net daily Sharpe above 0; V2: the annualised net return (365 × mean daily return) above the 5%/yr hurdle (CD9), on the daily excess over its daily rate. Both by moving-block bootstrap, B = 10000, blocks of 30 days; 95% percentile CIs. V2 reads three ways (overview §3): proven above the hurdle (passes), not proven (the CI contains 0), proven below (the CI lies below 0).

Costs on every fill: perp taker 0.05%, spot taker 0.10%, slippage per leg (default 0.05%, BTCUSDT 0.02%, ETHUSDT 0.02%); funding from the data on the perp leg; liquidation fee 1.0%.

## Equity

The row against `always_on` (no filter: sleeves at perp leverage 2 held on every live day) over the same days, both from their own `stats.json`.

| Statistic | entry_5 | always_on |
|---|---|---|
| annualised return (365 × mean) | 4.70% | 4.56% |
| CAGR | 4.76% | 4.62% |
| annualised vol | 2.97% | 2.95% |
| Sharpe (annualised) | 1.58 | 1.55 |
| Sharpe, 95% CI | 1.09 to 5.14 | 1.12 to 8.74 |
| max drawdown | 0.42% | 0.55% |
| worst day | -0.30% | -0.55% |
| worst day: date | 2024-12-02 | 2024-12-02 |
| worst month | -0.15% | -0.01% |
| worst month: month | 2025-03 | 2026-02 |

![equity](figures/equity.png)

## Hurdles

V2 against other hurdles, descriptive (overview §10.2): the same bootstrap draws as the verdict's V2, so the 5.0% row is the verdict's.

| Hurdle | Excess | 95% CI | p (one-sided) | Passes | Reading |
|---|---|---|---|---|---|
| 0.0% | 4.70% | 1.34% to 9.39% | 0.0001 | yes | proven above the hurdle (V2 passes) |
| 3.0% | 1.74% | -1.62% to 6.43% | 0.207 | no | not proven: the CI contains 0 |
| 5.0% | -0.18% | -3.54% to 4.51% | 0.568 | no | not proven: the CI contains 0 |
| 8.0% | -3.00% | -6.36% to 1.69% | 0.911 | no | not proven: the CI contains 0 |

![V2 bootstrap](figures/v2_bootstrap.png)

## Filter value and random timing

Computed for the portfolio primary only (Phase 5 §5.9).

## Return decomposition

Fractions of equity summed over days (each day's money over the previous day's equity), so funding + basis − fees − slippage is the net. Basis is the two legs' price P&L (a liquidation's loss to its price included); the rebalance and liquidation costs are a part of fees and slippage, not added to them (Phase 4 §4.7).

| Total | Value |
|---|---|
| funding | 9.99% |
| basis | 6.97% |
| fees | 2.54% |
| slippage | 1.24% |
| of which rebalances | 0.21% |
| of which liquidations | 0.58% |
| net (sum of daily returns) | 13.18% |
| compounded | 13.95% |
| share of live pair-days on | 64.1% |
| pair-days on | 6233 |
| pair-days live | 9723 |
| entries | 99 |

| Year | Days | Funding | Basis | Fees | Slippage | of which rebalances | of which liquidations | Net | Compounded | Share on | Entries |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2024 | 366 | 6.87% | 6.84% | 1.22% | 0.45% | 0.12% | 0.58% | 12.03% | 12.65% | 78.9% | 37 |
| 2025 | 365 | 2.02% | 0.06% | 0.78% | 0.47% | 0.05% | 0.00% | 0.83% | 0.83% | 61.6% | 38 |
| 2026 | 293 | 1.10% | 0.07% | 0.54% | 0.32% | 0.04% | 0.00% | 0.31% | 0.31% | 46.7% | 24 |

![decomposition per year](figures/decomposition.png)

| Pair | Funding | Basis | Fees | Slippage | Net | Days on | Days live | Entries |
|---|---|---|---|---|---|---|---|---|
| BCHUSDT | 0.41% | 2.27% | 0.34% | 0.16% | 2.17% | 283 | 1024 | 12 |
| BTCUSDT | 1.34% | 0.01% | 0.12% | 0.03% | 1.20% | 834 | 1024 | 6 |
| EOSUSDT | 0.81% | 0.07% | 0.12% | 0.08% | 0.68% | 424 | 507 | 6 |
| ETCUSDT | 1.33% | -0.01% | 0.18% | 0.12% | 1.02% | 804 | 1024 | 9 |
| ETHUSDT | 1.27% | 0.00% | 0.17% | 0.04% | 1.06% | 809 | 1024 | 8 |
| LINKUSDT | 1.27% | 0.17% | 0.28% | 0.12% | 1.05% | 840 | 1024 | 9 |
| LTCUSDT | 1.34% | 0.00% | 0.16% | 0.11% | 1.07% | 840 | 1024 | 8 |
| TRXUSDT | 0.39% | 4.14% | 0.51% | 0.27% | 3.75% | 336 | 1024 | 20 |
| XLMUSDT | 0.76% | 0.76% | 0.43% | 0.22% | 0.87% | 462 | 1024 | 16 |
| XRPUSDT | 1.06% | -0.45% | 0.22% | 0.08% | 0.31% | 601 | 1024 | 5 |

## Funding and basis per pair

From the run folder's `signals.parquet`: the trailing funding the rule decides on and the basis at the close, per pair, with the days the pair was hedged at the close shaded.

![trailing funding per pair](figures/funding.png)

![basis per pair](figures/basis.png)

## Margin events

Rebalance: at the close the perp wallet held less than half the margin at the target leverage; both legs were cut back at the next fill. Liquidation: the day high reached the liquidation price; the short was bought back there with the liquidation fee and the spot leg sold at the next fill (CD7).

| Event | Count | Cost (USDT) | Cost (% of equity) |
|---|---|---|---|
| rebalances | 53 | 23.33 | 0.213% |
| liquidations | 5 | 61.75 | 0.578% |

| Event | Date | Pair | Liquidation price / qty cut | Cost (USDT) | Cost (% of equity) |
|---|---|---|---|---|---|
| liquidation | 2024-03-02 | BCHUSDT | 403.408813 | 11.78 | 0.116% |
| liquidation | 2024-11-16 | XLMUSDT | 0.204175 | 12.34 | 0.115% |
| liquidation | 2024-12-02 | LINKUSDT | 24.424551 | 12.39 | 0.114% |
| liquidation | 2024-12-02 | XRPUSDT | 2.868206 | 12.22 | 0.113% |
| liquidation | 2024-12-03 | TRXUSDT | 0.300987 | 13.02 | 0.120% |
| rebalance | 2024-01-12 | ETCUSDT | 7.183983 | 0.52 | 0.005% |
| rebalance | 2024-02-06 | LINKUSDT | 9.163918 | 0.44 | 0.004% |
| rebalance | 2024-02-15 | BTCUSDT | 0.002705 | 0.27 | 0.003% |
| rebalance | 2024-02-16 | ETHUSDT | 0.051964 | 0.28 | 0.003% |
| rebalance | 2024-03-02 | ETHUSDT | 0.040596 | 0.26 | 0.003% |
| rebalance | 2024-03-03 | EOSUSDT | 155.310896 | 0.42 | 0.004% |
| rebalance | 2024-03-03 | LTCUSDT | 1.769263 | 0.41 | 0.004% |
| rebalance | 2024-03-04 | BTCUSDT | 0.002240 | 0.27 | 0.003% |
| rebalance | 2024-03-05 | ETCUSDT | 4.091891 | 0.37 | 0.004% |
| rebalance | 2024-07-18 | XLMUSDT | 1357.730112 | 0.37 | 0.004% |
| rebalance | 2024-09-28 | ETHUSDT | 0.054982 | 0.28 | 0.003% |
| rebalance | 2024-11-09 | BTCUSDT | 0.001928 | 0.28 | 0.003% |
| rebalance | 2024-11-09 | LINKUSDT | 10.331020 | 0.35 | 0.003% |
| rebalance | 2024-11-12 | ETCUSDT | 7.082126 | 0.42 | 0.004% |
| rebalance | 2024-11-12 | ETHUSDT | 0.049261 | 0.31 | 0.003% |
| rebalance | 2024-11-13 | XLMUSDT | 2081.207877 | 0.71 | 0.007% |
| rebalance | 2024-11-16 | XRPUSDT | 270.142059 | 0.60 | 0.006% |
| rebalance | 2024-11-17 | EOSUSDT | 388.490926 | 0.65 | 0.006% |
| rebalance | 2024-11-17 | XRPUSDT | 185.756013 | 0.53 | 0.005% |
| rebalance | 2024-11-20 | BTCUSDT | 0.001552 | 0.27 | 0.003% |
| rebalance | 2024-11-23 | LINKUSDT | 8.548280 | 0.35 | 0.003% |
| rebalance | 2024-11-23 | XRPUSDT | 153.526673 | 0.59 | 0.005% |
| rebalance | 2024-11-24 | ETCUSDT | 6.122121 | 0.46 | 0.004% |
| rebalance | 2024-11-25 | EOSUSDT | 201.883953 | 0.42 | 0.004% |
| rebalance | 2024-12-01 | BCHUSDT | 0.292744 | 0.39 | 0.004% |
| rebalance | 2024-12-01 | XRPUSDT | 97.458053 | 0.47 | 0.004% |
| rebalance | 2024-12-02 | EOSUSDT | 217.825143 | 0.61 | 0.006% |
| rebalance | 2024-12-02 | LTCUSDT | 1.471835 | 0.45 | 0.004% |
| rebalance | 2024-12-05 | ETCUSDT | 4.767551 | 0.45 | 0.004% |
| rebalance | 2024-12-06 | EOSUSDT | 108.532476 | 0.36 | 0.003% |
| rebalance | 2025-03-06 | BCHUSDT | 0.385764 | 0.37 | 0.003% |
| rebalance | 2025-05-09 | EOSUSDT | 219.118737 | 0.47 | 0.004% |
| rebalance | 2025-07-12 | XRPUSDT | 58.110312 | 0.40 | 0.004% |
| rebalance | 2025-07-16 | ETHUSDT | 0.048642 | 0.29 | 0.003% |
| rebalance | 2025-07-16 | LINKUSDT | 10.925393 | 0.44 | 0.004% |

The first 40 of 58 events (liquidations first); all are in `stats.json` and `events.parquet`.

## Cost re-pricing

The row re-run at other costs, descriptive (overview §10.4); the verdict uses VIP 0. **No cell equals the verdict's costs**: the pre-registered slippage is per pair (default 0.05%, BTCUSDT 0.02%, ETHUSDT 0.02%), while a cell's slippage applies to every pair.

| Slippage | Spot taker | Perp taker | Annualised net return | Sharpe | Excess over the hurdle | 95% CI | p (V2) | V2 reading | Fees | Slippage cost | Entries |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.00% | 0.075% | 0.050% | 5.26% | 1.77 | 0.38% | -3.03% to 5.01% | 0.451 | not proven: the CI contains 0 | 2.20% | 0.00% | 99 |
| 0.00% | 0.100% | 0.050% | 5.14% | 1.73 | 0.26% | -3.14% to 4.90% | 0.474 | not proven: the CI contains 0 | 2.54% | 0.00% | 99 |
| 0.02% | 0.075% | 0.050% | 5.07% | 1.71 | 0.19% | -3.22% to 4.83% | 0.489 | not proven: the CI contains 0 | 2.19% | 0.54% | 99 |
| 0.02% | 0.100% | 0.050% | 4.95% | 1.67 | 0.07% | -3.34% to 4.71% | 0.513 | not proven: the CI contains 0 | 2.54% | 0.54% | 99 |
| 0.05% | 0.075% | 0.050% | 4.78% | 1.61 | -0.10% | -3.50% to 4.55% | 0.545 | not proven: the CI contains 0 | 2.19% | 1.35% | 99 |
| 0.05% | 0.100% | 0.050% | 4.66% | 1.57 | -0.22% | -3.62% to 4.44% | 0.567 | not proven: the CI contains 0 | 2.54% | 1.35% | 99 |
| 0.10% | 0.075% | 0.050% | 4.30% | 1.45 | -0.58% | -3.98% to 4.13% | 0.626 | not proven: the CI contains 0 | 2.19% | 2.71% | 99 |
| 0.10% | 0.100% | 0.050% | 4.17% | 1.41 | -0.71% | -4.10% to 4.01% | 0.645 | not proven: the CI contains 0 | 2.54% | 2.71% | 99 |

## Per-pair rows

Each pair alone as a one-pair portfolio under the `entry_5` row, from its own folder's `stats.json`; descriptive, no say in the verdict. A pair whose data ends before the period end (a delisting) is not run alone; the portfolio closes it by the delisting rule.

| Pair | Annualised net return | Excess over the hurdle | 95% CI (excess) | p (V2) | V2 reading | Sharpe | p (V1) | Reading | Share on |
|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | 4.26% | -0.62% | -2.24% to 1.26% | 0.779 | not proven: the CI contains 0 | 8.95 | 0.0001 | positive below the hurdle | 81.4% |
| ETHUSDT | 3.78% | -1.10% | -2.80% to 0.92% | 0.871 | not proven: the CI contains 0 | 7.55 | 0.0001 | positive below the hurdle | 79.0% |
| BCHUSDT | 7.96% | 3.08% | -5.48% to 19.97% | 0.338 | not proven: the CI contains 0 | 0.57 | 0.164 | no edge | 27.6% |
| LINKUSDT | 3.65% | -1.23% | -3.20% to 1.08% | 0.867 | not proven: the CI contains 0 | 1.92 | 0.0001 | positive below the hurdle | 82.0% |
| ETCUSDT | 3.61% | -1.27% | -3.24% to 0.95% | 0.889 | not proven: the CI contains 0 | 4.05 | 0.0001 | positive below the hurdle | 78.5% |
| LTCUSDT | 3.86% | -1.02% | -2.96% to 1.14% | 0.84 | not proven: the CI contains 0 | 6.03 | 0.0001 | positive below the hurdle | 82.0% |
| XRPUSDT | 1.13% | -3.75% | -8.70% to -0.19% | 0.982 | proven below the hurdle: the CI lies entirely below 0 | 0.32 | 0.276 | no edge | 58.7% |
| EOSUSDT | n/a | n/a | n/a | n/a | not run alone in this period (no stats.json) | n/a | n/a | n/a | n/a |
| TRXUSDT | 13.67% | 8.79% | -6.71% to 38.83% | 0.355 | not proven: the CI contains 0 | 0.53 | 0.325 | no edge | 32.8% |
| XLMUSDT | 3.20% | -1.68% | -5.49% to 4.11% | 0.76 | not proven: the CI contains 0 | 0.73 | 0.0756 | no edge | 45.1% |

## Deflated Sharpe ratio

Computed for the portfolio primary only, with the seven pre-registered rows as trials.

## Unmodelled risks

A backtest cannot show the main risk of a carry trade: **exchange and counterparty risk** (an exchange failure, a frozen withdrawal), which is not modelled (overview §5 item 5). Also not modelled: auto-deleveraging, rule changes such as funding caps, and the yield idle USDT could earn (an off sleeve earns 0; the hurdle stands for it). Funding-interval changes are in the data. Costs are Binance VIP 0 without the BNB discount; the re-pricing table shows the sensitivity.

## Notes

- The V2 bootstrap figure redraws the verdict's replicates at report time with its own seed (`holdout/entry_5/v2`); their p and CI equal `stats.json`'s.
- Holdout `DONE`: status ok; failed 0, skipped 7 variants; output-neutral code change no.
- Numbers in the tables are `stats.json` values; the figures read the run folder's tables.
