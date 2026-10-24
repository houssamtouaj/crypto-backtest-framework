# Phase 2 — Pre-registration and screen

Read with `00-overview.md`. Delivers the ETF pre-registration and its
freeze, then the gross screen and its go/stop decision. The freeze comes
first; the screen can stop the project but cannot change anything in the
frozen file.

**Branch:** `etf/2-prereg-screen`, from `dev` after Phase 1 is merged.

**Stops for the user:** before the freeze, and after the screen.

## 2.1 `configs/etf/prereg.yaml`

```yaml
registered_on: 2026-10-24
universe: {tickers: [SPY, QQQ, IWM, GLD], rule: "fixed list: largest US-listed ETF for the S&P 500, Nasdaq-100, Russell 2000 and gold"}
periods:
  warmup:   {start: 2004-11-18}
  insample: {start: 2005-08-01, end: 2017-12-31}
  holdout:  {start: 2018-01-01, end: <holdout_end from data.yaml>}
  forward:  {start: null, reviews: [12, 36]}      # start set at the code lock; excluded from the hash
data: {download_date: <from data.yaml>, manifest_sha256: <of data/etf/manifest.json>}
signal:
  members: [return_sign, ma_filter, donchian]
  lookbacks: [20, 40, 80, 160]                   # bars = US trading days
  donchian_exit: half
  direction: long_flat
sizing:   {target_vol: 0.15, vol_window: 60, max_weight: 1.0, annualise_days: 252}
rebalance: {frequency: daily, weekday: null, drift_band: 0.20, fill: next_open}
costs:
  commission: 0.0
  slippage: {default: 0.0003, SPY: 0.0002, QQQ: 0.0002}
cash: {series: DTB3, day_count: 360}
exploratory:               # reported; no say in the verdict; DSR trials
  - {name: weekly,        rebalance: {frequency: weekly, weekday: friday}}   # decide on the week's last trading day
  - {name: fill_close,    rebalance: {fill: next_close}}
  - {name: no_vol_target, sizing: {target_vol: null}}                        # w* = exposure (cap 1)
  - {name: no_band,       rebalance: {drift_band: 0.0}}
reprice: {slippage: [0.0, 0.0002, 0.0005, 0.001, 0.002], commission: [0.0, 0.0005]}
stats:
  alpha: 0.05
  baseline_runs: 5000
  shift_margin_days: 60
  bootstrap_n: 10000
  block_len_days: 20
  master_seed: 20261024
screen: {baseline_runs: 1000, stop_if_p_at_least: 0.10}
verdict_rule:
  t1: "one-sided p of the portfolio net excess Sharpe against the random-days runs < alpha"
  t2: "one-sided paired block-bootstrap p of (strategy − vol-targeted buy-and-hold) net excess Sharpe < alpha"
  edge: "t1 and t2 (intersection-union; no multiplicity adjustment)"
  t3: "reported only: max drawdown at matched vol, paired block bootstrap"
  reported_for: [insample, holdout, forward_36m]
  forward_12m: descriptive only
```

The exact key set is validated (unknown or missing keys raise), with the
duplicate-key YAML loader (`config.read_yaml_unique`) and
`config.canonical_json` / `config_hash`. The hash covers the normalised
content except `periods.forward.start`. Signal lists must be in canonical
order, as in the crypto prereg. An exploratory row may override only the
`signal`, `sizing` and `rebalance` blocks and is stored fully merged.

The `data` block ties the prereg to the fetched snapshot: every later run
checks `data/etf/manifest.json` against `manifest_sha256` and refuses on a
mismatch *(my call)*.

## 2.2 Freeze

`perpbt etf prereg freeze` writes `configs/etf/prereg.lock` (JSON):

```json
{"frozen_on": "...", "prereg_hash": "...", "git_commit": "...",
 "code_version": null, "code_locked_on": null, "code_git_commit": null}
```

It checks the universe against `data.yaml` and the manifest hash first and
writes the file once (opened with `"x"`). Commit `prereg.yaml`, `data.yaml`
and the lock together; tag `etf-prereg-v1`. After the freeze,
`prereg.yaml` may change only in `periods.forward.start`.

## 2.3 The screen (`etf/screen.py`)

A deliberately simple gross model, independent of the Phase 3 executor:

- ETF return on day `d+1`: `w*_d × ret_{d+1} + (1 − w*_d) × cash_{d+1}`
  (no fill delay, no band, no costs).
- Portfolio return: the mean over the four ETFs. Excess return: minus
  `cash_{d+1}`.
- Statistic: Sharpe of the excess return (√252) over the in-sample days.
- Random paths: 1,000, each with one shared circular-shift offset `k` drawn
  uniformly from `[60, n − 60]` applied to every ETF's exposure series
  (Phase 4 §4.1), weights recomputed with the actual σ̂.
- `p_screen = (1 + #{paths ≥ observed}) / (1000 + 1)`.
- Seed: `rng_for(master_seed, prereg_hash, "screen")`.

**Decision.** If `p_screen ≥ 0.10`, stop: write `runs/etf/screen.json` and
`runs/etf/summary.md`, merge after the user's go, and do not build Phases
3–7. Otherwise continue; nothing in the pre-registration changes.

The screen also reports, descriptively, the gross excess Sharpe of the
vol-targeted buy-and-hold (exposure 1) under the same model, and of 100%
SPY. It does not gate on them.

**One shot.** `perpbt etf screen` refuses without a matching lock, on a
manifest mismatch, when any Sharpe is not finite, and when
`runs/etf/screen.json` exists. `screen.json` is serialised before the file
is opened, so a failure cannot leave a half-written file.

## 2.4 Tests

- Prereg: round trip; hash stability (key order, comments, `2` vs `2.0`);
  unknown/missing keys; the forward start excluded from the hash; freeze
  refuses to overwrite; manifest mismatch refused.
- Screen: on a synthetic trending series the strategy beats the shifted
  paths; on a random walk `p_screen` is roughly uniform over seeds; the
  shift preserves each path's time in market and number of exposure
  changes; the cash leg is credited on the uninvested share.

## 2.5 Exit criterion

Lock committed and tagged (after the user's go); `runs/etf/screen.json`
committed with `p_screen`, the observed and random-path Sharpe summary, the
descriptive benchmarks, and the decision. Report the decision to the user
before starting Phase 3.
