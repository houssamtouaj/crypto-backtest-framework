# Phase 5 — Experiments

Read with `00-overview.md`. Delivers the runner, the registry, the code
lock, the holdout runner and the forward runner.

**Branch:** `etf/5-experiments`, from `dev` after Phase 4 is merged.

## 5.1 Variants

An `EtfVariant` is the frozen prereg's primary or one exploratory row,
merged, plus a period (`insample`, `holdout`, `forward`). `variant_id` =
`config_hash` of the merged variant, the period, `prereg_hash` and the code
version.

## 5.2 Runner (`etf/runner.py`)

`perpbt etf run [--exploratory] [--period insample]`:

1. Gate: lock present, `prereg_hash` matches, manifest matches, code
   version matches the lock (once locked).
2. Load frames (with `allow_holdout` only in the holdout and forward
   runners).
3. Run the strategy, the benchmarks (§3.4) and, for the primary and each
   exploratory row, `M` random paths; per-ETF runs; re-pricing cells.
4. Write `runs/etf/<variant_id>/`: `daily.parquet`, `daily_etf.parquet`,
   `fills.parquet`, `events.parquet`, `baseline.parquet` (path Sharpes),
   `reprice.parquet`, `stats.json`, `variant.json`.
5. Append one row to `runs/etf/registry.jsonl` (the shared
   `experiments/registry_log` append-only log and file lock) and rebuild
   `runs/etf/results.parquet`.

A run whose `stats.json` exists is skipped unless `--force`. Outputs are
byte-identical on rerun.

## 5.3 Code lock (`etf/prereg.py`)

`ETF_CODE_PATHS` lists `etf/` and every shared module it imports,
directly or through others. Importing `trend/signals.py` pulls in
`trend/config.py` and `trend/daily.py`, which pull in `data/__init__.py`,
`data/store.py`, `indicators/__init__.py`, `indicators/daily.py`,
`indicators/atr.py` and `checks.py`. Start from that chain plus
`trend/__init__.py`, `trend/signals.py`, `trend/prereg.py` (if its lock
helpers are reused, as carry does), the `stats/` and `experiments/`
modules used, `config.py`, `version.py` and `__init__.py`.
`report/etf/` is outside it. A test pins the list against the actual
import graph of `perpbt.etf` (no missing module, nothing unused).

`perpbt etf lock-code` fills `code_version`, `code_locked_on`,
`code_git_commit` in `prereg.lock` and sets `periods.forward.start` to the
day after the lock. It refuses a dirty tree under `ETF_CODE_PATHS`.

## 5.4 Holdout (`etf/holdout.py`)

`perpbt etf holdout` runs the primary and the exploratory rows over
2018-01-01 – `holdout_end`, once:

- refuses if `runs/etf/holdout/DONE` exists, if the code version differs
  from the lock (unless `--allow-code-change --reason`), or if the
  in-sample runs are not all present;
- writes `DONE` (batch id, time, status) last, and also on failure, so a
  failed holdout is not silently retried; a second attempt needs a recorded
  `DONE` deletion with a reason in the registry;
- each `stats.json` carries `insample_ref` (the in-sample variant id) and
  the T3 scale `k` from it.

## 5.5 Forward (`etf/forward.py`)

The forward period needs prices after the frozen snapshot. `perpbt etf
forward-fetch` downloads fresh data into `data/etf/forward/<download_date>/`
(same sources and validation) and checks it against the frozen store on the
overlap: closes within 0.1% on 99% of days and dividends equal, else it
refuses. Forward runs use the frozen snapshot up to `holdout_end` (for
warmup) and the fresh data after it.

- `perpbt etf forward --review 12` needs data through
  `forward.start + 12 months`; descriptive only (no verdict).
- `perpbt etf forward --review 36` runs T1 and T2 with the verdict.

## 5.6 Tests

- Variant ids: stable, change with any field.
- Runner: skip and force, byte-identical rerun, registry append.
- Code lock: dirty-tree refusal; `ETF_CODE_PATHS` against the import graph;
  the carry guard still equal to `7ce43816…`.
- Holdout: refuses twice, refuses before in-sample runs, writes `DONE` on
  failure.
- Forward: overlap check accepts a matching store and refuses a revised one.

## 5.7 Exit criterion

Tests green; the full in-sample primary run done once as a smoke test into
a scratch directory (not `runs/etf/`) and its timing recorded.
