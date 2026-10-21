# Summary: does the first 15-minute bullish order block have an edge?

Pairs BTCUSDT, ETHUSDT, SOLUSDT; sessions utc, ny, london; in-sample 2020-01-01 to 2025-12-31. Every number below comes from `results.parquet` or the variants' `stats.json` unless marked as computed at report time. Per-cell detail is in each variant's `report.md`. R is a trade's planned risk (entry minus stop); net R is its result after fees, slippage and funding, in units of R.

## Answer

In-sample, after the Holm correction, none of the 9 primary cells beats random timing, random days or buy-and-hold. On the holdout, after the Holm correction, none of the 9 primary cells beats random timing, random days or buy-and-hold. Without the correction, 2 more in-sample comparisons (cell × benchmark) would pass at raw p < 0.05; about 1.4 of the 27 comparisons would pass by chance alone if there were no edge. The largest caveat is costs: averaged over the primary cells (computed at report time), a trade costs 0.21 R against a mean gross R of 0.01 before costs, and 0.7% of trades cost more than 1 R.

## 1. Verdict

The pre-registered rule (beats_baseline_a: holm-adjusted one-sided p_a < alpha; beats_baseline_b: holm-adjusted one-sided p_b < alpha; beats_buy_and_hold: holm-adjusted one-sided p_bh < alpha and sharpe_diff > 0; α = 0.05). p-values are one-sided; Holm adjusts each benchmark over the primary family of one period. "Raw p would pass" lists the benchmarks whose unadjusted p is below α, so the effect of the correction is visible. A: random entry time in the strategy's own sessions; B: random sessions and entry times; B&H: vol-scaled buy-and-hold.

| Cell | Period | n | Mean net R | 95% CI | p_A | p_A (Holm) | p_B | p_B (Holm) | p_BH | p_BH (Holm) | Sharpe diff | Beats A | Beats B | Beats B&H | Raw p would pass |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT / UTC | in-sample | 1848 | -0.341 | -0.410 to -0.272 | 0.507 | 1 | 0.792 | 1 | 1 | 1 | -4.65 | no | no | no | none |
| BTCUSDT / NY | in-sample | 555 | -0.153 | -0.279 to -0.027 | 0.936 | 1 | 0.307 | 1 | 1 | 1 | -1.76 | no | no | no | none |
| BTCUSDT / London | in-sample | 935 | -0.327 | -0.422 to -0.232 | 0.932 | 1 | 0.816 | 1 | 1 | 1 | -3.47 | no | no | no | none |
| ETHUSDT / UTC | in-sample | 1865 | -0.209 | -0.276 to -0.142 | 0.0848 | 0.678 | 0.415 | 1 | 1 | 1 | -3.32 | no | no | no | none |
| ETHUSDT / NY | in-sample | 572 | -0.140 | -0.259 to -0.018 | 0.898 | 1 | 0.54 | 1 | 1 | 1 | -1.81 | no | no | no | none |
| ETHUSDT / London | in-sample | 925 | -0.129 | -0.223 to -0.032 | 0.126 | 0.882 | 0.0498 | 0.448 | 1 | 1 | -1.92 | no | no | no | B |
| SOLUSDT / UTC | in-sample | 1639 | -0.152 | -0.224 to -0.081 | 0.0436 | 0.392 | 0.23 | 1 | 1 | 1 | -2.96 | no | no | no | A |
| SOLUSDT / NY | in-sample | 521 | -0.114 | -0.237 to 0.015 | 0.805 | 1 | 0.272 | 1 | 1 | 1 | -1.94 | no | no | no | none |
| SOLUSDT / London | in-sample | 786 | -0.229 | -0.331 to -0.125 | 0.961 | 1 | 0.731 | 1 | 1 | 1 | -3.05 | no | no | no | none |
| BTCUSDT / UTC | holdout | 248 | -0.504 | -0.694 to -0.311 | 0.862 | 1 | 0.813 | 1 | 1 | 1 | -5.68 | no | no | no | none |
| BTCUSDT / NY | holdout | 78 | -0.369 | -0.679 to -0.047 | 0.952 | 1 | 0.816 | 1 | 0.948 | 1 | -2.37 | no | no | no | none |
| BTCUSDT / London | holdout | 121 | -0.463 | -0.720 to -0.201 | 0.92 | 1 | 0.808 | 1 | 0.997 | 1 | -3.74 | no | no | no | none |
| ETHUSDT / UTC | holdout | 254 | -0.400 | -0.588 to -0.214 | 0.854 | 1 | 0.864 | 1 | 1 | 1 | -4.43 | no | no | no | none |
| ETHUSDT / NY | holdout | 74 | -0.104 | -0.437 to 0.231 | 0.531 | 1 | 0.372 | 1 | 0.663 | 1 | -0.51 | no | no | no | none |
| ETHUSDT / London | holdout | 126 | -0.169 | -0.436 to 0.104 | 0.404 | 1 | 0.204 | 1 | 0.858 | 1 | -1.23 | no | no | no | none |
| SOLUSDT / UTC | holdout | 252 | -0.335 | -0.525 to -0.143 | 0.466 | 1 | 0.553 | 1 | 0.999 | 1 | -3.78 | no | no | no | none |
| SOLUSDT / NY | holdout | 78 | 0.108 | -0.228 to 0.452 | 0.197 | 1 | 0.0538 | 0.484 | 0.349 | 1 | 0.66 | no | no | no | none |
| SOLUSDT / London | holdout | 112 | -0.406 | -0.672 to -0.131 | 0.927 | 1 | 0.704 | 1 | 0.992 | 1 | -3.17 | no | no | no | none |

## 2. Per cell

- BTCUSDT / UTC, in-sample: 1848 trades, mean net R −0.34 (95% CI −0.41 to −0.27). Does not beat random timing (adjusted p = 1), does not beat random days (adjusted p = 1), does not beat buy-and-hold (Sharpe difference −4.65, adjusted p = 1).
- BTCUSDT / NY, in-sample: 555 trades, mean net R −0.15 (95% CI −0.28 to −0.03). Does not beat random timing (adjusted p = 1), does not beat random days (adjusted p = 1), does not beat buy-and-hold (Sharpe difference −1.76, adjusted p = 1).
- BTCUSDT / London, in-sample: 935 trades, mean net R −0.33 (95% CI −0.42 to −0.23). Does not beat random timing (adjusted p = 1), does not beat random days (adjusted p = 1), does not beat buy-and-hold (Sharpe difference −3.47, adjusted p = 1).
- ETHUSDT / UTC, in-sample: 1865 trades, mean net R −0.21 (95% CI −0.28 to −0.14). Does not beat random timing (adjusted p = 0.678), does not beat random days (adjusted p = 1), does not beat buy-and-hold (Sharpe difference −3.32, adjusted p = 1).
- ETHUSDT / NY, in-sample: 572 trades, mean net R −0.14 (95% CI −0.26 to −0.02). Does not beat random timing (adjusted p = 1), does not beat random days (adjusted p = 1), does not beat buy-and-hold (Sharpe difference −1.81, adjusted p = 1).
- ETHUSDT / London, in-sample: 925 trades, mean net R −0.13 (95% CI −0.22 to −0.03). Does not beat random timing (adjusted p = 0.882), does not beat random days (adjusted p = 0.448; raw p = 0.0498 would pass without the correction), does not beat buy-and-hold (Sharpe difference −1.92, adjusted p = 1).
- SOLUSDT / UTC, in-sample: 1639 trades, mean net R −0.15 (95% CI −0.22 to −0.08). Does not beat random timing (adjusted p = 0.392; raw p = 0.0436 would pass without the correction), does not beat random days (adjusted p = 1), does not beat buy-and-hold (Sharpe difference −2.96, adjusted p = 1).
- SOLUSDT / NY, in-sample: 521 trades, mean net R −0.11 (95% CI −0.24 to 0.02). Does not beat random timing (adjusted p = 1), does not beat random days (adjusted p = 1), does not beat buy-and-hold (Sharpe difference −1.94, adjusted p = 1).
- SOLUSDT / London, in-sample: 786 trades, mean net R −0.23 (95% CI −0.33 to −0.13). Does not beat random timing (adjusted p = 1), does not beat random days (adjusted p = 1), does not beat buy-and-hold (Sharpe difference −3.05, adjusted p = 1).
- BTCUSDT / UTC, holdout: 248 trades, mean net R −0.50 (95% CI −0.69 to −0.31). Does not beat random timing (adjusted p = 1), does not beat random days (adjusted p = 1), does not beat buy-and-hold (Sharpe difference −5.68, adjusted p = 1).
- BTCUSDT / NY, holdout: 78 trades, mean net R −0.37 (95% CI −0.68 to −0.05). Does not beat random timing (adjusted p = 1), does not beat random days (adjusted p = 1), does not beat buy-and-hold (Sharpe difference −2.37, adjusted p = 1).
- BTCUSDT / London, holdout: 121 trades, mean net R −0.46 (95% CI −0.72 to −0.20). Does not beat random timing (adjusted p = 1), does not beat random days (adjusted p = 1), does not beat buy-and-hold (Sharpe difference −3.74, adjusted p = 1).
- ETHUSDT / UTC, holdout: 254 trades, mean net R −0.40 (95% CI −0.59 to −0.21). Does not beat random timing (adjusted p = 1), does not beat random days (adjusted p = 1), does not beat buy-and-hold (Sharpe difference −4.43, adjusted p = 1).
- ETHUSDT / NY, holdout: 74 trades, mean net R −0.10 (95% CI −0.44 to 0.23). Does not beat random timing (adjusted p = 1), does not beat random days (adjusted p = 1), does not beat buy-and-hold (Sharpe difference −0.51, adjusted p = 1).
- ETHUSDT / London, holdout: 126 trades, mean net R −0.17 (95% CI −0.44 to 0.10). Does not beat random timing (adjusted p = 1), does not beat random days (adjusted p = 1), does not beat buy-and-hold (Sharpe difference −1.23, adjusted p = 1).
- SOLUSDT / UTC, holdout: 252 trades, mean net R −0.34 (95% CI −0.52 to −0.14). Does not beat random timing (adjusted p = 1), does not beat random days (adjusted p = 1), does not beat buy-and-hold (Sharpe difference −3.78, adjusted p = 1).
- SOLUSDT / NY, holdout: 78 trades, mean net R 0.11 (95% CI −0.23 to 0.45). Does not beat random timing (adjusted p = 1), does not beat random days (adjusted p = 0.484), does not beat buy-and-hold (Sharpe difference 0.66, adjusted p = 1).
- SOLUSDT / London, holdout: 112 trades, mean net R −0.41 (95% CI −0.67 to −0.13). Does not beat random timing (adjusted p = 1), does not beat random days (adjusted p = 1), does not beat buy-and-hold (Sharpe difference −3.17, adjusted p = 1).

## 3. Holdout power

The smallest mean net R the holdout could detect with 80% power in a one-sided test of mean net R > 0 (normal approximation), given the in-sample standard deviation of net R: (z(1−α) + z(0.8)) · σ / √n, at α = 0.05 and at α/9 = 0.005556, the bar the best cell must clear under Holm. Computed at report time. It is an approximation: the verdict compares the cell with the random baselines, whose mean is not 0, rather than with 0.

| Cell | In-sample std of net R | Holdout n | MDE at α = 0.05 | MDE at α/9 | Holdout mean net R |
|---|---|---|---|---|---|
| BTCUSDT / UTC | 1.535 | 248 | 0.242 | 0.329 | -0.504 |
| BTCUSDT / NY | 1.497 | 78 | 0.421 | 0.573 | -0.369 |
| BTCUSDT / London | 1.500 | 121 | 0.339 | 0.461 | -0.463 |
| ETHUSDT / UTC | 1.508 | 254 | 0.235 | 0.320 | -0.400 |
| ETHUSDT / NY | 1.469 | 74 | 0.425 | 0.577 | -0.104 |
| ETHUSDT / London | 1.514 | 126 | 0.335 | 0.456 | -0.169 |
| SOLUSDT / UTC | 1.492 | 252 | 0.234 | 0.318 | -0.335 |
| SOLUSDT / NY | 1.473 | 78 | 0.415 | 0.564 | 0.108 |
| SOLUSDT / London | 1.461 | 112 | 0.343 | 0.467 | -0.406 |

A holdout that does not pass rules out little below the MDE: edges smaller than the MDE would often go undetected.

## 4. Cross-session context

The three session windows differ in length (UTC 24 h, NY 6.5 h, London 8.5 h, D1), so the number of opportunities differs as much as any edge. The shared-trade fraction is the share of a variant's trades whose candidate candle another session variant of the same pair also traded (computed at report time from the trade tables): the session variants are not independent tests.

| Cell | Sessions | Blocks seen | Orders | Fills | Trades (R subset) | Shared with any other session | Shared, by session |
|---|---|---|---|---|---|---|---|
| BTCUSDT / UTC | 2192 | 10665 | 2133 | 1848 | 1848 | 17.3% | NY 2.7%, London 15.2% |
| BTCUSDT / NY | 1566 | 10665 | 852 | 555 | 555 | 14.8% | UTC 8.8%, London 7.7% |
| BTCUSDT / London | 1566 | 10665 | 1206 | 935 | 935 | 33.5% | UTC 29.9%, NY 4.6% |
| ETHUSDT / UTC | 2192 | 10592 | 2135 | 1865 | 1865 | 17.6% | NY 3.1%, London 15.2% |
| ETHUSDT / NY | 1566 | 10592 | 871 | 572 | 572 | 18.2% | UTC 10.1%, London 10.3% |
| ETHUSDT / London | 1566 | 10592 | 1194 | 925 | 925 | 35.6% | UTC 30.6%, NY 6.4% |
| SOLUSDT / UTC | 1935 | 9247 | 1887 | 1639 | 1639 | 19.5% | NY 5.1%, London 15.7% |
| SOLUSDT / NY | 1383 | 9247 | 766 | 521 | 521 | 20.5% | UTC 15.9%, London 8.6% |
| SOLUSDT / London | 1383 | 9247 | 1010 | 786 | 786 | 35.8% | UTC 32.7%, NY 5.7% |

## 5. Cost sensitivity

Mean net R of each primary re-priced at other costs (s: slippage on stop and time exits, m: maker fee; taker fixed; D13). Descriptive only: the verdict uses each pair's registered costs (last column).

| Mean net R | s 0.00% / m 0.00% | s 0.00% / m 0.02% | s 0.02% / m 0.00% | s 0.02% / m 0.02% | s 0.05% / m 0.00% | s 0.05% / m 0.02% | s 0.10% / m 0.00% | s 0.10% / m 0.02% | Registered costs |
|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT / UTC | -0.166 | -0.282 | -0.225 | -0.341 | -0.314 | -0.430 | -0.462 | -0.578 | s 0.02% / m 0.02% |
| BTCUSDT / NY | -0.047 | -0.118 | -0.082 | -0.153 | -0.133 | -0.204 | -0.219 | -0.290 | s 0.02% / m 0.02% |
| BTCUSDT / London | -0.173 | -0.275 | -0.225 | -0.327 | -0.303 | -0.405 | -0.433 | -0.535 | s 0.02% / m 0.02% |
| ETHUSDT / UTC | -0.083 | -0.168 | -0.125 | -0.209 | -0.187 | -0.271 | -0.290 | -0.375 | s 0.02% / m 0.02% |
| ETHUSDT / NY | -0.059 | -0.113 | -0.086 | -0.140 | -0.126 | -0.181 | -0.193 | -0.248 | s 0.02% / m 0.02% |
| ETHUSDT / London | -0.016 | -0.093 | -0.052 | -0.129 | -0.107 | -0.183 | -0.197 | -0.274 | s 0.02% / m 0.02% |
| SOLUSDT / UTC | -0.045 | -0.093 | -0.069 | -0.117 | -0.105 | -0.152 | -0.164 | -0.212 | s 0.05% / m 0.02% |
| SOLUSDT / NY | -0.036 | -0.071 | -0.054 | -0.088 | -0.080 | -0.114 | -0.123 | -0.157 | s 0.05% / m 0.02% |
| SOLUSDT / London | -0.122 | -0.168 | -0.147 | -0.192 | -0.183 | -0.229 | -0.243 | -0.289 | s 0.05% / m 0.02% |

Holm-adjusted p_A over the 9 primaries at each cost cell (computed at report time), and the first cost cell, from the most expensive (slippage, then maker fee, descending), at which the adjusted p_A falls below α = 0.05:

| p_A (Holm) | s 0.00% / m 0.00% | s 0.00% / m 0.02% | s 0.02% / m 0.00% | s 0.02% / m 0.02% | s 0.05% / m 0.00% | s 0.05% / m 0.02% | s 0.10% / m 0.00% | s 0.10% / m 0.02% | First passing cell |
|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT / UTC | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | none |
| BTCUSDT / NY | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | none |
| BTCUSDT / London | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | none |
| ETHUSDT / UTC | 0.427 | 0.581 | 0.521 | 0.678 | 0.64 | 0.816 | 0.864 | 1 | none |
| ETHUSDT / NY | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | none |
| ETHUSDT / London | 0.816 | 0.826 | 0.866 | 0.882 | 0.952 | 0.977 | 1 | 1 | none |
| SOLUSDT / UTC | 0.317 | 0.337 | 0.331 | 0.353 | 0.364 | 0.392 | 0.407 | 0.434 | none |
| SOLUSDT / NY | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | none |
| SOLUSDT / London | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | none |

## 6. Grid overview

| Cell | Best variant (vs primary) | Variants | n | Mean net R | p_A | DSR (cell) | N (cell) | DSR (all) | N (all) |
|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT / UTC | stop_buffer=0.25% | 36 | 1859 | -0.189 | 0.465 | 0.000 | 36 | 0.000 | 324 |
| BTCUSDT / NY | confirm_n=5, swing_k=1 | 36 | 865 | -0.095 | 0.0519 | 0.000 | 36 | 0.000 | 324 |
| BTCUSDT / London | stop_buffer=0.25% | 36 | 937 | -0.204 | 0.98 | 0.000 | 36 | 0.000 | 324 |
| ETHUSDT / UTC | stop_buffer=0.25% | 36 | 1867 | -0.088 | 0.00399 | 0.000 | 36 | 0.000 | 324 |
| ETHUSDT / NY | stop_buffer=0.25% | 36 | 572 | -0.083 | 0.794 | 0.003 | 36 | 0.000 | 324 |
| ETHUSDT / London | hold_rule=max_hold 72h, stop_buffer=0.5 ATR | 36 | 926 | -0.084 | 0.447 | 0.000 | 36 | 0.000 | 324 |
| SOLUSDT / UTC | r_target=1 | 36 | 1639 | -0.119 | 0.014 | 0.000 | 36 | 0.000 | 324 |
| SOLUSDT / NY | stop_buffer=0.25% | 36 | 521 | -0.064 | 0.677 | 0.002 | 36 | 0.000 | 324 |
| SOLUSDT / London | confirm_n=2, swing_k=1 | 36 | 947 | -0.179 | 0.493 | 0.000 | 36 | 0.000 | 324 |

DSR (cell) deflates against the pair × session's grid, DSR (all) against every grid variant; N is the number of trials.

20 of 324 grid variants with baselines have raw p_A < 0.05; about 16.2 would by chance if none had an edge. The best cell is selected after the fact, so its raw p overstates; the DSR deflates its Sharpe for the number of trials (conservatively, since the variants share most of their trades).

## 7. What was pre-registered and what changed

- Registered on 2026-09-26; data download date 2026-10-21; in-sample 2020-01-01 to 2025-12-31; holdout 2026-01-01 to 2026-10-20.
- Current prereg hash `f9359030966d5bb8dd200dff42e7a89cea13102829c94e86db4854df92050125`.
- Frozen on 2026-10-21 (lock: prereg hash `f9359030966d5bb8dd200dff42e7a89cea13102829c94e86db4854df92050125`, matches the current file; code version `c3c05e53c0f38a9e57d65b716a78ddfb198004a84dd66fde28cf145808135dc5`; git commit `3ad5c3a3d9e8db9b3a485c45e90fb90dbdababef`; data download date 2026-10-21).
- Results code version: `c3c05e53c0f38a9e57d65b716a78ddfb198004a84dd66fde28cf145808135dc5` (same as the freeze).
- Holdout: ok at 2026-10-21T22:19:29+00:00 (batch dc1fa5e72217); failed cells: 0.
- Holdout attempts in the registry: 1.
