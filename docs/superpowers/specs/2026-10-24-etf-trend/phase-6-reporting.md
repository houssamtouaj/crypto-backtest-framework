# Phase 6 — Reporting

Read with `00-overview.md`. Delivers figures, the per-run report and the
summary. Lives in `perpbt/report/etf/`, outside `ETF_CODE_PATHS`, so report
changes never touch the code lock.

**Branch:** `etf/6-reporting`, from `dev` after Phase 5 is merged.

## 6.1 Figures (per run, `runs/etf/<variant_id>/figures/`)

1. **Growth of 100**: strategy, vol-targeted buy-and-hold, 100% SPY,
   equal-weight buy-and-hold, and cash (T-bills), log scale.
2. **Invested share** over time (portfolio), with each ETF's exposure as
   small multiples.
3. **Drawdown** of the strategy and of 100% SPY.
4. **T1**: histogram of the random paths' Sharpe with the observed value.
5. **T2**: bootstrap distribution of the Sharpe difference with the CI.
6. **Per year**: strategy minus SPY return, bars.

PNG at modest resolution (the carry figures reached 10–15 MB each; keep
each under 1 MB).

## 6.2 Per-run report (`report.md`)

Sections: the question; the rule in five lines; the verdict box (T1, T2,
reading, T3); the plain-language paragraph (§6.4); the diagnostics tables
(Phase 4 §4.7); re-pricing table; per-ETF table; caveats (holdout not
unseen, Yahoo data, correlated basket, strategies tried = 4). Every number
comes from `stats.json` or the parquet files, with the file named.

## 6.3 Summary (`runs/etf/summary.md`)

As `runs/carry/summary.md`: question, rule, screen, code lock, a verdict
table per period (in-sample, holdout, forward 12m, forward 36m), the
exploratory rows per period, the caveats, and the line "This is the fourth
strategy tested in this repo; the verdict is not adjusted for that."

## 6.4 Plain-language paragraph

Each report and the summary open with three or four sentences a stock
investor can read without the statistics: did it beat holding SPY, by how
much per year, with how much smaller (or larger) drawdowns, and whether
that is proven. No jargon (no "T1", "IUT", "DSR") in that paragraph.

## 6.5 CLI and tests

`perpbt etf report [<variant_id> | --all]` and `perpbt etf summary`.
Tests: report renders from fixture runs; every number in the verdict box
matches `stats.json`; figure files exist and are under 1 MB.

## 6.6 Exit criterion

Tests green; reports rendered for the smoke-test run in a scratch
directory.
