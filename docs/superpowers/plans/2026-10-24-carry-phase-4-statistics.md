# Carry Phase 4 — Statistics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `perpbt/carry/verdict.py` (V1, V2, the verdict, the three-way reading, the hurdle table, the `stats.json` schema and its serialiser) and `perpbt/carry/diagnostics.py` (spec §4.4, descriptive: annual stats, filter value, random timing through `run_batch`, the decomposition, rebalances and liquidations, cost re-pricing, DSR with N = 7). A library for the Phase 5 runner; nothing here loads data or writes files.

**Architecture:** `verdict.py` reuses the screen's `v1_stats`, `v2_stats` and `daily_hurdle` by import (not a fork, not a move). Every random statistic takes a generator from `rng_for(master_seed, prereg_hash, purpose)` through a `Seeds` helper that owns the purpose labels. Diagnostics read only `PathResult` tables, `BatchResult` arrays and the PairFrames the caller passes; random timing and re-pricing call `run_batch` / `run_path` with the primary's `ExecSpec` (re-pricing via `dataclasses.replace` on its `costs`). `daily_pair` gains three per-pair columns (`fees`, `slippage`, `basis_pnl`; appended, backward compatible) so the per-pair decomposition sums exactly to the daily totals.

**Tech Stack:** Python 3.11, numpy, pandas, scipy (through `stats.dsr`), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-23-funding-carry/phase-4-statistics.md` with `00-overview.md` (CD8, CD9, CD11, §3, §10.2), Phase 2 §2.6, Phase 3 §3.8, trend Phase 4 §4.1 (the random-timing rule).

## Global Constraints

- Branch `carry/4-statistics` from dev ef5f5bf.
- `tests/carry/test_real_screen.py` stays green (screen.json byte for byte). `perpbt/carry/screen.py`, `configs/carry/prereg.yaml`, its lock and `runs/carry/` are unchanged.
- Data only through `carry.frames.load_pairframe` (in-sample, never `allow_holdout`); the new modules load nothing. The AST guards in `tests/carry/test_frames.py` stay green and unwidened (no `.ret` attribute, no store names, no `allow_holdout=True`, no `**` splat).
- No real-data performance number: synthetic frames for every statistic; real-data tests check structure, runtime and determinism only, and never call a function that returns a Sharpe, p, return or P&L.
- Shared modules change only backward compatibly; the first strategy's and trend's suites stay green.
- Do not run any first-strategy or trend run command, nor `perpbt carry screen` or `perpbt carry prereg freeze`. Long jobs in the foreground, one at a time.

## My calls (labelled; listed in the hand-off)

1. **Reuse, not move.** `verdict.py` imports `v1_stats`, `v2_stats`, `daily_hurdle` and `DAYS_PER_YEAR` from `screen.py`. The screen file stays byte for byte as frozen, and there is one implementation.
2. **Reading when V2 passes and V1 fails:** `no_edge` (the spec's `positive_below_hurdle` is "V1 passes, V2 does not"; edge needs both).
3. **Seeds** `rng_for(stats.master_seed, prereg_hash, purpose)`, purposes `"<period>/<row>/<stat>"`:
   - `<period>/<row>/v1`: V1; the annual table's Sharpe CI is V1's (same draws, not drawn twice);
   - `<period>/<row>/v2`: V2 at `hurdle.annual` and every `hurdle.descriptive` value (one stream, a fresh generator per hurdle, so the 5% row equals the verdict's);
   - `<period>/<row>/reprice/v2`: V2 of every re-pricing cell (one stream shared by the cells: common random numbers, so cells differ only by costs);
   - `<period>/filter_value`: the paired Sharpe difference primary − always_on;
   - `<period>/random_timing`: the shift offsets.
   `<period>` is `insample`, `holdout`, `forward_12m`, `forward_36m`; `<row>` the row name (Phase 5 may append `/<pair>` for single-pair runs). None of these collides with the screen's purposes (`screen…`).
4. **Random timing statistic:** both the annualised net return (`365 × mean`, V2's statistic) and the Sharpe of every path; the primary's rank `1 + #{paths > primary}` and the share `(1 + #{paths ≥ primary}) / (M' + 1)` over the `M'` finite paths, with the paths' mean, std and 5/50/95 percentiles. Shift: `n` = the number of return days; `k ~ U{m, …, n − m}` (inclusive, `m = shift_margin_days`), one per path, shared by the pairs; pair `i` with `n_i` live decision days takes `on'[j0 + t] = on[j0 + (t + k) mod n_i]`. Rows outside the period are off (never read). Refuses `n < 2m`.
5. **Path returns from equity:** a `BatchResult`'s returns are recomputed as `equity / previous equity − 1` with `equity0` first, the executor's own expression (bit-identical); the AST guard forbids reading a `.ret` attribute in `perpbt/carry/`.
6. **Decomposition units:** each day's money component divided by the previous day's equity (`E_{t−1}`, `equity0` first), summed over the year or the pair, so `funding − fees − slippage + basis = Σ ret` exactly (to float rounding). Rebalance and liquidation costs are "of which" lines inside fees + slippage (fills with reason `rebalance` / `liquidation`); a liquidation's loss to the liquidation price is in `basis`. Days on = pair-days hedged at the close (`perp_qty < 0`); entries = perp fills with reason `enter`.
7. **Per-pair columns:** `daily_pair` gains `fees`, `slippage`, `basis_pnl` (appended after `funding`), the per-pair amounts the executor already sums for `daily`.
8. **Liquidation cost per event:** the fills of reason `liquidation` of that pair from its date until the pair's next liquidation (the perp buy-back with its fee, and the later spot sale).
9. **Annual stats:** `ann_return` (`365 × mean`, as V2), `cagr` (`(E_T / E_0)^(365 / T) − 1`), `ann_vol` (`√365 × std`, ddof 1), Sharpe and CI (V1's), max drawdown (`stats.diagnostics.max_drawdown`), worst day and worst calendar month (compounded) with their dates.
10. **Re-pricing cell:** `costs = replace(costs, spot_taker=s_t, slippage={"default": s})`: the cell's slippage applies to every pair (the grid is one value per leg); `perp_taker` stays. Each cell is one `run_path`; reported: annualised net return, Sharpe, V2 `p`, reading, and the cell's cost totals.
11. **DSR:** `dsr_from_trials(sr_moments(primary), [daily SR of the 7 rows])`, the primary among the trials (N = 7); refuses unless exactly the primary and the six pre-registered exploratory rows are given.
12. **Filter value:** `paired_sharpe_diff(primary, always_on, B, rng, block_len)` with its CI and `p`, descriptive; aligned day by day (refuses different dates).
13. **`stats.json`:** `{"schema": "carry-stats/1", "prereg_hash", "period": {name, start, end}, "row", "n_days", "v1", "v2", "edge", "reading", "hurdles", "diagnostics"}`; `diagnostics` is whatever blocks the caller computed (`annual`, `decomposition`, `margin`, `reprice`, and for the primary `filter_value`, `random_timing`, `dsr`), each documented. Serialised with `indent=2`, `allow_nan=False`, non-finite floats → `null`, numpy scalars → Python; key order fixed by construction.

## Review Focus

1. The verdict and readings are exactly spec §4.3 (+ call 2); V2 is the screen's function.
2. Seeds: distinct purposes, same inputs → the same `stats.json` bytes; a different master seed → different bytes.
3. Random timing: one offset per path shared by all pairs; within each pair's live days; `k mod n_i`; per-pair time on preserved.
4. Decomposition sums to `Σ ret` per year and per pair; rebalance/liquidation costs match the fills.
5. Re-pricing: the cell equal to the base costs reproduces `run_path`; no new constant.
6. No data loading, no `.ret` attribute, AST guards untouched.

---

### Task 1: `verdict.py`

**Files:** Create `perpbt/carry/verdict.py`; Test `tests/carry/test_verdict.py`; add a hand case to `tests/test_bootstrap.py`.

**Produces:** `SCHEMA`, `READINGS`, `reading(v1_passes, v2_passes)`, `Seeds(master_seed, prereg_hash, period)` with `.rng(*parts)` / `.purpose(*parts)`, `evaluate(ret, *, alpha, B, block_len, hurdle, descriptive, rng_v1, rng_v2)` → `{n_days, v1, v2, edge, reading, hurdles}`, `evaluate_row(ret, prereg, seeds, row)`, `stats_document(...)`, `stats_json(doc)`.

- [ ] Step 1: Tests: V1 and V2 on constructed series (10%/yr with small noise passes V2 at 5%; 4%/yr does not; a negative series fails V1, below_hurdle); the reading for each of the four combinations; the hurdle table's 5% row equals the verdict's V2; `stats_json` same seed → same bytes, other seed → different; NaN → null; `block_bootstrap_mean` hand case (fake generator) and seed determinism.
- [ ] Step 2: Run → fail. Step 3: implement. Step 4: green. Commit.

### Task 2: per-pair columns, annual stats, decomposition, margin events

**Files:** Modify `perpbt/carry/executor.py` (`PAIR_COLUMNS` + three columns); Create `perpbt/carry/diagnostics.py`; Test `tests/carry/test_diagnostics.py`.

- [ ] Step 1: Tests: per-pair `fees + slippage + basis_pnl + funding` sums equal the daily columns (random messy paths); annual stats on a hand series (worst day/month, drawdown, cagr); decomposition per year and per pair sums to `Σ ret`; rebalance/liquidation counts, dates and costs match the fills on the messy frames.
- [ ] Step 2: Run → fail. Step 3: implement. Step 4: green (and `tests/carry`). Commit.

### Task 3: random timing, re-pricing, filter value, DSR

**Files:** Modify `perpbt/carry/diagnostics.py`; Test `tests/carry/test_diagnostics.py`.

- [ ] Step 1: Tests: offsets in `[m, n − m]`, determinism; the shift on a hand case (two pairs, a late-listed one with `n_i < n`); per-pair on-count within live days preserved; `run_batch` path returns equal `run_path` on the shifted states; the summary's rank/share on a constructed case; re-pricing grid has 8 cells, the base-cost cell equals `run_path`, more slippage → lower return on a path that trades; filter value equals `paired_sharpe_diff`; DSR N = 7 equals `dsr_from_trials`, refuses a missing row.
- [ ] Step 2: Run → fail. Step 3: implement. Step 4: green. Commit.

### Task 4: end-to-end determinism and real-data checks

**Files:** Test `tests/carry/test_stats_document.py` (synthetic, end to end); `tests/carry/test_real_diagnostics.py` (slow).

- [ ] Step 1: Synthetic: every row of a small prereg-like set through `run_path`, the full `stats.json` twice → identical bytes; other master seed → different; schema keys as documented.
- [ ] Step 2: Real (slow): shifted states of the real primary (1,000 paths) keep each pair's on-count; `run_batch` on them under 60 s; same seed → identical arrays; re-pricing ExecSpecs (8) build from the prereg. Nothing printed; no statistic computed.
- [ ] Step 3: Commit.

### Task 5: Notes, suite, review, merge stop

- [ ] Step 1: Spec §4.7 implementation notes (the calls above); progress log here.
- [ ] Step 2: Full suite `python -m pytest -q` (slow included, max timeout) → green. Commit.
- [ ] Step 3: Fresh whole-branch review (most capable model); fix; suite green.
- [ ] Step 4: **STOP** before `git merge --no-ff carry/4-statistics` into `dev`.

## Progress log

Spec: docs/superpowers/specs/2026-10-23-funding-carry/phase-4-statistics.md
