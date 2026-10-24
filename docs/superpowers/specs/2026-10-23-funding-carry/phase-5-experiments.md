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

## 5.9 Implementation notes (Phase 5 build, 2026-10-24)

Builder's calls taken while building this phase. Nothing was run on real data: no variant, holdout or forward
command ran, and no return, Sharpe, p-value, P&L or funding total was computed on real data. The calls are also in
the docstrings of `perpbt/carry/runner.py`, `holdout.py` and `forward.py`.

- **The Phase 2 pointer is fixed** *(my call)*. `data.ccxt_fetch.fetch_funding_range` stamps each fetched event
  with the hours since the previous event (the last stored event before the first), the convention of the bulk
  archive: on the stored data the gap before each event equals its `interval_h` on every carry pair (and on all but
  one SOLUSDT event of 2022-11). A gap that is not an allowed interval (a missing event) keeps the previous
  interval. `fetch_tail` passes the last stored event's time. Only timestamps and intervals were read for the check.
- **Registry module** *(my call)*. `experiments/registry.py` imports the first strategy (`stats.variant`,
  `strategy.order_block`). `Registry`, `file_lock` and `now_ms` moved to `experiments/registry_log.py`, re-exported
  by `registry.py` (backward compatible); carry imports the lean module.
- **Scoped code version** *(my call)*. `version.files_code_version(paths)` hashes listed files exactly as
  `code_version` hashes a tree. `carry.prereg.CARRY_CODE_PATHS` is §5.3's list made exact by the transitive import
  test: it adds `trend/prereg.py` and `trend/config.py` (the lock helpers carry reuses), the package `__init__.py`
  files (they run on import) and `experiments/registry_log.py` in place of `experiments/registry.py`. `cli.py` and
  `report/` are outside it.
- **Code lock.** `lock_code` refuses unless the prereg is frozen and unchanged, the lock has no code version and
  `forward.start` is null. It sets `forward.start` in the YAML text (the first `start: null` after `forward:`, flow
  or block style, comments kept), checks the new text keeps the hash, then replaces the prereg and the lock whole.
- **Variants** *(my call)*. 7 rows × (portfolio + 10 pairs) = 77 per period; a per-pair variant is named
  `<row>/<pair>` (its `stats.json` row and seed purposes). `variant_id` = SHA-256 of the canonical JSON of
  `{strategy, prereg_hash, row, scope, pairs, signal, sleeve, period}`: no code version (it is in the registry
  rows), so the locked in-sample run keeps its folder.
- **Per-pair rows** *(my call)*. Only the portfolio primary gets `filter_value`, `random_timing` (and
  `random_timing.parquet`) and `dsr`; every variant gets `annual`, `decomposition`, `margin`, `reprice`. A per-pair
  variant whose pair's frame ends before the period end (EOSUSDT in the holdout and forward) is skipped with a
  `skipped` registry row; the portfolio closes that pair by the delisting rule.
- **Folders and rows.** In-sample `runs/carry/<id>/`, holdout `runs/carry/holdout/<id>/`, forward
  `runs/carry/forward/<id>/`; files `daily`, `daily_pair`, `fills`, `events`, `reprice`(, `random_timing`)
  `.parquet`, `variant.json`, `stats.json` last; byte-identical on rerun. Registry rows (`runs/carry/registry.jsonl`)
  carry `variant_id, name, kind, period, period_start, period_end, market ("um+spot"), status, code_version,
  git_commit, prereg_hash, batch, started_ms, finished_ms, reason, error`; statuses `started | ok | failed | skipped
  | output_neutral`. `results.parquet` has one row per `stats.json` (run, holdout, forward) with the headline
  numbers; a 12-month review has null test columns.
- **Re-pricing note for Phase 6** (from Phase 4): no `reprice.parquet` cell equals the verdict's costs, because the
  pre-registered slippage is per pair and a cell's applies to every pair (noted in `runner.reprice_table`).
- **Code check** *(my call)*. `carry run`, `holdout` and `forward` refuse an unlocked code. When the carry code
  version differs from the lock, the in-sample portfolio primary is rerun into a temporary folder; if its
  `daily.parquet` and `stats.json` equal the bytes of the run made under the locked code (the folder's latest
  attempt must be an `ok` row with the lock's code version), an `output_neutral` row is recorded and the run
  proceeds; otherwise `--allow-code-change --reason` is required (the reason goes into every row and `DONE`; a
  `--reason` without the flag is ignored with a warning).
- **`carry run`** *(my call)*. Needs the code lock (Phase 7 locks immediately before it). A variant whose folder
  has a `stats.json` and whose latest attempt (`started | ok | failed` row; the code-check and `skipped` rows do not
  count) is `ok` is skipped, never overwritten (no `--force`); a failed variant does not stop the others.
- **Data through the end** *(my call)*. `runner.check_caught_up` refuses a run unless every pair either reaches the
  period's last day in full (both legs through its 23:45 candle, the perp's funding through its last event) or is a
  known delisting whose frame ends on its delisting day: `runner.KNOWN_DELISTINGS = {EOSUSDT: 2025-05-21}` (the
  perp's last candle is 2025-05-21 09:00; a slow test checks it against the store manifest). A distance rule ("ends
  more than 7 days early = delisted") was rejected: a leg never fetched past 2024-01-01 would read as ten delistings,
  and a real delisting near a forward window's fixed end would block the review for good. A delisting met later is
  added to the list before that run (a carry code change, output-neutral for the in-sample primary). `carry run`,
  `holdout` and `forward` all apply it.
- **Holdout** *(my call where §5.4 is silent)*. Refusals before any holdout load: the prereg hash, `DONE`, the code
  lock, no in-sample primary `stats.json`, the code check. Then every member is loaded through 2026-10-20 and the
  data check above may refuse; nothing has run then, so no `DONE` is written. All 77 variants run; `DONE` (JSON:
  status `ok | failed | interrupted`, failed, skipped, batch, hashes, code versions, output-neutral flag, reason,
  prior attempts, variant ids) is written in a `finally`, even on a failed variant or an interruption; a failing
  `results.parquet` rebuild is logged and does not mask the outcome. A hard kill writes no `DONE`; its `started`
  rows stay in the registry and a later holdout lists them in `prior_attempts`.
- **Code lock recovery.** A crash between `lock_code`'s two writes leaves `forward.start` = tomorrow and no code
  lock; rerun the same UTC day, it completes the lock. Any other set forward start refuses.
- **Forward** *(my call)*. `--review` is required. Each review runs the portfolio primary on a fixed window: the
  first 365 days (`forward_12m`) or 1,095 days (`forward_36m`) from `forward.start`, allowed once the last complete
  UTC day reaches its end, so the day it is run cannot choose the confirmatory end. The 12-month review's
  `stats.json` (`carry-forward-review/1`) has no p-value: annual stats with the point Sharpe, decomposition, margin
  events and six metrics (annualised return, funding, basis, cost drag, entries a year, share on) against the
  in-sample primary's per-year range. The 36-month review is the full `carry-stats/1` document. `carry forward`
  fetches both legs' 15m tails first (perps with the ccxt tail, spot from the archive) unless `--no-fetch`. A review
  whose folder has a `stats.json` refuses to run again.
- **`.gitignore`** admits `runs/carry/registry.jsonl`, `results.parquet`, every `stats.json` and
  `holdout/DONE`, for Phase 7.

**Exit criterion.** `tests/carry/test_dry_run.py` runs freeze → lock-code → `carry run` (28 variants of three
synthetic pairs) → a rerun that changes nothing → `carry holdout` (21 run, 7 per-pair skipped) → the 12-month
forward review, on synthetic stores (about 25 s).

**Review fixes (fresh reviewer, 2026-10-24: "merge with fixes"; 0 Critical, 2 Important, 7 Minor; all fixed or
noted).**
- (Important) An output-neutral `carry run` reran and overwrote the complete in-sample primary, because the
  `output_neutral` row became its latest row. The skip now looks at run attempts only; a test pins that the folder's
  bytes and mtimes stay unchanged.
- (Important) The data check ignored funding: candles through the end with funding stopping at a month end (no ccxt
  tail) passed, and a one-shot review would have run with days of zero funding. It now refuses; tests for both the
  unit check and end-to-end holdout/forward refusals.
- (Minor) The distance-based delisting rule was replaced by the known-delistings list (above).
- (Minor) The output-neutral reference must be the run made under the locked code.
- (Minor) `lock_code` completes a same-day interrupted lock.
- (Minor) `DONE` says `interrupted` when the run did not finish; a rebuild error cannot mask the outcome.
- (Minor) The CLI warns about `--reason` without `--allow-code-change`, and a run-time error in `carry run` exits 1
  (it was 2, "usage").
- (Minor) The `run_insample` docstring said nothing loads before the code check; the in-sample frames do.
- (Phase 6 pointer) Carry report code must live outside `perpbt/carry/` (the spec's `perpbt/report/carry/`):
  `carry/` is in `CARRY_CODE_PATHS`, so a report edit there after the lock would be a carry code change and trigger
  the output-neutral rerun at every holdout or forward run.
