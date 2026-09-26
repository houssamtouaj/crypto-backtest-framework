# Phase 6 — Experiments

Read with `00-overview.md`. Delivers the pre-registration file and its
loader, variant identity, the append-only registry, grid enumeration, the
multiprocessing runner with resume, the baselines runner, and the one-shot
holdout runner. Depends on Phase 5.

## 6.1 Pre-registration (`experiments/prereg.py`)

`configs/prereg.yaml` (full text in §6.8) declares the periods, pairs,
sessions, primary parameters, execution defaults with per-pair overrides,
the grid, the statistics settings, and the verdict rule. The loader
validates it against the Phase 0 dataclasses and computes
`prereg_hash = sha256(canonical_json(loaded content))`, so comments and
formatting do not matter.

`configs/prereg.lock` is written once by `perpbt prereg freeze` in Phase 8
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

- `runs/registry.jsonl`, append-only, one line per run attempt:
  `{variant_id, status: started|ok|failed, run_ms, code_version,
  git_commit, seed, is_holdout, params_json, artifacts_path, error}`.
- `runs/results.parquet`, derived: rebuilt from every `stats.json` on
  demand; one row per variant.

### `results` columns

| column | notes |
|---|---|
| variant_id, run_ms, code_version, git_commit, seed | |
| params_json | full VariantConfig |
| pair, session_variant, period_start, period_end, is_holdout, is_primary | |
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
each flagged with the heatmap or single it belongs to and `is_primary`.
`perpbt grid --dry-run` prints the list and the count.

## 6.5 Runner (`experiments/runner.py`)

```python
def run_variant(cfg: VariantConfig, data_cfg: DataConfig, *, master_seed: int) -> Path
```

Loads candles (15m, and 1m if `use_1m`) and funding through the stores
(guard active unless `cfg.is_holdout`), builds the calendar and
indicators, runs the simulator, computes Phase 5 statistics, writes
`runs/<variant_id>/{orders,fills,trades,daily,events}.parquet`,
`stats.json`, `config.yaml`, and appends the registry rows (`started`,
then `ok` or `failed` with the traceback).

Driver: `ProcessPoolExecutor` with `spawn` (Windows), one variant per
task, `--workers` default 8. Each worker process caches loaded data per
`(pair, tf)` so a pair's candles are read once per process. Resume: a
variant whose `stats.json` exists and whose latest registry row is `ok`
with the same `variant_id` is skipped; `--force` reruns.

Baselines are a separate pass because they need the trade table:
`perpbt baselines --primary --runs 5000` and
`perpbt baselines --grid --runs 500` write `baseline_a.parquet` (the slot
table) and `baseline_b.parquet` (per-run component means) into the variant
folder and update `stats.json`. Holm adjustment runs once all nine primary
cells have baselines (`perpbt stats holm`).

## 6.6 Holdout runner (`experiments/holdout.py`)

`perpbt holdout` is the only code path that loads data with
`allow_holdout=True`. It:

1. Requires `configs/prereg.lock`; recomputes `prereg_hash` and refuses on
   mismatch.
2. Refuses if `runs/holdout/DONE` exists.
3. Compares `code_version` with the lock. On mismatch it refuses unless
   `--allow-code-change --reason "<text>"` is given; the reason is written
   to the registry and printed in the summary. This allows a bug fix
   after freezing without hiding it.
4. Runs the nine primary configurations, and only those, with
   `is_holdout=True`, period `holdout.start` to `holdout.end`, in one
   invocation, including baselines at the primary budget and the Holm
   adjustment over the holdout family.
5. Writes `runs/holdout/DONE` with the timestamp, both hashes, and the
   list of variant ids.

There is no `--force`. Rerunning the holdout means deleting `DONE` by
hand, which the summary will report as a second touch (the registry keeps
every attempt).

## 6.7 CLI

```
perpbt prereg validate | freeze
perpbt grid --dry-run | --run [--workers N] [--force]
perpbt run --primary [--pair BTCUSDT --session ny]
perpbt baselines --primary|--grid --runs M
perpbt stats holm
perpbt holdout [--allow-code-change --reason "..."]
```

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
  same id; registry row count equals attempts; `results.parquet` rebuilt
  from `stats.json` files matches the registry's `ok` set.
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
runs end to end on a synthetic data directory in the test suite.
