# Phase 6 — Reporting

Read with `00-overview.md`. Delivers the figures, the per-run report and
the summary. `perpbt/report/carry/` is outside the carry code version (as
`report/` is for the first strategy), so report edits never count as code
changes for the holdout or forward runs.

**Branch:** `carry/6-reporting`, from `dev` after Phase 5 is merged.

## 6.1 Figures (per run)

- Equity curve of the primary, `always_on`, and the hurdle line (5%/yr
  compounding), log scale.
- Trailing annualised funding per pair with the entry/exit thresholds and
  the on periods shaded (one small panel per pair).
- Basis (perp/spot − 1) per pair, daily, with the on periods shaded.
- Return decomposition per year: funding, basis, fees, slippage,
  rebalances, liquidations (stacked bars).
- V2: the bootstrap distribution of the annualised excess return with the
  0 line and the CI; the random-timing distribution with the primary
  marked.

Figures follow the repo's existing helpers (`perpbt/report/figures.py`
style: PNG, fixed sizes, no interactive output).

## 6.2 Report (`runs/carry/<variant_id>/report.md`)

Headline: the verdict sentence (edge / positive below the hurdle / no
edge) with V1 and V2 p-values, the annualised net return and its CI, and
the three-way V2 reading. Then the diagnostics of Phase 4 §4.4 as tables,
the hurdle table (0 / 3 / 5 / 8%), the re-pricing table, and the stated
unmodelled risks (overview §5 item 5).

## 6.3 Summary (`runs/carry/summary.md`)

One page: the question, the rule, the periods, the verdict per period
(in-sample, holdout, forward when it exists), with links to the reports.
When the screen stopped the project, the summary is the screen's.

## 6.4 Tests

Reports render from a synthetic run; every number in the headline comes
from `stats.json`; byte-identical on rerun.

## 6.5 Exit criterion

Tests green; a synthetic run's report reviewed by eye.
