# Phase 7 — Reporting

Read with `00-overview.md`. Delivers the figures, the per-variant report,
and the plain-language summary with the pre-registered verdict. Depends on
Phase 6. Output is matplotlib PNGs and Markdown; every number comes from
`stats.json` or `results.parquet`, never recomputed in the report layer.

## 7.1 Figures (`report/figures.py`)

Each function takes the tables it needs and a path, writes one PNG, and
returns the path. All figures render from a synthetic `SimResult`.

| figure | content |
|---|---|
| `equity` | strategy equity vs vol-scaled buy-and-hold, log scale, holdout shaded when present |
| `drawdown` | daily drawdown of both |
| `rolling` | rolling 6-month mean net R and rolling Sharpe, stepped monthly |
| `per_year` | bar table: n, win rate, mean net R, Sharpe, max DD, fill rate |
| `baseline_a`, `baseline_b` | histogram of run means with the observed value and p marked |
| `heatmap_1..3` | mean net R, Sharpe, and n per cell; primary cell outlined |
| `reprice` | 4 × 2 grid of mean net R with `p_A` annotated (D13) |
| `cost_r` | distribution of cost in R with the share above 1 R stated |
| `stop_dist` | stop distance in percent and in ATR; implied leverage |
| `mae_mfe` | MAE vs MFE scatter in R, coloured by exit reason |
| `hold_time` | hold-time distribution by exit reason |
| `regimes` | mean net R and n by trend and vol regime |
| `calendar` | mean net R by day of week and by entry hour (UTC) |
| `funnel` | impulses → blocks seen → orders → fills → trades, with every skip reason |
| `concurrency` | open positions over time, max marked |

## 7.2 Per-variant report (`report/report.py`)

`runs/<variant_id>/report.md`, sections in this order:

1. Header: pair, session, period, `variant_id`, code version, seed,
   primary or grid role.
2. Funnel table with all skip counts (answers "why so few trades" first).
3. Headline: n, win rate, mean net R with both CIs, Sharpe with CI, max
   DD, exposure.
4. Baselines: `p_A`, `z_A`, `p_B`, `z_B`, `n_runs`; both histograms.
5. Buy-and-hold: Sharpe difference with CI and `p_BH`; equity and
   drawdown figures.
6. Costs: cost-in-R distribution, share above 1 R, stop-distance and
   leverage distributions, the re-pricing table.
7. Robustness: the three heatmaps (grid variants only appear once the grid
   has run; the primary report links to them).
8. Alpha decay: rolling figure and per-year table.
9. Regimes and calendar breakdowns.
10. Diagnostics: MAE/MFE, hold time, concurrency, fill and resolution
    counts, `data_end` count, shared-trade fraction with the other session
    variants.
11. DSR at both `N` with the effective-trials caveat.
12. Notes: any `15m_pessimistic_missing_1m` counts, gap overlaps, and the
    holdout code-change reason if present.

## 7.3 Summary (`report/summary.py`)

`runs/summary.md`, the document the project exists to produce.

1. **Verdict table.** One row per primary cell (pair × session) and per
   period (in-sample, holdout when run): `n`, mean net R with CI,
   `p_A` raw and Holm-adjusted, `p_B` raw and adjusted, `p_BH` raw and
   adjusted, Sharpe difference, and three booleans per the verdict rule.
   A fourth column states whether the raw p would have passed, so the
   effect of the correction is visible.
2. **Sentences.** For each cell, a table-driven sentence, for example:
   "BTCUSDT / NY, in-sample: 412 trades, mean net R 0.06 (95% CI −0.02 to
   0.14). Does not beat random timing (adjusted p = 0.31), does not beat
   random days (adjusted p = 0.44), does not beat buy-and-hold (Sharpe
   difference −0.3, adjusted p = 0.71)." The sentence generator is a
   pure function of the numbers and is unit-tested for every combination
   of outcomes.
3. **Holdout power.** Trade counts on holdout per cell and the smallest
   mean net R that the holdout could have detected at 80% power given the
   in-sample R standard deviation, so a null holdout result is read
   correctly.
4. **Cross-session context.** Sessions, blocks seen, orders, fills per
   variant (D1 caveat), and the shared-trade fractions.
5. **Cost sensitivity.** The re-pricing tables for the nine primaries,
   with the cell at which each primary would first show adjusted
   `p_A < 0.05`, if any.
6. **Grid overview.** Best cell per pair × session by mean net R with its
   DSR, and how many of the 324 cells have raw `p_A < 0.05` versus the
   ~16 expected by chance.
7. **What was pre-registered and what changed.** The lock hashes, the
   freeze date, any holdout code-change reason, and the number of holdout
   attempts in the registry.
8. **Plain-language answer**, three to six sentences, generated from the
   verdict table: whether any primary cell shows an edge after
   correction, in-sample and on holdout, and the single largest caveat
   (typically costs or beta).

## 7.4 Tasks and tests

- **7.1 Figures.** Every figure renders from a synthetic `SimResult`
  without error and produces a non-empty PNG; missing optional inputs
  (no holdout, no baselines yet) render with a placeholder note rather
  than failing.
- **7.2 Per-variant report.** Renders from the Phase 4 smoke run's
  `stats.json`; every section present; no number in the Markdown differs
  from `stats.json` (the test parses the tables back).
- **7.3 Summary and verdict.** Table-driven test of the sentence
  generator over all 2³ boolean outcomes × {in-sample, holdout} × {raw
  passes, raw fails}; Holm columns match `results.parquet`; the power
  calculation matches a hand example; the summary renders with holdout
  absent and present.

Exit criterion: `perpbt report` produces the nine primary reports and the
summary from synthetic runs in the test suite; the summary reads
correctly to someone who has not seen the code.
