# Phase 5 — Experiments

Read with `00-overview.md`. Delivers the variant set, the runner, the
registry, the code lock, the spot holdout runner, and the forward runner.

**Branch:** `trend/5-experiments`, from `dev` after Phase 4 is merged.

## 5.1 Variants

From the frozen pre-registration: the **primary** (long/flat, daily, 00:15
fills) and the five **exploratory** variants. Each variant is run for the
portfolio and for each pair alone. `variant_id` = hash of the variant's
normalised content plus the period and market (same mechanism as the first
strategy's `config_hash`).

## 5.2 Runner (`trend/runner.py`)

`run_variant(variant, period) → runs/trend/<variant_id>/`:
`daily.parquet`, `daily_pair.parquet`, `fills.parquet`, `events.parquet`,
`bh_daily.parquet`, `baseline_runs.parquet` (Sharpe per random path),
`reprice.parquet`, `stats.json`. Outputs are byte-identical on rerun. The
primary runs with `M = 5,000`; exploratory variants with `M = 1,000`
(their p1 is descriptive). *(builder's call)* No multiprocessing is needed
at these runtimes.

## 5.3 Registry

`runs/trend/registry.jsonl` through `experiments.registry.Registry` (same
append-only file and lock). Trend rows: `variant_id, name, kind (run |
holdout | forward), period, market, status, code_version, git_commit,
prereg_hash, batch, started_ms, finished_ms, reason`. `results.parquet` is
one row per variant and period with the headline numbers.

## 5.4 Code lock

`perpbt trend lock-code` fills the lock's `code_version`,
`code_locked_on`, and `code_git_commit`, and sets
`periods.forward.start` to the next UTC day. It refuses if already set. It
is run immediately before the in-sample primary run (Phase 7).

**Scope of the trend code version** *(builder's call)*. The forward
confirmation is three years away; hashing all of `perpbt/` would make any
later work in the repo (a third strategy, a data-layer fix to keep
fetching) a "code change" for it. The trend code version is
`version.code_version()` restricted to the files the trend runs use:
`perpbt/trend/`, `perpbt/data/`, `perpbt/indicators/daily.py`,
`perpbt/stats/` (bootstrap, dsr, diagnostics, seeds),
`perpbt/experiments/registry.py`, `perpbt/config.py`, `perpbt/version.py`,
`perpbt/checks.py` — the list is a constant in `trend/prereg.py` and a
test fails if a trend module imports a `perpbt` module outside it. The
first strategy's `code_version()` default is unchanged.

**Output-neutral changes.** If the trend code version differs from the
lock, `holdout` and `forward` first re-run the in-sample primary with the
current code; if its `daily.parquet` and `stats.json` are byte-identical to
the locked run's, the change is recorded as output-neutral in the registry
and the run proceeds without `--allow-code-change`. Otherwise the flag and
a reason are required, as before.

## 5.5 Spot holdout (`trend/holdout.py`)

`perpbt trend holdout` runs the primary and its robustness rows (weekly,
fill offsets; long/short as exploratory) on the spot universe, once:

- Refuses if `runs/trend/holdout/DONE` exists, if the prereg hash differs
  from the lock, or if the code version differs from the locked one unless
  `--allow-code-change --reason "..."` is given (the reason goes into the
  registry and the summary).
- The only caller of `allow_holdout=True` on the spot store.
- Funding: the assumed `spot_funding_per_8h` at 00:00, 08:00, 16:00 UTC
  on the held position. **The holdout verdict uses the pre-registered perp
  costs plus this assumed funding.** The zero-funding row and a spot-fee
  row (taker 0.10%) are descriptive only.
- T3 uses the in-sample `k`.
- Writes `DONE` at the end even if a variant failed (as the first strategy
  does).

## 5.6 Forward runs (`trend/forward.py`)

`perpbt trend forward [--review 12|36]` runs the primary on perps from
`periods.forward.start` to the last complete UTC day, after fetching the
tail. Each run is a new registry row (`kind: forward`); earlier forward
results are kept, never overwritten. It is the only caller allowed to load
perp data after `insample_end`. The same code-version check as the
holdout applies. Universe fixed; members delisted later exit by the
delisting rule; no new members.

- `--review 12` (allowed once forward data covers 365 days): descriptive
  report — returns, turnover, cost and funding drag, exposure, and whether
  each sits inside the in-sample per-year range. No p-values.
- `--review 36` (allowed once forward data covers 1,095 days): T1 and T2
  on the forward period alone, the verdict rule unchanged. The forward and
  in-sample periods are never pooled.

## 5.7 CLI

`perpbt trend universe | prereg validate | prereg freeze | screen |
lock-code | run [--primary | --exploratory] | holdout | forward | report`.

## 5.8 Tests

- Holdout refusals (DONE, hash, code version), the DONE write on failure,
  the guard grep (`allow_holdout=True` only in `trend/holdout.py` and
  `trend/forward.py` within `perpbt/trend/`). The first strategy's
  `tests/test_holdout.py::test_allow_holdout_true_appears_only_in_holdout_py`
  is widened to allow exactly these two trend modules; it is the only
  first-strategy test edited.
- Forward: refuses before 365 / 1,095 days for the reviews; appends rows.
- Lock-code: refuses a second time; sets the forward start.
- Runner: byte-identical outputs on rerun; registry rows complete.

## 5.9 Exit criterion

Tests green; a full in-sample dry run on a **synthetic** dataset end to end.
No real-data result is produced in this phase.
