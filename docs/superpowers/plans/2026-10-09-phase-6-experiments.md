# Phase 6 — Experiments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the pre-registration file into runs: a validated, hashed `prereg.yaml` with its lock, variant identity, the append-only registry and the derived `results.parquet`, grid enumeration (324), the spawn-safe multiprocessing runner with resume, the baselines pass, the Holm and DSR family passes, and the one-shot holdout runner.

**Architecture:** `perpbt/experiments/` holds five modules. `prereg.py` parses `configs/prereg.yaml` into typed objects (Phase 0 dataclasses where they exist), hashes the *normalised* content, and owns the lock. `grid.py` turns the prereg into `GridVariant`s (a `VariantConfig` plus its heatmap/single memberships). `registry.py` appends JSON lines under a cross-process file lock and rebuilds `results.parquet` from every `stats.json`. `runner.py` loads a variant's market (guarded stores, per-process cache), simulates, computes Phase 5 statistics, writes the variant folder, and drives many variants through a `spawn` process pool; it also holds the baselines pass and the family passes (Holm, DSR). `holdout.py` is the only caller that passes `allow_holdout=True`.

**Tech Stack:** Python 3.11, numpy, pandas, pyarrow, PyYAML, `concurrent.futures.ProcessPoolExecutor` with the `spawn` context, pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-ob-backtest/phase-6-experiments.md`, with `00-overview.md` (§6 conventions, D6, D11, D15, §9.1 grid) and `phase-5-statistics.md` §5.10 (`stats.json`). Consumers: `phase-7-reporting.md` (reads `stats.json`, `results.parquet`, the registry) and `phase-8-runs.md` (the CLI protocol).

**Execution:** Native (memory `workflow-end-to-end-phases`), one fresh whole-branch review at the end, merge only after the user says so.

## Global Constraints

- Branch `phase/6-experiments` from `dev` at `831ea68`; spec amendments in the same commit as the deviating code.
- `stats.json` and every Parquet file in a variant folder are byte-identical across reruns of the same `variant_id`; nothing time- or commit-dependent goes into them (timestamps, the git commit and runtimes live in the registry only).
- The literal `allow_holdout=True` appears only in `perpbt/experiments/holdout.py`.
- Fast suite `python -m pytest -q -m "not slow"`; the multiprocessing test uses `spawn` with 2 workers on a synthetic data directory.

## Decisions where the spec is silent or conflicts (recorded in the spec in the implementing commit)

1. **Hash over normalised content (Phase 0 pointer).** `prereg_hash` is the SHA-256 of the canonical JSON of the *validated* prereg: every value that maps to a Phase 0 dataclass field goes through that dataclass (so `r_target: 2` and `2.0` hash the same), dates become ISO strings, `data_download_date` and `holdout.end` are removed. The YAML loader rejects duplicate keys.
2. **Explicit prereg.** `primary` must name every `StrategyParams` field and `execution` every `ExecConfig` field (no silent defaults in a pre-registration). Session names must be `utc`, `ny` or `london`.
3. **Per-variant baseline budget.** `VariantConfig.stats.baseline_runs` is `baseline_runs_primary` for primary cells and `baseline_runs_grid` otherwise; `--runs` overrides it for one pass.
4. **`run_variant` signature.** The master seed is `cfg.stats.master_seed` (part of the variant id), so the keyword is dropped; `runs_dir`, `labels`, `baseline_runs`, `insample_ref`, `allow_holdout`, `reason` and `batch` are keywords.
5. **Registry rows.** Each attempt appends a `started` row and then `ok` or `failed`; the number of `started` rows equals the attempts. Extra fields: `kind` (`run` or `baselines`), `reason` (holdout code change), `batch` (one id per CLI invocation), `runtime_s`. Workers append under a lock file (`msvcrt`/`fcntl`), so lines never interleave.
6. **Baselines pass re-simulates.** It reruns the simulator (deterministic, ~2 s), checks the result's `summary` against the stored `stats.json`, runs the baselines, rewrites `stats.json` and writes `baseline_a.parquet` (slot table), `baseline_a_runs.parquet` (A's per-run component means, for the Phase 7 histogram) and `baseline_b.parquet`.
7. **Extra outputs.** `buyhold.parquet` (`bh_daily`, for Phase 7's equity figure); `stats.json` gains an `experiment` block (`is_primary`, `members`, `code_version`); `trades.parquet` carries the regime labels.
8. **DSR pass.** `perpbt stats dsr` writes `dsr.local` (the pair × session's grid) and `dsr.global` (all in-sample grid variants) into every grid variant's `stats.json`; it refuses until every grid variant has a `stats.json`. Phase 8 step 4 gains this command.
9. **Holm block.** `perpbt stats holm` refuses until all nine in-sample primary cells have baselines; writes `holm` (`family`, `variant_ids`, `alpha`, `p_{a,b,bh}_adj`, `p_{a,b,bh}_bonf`) into each. The holdout runner does the same over the holdout family.
10. **Holdout.** Requires the in-sample primary `stats.json` of each cell under the current code (its `insample_ref`), runs sequentially in-process, and writes `runs/holdout/DONE` even when a cell fails (status recorded), so any rerun needs the manual deletion. Prior holdout batches in the registry are reported as earlier touches.
11. **Results rebuild.** Scans `runs/*/stats.json`; `run_ms`, `git_commit`, `runtime_s` come from the latest `ok` `run` row of the registry. `perpbt stats results` rebuilds on demand; the family passes and the drivers rebuild at their end.

## Tasks

- [ ] **6.1 Pre-registration** — `experiments/prereg.py` (`load_prereg`, `Prereg.content/hashed_content/prereg_hash/exec_for/data_config/variant`, `freeze`, `read_lock`), `configs/prereg.yaml`; tests `tests/test_prereg.py` (spec 6.1 list).
- [ ] **6.2 Grid** — `experiments/grid.py` (`GridVariant`, `cell_params`, `enumerate_grid`, `primary_variants`); tests `tests/test_grid.py` (36 per cell, 324, primary once, differs only on its axes).
- [ ] **6.3 Registry and results** — `experiments/registry.py`; tests `tests/test_registry.py` (variant id stability, lock, row counts, results vs ok set).
- [ ] **6.4 Runner** — `experiments/runner.py` (`load_market`, `run_variant`, `run_many`, `add_baselines`, `baselines_many`, `apply_holm`, `apply_dsr`); synthetic data directory helper `tests/experiment_harness.py`; tests `tests/test_runner.py` (outputs, resume after a failure, `--force`, byte-identical, spawn).
- [ ] **6.5 Baselines pass** — files, `stats.json` update, byte-identical second run.
- [ ] **6.6 Holdout** — `experiments/holdout.py`; tests `tests/test_holdout.py`.
- [ ] **6.7 CLI** — `prereg validate|freeze`, `grid --dry-run|--run`, `run --primary`, `baselines`, `stats holm|dsr|results`, `holdout`; CLI tests including the 324 dry run and the end-to-end pipeline on the synthetic directory.
- [ ] Spec amendments, whole-branch review, memory update, ask to merge.
