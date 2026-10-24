# Carry Phase 5 — Experiments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The carry variant set, runner, registry rows, `results.parquet`, scoped code lock, one-shot holdout runner, forward runner and `perpbt carry run | lock-code | holdout | forward`, proven on synthetic data end to end.

**Architecture:** `carry/runner.py` turns the frozen prereg into variants (7 rows × portfolio + each pair), runs one variant on PairFrames the caller loaded (Phase 3 `run_path`, Phase 4 `evaluate_row` / `row_diagnostics` / `primary_diagnostics`), writes `runs/carry/[holdout|forward/]<variant_id>/` and registry rows, and rebuilds `runs/carry/results.parquet`. `carry/prereg.py` gains the scoped code version and `lock_code`. `carry/holdout.py` and `carry/forward.py` are the only carry modules passing `allow_holdout=True`. A lean `experiments/registry_log.py` holds `Registry` so carry does not import the first strategy.

**Tech Stack:** Python 3.11, numpy, pandas, pyarrow, pytest.

**Spec:** `docs/superpowers/specs/2026-10-23-funding-carry/phase-5-experiments.md` (with `00-overview.md` CD5, CD11, CD13, §4, §10.6; trend `phase-5-experiments.md` as the model; carry Phase 2 §2.6, Phase 3 §3.8, Phase 4 §4.7).

## Global Constraints

- Data only through `carry.frames.load_pairframe`; only `carry/holdout.py` and `carry/forward.py` pass `allow_holdout=True` (AST test in `tests/carry/test_frames.py`; first-strategy grep test widened by exactly these two).
- No `.ret` attribute read in `perpbt/carry/` (read `daily["ret"]`); only `frames.py`/`basis.py` name the stores.
- Shared modules change only backward compatibly; the first strategy's and trend's suites and `tests/carry/test_real_screen.py` stay green.
- Do not change `configs/carry/prereg.yaml`, `configs/carry/prereg.lock` or `runs/carry/` in the repo; never run `perpbt carry lock-code | run | holdout | forward | screen | prereg freeze` or any first-strategy/trend run command.
- No real-data performance number: real-data tests check structure, runtime, determinism only.
- Seeds only through `verdict.Seeds` purposes (`<period>/<row>/…`), per-pair rows named `<row>/<pair>`.
- Outputs byte-identical on rerun: nothing time- or commit-dependent inside a variant folder.

## Review Focus

1. A pair whose frame ends before the period end (EOS in the holdout/forward) — the portfolio treats it as a delisting; its per-pair variant is skipped with a registry row, not failed. Test in Task 3.
2. Data not fetched through the window end (a live pair ending a few days early would read as a delisting) — holdout/forward refuse before running. Test in Tasks 5/6.
3. Holdout crashing mid-way (data load or a variant) — `DONE` still written with `status: failed`. Test in Task 5.
4. Lock-code on a prereg whose `forward:` block is in block style or flow style — the edit lands and the hash is unchanged. Test in Task 2.
5. A rerun of `carry run` after a complete run — complete variants are skipped, never overwritten. Test in Task 4.

---

### Task 1: Per-event funding intervals in the ccxt tail; lean registry module

**Files:** Modify `perpbt/data/ccxt_fetch.py` (`fetch_funding_range`), `perpbt/data/fetch.py` (`fetch_tail`); Create `perpbt/experiments/registry_log.py`; Modify `perpbt/experiments/registry.py`; Tests `tests/test_ccxt_fetch.py`, `tests/test_fetch.py`, `tests/test_registry.py`.

**Produces:** `fetch_funding_range(exchange, pair, start_ms, end_ms, *, interval_h, prev_ms=None, limit=...)`; `registry_log.{REGISTRY_FILE, now_ms, file_lock, Registry}` re-exported by `registry.py`.

- [ ] Step 1: Tests: events 8 h, 8 h, then 4 h → stamped 8, 8, 4, 4…; a missing event (16 h gap) keeps the previous interval; the first event takes its gap from `prev_ms`; `prev_ms=None` → the `interval_h` argument for the first; `fetch_tail` passes the manifest's `last_funding_ms` (existing 4 h test still green; new test: stored 8 h, fetched tail switches to 4 h → stored intervals 8…4). `registry.Registry is registry_log.Registry`.
- [ ] Step 2: Run → fail. Step 3: implement — interval of event k = hours since the previous event when in `validate.ALLOWED_INTERVALS`, else the previous event's interval (the bulk archive's convention, checked on stored data: the gap before each event matches its interval on every carry pair). Step 4: green. Commit.

### Task 2: Scoped code version and `lock_code`

**Files:** Modify `perpbt/version.py` (+ `files_code_version`), `perpbt/carry/prereg.py` (+ `CARRY_CODE_PATHS`, `carry_code_version`, `lock_code`, `LockRefused`); Tests `tests/test_version.py`, `tests/carry/test_code_lock.py`.

**Produces:**
- `files_code_version(paths: Sequence[str], root=PACKAGE_DIR) -> str` — entries relative to `root`; `"x/"` = every `*.py` under `x`; same absorption as `code_version` over the resolved files in sorted POSIX order; a missing entry raises.
- `CARRY_CODE_PATHS` = spec §5.3's list made exact: `__init__.py, carry/, trend/__init__.py, trend/config.py, trend/daily.py, trend/prereg.py, data/, indicators/__init__.py, indicators/daily.py, indicators/atr.py, stats/__init__.py, stats/bootstrap.py, stats/dsr.py, stats/diagnostics.py, stats/seeds.py, experiments/__init__.py, experiments/registry_log.py, config.py, version.py, checks.py`.
- `carry_code_version(root=PACKAGE_DIR) -> str`.
- `lock_code(prereg_path, *, today: date | None = None, code_version: str | None = None) -> dict` — `check_frozen`; refuses (`LockRefused`) if the lock's `code_version` is set or `periods.forward.start` is set; fills `code_version`, `code_locked_on`, `code_git_commit`; sets `forward.start` = today + 1 day in the YAML text (first `start: null` after the `forward:` key; flow or block style); re-parses and refuses unless the hash is unchanged and `forward_start` is the new day; writes both files via temp + `os.replace`.

- [ ] Step 1: Tests: transitive import closure of every `perpbt.carry.*` module (AST, imports inside functions included, parent packages' `__init__` included) ⊆ files of `CARRY_CODE_PATHS`; every entry exists; editing `perpbt/report/x.py` or `experiments/runner.py` in a tmp copy leaves `carry_code_version` unchanged, editing `carry/x.py` changes it; `lock_code` once (second call refuses), forward start = next day, hash unchanged, lock keys exact, works on flow-style (real file text) and block-style YAML, refuses unfrozen / hash mismatch.
- [ ] Step 2: Run → fail. Step 3: implement. Step 4: green. Commit.

### Task 3: Variants and `run_variant`

**Files:** Create `perpbt/carry/runner.py`; Test `tests/carry/test_runner.py`, helper `tests/carry/runenv.py` (synthetic stores and prereg).

**Produces (runner.py):**
- `Period(name, start, end)` (frozen; `name` in `verdict.PERIODS`); `insample_period(prereg)`, `holdout_period(prereg)`.
- `Variant(row, scope, signal, sleeve, pairs)` — `scope` `"portfolio"` or a pair; `.name` (`row` or `row/pair`), `.content(prereg_hash, period) -> dict`, `.variant_id(prereg_hash, period) -> str` (`config_hash` of the content, 64 hex).
- `variants(prereg, rows: Sequence[str] | None = None) -> list[Variant]` — rows in `row_names` order, each portfolio then each member.
- `load_frames(data_cfg, pairs, *, end, allow_holdout=False) -> dict[str, PairFrame]`; `check_data_config(prereg, data_cfg)`.
- `live_through(frames, variant, period) -> bool` — every frame of the variant reaches `period.end`.
- `CarryRun(prereg, frames, period, out_root: Path, registry: Registry, kind, batch, code_version, reason=None)` with `.run(variant) -> Path` (writes `out_root/<id>/`) and `.skip(variant, why)`.
- Folder: `daily, daily_pair, fills, events, reprice.parquet`, `random_timing.parquet` (portfolio primary only), `variant.json`, `stats.json` last. Registry rows: `variant_id, name, kind, period, period_start, period_end, market ("um+spot"), status (started|ok|failed|skipped|output_neutral), code_version, git_commit, prereg_hash, batch, started_ms, finished_ms, reason, error`.

- [ ] Step 1: Tests: 77 distinct ids for the real prereg; id changes with period and row, not with code version; a run writes every file, `stats.json` row/period/schema; rerun into the same folder → identical bytes for every file; per-pair row named `primary/AAAUSDT`, no `random_timing.parquet`; registry `started` then `ok` with all keys; a failing variant → `failed` row with traceback, exception re-raised; per-pair variant of a pair ending early → `live_through` false and `skip` writes a `skipped` row.
- [ ] Step 2: Run → fail. Step 3: implement. Step 4: green. Commit.

### Task 4: `results.parquet`, code check, `carry run` and `lock-code` CLI

**Files:** Modify `perpbt/carry/runner.py` (+ `rebuild_results`, `code_check`, `insample_primary_identical`, `run_insample`); Modify `perpbt/cli.py`; Tests `tests/carry/test_runner.py`, `tests/carry/test_run_cli.py`.

**Produces:**
- `rebuild_results(carry_root) -> Path` — one row per `stats.json` under `carry_root/<id>`, `holdout/<id>`, `forward/<id>`; columns `RESULT_COLUMNS` (variant_id, kind, name, row, scope, period, period_start, period_end, n_days, ann_return(+ci), ann_excess(+ci), p2, v2_passes, v2_reading, sharpe(+ci), p1, v1_passes, edge, reading, cagr, ann_vol, max_drawdown, share_on, entries, rebalances, liquidations, code_version, git_commit, artifacts_path); review documents have null test columns.
- `code_check(lock, cv, *, allow_code_change, reason, neutral: Callable[[], bool]) -> dict` → `{"code_version", "lock_code_version", "output_neutral", "reason"}`; refuses (`CodeChanged`) when unlocked, or changed, not neutral, no flag+reason.
- `insample_primary_identical(prereg, frames, carry_root, cv) -> bool` — reruns the in-sample portfolio primary into a temp dir (temp registry) and compares `daily.parquet` and `stats.json` bytes with `carry_root/<id>/`.
- `run_insample(prereg, data_cfg, carry_root, *, rows, code: dict, batch) -> dict` — skips complete variants (stats.json present and latest registry row `ok`), records `output_neutral` row when applicable, runs the rest (a failure does not stop the others), rebuilds results; returns `{"ok", "exists", "failed", "skipped"}` lists.
- CLI `perpbt carry run [--primary | --exploratory] [--allow-code-change --reason]` (exit 1 on refusal or a failed variant) and `perpbt carry lock-code`.

- [ ] Step 1: Tests: results rows per folder kind with the documented columns; `code_check` cases (unlocked, equal, changed+neutral, changed+flag+reason, flag without reason, changed alone); CLI: `run` refuses before lock-code; after `lock_code(today=…)` `run --primary` writes 4 portfolio+pair folders for 3 synthetic pairs (1 + 3) and a results table; rerun reports `exists` and leaves bytes unchanged; `lock-code` CLI refuses the second time.
- [ ] Step 2: Run → fail. Step 3: implement. Step 4: green. Commit.

### Task 5: Holdout

**Files:** Create `perpbt/carry/holdout.py`; Modify `perpbt/carry/frames.py` (+ `stored_last_ms`), `perpbt/cli.py`, `tests/test_holdout.py` (grep test widened); Tests `tests/carry/test_holdout.py`.

**Produces:**
- `frames.stored_last_ms(data_cfg, pair) -> int | None` — the earlier of the perp and spot 15m manifests' `last_open_ms` (metadata only; None when either is missing).
- `check_caught_up(data_cfg, pairs, end)` (runner) — refuses when a pair's stored data ends within `DELISTED_AFTER_DAYS` before `end`'s last candle (a stale fetch, not a delisting).
- `run_holdout(prereg_path, data_cfg, *, runs_dir, allow_code_change=False, reason=None, code_version=None) -> dict` — refusals (`HoldoutRefused`): hash, `DONE`, unlocked code, no in-sample primary `stats.json`, code changed (after the output-neutral check), stale data; then every variant on `holdout_period` under `runs/carry/holdout/`, skips per-pair variants not live through the end, writes `DONE` (JSON) in `finally`, rebuilds results.
- CLI `perpbt carry holdout [--allow-code-change --reason]`.

- [ ] Step 1: Tests: each refusal (and nothing loaded/written); `DONE` written with `status: failed` when a variant raises (monkeypatched) and when loading raises; second run refuses; output-neutral path records the row and proceeds; the widened grep test equals `["carry/forward.py", "carry/holdout.py", "experiments/holdout.py"]`.
- [ ] Step 2: Run → fail. Step 3: implement. Step 4: green. Commit.

### Task 6: Forward

**Files:** Create `perpbt/carry/forward.py`; Modify `perpbt/cli.py`; Test `tests/carry/test_forward.py`.

**Produces:**
- `REVIEW_DAYS = {12: 365, 36: 1095}`; `review_period(prereg, months) -> Period` (`forward_12m` / `forward_36m`, `start` = `forward_start`, `end` = start + days − 1).
- `fetch_tails(data_cfg, pairs, **run_fetch_kw)` — `run_fetch` for `um` with `ccxt_tail=True`, then `spot`.
- `review_document(result, cfg, insample_stats, *, prereg_hash, period) -> dict` (schema `carry-forward-review/1`, no p-value): `annual` (point Sharpe, no CI), `decomposition`, `margin`, `metrics` {ann_return, funding, basis, cost_drag, entries_per_year, share_on}: forward value, in-sample per-year min/max (annualised by `365 / n_days`), `inside`.
- `run_forward(prereg_path, data_cfg, *, runs_dir, review, today=None, allow_code_change=False, reason=None, code_version=None, fetch=None) -> Path` — refusals (`ForwardRefused`): hash, no forward start, review not pre-registered, fewer days than the review needs, folder exists, code changed, stale data, review 12 without the in-sample primary `stats.json`; review 36 → `CarryRun.run(primary portfolio)`; review 12 → tables + review `stats.json`; registry `kind: forward`.
- CLI `perpbt carry forward --review {12,36} [--no-fetch] [--allow-code-change --reason]`.

- [ ] Step 1: Tests: refuses at 364 / 1,094 days, runs at 365 / 1,095; review 12 document has no key `p` anywhere and the `inside` flags against a hand in-sample table; review 36 stats.json period `forward_36m`; second run of a review refuses; each run appends registry rows; `fetch_tails` calls `run_fetch` twice with the right markets (monkeypatched).
- [ ] Step 2: Run → fail. Step 3: implement. Step 4: green. Commit.

### Task 7: Synthetic dry run, real-config checks, notes, suite, review

**Files:** Test `tests/carry/test_dry_run.py` (slow), `tests/carry/test_real_variants.py`; Modify `.gitignore`; spec `phase-5-experiments.md` §5.9; this plan's progress log.

- [ ] Step 1: Dry run on synthetic stores 2020-01-01 .. 2027-11: freeze → `lock_code(today=2026-10-25)` → CLI `carry run` → CLI `carry holdout` → `run_forward(review=12, today=2027-10-27)`; asserts the folders, `DONE` ok, registry kinds, results rows, every `stats.json` schema; a second CLI `carry run` changes nothing.
- [ ] Step 2: Real config (no data loaded): 77 distinct variant ids; `check_data_config` passes on `configs/carry/data.yaml`; `carry_code_version` hex64.
- [ ] Step 3: `.gitignore` admits `runs/carry/registry.jsonl`, `results.parquet`, `*/stats.json`, `holdout/DONE`. Spec §5.9 notes. Full suite (max timeout) green. Commit.
- [ ] Step 4: Fresh whole-branch review on the most capable model; fix; suite green; STOP before the merge.

## Progress log

Spec: docs/superpowers/specs/2026-10-23-funding-carry/phase-5-experiments.md
