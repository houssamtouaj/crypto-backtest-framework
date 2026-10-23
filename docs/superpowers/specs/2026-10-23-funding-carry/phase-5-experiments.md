# Phase 5 — Experiments

Read with `00-overview.md`. Delivers the variant set, the runner, the
registry, the code lock, the holdout runner, and the forward runner. The
trend strategy's Phase 5 spec
(`docs/superpowers/specs/2026-10-23-trend-following/phase-5-experiments.md`)
is the model; differences are listed here.

**Branch:** `carry/5-experiments`, from `dev` after Phase 4 is merged.

## 5.1 Variants and runner

The primary and the six exploratory rows, each for the portfolio and each
pair alone. `variant_id` = hash of the variant's normalised content plus
the period. `run_variant(variant, period) → runs/carry/<variant_id>/`:
`daily.parquet`, `daily_pair.parquet`, `fills.parquet`, `events.parquet`,
`random_timing.parquet` (primary only), `reprice.parquet`, `stats.json`.
Byte-identical on rerun.

## 5.2 Registry

`runs/carry/registry.jsonl` through `experiments.registry.Registry`; rows
as trend Phase 5 §5.3 with `kind (run | holdout | forward)`.
`results.parquet`: one row per variant and period.

## 5.3 Code lock

`perpbt carry lock-code` fills the lock's code fields and sets
`periods.forward.start` to the next UTC day; refuses if set. Run
immediately before the in-sample primary run (Phase 7). The carry code
version is `code_version()` restricted to the files carry runs use:
`perpbt/carry/`, `perpbt/trend/daily.py`, `perpbt/data/`,
`perpbt/indicators/daily.py`, `perpbt/indicators/atr.py`,
`perpbt/stats/` (bootstrap, dsr, diagnostics, seeds),
`perpbt/experiments/registry.py`, `perpbt/config.py`, `perpbt/version.py`,
`perpbt/checks.py`. A test fails if a carry module imports a `perpbt`
module outside the list, **transitively** (the trend Phase 1 review found
`indicators/daily` → `indicators/atr`). Output-neutral changes pass as in
trend Phase 5 §5.4.

## 5.4 Holdout (`carry/holdout.py`)

`perpbt carry holdout` runs the primary and the exploratory rows on
2024-01-01 – 2026-10-20, once:

- Refuses if `runs/carry/holdout/DONE` exists, if the prereg hash differs
  from the lock, or if the code version differs unless `--allow-code-change
  --reason "..."`.
- The only caller (with `forward.py`) of `allow_holdout=True` in
  `perpbt/carry/`; the first strategy's grep test is widened to allow
  exactly these two carry modules besides `experiments/holdout.py`
  (trend Phase 5 was never built, so no trend module is allowed).
- The verdict (V1, V2) on the holdout uses the frozen rule and costs; the
  random-timing and re-pricing diagnostics are descriptive.
- Writes `DONE` at the end even if a variant failed.

## 5.5 Forward (`carry/forward.py`)

As trend Phase 5 §5.6: from `periods.forward.start` to the last complete
UTC day after fetching both legs' tails; `--review 12` descriptive once
365 days exist, `--review 36` confirmatory V1/V2 once 1,095 days exist;
never pooled with earlier periods; each run a new registry row.

## 5.6 CLI

`perpbt carry prereg validate | prereg freeze | screen | lock-code | run
[--primary | --exploratory] | holdout | forward | report`.

## 5.7 Tests

Holdout refusals and the DONE write on failure; the widened grep test;
forward refusals before 365 / 1,095 days; lock-code once; runner
byte-identical; registry rows complete; the transitive import-scope test.

## 5.8 Exit criterion

Tests green; a full in-sample dry run on a synthetic dataset end to end.
No real-data result in this phase.
