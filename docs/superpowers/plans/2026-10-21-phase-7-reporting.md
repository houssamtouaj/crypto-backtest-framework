# Phase 7 — Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the variant folders, `results.parquet`, the registry and the lock into the figures, one `report.md` per variant and `runs/summary.md` with the pre-registered verdict.

**Architecture:** `perpbt/report/` holds four modules. `markdown.py` owns number formatting (one format per kind of number, ASCII minus, `%` for fractions shown as percent, `n/a` for missing) and Markdown tables, so the tests can parse every table back. `figures.py` has one function per figure: it takes the tables it needs and a path, writes one PNG (matplotlib, Agg) and returns the path; a missing optional input renders a placeholder note. `report.py` reads one variant folder (`VariantRun`: `stats.json`, `config.yaml`, the Parquet tables) and writes `report.md` and `figures/` from it; the context the folder cannot know (heatmap rows, the other sessions' trades, the holdout continuation, gap overlaps, the holdout reason) comes in a `ReportContext`. `summary.py` holds the pure verdict/sentence/power functions and builds `runs/summary.md` from `results.parquet`, the primaries' `stats.json`, the prereg, the lock, the registry and `holdout/DONE`. `perpbt report` rebuilds `results.parquet`, writes the primary reports (in-sample and, when run, holdout), with `--grid` every in-sample grid report, and the summary.

**Tech Stack:** Python 3.11, numpy, pandas, pyarrow, matplotlib (Agg), scipy (normal quantiles), pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-ob-backtest/phase-7-reporting.md`, with `00-overview.md` (D1, D11, D13, §10 risks) and Phase 5 §5.10 / Phase 6 §6.3, §6.5, §6.6 (inputs).

**Execution:** Native (memory `workflow-end-to-end-phases`), one fresh whole-branch review at the end, merge only after the user says so.

## Global Constraints

- Every number in a report table comes from `stats.json` (the per-variant report) or `results.parquet` (the summary's verdict table); the report layer does not recompute them. Numbers it derives (holdout power, Holm over the nine at each re-pricing cell, shared-trade fractions, gap overlaps) are labelled as computed at report time.
- Figures read only the folder's tables and `stats.json`; nothing is simulated.
- Fast suite `python -m pytest -q -m "not slow"`; the slow test renders the report from the real BTC 2024 smoke run.

## Decisions where the spec is silent (recorded in the spec in the implementing commit)

1. **Report scope.** `perpbt report` writes the in-sample primary reports, the holdout primary reports when the holdout ran, and the summary; `--grid` adds a report for every in-sample grid variant. The three heatmaps of a pair × session render in the in-sample primary's report (`figures/heatmap_n.png`); the primary report lists the grid variants with links to their folders' `report.md`.
2. **Holdout power.** Minimum detectable mean net R for a one-sided test of mean net R > 0 (normal approximation) at 80% power: `MDE = (z_{1−α} + z_{0.80}) · σ_in / √n_holdout`, `σ_in` the in-sample `std_net_r`. Shown at `α` and at `α/9` (the first Holm step, the bar the best cell must clear).
3. **Cost sensitivity, "first" cell.** Holm over the nine in-sample primaries is applied at each re-pricing cell to the cell's `p_A`; cells are walked from the most expensive (slippage descending, then maker descending); "first" is the first cell where the adjusted `p_A < α`.
4. **Equity figure.** The in-sample primary's figure continues into the holdout when it ran (the holdout equity chained onto the in-sample end), holdout shaded. Buy-and-hold is `start_equity · Π(1 + scale · ret)` with the stored `buy_and_hold.scale`.
5. **Shared-trade fraction.** Siblings are the variants of the same pair, period and strategy parameters in the other sessions (`diagnostics.shared_fraction` over their `trades.parquet`).
6. **Gap overlaps.** Trades whose `[entry_ms, exit_ms]` overlaps a gap of the pair's 15m or 1m `manifest.json` (`gaps`); "not checked" when no manifest is found.
7. **Raw-pass column.** Per cell, the benchmarks whose raw p would pass (`B&H` also needs `sharpe_diff > 0`); the sentence for a benchmark that fails adjusted but passes raw says so.
8. **Largest caveat.** Beta when some in-sample cell beats a timing baseline (A or B, adjusted) but not buy-and-hold; otherwise costs, stated with the mean cost in R and the share of trades with cost above 1 R across the primaries.

## Tasks

- [ ] **7.0 Markdown helpers** — `report/markdown.py` (`fmt_*`, `table`, `parse_tables`) with tests.
- [ ] **7.1 Figures** — `report/figures.py` (the 17 figures of §7.1), tests `tests/test_figures.py` (each renders a non-empty PNG from a synthetic `SimResult`; missing inputs render placeholders).
- [ ] **7.2 Per-variant report** — `report/report.py` (`VariantRun`, `ReportContext`, `render_report`, `write_report`, `gap_overlaps`), tests `tests/test_report.py` (sections in order; every table number found in `stats.json`); slow test on the smoke run.
- [ ] **7.3 Summary** — `report/summary.py` (`verdict`, `cell_sentence`, `min_detectable_effect`, `plain_answer`, `build_summary`, `write_summary`), tests `tests/test_summary.py` (2³ × 2 × 2 sentence table, Holm columns vs `results.parquet`, power hand example, holdout absent and present).
- [ ] **7.4 CLI** — `perpbt report [--grid]`; the exit test: nine synthetic primaries (3 pairs × 3 sessions) through the Phase 8 protocol, then `report`.
- [ ] Spec amendments, whole-branch review, memory update, ask to merge.
