# Phase 6 — Reporting

Read with `00-overview.md`. Delivers the figures, the per-variant report,
and the summary. Lives in `perpbt/report/trend/` (outside `code_version`,
like the first strategy's reports), reusing `report/markdown.py` for number
formats and tables and the first strategy's figure style.

**Branch:** `trend/6-reporting`, from `dev` after Phase 5 is merged.

## 6.1 Figures (matplotlib PNG)

1. Equity, log scale: strategy, vol-targeted buy-and-hold, and the ex-post
   scaled buy-and-hold reference line.
2. Drawdown: strategy (scaled to matched vol) vs buy-and-hold.
3. Exposure over time: portfolio gross exposure and the per-pair ensemble
   exposure as a heat strip.
4. T1: histogram of the random-path Sharpes with the observed value.
5. T2: bootstrap distribution of the Sharpe difference with its CI.
6. Rolling 1-year Sharpe, strategy vs buy-and-hold.
7. Costs and funding per year, stacked.
8. Per-pair net Sharpe with CIs.

## 6.2 Per-variant report (`report.md`)

Header (variant, period, market, code version, prereg hash); the verdict
block (T1, T2, edge, the reading, T3); the power note from overview §3 with
the measured random-path spread; diagnostics tables (Phase 4 §4.6); the
re-pricing table; events (liquidations, delistings, late fills); figures.

## 6.3 Summary (`runs/trend/summary.md`)

One table: rows = in-sample, spot holdout, forward 12 m (descriptive),
forward 36 m; columns = net Sharpe, buy-and-hold Sharpe, p1, p2, edge,
reading, T3. Then the exploratory rows and the DSR. Then the plain-language
answer, which must say which of "proven worse / not proven / proven better"
applies to T2, and must name the holdout's bear-market caveat. If the
project stopped at the screen, the summary is the screen result only.

## 6.4 Tests

Numbers in the Markdown are parsed back and compared to `stats.json` (as
the first strategy's report tests do); every figure is produced on
synthetic data without error.

## 6.5 Exit criterion

Reports and summary render on the synthetic end-to-end run.
