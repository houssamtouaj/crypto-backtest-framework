# Funding carry — exploratory row entry_20, holdout

- Row: `entry_20` (exploratory: reported, no say in the verdict (a DSR trial))
- Signal: 7-day trailing funding (annualised); on above 20%/yr, off below 0%/yr, otherwise the previous state
- Sleeve: perp leverage 2 (equal sleeves, spot notional L / (L + 1) of a sleeve)
- Pairs: BTCUSDT, ETHUSDT, BCHUSDT, LINKUSDT, ETCUSDT, LTCUSDT, XRPUSDT, EOSUSDT, TRXUSDT, XLMUSDT
- Period: 2024-01-01 to 2026-10-20 (1024 return days)
- Variant id: `62b36ca4f9f1460b7793dac7d19377c4ca55e963f666c4d5a8b87d18e909f6b8`
- Pre-registration hash: `a44eee47990843ff13c81da0dd0db23d82a0811fdb4896366482818a6e14b242`
- Code version: `7ce43816563f41f4f2aa7318d46e382e027e7a547652ac4d6b748454fa80f240` (git `e079ff023e2615af99dcc3efc96dbc5c3433d803`)

## Headline

**Descriptive (holdout; exploratory row `entry_20`, no say in the verdict): it would read positive below the hurdle.** V1 (net Sharpe above 0) passes, p = 0.0001; V2 (annualised net return above the 5%/yr hurdle) fails, p = 0.806. Annualised net return 3.50% (95% CI 0.88% to 7.04%); excess over the hurdle -1.37% (95% CI -4.00% to 2.16%): not proven: the CI contains 0.

**Caveat: this holdout is not fully unseen** (overview §4). No carry return, basis series or per-pair funding statistic had been computed on it in this repo, but the trend screen loaded these perp prices, the first strategy charged BTCUSDT and ETHUSDT funding over the whole window (its reports show mean funding per trade), and general market knowledge covers it. The 36-month forward test is the only fully unseen one.

| Headline | Value |
|---|---|
| V1: net Sharpe (annualised) | 1.53 |
| V1: 95% CI | 0.95 to 7.13 |
| V1: p (one-sided) | 0.0001 |
| V1 passes | yes |
| V2: annualised net return | 3.50% |
| V2: annualised net return, 95% CI | 0.88% to 7.04% |
| V2: hurdle | 5.0% |
| V2: excess over the hurdle | -1.37% |
| V2: excess, 95% CI | -4.00% to 2.16% |
| V2: p (one-sided) | 0.806 |
| V2 passes | no |
| V2 reading | not proven: the CI contains 0 |
| Reading | positive below the hurdle |
| Return days | 1024 |

## Rule

Edge iff V1 and V2 both pass, each one-sided at α = 0.05, unadjusted (overview CD8). V1: the portfolio's net daily Sharpe above 0; V2: the annualised net return (365 × mean daily return) above the 5%/yr hurdle (CD9), on the daily excess over its daily rate. Both by moving-block bootstrap, B = 10000, blocks of 30 days; 95% percentile CIs. V2 reads three ways (overview §3): proven above the hurdle (passes), not proven (the CI contains 0), proven below (the CI lies below 0).

Costs on every fill: perp taker 0.05%, spot taker 0.10%, slippage per leg (default 0.05%, BTCUSDT 0.02%, ETHUSDT 0.02%); funding from the data on the perp leg; liquidation fee 1.0%.

## Equity

The row against `always_on` (no filter: sleeves at perp leverage 2 held on every live day) over the same days, both from their own `stats.json`.

| Statistic | entry_20 | always_on |
|---|---|---|
| annualised return (365 × mean) | 3.50% | 4.56% |
| CAGR | 3.54% | 4.62% |
| annualised vol | 2.29% | 2.95% |
| Sharpe (annualised) | 1.53 | 1.55 |
| Sharpe, 95% CI | 0.95 to 7.13 | 1.12 to 8.74 |
| max drawdown | 0.34% | 0.55% |
| worst day | -0.34% | -0.55% |
| worst day: date | 2024-12-02 | 2024-12-02 |
| worst month | -0.01% | -0.01% |
| worst month: month | 2025-03 | 2026-02 |

![equity](figures/equity.png)

## Hurdles

V2 against other hurdles, descriptive (overview §10.2): the same bootstrap draws as the verdict's V2, so the 5.0% row is the verdict's.

| Hurdle | Excess | 95% CI | p (one-sided) | Passes | Reading |
|---|---|---|---|---|---|
| 0.0% | 3.50% | 0.88% to 7.04% | 0.0001 | yes | proven above the hurdle (V2 passes) |
| 3.0% | 0.55% | -2.07% to 4.08% | 0.378 | no | not proven: the CI contains 0 |
| 5.0% | -1.37% | -4.00% to 2.16% | 0.806 | no | not proven: the CI contains 0 |
| 8.0% | -4.19% | -6.81% to -0.66% | 0.989 | no | proven below the hurdle: the CI lies entirely below 0 |

![V2 bootstrap](figures/v2_bootstrap.png)

## Filter value and random timing

Computed for the portfolio primary only (Phase 5 §5.9).

## Return decomposition

Fractions of equity summed over days (each day's money over the previous day's equity), so funding + basis − fees − slippage is the net. Basis is the two legs' price P&L (a liquidation's loss to its price included); the rebalance and liquidation costs are a part of fees and slippage, not added to them (Phase 4 §4.7).

| Total | Value |
|---|---|
| funding | 6.28% |
| basis | 4.57% |
| fees | 0.79% |
| slippage | 0.22% |
| of which rebalances | 0.06% |
| of which liquidations | 0.46% |
| net (sum of daily returns) | 9.83% |
| compounded | 10.25% |
| share of live pair-days on | 27.2% |
| pair-days on | 2644 |
| pair-days live | 9723 |
| entries | 20 |

| Year | Days | Funding | Basis | Fees | Slippage | of which rebalances | of which liquidations | Net | Compounded | Share on | Entries |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2024 | 366 | 5.90% | 4.57% | 0.75% | 0.20% | 0.06% | 0.46% | 9.52% | 9.91% | 61.5% | 20 |
| 2025 | 365 | 0.37% | 0.01% | 0.05% | 0.03% | 0.00% | 0.00% | 0.31% | 0.31% | 11.5% | 0 |
| 2026 | 293 | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.0% | 0 |

![decomposition per year](figures/decomposition.png)

| Pair | Funding | Basis | Fees | Slippage | Net | Days on | Days live | Entries |
|---|---|---|---|---|---|---|---|---|
| BCHUSDT | 0.14% | 1.44% | 0.13% | 0.02% | 1.42% | 65 | 1024 | 2 |
| BTCUSDT | 0.75% | 0.01% | 0.04% | 0.01% | 0.71% | 373 | 1024 | 2 |
| EOSUSDT | 0.68% | 0.03% | 0.03% | 0.02% | 0.65% | 318 | 507 | 2 |
| ETCUSDT | 0.77% | 0.01% | 0.04% | 0.02% | 0.72% | 330 | 1024 | 2 |
| ETHUSDT | 0.80% | 0.01% | 0.04% | 0.01% | 0.76% | 308 | 1024 | 2 |
| LINKUSDT | 0.65% | 0.19% | 0.14% | 0.02% | 0.68% | 237 | 1024 | 2 |
| LTCUSDT | 0.96% | 0.00% | 0.02% | 0.02% | 0.92% | 431 | 1024 | 1 |
| TRXUSDT | 0.19% | 3.31% | 0.18% | 0.05% | 3.27% | 65 | 1024 | 4 |
| XLMUSDT | 0.54% | 0.02% | 0.04% | 0.02% | 0.51% | 181 | 1024 | 2 |
| XRPUSDT | 0.81% | -0.46% | 0.13% | 0.02% | 0.20% | 336 | 1024 | 1 |

## Funding and basis per pair

From the run folder's `signals.parquet`: the trailing funding the rule decides on and the basis at the close, per pair, with the days the pair was hedged at the close shaded.

![trailing funding per pair](figures/funding.png)

![basis per pair](figures/basis.png)

## Margin events

Rebalance: at the close the perp wallet held less than half the margin at the target leverage; both legs were cut back at the next fill. Liquidation: the day high reached the liquidation price; the short was bought back there with the liquidation fee and the spot leg sold at the next fill (CD7).

| Event | Count | Cost (USDT) | Cost (% of equity) |
|---|---|---|---|
| rebalances | 16 | 6.59 | 0.063% |
| liquidations | 4 | 48.65 | 0.462% |

| Event | Date | Pair | Liquidation price / qty cut | Cost (USDT) | Cost (% of equity) |
|---|---|---|---|---|---|
| liquidation | 2024-03-02 | BCHUSDT | 431.748164 | 11.55 | 0.114% |
| liquidation | 2024-12-02 | LINKUSDT | 24.424551 | 12.22 | 0.115% |
| liquidation | 2024-12-02 | XRPUSDT | 2.868206 | 12.22 | 0.115% |
| liquidation | 2024-12-03 | TRXUSDT | 0.318328 | 12.67 | 0.119% |
| rebalance | 2024-01-12 | ETCUSDT | 7.183983 | 0.52 | 0.005% |
| rebalance | 2024-02-06 | LINKUSDT | 9.163918 | 0.44 | 0.004% |
| rebalance | 2024-02-15 | BTCUSDT | 0.002705 | 0.27 | 0.003% |
| rebalance | 2024-02-16 | ETHUSDT | 0.051964 | 0.28 | 0.003% |
| rebalance | 2024-03-02 | ETHUSDT | 0.040596 | 0.26 | 0.003% |
| rebalance | 2024-03-03 | EOSUSDT | 155.310896 | 0.42 | 0.004% |
| rebalance | 2024-03-03 | LTCUSDT | 1.769263 | 0.41 | 0.004% |
| rebalance | 2024-03-04 | BTCUSDT | 0.002240 | 0.27 | 0.003% |
| rebalance | 2024-03-05 | ETCUSDT | 4.091891 | 0.37 | 0.004% |
| rebalance | 2024-11-16 | XRPUSDT | 270.142059 | 0.60 | 0.006% |
| rebalance | 2024-11-17 | XRPUSDT | 185.756013 | 0.53 | 0.005% |
| rebalance | 2024-11-23 | LINKUSDT | 9.115148 | 0.37 | 0.004% |
| rebalance | 2024-11-23 | XRPUSDT | 153.526673 | 0.59 | 0.006% |
| rebalance | 2024-12-01 | XRPUSDT | 97.458053 | 0.47 | 0.004% |
| rebalance | 2024-12-02 | LTCUSDT | 1.471835 | 0.45 | 0.004% |
| rebalance | 2024-12-07 | ETHUSDT | 0.043614 | 0.33 | 0.003% |

## Cost re-pricing

The row re-run at other costs, descriptive (overview §10.4); the verdict uses VIP 0. **No cell equals the verdict's costs**: the pre-registered slippage is per pair (default 0.05%, BTCUSDT 0.02%, ETHUSDT 0.02%), while a cell's slippage applies to every pair.

| Slippage | Spot taker | Perp taker | Annualised net return | Sharpe | Excess over the hurdle | 95% CI | p (V2) | V2 reading | Fees | Slippage cost | Entries |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.00% | 0.075% | 0.050% | 3.61% | 1.57 | -1.27% | -3.96% to 2.28% | 0.796 | not proven: the CI contains 0 | 0.72% | 0.00% | 20 |
| 0.00% | 0.100% | 0.050% | 3.58% | 1.56 | -1.29% | -3.96% to 2.24% | 0.8 | not proven: the CI contains 0 | 0.79% | 0.00% | 20 |
| 0.02% | 0.075% | 0.050% | 3.57% | 1.56 | -1.31% | -3.97% to 2.23% | 0.801 | not proven: the CI contains 0 | 0.72% | 0.10% | 20 |
| 0.02% | 0.100% | 0.050% | 3.55% | 1.55 | -1.33% | -3.98% to 2.19% | 0.805 | not proven: the CI contains 0 | 0.79% | 0.10% | 20 |
| 0.05% | 0.075% | 0.050% | 3.52% | 1.53 | -1.36% | -3.99% to 2.15% | 0.808 | not proven: the CI contains 0 | 0.72% | 0.26% | 20 |
| 0.05% | 0.100% | 0.050% | 3.49% | 1.52 | -1.39% | -4.00% to 2.11% | 0.812 | not proven: the CI contains 0 | 0.79% | 0.26% | 20 |
| 0.10% | 0.075% | 0.050% | 3.43% | 1.50 | -1.45% | -4.02% to 2.04% | 0.821 | not proven: the CI contains 0 | 0.72% | 0.51% | 20 |
| 0.10% | 0.100% | 0.050% | 3.40% | 1.49 | -1.48% | -4.03% to 2.00% | 0.825 | not proven: the CI contains 0 | 0.79% | 0.51% | 20 |

## Per-pair rows

Each pair alone as a one-pair portfolio under the `entry_20` row, from its own folder's `stats.json`; descriptive, no say in the verdict. A pair whose data ends before the period end (a delisting) is not run alone; the portfolio closes it by the delisting rule.

| Pair | Annualised net return | Excess over the hurdle | 95% CI (excess) | p (V2) | V2 reading | Sharpe | p (V1) | Reading | Share on |
|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | 2.53% | -2.35% | -3.88% to -0.47% | 0.991 | proven below the hurdle: the CI lies entirely below 0 | 6.37 | 0.0001 | positive below the hurdle | 36.4% |
| ETHUSDT | 2.73% | -2.15% | -3.79% to -0.16% | 0.982 | proven below the hurdle: the CI lies entirely below 0 | 6.67 | 0.0001 | positive below the hurdle | 30.1% |
| BCHUSDT | 5.23% | 0.35% | -4.89% to 10.63% | 0.363 | not proven: the CI contains 0 | 0.57 | 0.0318 | positive below the hurdle | 6.3% |
| LINKUSDT | 2.43% | -2.45% | -4.21% to -0.16% | 0.981 | proven below the hurdle: the CI lies entirely below 0 | 1.36 | 0.0012 | positive below the hurdle | 23.1% |
| ETCUSDT | 2.57% | -2.31% | -4.05% to -0.05% | 0.977 | proven below the hurdle: the CI lies entirely below 0 | 5.17 | 0.0001 | positive below the hurdle | 32.2% |
| LTCUSDT | 3.30% | -1.58% | -3.38% to 0.71% | 0.924 | not proven: the CI contains 0 | 6.76 | 0.0001 | positive below the hurdle | 42.1% |
| XRPUSDT | 0.73% | -4.15% | -9.05% to -0.64% | 0.991 | proven below the hurdle: the CI lies entirely below 0 | 0.21 | 0.354 | no edge | 32.8% |
| EOSUSDT | n/a | n/a | n/a | n/a | not run alone in this period (no stats.json) | n/a | n/a | n/a | n/a |
| TRXUSDT | 11.90% | 7.02% | -4.97% to 30.89% | 0.353 | not proven: the CI contains 0 | 0.57 | 0.0781 | no edge | 6.3% |
| XLMUSDT | 1.82% | -3.06% | -4.65% to -0.90% | 0.996 | proven below the hurdle: the CI lies entirely below 0 | 3.37 | 0.0032 | positive below the hurdle | 17.7% |

## Deflated Sharpe ratio

Computed for the portfolio primary only, with the seven pre-registered rows as trials.

## Unmodelled risks

A backtest cannot show the main risk of a carry trade: **exchange and counterparty risk** (an exchange failure, a frozen withdrawal), which is not modelled (overview §5 item 5). Also not modelled: auto-deleveraging, rule changes such as funding caps, and the yield idle USDT could earn (an off sleeve earns 0; the hurdle stands for it). Funding-interval changes are in the data. Costs are Binance VIP 0 without the BNB discount; the re-pricing table shows the sensitivity.

## Notes

- The V2 bootstrap figure redraws the verdict's replicates at report time with its own seed (`holdout/entry_20/v2`); their p and CI equal `stats.json`'s.
- Holdout `DONE`: status ok; failed 0, skipped 7 variants; output-neutral code change no.
- Numbers in the tables are `stats.json` values; the figures read the run folder's tables.
