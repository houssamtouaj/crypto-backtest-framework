# Phase 2 — Pre-registration and screen

Read with `00-overview.md`. Delivers the trend pre-registration file and its
freeze, then the gross signal screen and its go/stop decision. The freeze
comes first; the screen can stop the project but cannot change anything in
the frozen file.

**Branch:** `trend/2-prereg-screen`, from `dev` after Phase 1 is merged.

## 2.1 `configs/trend/prereg.yaml`

```yaml
registered_on: 2026-10-23
universe:                  # copied from configs/trend/universe.json; part of the hash
  um:   {rule: "...", members: [...]}
  spot: {rule: "...", members: [...]}
periods:
  warmup:   {start: 2020-01-01, end: 2020-06-30}
  insample: {start: 2020-07-01, end: 2026-10-20}
  holdout:  {market: spot, end: 2019-12-31}      # start is per pair, at warmup
  forward:  {start: null, reviews: [12, 36]}      # start set at the code lock; excluded from the hash
signal:
  members: [return_sign, ma_filter, donchian]
  lookbacks: [20, 40, 80, 160]
  donchian_exit: half
  direction: long_flat
sizing:   {target_vol: 0.30, vol_window: 60, max_weight: 2.0, max_gross: 2.0}
rebalance: {frequency: daily, drift_band: 0.20, fill_offset_min: 15}
costs:
  fee_taker: 0.0005
  slippage: {default: 0.0005, BTCUSDT: 0.0002, ETHUSDT: 0.0002}
  mmr: {default: 0.01, BTCUSDT: 0.004, ETHUSDT: 0.005}
  spot_funding_per_8h: 0.0001
exploratory:               # reported; no say in the verdict; DSR trials
  - {name: long_short, signal: {direction: long_short}}
  - {name: weekly, rebalance: {frequency: weekly}}       # decisions on Sunday close, fills Monday
  - {name: fill_0100, rebalance: {fill_offset_min: 60}}
  - {name: fill_0400, rebalance: {fill_offset_min: 240}}
  - {name: fill_0800, rebalance: {fill_offset_min: 480}}
reprice: {slippage: [0.0, 0.0002, 0.0005, 0.001, 0.002], fee_taker: [0.0004, 0.0005]}
stats:
  alpha: 0.05
  baseline_runs: 5000
  shift_margin_days: 60
  bootstrap_n: 10000
  block_len_days: 20
  master_seed: 20261023
screen: {baseline_runs: 1000, stop_if_p_at_least: 0.10}
verdict_rule:
  t1: "one-sided p of the portfolio net Sharpe against the random-days runs < alpha"
  t2: "one-sided paired block-bootstrap p of (strategy − vol-targeted buy-and-hold) net Sharpe < alpha"
  edge: "t1 and t2 (intersection-union; no multiplicity adjustment)"
  t3: "reported only: max drawdown at matched vol, paired block bootstrap"
  reported_for: [insample, holdout, forward_36m]
  forward_12m: descriptive only
```

The exact key set is validated (unknown or missing keys raise), as in the
first strategy's `experiments/prereg.py`; reuse its duplicate-key YAML
loader and `config.canonical_json` / `config_hash`. The hash covers the
normalised content except `periods.forward.start`.

The block length is 20 days rather than the first strategy's 10 because
trend exposures persist for weeks. *(builder's call)*

## 2.2 Freeze

`perpbt trend prereg freeze` writes `configs/trend/prereg.lock`:

```json
{"frozen_on": "...", "prereg_hash": "...", "git_commit": "...",
 "code_version": null, "code_locked_on": null, "code_git_commit": null}
```

The lock is a new JSON format with its own `freeze` / `read_lock` in
`trend/prereg.py`; the first strategy's lock code (YAML, fixed
`LOCK_KEYS`, requires `data_download_date`) is not reused.

Commit `prereg.yaml`, `universe.json`, and the lock together; tag
`trend-prereg-v1`. The code fields are filled by the code lock in Phase 5.
After the freeze, `prereg.yaml` may change only in `periods.forward.start`.

## 2.3 The screen (`trend/screen.py`)

A deliberately simple, gross return model, independent of the Phase 3
executor:

- Pair return on day `d+1`: `w*_{d} × (c_{d+1} / c_d − 1)`; no fill delay,
  no drift band, no fees, no slippage, **no funding**.
- Portfolio return: the mean over pairs active on `d`.
- Statistic: annualised Sharpe of the portfolio over the in-sample period.
- Random-days runs: 1,000 paths, each pair's exposure series `e` shifted by
  the T1 rule (Phase 4 §4.1), weights recomputed with the actual `σ̂`.
- `p_screen = (1 + #{runs ≥ observed}) / (1000 + 1)`.

**Decision.** If `p_screen ≥ 0.10`, stop: write the result to
`runs/trend/screen.json` and `runs/trend/summary.md`, merge, and do not
build Phases 3–7. Otherwise continue; nothing in the pre-registration
changes.

The screen also reports, descriptively, the gross vol-targeted
buy-and-hold Sharpe under the same simple model. It does not gate on it.

Since the random-days runs keep each pair's time in market and number of
signal changes, costs mostly cancel in T1; the screen is close to a preview
of T1 without funding. Its value is that it costs Phases 0–2 instead of the
whole build.

## 2.4 Tests

- Prereg: round trip, hash stability (key order, comments, `2` vs `2.0`),
  unknown/missing keys, the forward start excluded from the hash, freeze
  refuses to overwrite a lock.
- Screen: on a synthetic trending series the strategy beats the shifted
  paths; on a random walk `p_screen` is roughly uniform over seeds; the
  shift preserves each path's time in market and number of exposure
  changes exactly.

## 2.5 Exit criterion

Lock committed and tagged; `runs/trend/screen.json` committed with
`p_screen`, the observed and random-path Sharpe distribution summary, and
the decision. Report the decision to the user before starting Phase 3.

## 2.6 Implementation notes (Phase 2 build, 2026-10-23)

Builder's calls taken while building this phase; none changes a trading
rule.

- **Loader.** The duplicate-key YAML loader moved from
  `experiments/prereg.py` to `config.read_yaml_unique` (the first strategy
  imports it from there): `experiments/prereg.py` is outside the Phase 5
  §5.4 import scope, which `tests/trend/test_imports.py` pins.
- **Canonical order.** `signal.members` must be listed in the order
  `return_sign, ma_filter, donchian` and `lookbacks` strictly increasing
  (`SignalConfig` rejects anything else), so two files with the same signal
  hash the same and an edited order cannot slip through.
- **Exploratory rows** are stored as the full merged `signal` and
  `rebalance` blocks (the hash covers the merged values); a row may override
  only those two blocks.
- **Lock.** `configs/trend/prereg.lock` is JSON in the key order of §2.2;
  `freeze` checks the universe against `universe.json` first and writes the
  file once. `check_frozen` (the gate for the screen and every later run)
  refuses a missing lock or a hash that differs from it; editing
  `periods.forward.start` alone passes.
- **Screen days.** Decisions are made on the day before each in-sample
  day, so the returns fall on 2020-07-01 .. 2026-10-20 (`n` = 2,303 days).
  A pair is active on decision day `d` when its signal is valid and day
  `d+1` is in its DayFrame; EOSUSDT leaves on its last day (2025-05-21).
  A day with no active pair returns 0 (none occurs in-sample).
- **Shift.** One offset per path on `[60, n − 60]`, each pair rotated by
  `k mod n_i` inside its own active days; weights recomputed with
  `signals.target_weight` and the actual `σ̂`. The paths are computed in
  chunks of 100 (output-neutral, pinned by a test).
- **One shot.** `perpbt trend screen` refuses without a matching lock and
  when `runs/trend/screen.json` exists (the file is also opened with
  `"x"`). Seed: `rng_for(master_seed, prereg_hash, "screen")`. On stop it
  also writes `runs/trend/summary.md`; `.gitignore` admits both files.
