# Phase 6 — Experiments

Read with `00-overview.md`. Delivers the pre-registration file and its
loader, variant identity, the append-only registry, grid enumeration, the
multiprocessing runner with resume, the baselines runner, and the one-shot
holdout runner. Depends on Phase 5.

**Branch:** `phase/6-experiments`, created from `dev` after Phase 5 is
merged; merged into `dev` with `--no-ff` when the exit criterion below is
met (overview §8.1).

## 6.1 Pre-registration (`experiments/prereg.py`)

`configs/prereg.yaml` (full text in §6.8) declares the periods, pairs,
sessions, primary parameters, execution defaults with per-pair overrides,
the grid, the statistics settings, and the verdict rule. The loader
validates it against the Phase 0 dataclasses and computes
`prereg_hash = sha256(canonical_json(normalised content))`, so comments,
formatting, key order and number spelling do not matter. "Normalised"
means every value that maps to a Phase 0 dataclass field went through
that dataclass (`r_target: 2` and `2.0` hash the same; a grid axis value
is normalised as the `StrategyParams` field it sets) and dates are ISO
strings. The loader rejects duplicate YAML keys, unknown keys, session
names other than `utc`, `ny`, `london`, and a `primary` or `execution`
block that leaves any field to its default (a pre-registration names
every value).

`configs/prereg.lock` is written once by `perpbt prereg freeze` in Phase 8
(it refuses while `data_download_date` is null, and refuses to overwrite)
and committed:

```yaml
frozen_on: 2026-09-27
prereg_hash: <sha256>
code_version: <sha256 of perpbt/ at freeze>
git_commit: <hash>
data_download_date: 2026-09-27
```

After freezing, `prereg.yaml` may still receive `data_download_date` and
`holdout.end` (they are excluded from the hash); any other edit changes
the hash and the holdout runner refuses to run.

## 6.2 Variant identity

`VariantConfig.variant_id(code_version)` = SHA-256 of the canonical JSON of
the config plus the code version (D15). Two runs with the same id are the
same experiment and produce byte-identical outputs. A change to any file
under `perpbt/` yields new ids for everything; a change to docs, tests, or
configs does not.

## 6.3 Registry (`experiments/registry.py`)

- `runs/registry.jsonl`, append-only. Every attempt appends a `started`
  line, then an `ok` or `failed` line, so the number of `started` lines is
  the number of attempts and a killed process leaves a `started` line
  without a final one:
  `{variant_id, kind: run|baselines, status: started|ok|failed, run_ms,
  code_version, git_commit, seed, is_holdout, params_json,
  artifacts_path, error, reason, batch, runtime_s}`. `error` holds the
  traceback, `reason` the holdout code-change reason, `batch` one id per
  CLI invocation. Worker processes append under an OS file lock
  (`registry.jsonl.lock`), so lines never interleave.
- `runs/results.parquet`, derived: rebuilt from every
  `runs/<variant_id>/stats.json` (with its `config.yaml`) by every driver
  and family pass, and on demand (`perpbt stats results`); one row per
  variant. `run_ms`, `git_commit` and `runtime_s` come from the variant's
  latest `ok` run line.

### `results` columns

| column | notes |
|---|---|
| variant_id, run_ms, code_version, git_commit, seed | |
| params_json | full VariantConfig |
| pair, session_variant, period_start, period_end, is_holdout, is_primary, members | `members`: JSON list of the heatmaps/singles the variant belongs to |
| n_sessions, n_impulses, n_blocks_seen, n_orders, n_filled, fill_rate, n_trades, n_trades_r | `n_trades_r` excludes `data_end` |
| skip_* | one column per skip reason |
| win_rate, mean_gross_r, mean_net_r, mean_net_r_ci_lo/hi | trade bootstrap |
| mean_net_r_ci_block_lo/hi | day-block bootstrap |
| sharpe_ann, sharpe_ci_lo/hi | daily, block bootstrap |
| max_dd, exposure_time, exposure_notional, avg_hold_min, avg_cost_r, share_cost_r_gt_1, max_concurrent | |
| p_a, z_a, p_b, z_b, n_baseline_runs | nullable until baselines run |
| p_a_adj, p_b_adj, p_bh_adj | Holm-adjusted; primary cells only |
| bh_sharpe, sharpe_diff, sharpe_diff_ci_lo/hi, p_bh | vs buy-and-hold |
| dsr_local, dsr_global | N = 36 and N = 324 |
| reprice_json | the 8-cell re-pricing table |
| runtime_s, artifacts_path | |

## 6.4 Grid enumeration (`experiments/grid.py`)

From `prereg.grid`, for each pair × session: the cartesian product of each
heatmap's axes with every other parameter at the primary value, plus each
single. Deduplicate by `config_hash` (the primary appears in every
heatmap). Result: 36 unique variants per pair × session, 324 in total,
each flagged with the heatmaps or singles it belongs to (`heatmap_1..3`,
`single_1..5`; the primary belongs to all three heatmaps) and
`is_primary`. A variant's `stats.baseline_runs` is
`baseline_runs_primary` for a primary cell and `baseline_runs_grid`
otherwise. `perpbt grid --dry-run` prints the list and the count.

## 6.5 Runner (`experiments/runner.py`)

```python
def run_variant(cfg: VariantConfig, data_cfg: DataConfig, *, runs_dir, labels=None,
                baseline_runs=None, insample_ref=None, allow_holdout=False,
                reason=None, batch="") -> Path
```

The master seed is `cfg.stats.master_seed` (part of the variant id).
Loads 15m candles from `warmup_start`, 1m candles (if `use_1m`) and
funding from `period_start`, all to `period_end`, through the stores; the
guard is active unless `allow_holdout`, which only `holdout.py` passes, so
a holdout config sent through the normal runner raises
`HoldoutAccessError`. Builds the calendar, runs the simulator (which
builds the indicators), computes Phase 5 statistics (and baselines when
`baseline_runs`), and writes `runs/<variant_id>/`:
`{orders,fills,trades,daily,events}.parquet` (trades with the regime
labels), `buyhold.parquet` (`bh_daily`, for Phase 7's equity figure),
`config.yaml`, and last `stats.json`, which gains an `experiment` block
(`is_primary`, `members`, `code_version`). An existing `stats.json` is
deleted first, so its presence marks a complete folder. Nothing time- or
commit-dependent goes into the folder. The registry gets `started`, then
`ok` or `failed` with the traceback.

Driver: `ProcessPoolExecutor` with `spawn` (Windows), one variant per
task, `--workers` default 8, tasks sorted by pair. Each worker process
caches the loaded data of one pair (keyed by pair, timeframe, range and
the guard flag; another pair evicts it), so a pair's candles are read once
per process and memory stays at one pair per worker. Resume: a variant
whose `stats.json` exists and whose latest `run` line is `ok` is skipped;
`--force` reruns. A failed variant does not stop the others.

Baselines are a separate pass because they need the trade table:
`perpbt baselines --primary` and `perpbt baselines --grid` (non-primary
grid variants; `--runs M` overrides the prereg budget) re-simulate the
stored variant (deterministic, a few seconds), refuse if the result's
`summary` differs from the stored `stats.json`, write
`baseline_a.parquet` (the slot table), `baseline_a_runs.parquet` and
`baseline_b.parquet` (per-run component means of A and B; the Phase 7
histograms) into the variant folder, and rewrite `stats.json`. A variant
whose `stats.json` already has that budget is skipped unless `--force`.

Family passes, run last because any rewrite of a `stats.json` (a rerun
or a baselines pass) drops their values:
- `perpbt stats holm` refuses until all nine in-sample primary cells have
  baselines, then writes a `holm` block into each (`family`,
  `variant_ids`, `n`, `alpha`, `p_{a,b,bh}_adj` and, for reference,
  `p_{a,b,bh}_bonf`).
- `perpbt stats dsr` refuses until every in-sample grid variant has a
  `stats.json`, then fills `dsr.local` (trials: the pair × session's 36)
  and `dsr.global` (all 324) of every grid variant with
  `dsr.dsr_from_trials` (`N`, `V`, `sr_star`, `dsr`).

## 6.6 Holdout runner (`experiments/holdout.py`)

`perpbt holdout` is the only code path that loads data with
`allow_holdout=True`. It:

1. Requires `configs/prereg.lock`; recomputes `prereg_hash` and refuses on
   mismatch. Requires `holdout.end`, and the in-sample primary
   `stats.json` of every cell under the current code (its `insample_ref`:
   in-sample vol median and σ's for the regime labels and the
   buy-and-hold scaling).
2. Refuses if `runs/holdout/DONE` exists.
3. Compares `code_version` with the lock. On mismatch it refuses unless
   `--allow-code-change --reason "<text>"` is given; the reason is written
   to the registry and printed in the summary. This allows a bug fix
   after freezing without hiding it.
4. Runs the nine primary configurations, and only those
   (`check_holdout_config` rejects anything else), with `is_holdout=True`,
   period `holdout.start` to `holdout.end`, in one invocation and in
   process, including baselines at the primary budget and the Holm
   adjustment over the holdout family.
5. Writes `runs/holdout/DONE` (YAML) with the timestamp, the status, the
   batch, both hashes (prereg and code version, plus the lock's code
   version), the reason, the earlier holdout batches found in the
   registry, and the list of variant ids. `DONE` is written even when a
   cell fails, so any second touch needs its manual deletion.

There is no `--force`. Rerunning the holdout means deleting `DONE` by
hand, which the summary will report as a second touch (the registry keeps
every attempt).

## 6.7 CLI

```
perpbt prereg validate | freeze
perpbt grid --dry-run | --run [--workers N] [--force]
perpbt run --primary [--pair BTCUSDT --session ny] [--workers N] [--force]
perpbt baselines --primary|--grid [--runs M] [--workers N] [--force]
perpbt stats holm | dsr | results
perpbt holdout [--allow-code-change --reason "..."]
```

Every command takes `--prereg` (default `configs/prereg.yaml`),
`--data-dir` (default `data`) and `--runs-dir` (default `runs`). Exit 1
when a variant failed, a family pass is incomplete, or the holdout is
refused; 2 for a config or usage error.

## 6.8 `configs/prereg.yaml` (v2 draft)

```yaml
registered_on: 2026-09-26
data_download_date: null            # set at fetch; excluded from the hash
insample: {start: 2020-01-01, end: 2025-12-31}
holdout:  {start: 2026-01-01, end: null}   # end = data_download_date; excluded from the hash
warmup_start: 2019-11-01
pairs: [BTCUSDT, ETHUSDT, SOLUSDT]
listing: {BTCUSDT: 2019-09-08, ETHUSDT: 2019-11-27, SOLUSDT: 2020-09-14}
sessions:
  utc:    {tz: UTC,              open: "00:00", close: "24:00", days: [0,1,2,3,4,5,6]}
  ny:     {tz: America/New_York, open: "09:30", close: "16:00", days: [0,1,2,3,4]}
  london: {tz: Europe/London,    open: "08:00", close: "16:30", days: [0,1,2,3,4]}
primary:
  swing_k: 2
  confirm_n: 3
  zone: full
  entry_level: top
  stop_buffer: {kind: atr, value: 0.1}
  r_target: 2.0
  hold_rule: {kind: none}
  trend_filter: false
  pierce: 0.0
  structure_break: fresh            # D2
  skip_mitigated: continue          # D4
execution:
  fee_maker: 0.0002
  fee_taker: 0.0005
  slippage: 0.0002
  mmr: 0.004
  risk_per_trade: 0.01
  max_leverage: 25
  start_equity: 10000
  use_1m: true
  per_pair:
    ETHUSDT: {mmr: 0.005}
    SOLUSDT: {mmr: 0.010, slippage: 0.0005}
grid:                                # other axes held at primary values
  heatmaps:
    - {axes: [r_target, entry_level], r_target: [1, 1.5, 2, 3], entry_level: [top, mid]}
    - {axes: [swing_k, confirm_n], swing_k: [1, 2, 3], confirm_n: [2, 3, 5]}
    - {axes: [stop_buffer, hold_rule],
       stop_buffer: [{kind: atr, value: 0}, {kind: atr, value: 0.1}, {kind: atr, value: 0.25}, {kind: atr, value: 0.5}],
       hold_rule: [{kind: none}, {kind: session_end}, {kind: max_hold, hours: 24}, {kind: max_hold, hours: 72}]}
  singles:
    - {zone: body}
    - {pierce: 0.0005}
    - {trend_filter: true}
    - {stop_buffer: {kind: pct, value: 0.001}}
    - {stop_buffer: {kind: pct, value: 0.0025}}
stats:
  bootstrap_n: 10000
  block_len_days: 10
  baseline_runs_primary: 5000
  baseline_runs_grid: 500
  alpha: 0.05
  master_seed: 20260926
  reprice_slippage: [0.0, 0.0002, 0.0005, 0.001]
  reprice_maker: [0.0, 0.0002]
primary_family:                      # D11
  cells: all pairs x all sessions at the primary parameters (9)
  correction: holm
  applied_per_benchmark: [baseline_a, baseline_b, buy_and_hold]
verdict_rule:                        # D11; inlined so it is part of the hash
  beats_baseline_a: "holm-adjusted one-sided p_a < alpha"
  beats_baseline_b: "holm-adjusted one-sided p_b < alpha"
  beats_buy_and_hold: "holm-adjusted one-sided p_bh < alpha and sharpe_diff > 0"
  reported_for: [insample, holdout]
  statistic_for_baselines: mean net R over the R subset (exit_reason != data_end)
  costs_for_verdict: the execution block above (re-pricing is descriptive only)
```

## 6.9 Tasks and tests

- **6.1 Pre-registration loader, hash, lock.** Same content in different
  key order or with comments → same hash; changing any hashed field
  changes it; changing `data_download_date` or `holdout.end` does not;
  invalid values (unknown session name, `max_hold` without hours) raise;
  `freeze` writes the lock and refuses to overwrite it.
- **6.2 Variant ids and registry.** Same config, different key order →
  same id; a changed `.py` byte → new id; a changed test or doc file →
  same id; the number of `started` lines equals attempts; concurrent
  appends from spawned processes never interleave; `results.parquet`
  rebuilt from `stats.json` files matches the registry's `ok` set.
- **6.3 Grid enumeration.** Dry run yields exactly 36 unique variants per
  pair × session, 324 total, each primary flagged once; every grid
  variant differs from the primary in exactly the axis or axes of its
  heatmap or single.
- **6.4 Runner and resume.** On synthetic data with a tiny grid: all
  outputs written; an interrupted run (simulated failure in one worker)
  resumes without rerunning finished variants; `--force` reruns; outputs
  are byte-identical across two full runs; the driver works under
  `spawn`.
- **6.5 Baselines runner.** Writes both Parquet files and updates
  `stats.json`; a second invocation with the same seed is byte-identical.
- **6.6 Holdout runner.** Second invocation refuses; hash mismatch
  refuses; code-version mismatch refuses without the flag and records the
  reason with it; a non-primary config is rejected; in-sample loads still
  raise `HoldoutAccessError` after a holdout run (the guard is not
  globally disabled).

Exit criterion: `perpbt grid --dry-run` prints 324; the full pipeline
(Phase 8's commands in order, through the CLI) runs end to end on a
synthetic data directory in the test suite. Then
merge into `dev` and delete the branch.
