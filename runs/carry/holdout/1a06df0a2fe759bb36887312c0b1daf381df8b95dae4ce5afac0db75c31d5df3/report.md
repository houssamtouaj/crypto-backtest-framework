# Funding carry — exploratory row lookback_14d, holdout

- Row: `lookback_14d` (exploratory: reported, no say in the verdict (a DSR trial))
- Signal: 14-day trailing funding (annualised); on above 10%/yr, off below 0%/yr, otherwise the previous state
- Sleeve: perp leverage 2 (equal sleeves, spot notional L / (L + 1) of a sleeve)
- Pairs: BTCUSDT, ETHUSDT, BCHUSDT, LINKUSDT, ETCUSDT, LTCUSDT, XRPUSDT, EOSUSDT, TRXUSDT, XLMUSDT
- Period: 2024-01-01 to 2026-10-20 (1024 return days)
- Variant id: `1a06df0a2fe759bb36887312c0b1daf381df8b95dae4ce5afac0db75c31d5df3`
- Pre-registration hash: `a44eee47990843ff13c81da0dd0db23d82a0811fdb4896366482818a6e14b242`
- Code version: `7ce43816563f41f4f2aa7318d46e382e027e7a547652ac4d6b748454fa80f240` (git `e079ff023e2615af99dcc3efc96dbc5c3433d803`)

## Headline

**Descriptive (holdout; exploratory row `lookback_14d`, no say in the verdict): it would read positive below the hurdle.** V1 (net Sharpe above 0) passes, p = 0.0001; V2 (annualised net return above the 5%/yr hurdle) fails, p = 0.66. Annualised net return 4.22% (95% CI 1.32% to 8.28%); excess over the hurdle -0.66% (95% CI -3.56% to 3.40%): not proven: the CI contains 0.

**Caveat: this holdout is not fully unseen** (overview §4). No carry return, basis series or per-pair funding statistic had been computed on it in this repo, but the trend screen loaded these perp prices, the first strategy charged BTCUSDT and ETHUSDT funding over the whole window (its reports show mean funding per trade), and general market knowledge covers it. The 36-month forward test is the only fully unseen one.

| Headline | Value |
|---|---|
| V1: net Sharpe (annualised) | 1.42 |
| V1: 95% CI | 0.91 to 8.12 |
| V1: p (one-sided) | 0.0001 |
| V1 passes | yes |
| V2: annualised net return | 4.22% |
| V2: annualised net return, 95% CI | 1.32% to 8.28% |
| V2: hurdle | 5.0% |
| V2: excess over the hurdle | -0.66% |
| V2: excess, 95% CI | -3.56% to 3.40% |
| V2: p (one-sided) | 0.66 |
| V2 passes | no |
| V2 reading | not proven: the CI contains 0 |
| Reading | positive below the hurdle |
| Return days | 1024 |

## Rule

Edge iff V1 and V2 both pass, each one-sided at α = 0.05, unadjusted (overview CD8). V1: the portfolio's net daily Sharpe above 0; V2: the annualised net return (365 × mean daily return) above the 5%/yr hurdle (CD9), on the daily excess over its daily rate. Both by moving-block bootstrap, B = 10000, blocks of 30 days; 95% percentile CIs. V2 reads three ways (overview §3): proven above the hurdle (passes), not proven (the CI contains 0), proven below (the CI lies below 0).

Costs on every fill: perp taker 0.05%, spot taker 0.10%, slippage per leg (default 0.05%, BTCUSDT 0.02%, ETHUSDT 0.02%); funding from the data on the perp leg; liquidation fee 1.0%.

## Equity

The row against `always_on` (no filter: sleeves at perp leverage 2 held on every live day) over the same days, both from their own `stats.json`.

| Statistic | lookback_14d | always_on |
|---|---|---|
| annualised return (365 × mean) | 4.22% | 4.56% |
| CAGR | 4.27% | 4.62% |
| annualised vol | 2.96% | 2.95% |
| Sharpe (annualised) | 1.42 | 1.55 |
| Sharpe, 95% CI | 0.91 to 8.12 | 1.12 to 8.74 |
| max drawdown | 1.21% | 0.55% |
| worst day | -1.21% | -0.55% |
| worst day: date | 2024-12-02 | 2024-12-02 |
| worst month | -0.02% | -0.01% |
| worst month: month | 2026-10 | 2026-02 |

![equity](figures/equity.png)

## Hurdles

V2 against other hurdles, descriptive (overview §10.2): the same bootstrap draws as the verdict's V2, so the 5.0% row is the verdict's.

| Hurdle | Excess | 95% CI | p (one-sided) | Passes | Reading |
|---|---|---|---|---|---|
| 0.0% | 4.22% | 1.32% to 8.28% | 0.0001 | yes | proven above the hurdle (V2 passes) |
| 3.0% | 1.27% | -1.64% to 5.33% | 0.26 | no | not proven: the CI contains 0 |
| 5.0% | -0.66% | -3.56% to 3.40% | 0.66 | no | not proven: the CI contains 0 |
| 8.0% | -3.47% | -6.38% to 0.59% | 0.956 | no | not proven: the CI contains 0 |

![V2 bootstrap](figures/v2_bootstrap.png)

## Filter value and random timing

Computed for the portfolio primary only (Phase 5 §5.9).

## Return decomposition

Fractions of equity summed over days (each day's money over the previous day's equity), so funding + basis − fees − slippage is the net. Basis is the two legs' price P&L (a liquidation's loss to its price included); the rebalance and liquidation costs are a part of fees and slippage, not added to them (Phase 4 §4.7).

| Total | Value |
|---|---|
| funding | 7.98% |
| basis | 5.30% |
| fees | 1.09% |
| slippage | 0.35% |
| of which rebalances | 0.11% |
| of which liquidations | 0.58% |
| net (sum of daily returns) | 11.85% |
| compounded | 12.44% |
| share of live pair-days on | 48.8% |
| pair-days on | 4748 |
| pair-days live | 9723 |
| entries | 29 |

| Year | Days | Funding | Basis | Fees | Slippage | of which rebalances | of which liquidations | Net | Compounded | Share on | Entries |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2024 | 366 | 6.45% | 5.27% | 0.89% | 0.22% | 0.11% | 0.58% | 10.62% | 11.07% | 71.6% | 20 |
| 2025 | 365 | 1.37% | 0.03% | 0.15% | 0.09% | 0.01% | 0.00% | 1.16% | 1.17% | 50.9% | 6 |
| 2026 | 293 | 0.16% | 0.00% | 0.06% | 0.04% | 0.00% | 0.00% | 0.07% | 0.07% | 14.5% | 3 |

![decomposition per year](figures/decomposition.png)

| Pair | Funding | Basis | Fees | Slippage | Net | Days on | Days live | Entries |
|---|---|---|---|---|---|---|---|---|
| BCHUSDT | 0.26% | 2.39% | 0.20% | 0.06% | 2.39% | 174 | 1024 | 5 |
| BTCUSDT | 0.99% | 0.01% | 0.07% | 0.02% | 0.91% | 510 | 1024 | 3 |
| EOSUSDT | 0.77% | 0.02% | 0.04% | 0.03% | 0.72% | 405 | 507 | 2 |
| ETCUSDT | 0.99% | 0.01% | 0.03% | 0.02% | 0.95% | 674 | 1024 | 2 |
| ETHUSDT | 1.08% | 0.00% | 0.04% | 0.01% | 1.03% | 700 | 1024 | 2 |
| LINKUSDT | 0.98% | -0.71% | 0.15% | 0.04% | 0.08% | 722 | 1024 | 3 |
| LTCUSDT | 1.17% | 0.00% | 0.02% | 0.01% | 1.13% | 702 | 1024 | 1 |
| TRXUSDT | 0.28% | 3.88% | 0.19% | 0.05% | 3.92% | 149 | 1024 | 4 |
| XLMUSDT | 0.55% | 0.15% | 0.20% | 0.07% | 0.43% | 244 | 1024 | 5 |
| XRPUSDT | 0.92% | -0.45% | 0.15% | 0.03% | 0.29% | 468 | 1024 | 2 |

## Funding and basis per pair

From the run folder's `signals.parquet`: the trailing funding the rule decides on and the basis at the close, per pair, with the days the pair was hedged at the close shaded.

![trailing funding per pair](figures/funding.png)

![basis per pair](figures/basis.png)

## Margin events

Rebalance: at the close the perp wallet held less than half the margin at the target leverage; both legs were cut back at the next fill. Liquidation: the day high reached the liquidation price; the short was bought back there with the liquidation fee and the spot leg sold at the next fill (CD7).

| Event | Count | Cost (USDT) | Cost (% of equity) |
|---|---|---|---|
| rebalances | 28 | 12.03 | 0.113% |
| liquidations | 5 | 61.37 | 0.576% |

| Event | Date | Pair | Liquidation price / qty cut | Cost (USDT) | Cost (% of equity) |
|---|---|---|---|---|---|
| liquidation | 2024-03-02 | BCHUSDT | 397.581739 | 11.73 | 0.115% |
| liquidation | 2024-11-16 | XLMUSDT | 0.216275 | 12.25 | 0.114% |
| liquidation | 2024-12-02 | LINKUSDT | 26.802496 | 12.24 | 0.114% |
| liquidation | 2024-12-02 | XRPUSDT | 2.868206 | 12.22 | 0.113% |
| liquidation | 2024-12-03 | TRXUSDT | 0.306340 | 12.93 | 0.120% |
| rebalance | 2024-01-12 | ETCUSDT | 7.183983 | 0.52 | 0.005% |
| rebalance | 2024-02-06 | LINKUSDT | 9.163918 | 0.44 | 0.004% |
| rebalance | 2024-02-15 | BTCUSDT | 0.002705 | 0.27 | 0.003% |
| rebalance | 2024-02-16 | ETHUSDT | 0.051964 | 0.28 | 0.003% |
| rebalance | 2024-03-02 | ETHUSDT | 0.040596 | 0.26 | 0.003% |
| rebalance | 2024-03-03 | EOSUSDT | 155.310896 | 0.42 | 0.004% |
| rebalance | 2024-03-03 | LTCUSDT | 1.769263 | 0.41 | 0.004% |
| rebalance | 2024-03-04 | BTCUSDT | 0.002240 | 0.27 | 0.003% |
| rebalance | 2024-03-05 | ETCUSDT | 4.091891 | 0.37 | 0.004% |
| rebalance | 2024-11-10 | ETHUSDT | 0.053998 | 0.32 | 0.003% |
| rebalance | 2024-11-11 | EOSUSDT | 363.798563 | 0.52 | 0.005% |
| rebalance | 2024-11-12 | BTCUSDT | 0.002332 | 0.39 | 0.004% |
| rebalance | 2024-11-12 | LINKUSDT | 10.664133 | 0.39 | 0.004% |
| rebalance | 2024-11-12 | XLMUSDT | 1370.468511 | 0.39 | 0.004% |
| rebalance | 2024-11-16 | XLMUSDT | 1432.701224 | 0.52 | 0.005% |
| rebalance | 2024-11-16 | XRPUSDT | 270.142059 | 0.60 | 0.006% |
| rebalance | 2024-11-17 | XRPUSDT | 185.756013 | 0.53 | 0.005% |
| rebalance | 2024-11-23 | EOSUSDT | 300.789310 | 0.57 | 0.005% |
| rebalance | 2024-11-23 | XRPUSDT | 153.526673 | 0.59 | 0.005% |
| rebalance | 2024-11-25 | LINKUSDT | 8.901220 | 0.40 | 0.004% |
| rebalance | 2024-11-30 | EOSUSDT | 204.699886 | 0.50 | 0.005% |
| rebalance | 2024-12-01 | XRPUSDT | 97.458053 | 0.47 | 0.004% |
| rebalance | 2024-12-02 | LTCUSDT | 1.471835 | 0.45 | 0.004% |
| rebalance | 2024-12-04 | EOSUSDT | 211.146549 | 0.72 | 0.006% |
| rebalance | 2024-12-05 | ETHUSDT | 0.040139 | 0.29 | 0.003% |
| rebalance | 2024-12-07 | BCHUSDT | 0.242711 | 0.38 | 0.003% |
| rebalance | 2025-08-13 | LINKUSDT | 7.838781 | 0.46 | 0.004% |
| rebalance | 2025-08-14 | ETHUSDT | 0.033065 | 0.30 | 0.003% |

## Cost re-pricing

The row re-run at other costs, descriptive (overview §10.4); the verdict uses VIP 0. **No cell equals the verdict's costs**: the pre-registered slippage is per pair (default 0.05%, BTCUSDT 0.02%, ETHUSDT 0.02%), while a cell's slippage applies to every pair.

| Slippage | Spot taker | Perp taker | Annualised net return | Sharpe | Excess over the hurdle | 95% CI | p (V2) | V2 reading | Fees | Slippage cost | Entries |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.00% | 0.075% | 0.050% | 4.38% | 1.48 | -0.50% | -3.44% to 3.64% | 0.636 | not proven: the CI contains 0 | 0.99% | 0.00% | 29 |
| 0.00% | 0.100% | 0.050% | 4.35% | 1.46 | -0.53% | -3.47% to 3.60% | 0.642 | not proven: the CI contains 0 | 1.09% | 0.00% | 29 |
| 0.02% | 0.075% | 0.050% | 4.33% | 1.46 | -0.55% | -3.48% to 3.59% | 0.644 | not proven: the CI contains 0 | 0.99% | 0.16% | 29 |
| 0.02% | 0.100% | 0.050% | 4.29% | 1.45 | -0.59% | -3.50% to 3.55% | 0.649 | not proven: the CI contains 0 | 1.09% | 0.16% | 29 |
| 0.05% | 0.075% | 0.050% | 4.24% | 1.43 | -0.64% | -3.53% to 3.50% | 0.656 | not proven: the CI contains 0 | 0.99% | 0.39% | 29 |
| 0.05% | 0.100% | 0.050% | 4.21% | 1.42 | -0.67% | -3.55% to 3.45% | 0.662 | not proven: the CI contains 0 | 1.09% | 0.39% | 29 |
| 0.10% | 0.075% | 0.050% | 4.10% | 1.39 | -0.78% | -3.62% to 3.35% | 0.677 | not proven: the CI contains 0 | 0.99% | 0.78% | 29 |
| 0.10% | 0.100% | 0.050% | 4.07% | 1.37 | -0.81% | -3.65% to 3.31% | 0.683 | not proven: the CI contains 0 | 1.09% | 0.78% | 29 |

## Per-pair rows

Each pair alone as a one-pair portfolio under the `lookback_14d` row, from its own folder's `stats.json`; descriptive, no say in the verdict. A pair whose data ends before the period end (a delisting) is not run alone; the portfolio closes it by the delisting rule.

| Pair | Annualised net return | Excess over the hurdle | 95% CI (excess) | p (V2) | V2 reading | Sharpe | p (V1) | Reading | Share on |
|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | 3.27% | -1.61% | -3.26% to 0.35% | 0.952 | not proven: the CI contains 0 | 7.51 | 0.0001 | positive below the hurdle | 49.8% |
| ETHUSDT | 3.70% | -1.17% | -2.80% to 0.77% | 0.898 | not proven: the CI contains 0 | 8.69 | 0.0001 | positive below the hurdle | 68.4% |
| BCHUSDT | 8.83% | 3.95% | -5.12% to 21.73% | 0.354 | not proven: the CI contains 0 | 0.59 | 0.0848 | no edge | 17.0% |
| LINKUSDT | 0.18% | -4.70% | -11.58% to -0.33% | 0.985 | proven below the hurdle: the CI lies entirely below 0 | 0.04 | 0.445 | no edge | 70.5% |
| ETCUSDT | 3.42% | -1.46% | -3.20% to 0.77% | 0.912 | not proven: the CI contains 0 | 6.11 | 0.0001 | positive below the hurdle | 65.8% |
| LTCUSDT | 4.08% | -0.80% | -2.51% to 1.35% | 0.792 | not proven: the CI contains 0 | 8.21 | 0.0001 | positive below the hurdle | 68.6% |
| XRPUSDT | 1.03% | -3.85% | -8.78% to -0.31% | 0.986 | proven below the hurdle: the CI lies entirely below 0 | 0.29 | 0.301 | no edge | 45.7% |
| EOSUSDT | n/a | n/a | n/a | n/a | not run alone in this period (no stats.json) | n/a | n/a | n/a | n/a |
| TRXUSDT | 14.12% | 9.24% | -4.95% to 36.96% | 0.348 | not proven: the CI contains 0 | 0.58 | 0.0376 | positive below the hurdle | 14.6% |
| XLMUSDT | 1.56% | -3.32% | -4.90% to -1.03% | 0.996 | proven below the hurdle: the CI lies entirely below 0 | 1.51 | 0.0289 | positive below the hurdle | 23.8% |

## Deflated Sharpe ratio

Computed for the portfolio primary only, with the seven pre-registered rows as trials.

## Unmodelled risks

A backtest cannot show the main risk of a carry trade: **exchange and counterparty risk** (an exchange failure, a frozen withdrawal), which is not modelled (overview §5 item 5). Also not modelled: auto-deleveraging, rule changes such as funding caps, and the yield idle USDT could earn (an off sleeve earns 0; the hurdle stands for it). Funding-interval changes are in the data. Costs are Binance VIP 0 without the BNB discount; the re-pricing table shows the sensitivity.

## Notes

- The V2 bootstrap figure redraws the verdict's replicates at report time with its own seed (`holdout/lookback_14d/v2`); their p and CI equal `stats.json`'s.
- Holdout `DONE`: status ok; failed 0, skipped 7 variants; output-neutral code change no.
- Numbers in the tables are `stats.json` values; the figures read the run folder's tables.
