# Carry Phase 6 — Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The carry figures, the per-run `report.md`, `runs/carry/summary.md` and `perpbt carry report`, proven on a synthetic run (in-sample, holdout, both forward reviews), byte-identical on rerun, every headline number traced to `stats.json`.

**Architecture:** New package `perpbt/report/carry/` (outside `CARRY_CODE_PATHS`): `figures.py` (PNG per figure, the first strategy's `report/figures.py` plumbing and colours), `report.py` (scans run folders, renders one report per portfolio variant, a descriptive layout for the 12-month review), `summary.py` (the one-page summary; leaves the screen's summary alone when the screen stopped). The only carry-code change: the runner writes one more table, `signals.parquet` (trailing funding, state, basis per pair and day), so the funding and basis figures read the run folder. The V2 replicates are redrawn at report time with the verdict's own seed and checked against `stats.json`.

**Tech Stack:** Python 3.11, numpy, pandas, pyarrow, matplotlib (object API, Agg), pytest.

**Spec:** `docs/superpowers/specs/2026-10-23-funding-carry/phase-6-reporting.md` (with `00-overview.md` §2 CD8/CD9, §3 "Consequence for the report", §4 "How clean the holdout is", §5 item 5, §10.2; Phase 4 §4.7; Phase 5 §5.9).

## Global Constraints

- Report code lives in `perpbt/report/carry/` only; it loads no market data (no `perpbt.data.store`, no `load_pairframe`): it reads run folders, the registry, `holdout/DONE`, `screen.json`, the prereg and its lock.
- Shared modules change only backward compatibly; the first strategy's, trend's suites and `tests/carry/test_real_screen.py`, `tests/carry/test_real_runner.py` stay green.
- Do not change `configs/carry/prereg.yaml`, its lock or `runs/carry/`; never run `perpbt carry lock-code | run | holdout | forward | screen | prereg freeze | report` on the real `runs/`, nor any first-strategy or trend run command.
- No real-data performance number anywhere (tests check structure only).
- Every number in a report's headline and tables is a `stats.json` value (this folder's, the sibling `always_on` portfolio's, or the row's per-pair folders'); numbers derived at report time are only in figures and are labelled.
- Byte-identical on rerun: no timestamps, nothing machine-dependent in `report.md`, `summary.md` or the PNGs.

## Review Focus

1. A 12-month review folder (`carry-forward-review/1`, no `v1`/`v2`) — its report has no p-value anywhere. Test in Task 3.
2. A holdout where a per-pair variant was skipped (EOS-like delisting) — the per-pair table shows the pair as not run, the report still renders. Test in Task 3 (synthetic CCCUSDT delisted).
3. `always_on` (thresholds −1e9) and `lev_1` reports — no threshold lines off the scale; random-timing, filter-value and DSR sections say "primary only". Test in Task 2/3.
4. The screen stopped (`screen.json` decision `stop`) — `carry report` never overwrites the screen's `summary.md`. Test in Task 4.
5. A `stats.json` whose `prereg_hash` differs from the given prereg — refuses rather than mixing pre-registrations. Test in Task 4.

---

### Task 1: `signals.parquet` in every run folder

**Files:** Modify `perpbt/carry/runner.py` (`SIGNAL_COLUMNS`, `signal_table`, `TABLES`, `evaluate_variant`), `perpbt/carry/forward.py` (`evaluate_review` tables); Tests `tests/carry/test_runner.py`, `tests/carry/test_forward.py`.

**Produces:** `runner.SIGNAL_COLUMNS = ("date", "pair", "fbar", "on", "basis")`; `runner.signal_table(frames, signals: Mapping[str, CarrySignals], period) -> DataFrame` — one row per pair (variant order) and frame day in `period.start .. period.end`: ISO date, the pair, `f̄` (NaN kept), the decision state at that day's close, `perp.c / spot.c − 1`. `TABLES` gains `"signals"`.

- [ ] Step 1: Tests: the run folder file set includes `signals.parquet`; its rows cover exactly the period's days per pair, `fbar`/`on` equal `compute_signals` on the frame, `basis` equals `PairFrame.basis`; a per-pair variant has only its pair; the forward review folder has it too; rerun byte-identical (existing test).
- [ ] Step 2: Fail; implement; green; commit.

### Task 2: `perpbt/report/carry/figures.py`

**Files:** Create `perpbt/report/carry/__init__.py`, `perpbt/report/carry/figures.py`; Test `tests/carry/test_report_figures.py`.

**Produces** (each writes one PNG and returns its path; a missing input gives `report.figures.placeholder`):
- `equity(daily, path, *, hurdle_annual, always_on=None, row="primary")` — log scale; the row, `always_on`'s equity on the same dates, and the hurdle line `E0 (1 + h_d)^t` with `E0 = equity_0 / (1 + ret_0)`.
- `funding(signals, daily_pair, path, *, entry, exit)` — one panel per pair, `f̄` in %/yr, threshold lines only when finite and |x| ≤ 10 (else "always on: no thresholds"), hedged days (`perp_qty < 0`) shaded.
- `basis(signals, daily_pair, path)` — one panel per pair, basis in %, hedged days shaded.
- `decomposition(per_year, path)` — per year stacked bars funding, basis, −fees, −slippage (positive up, negative down) with the net (`ret_sum`) marked; a second panel "of which" −rebalance and −liquidation cost (they are part of fees + slippage, not added).
- `v2_bootstrap(replicates, v2, path)` — histogram, 0 line, CI lines, observed excess.
- `random_timing(table, rt, path)` — path histograms of `ann_return` and `sharpe` with the primary marked.
- `FIGURES` tuple of the six names.

- [ ] Step 1: Tests: every figure is a PNG > 2 kB from synthetic tables; placeholders for `None`/empty input; always-on thresholds give no line (no exception, PNG written); the same inputs give the same bytes twice.
- [ ] Step 2: Fail; implement; green; commit.

### Task 3: `perpbt/report/carry/report.py`

**Files:** Create `perpbt/report/carry/report.py`; Tests `tests/carry/reportenv.py` (synthetic world: `make_env` to 2029-10-28, lock-code, `carry run`, `carry holdout`, both forward reviews), `tests/carry/test_report.py`.

**Produces:**
- `RunFolder(path, kind, stats, variant)` with `.load(path, kind)`, `.table(name)`, `.row`, `.scope`, `.period`, `.is_review`, `.is_primary`.
- `scan(carry_root) -> list[RunFolder]` — every folder with `stats.json` under `<root>/<id>`, `holdout/<id>`, `forward/<id>`, sorted (kind, period, row order, scope).
- `ReportContext(alpha, B, block_len, master_seed, always_on: RunFolder | None, pairs: list[tuple[str, RunFolder | None]], notes: list[str], reason: str | None)`.
- `v2_replicates(daily, stats, ctx) -> np.ndarray | None` — `365 × block_bootstrap_mean(ret − h_d, B, Seeds(...).rng(row, "v2"), block_len)`; None unless its p and CI equal `stats.json`'s.
- `verdict_sentence(stats, *, primary: bool) -> str`, `v2_reading_text(reading) -> str`.
- `render_report(run, ctx) -> str` (writes `figures/`), `render_review(run, ctx) -> str`, `write_reports(carry_root, prereg, lock) -> list[Path]` — one `report.md` per portfolio folder; per-pair folders get none (their rows are a table in their row's report).

Sections (carry-stats/1): Headline (sentence; holdout caveat; table) · Rule · Equity (figure, annual table row vs always_on) · Hurdles · Filter value and random timing (primary; else "primary only") · Return decomposition (total / per year / per pair, figure) · Funding and basis per pair (figures) · Margin events · Cost re-pricing (note: no cell equals the per-pair costs) · Per-pair rows (descriptive) · Deflated Sharpe ratio · Unmodelled risks · Notes. Review layout: Review (metrics vs in-sample range, no p-value) · Equity · Return decomposition · Funding and basis · Margin events · Unmodelled risks · Notes.

- [ ] Step 1: Tests on the synthetic world: one report per portfolio folder (7 in-sample, 7 holdout, 2 forward); headings in order; every number of the headline section and of every table cell is a `stats.json` value; the verdict sentence matches `reading`; holdout reports carry the caveat beside the verdict, in-sample ones do not; the 12-month report has no p-value; exploratory reports say "descriptive" and "primary only"; the holdout per-pair table marks CCCUSDT not run; every linked figure exists and is a PNG; a second `write_reports` gives identical bytes; the replicate check matches.
- [ ] Step 2: Fail; implement; green; commit.

### Task 4: summary, CLI, .gitignore

**Files:** Create `perpbt/report/carry/summary.py`; Modify `perpbt/cli.py` (`carry report`), `.gitignore`; Tests `tests/carry/test_report_summary.py` (with the Task 3 world), `tests/carry/test_report_scope.py`.

**Produces:** `build_summary(carry_root, prereg, lock) -> str | None` (None when `screen.json` says stop), `write_summary(...) -> Path | None`; `report_all(prereg_path, runs_dir) -> dict` (refuses, `ReportRefused`, unless frozen and every `stats.json` has the prereg's hash); CLI `perpbt carry report [--prereg] [--runs-dir]`, exit 0 / 1 refused.

Summary: the question (overview §1), the rule (V1, V2, hurdle, α, block, B, costs from the prereg), the screen line (`screen.json`, when present), the code lock, a periods table (in-sample, holdout, forward 12 / 36: dates, status, reading, V1 p, V2 p, annualised return [CI], V2 reading, link), the exploratory rows per period (descriptive), the holdout caveat, the 12-month review's metrics (inside / outside), unmodelled risks pointer.

- [ ] Step 1: Tests: summary numbers in tables are `stats.json` values; links resolve; with `screen.json` stop the summary is untouched and the CLI says so; mismatched prereg hash refuses; CLI writes reports + summary and a rerun is byte-identical; no `perpbt/report/carry/` file is in `CARRY_CODE_PATHS`' files and none imports `perpbt.data` or `carry.frames`; `.gitignore` admits `runs/carry/[holdout/|forward/]*/report.md` and `figures/*.png` (`git check-ignore`).
- [ ] Step 2: Fail; implement; green; commit.

### Task 5: eye check, notes, suite, review

- [ ] Render the synthetic world's reports into the scratchpad; look at every figure and the primary/holdout/review reports.
- [ ] Spec §6.6 implementation notes (calls); this plan's progress log; full suite (max timeout) green; commit.
- [ ] Fresh whole-branch review on the most capable model; fix; suite green; STOP before the merge.

## Progress log

Spec: docs/superpowers/specs/2026-10-23-funding-carry/phase-6-reporting.md
