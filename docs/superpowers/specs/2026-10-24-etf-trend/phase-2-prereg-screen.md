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
  (Phase 4 §4.2), weights recomputed with the actual σ̂.
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

## 2.6 Implementation notes (2026-10-24/25, branch `etf/2-prereg-screen`)

**Code.** `perpbt/etf/prereg.py` (`EtfPrereg`, `parse_prereg`, `load_prereg`, `check_snapshot`, `freeze`,
`read_lock`, `check_frozen`), `perpbt/etf/screen.py` (`ScreenInputs`, `screen_inputs`, `portfolio_excess`,
`sharpe252`, `shift_offsets`, `shifted_exposure`, `run_screen`, `screen_from_store`, `write_screen`), the remaining
prereg blocks in `perpbt/etf/config.py`, and `perpbt etf prereg validate|freeze` and `perpbt etf screen` in
`cli.py`. Nothing under `perpbt/trend/` or in `CARRY_CODE_PATHS` was edited. Plan and log:
`docs/superpowers/plans/2026-10-24-etf-phase-2-prereg-screen.md`.

**Builder's calls** (on the standing instruction to take the recommended option; the user can overturn any):

1. The exact-key helpers, the `stats` and `screen` blocks (`TrendStats`, `ScreenConfig`), the verdict text check
   and `LOCK_KEYS` are imported from `trend/prereg.py`; their key sets are this spec's exactly.
2. New blocks in `etf/config.py`: `EtfRebalanceConfig` (`frequency`, `weekday`, `drift_band`, `fill` ∈
   {next_open, next_close}), `EtfCostConfig` (`commission`, `slippage` with `default`; each cost in [0, 0.1)),
   `EtfCashConfig` (`day_count` must be 360, what `frame.cash_returns` computes), `EtfRepriceConfig`.
3. Weekly rebalancing accepts only `weekday: friday`, read as the week's last trading day; Phase 3 defines the
   holiday weeks.
4. Every signal block, primary and exploratory, must be `long_flat` (ED13).
5. Slippage keys other than `default` must be universe tickers; an exploratory row that changes nothing after the
   merge is refused.
6. `check_snapshot` (run by validate, freeze and the screen) requires the tickers in order, `cash.series`, the
   warmup, in-sample and holdout dates and the download date to equal `configs/etf/data.yaml`, and
   `data/etf/manifest.json` to hash to `data.manifest_sha256`.
7. The screen checks the start with `etf.signals.summarise` and refuses frames that end more than 5 calendar days
   before `insample.end`. `screen.json` also carries, descriptively: per ETF the strategy's and the buy-and-hold's
   excess Sharpe, mean `w*` and mean exposure; the mean cash return a year.
8. `summary.md` is written only on stop, as in trend; on go only `screen.json`. `.gitignore` admits both files.
9. `tests/etf/test_real_screen.py` pins the committed `screen.json` (all but `computed_on`, `git_commit`); it
   skips until the file exists, so no strategy return was computed on the real store before the one-shot run.

**Review.** One fresh reviewer (opus); verdict "freeze-ready after one fix". Important: `.gitignore` ignored
`runs/etf/screen.json` (fixed; a test checks). Minors fixed: slippage keys, the no-op exploratory row, YAML errors
in the CLI, tests for the end-gap refusal and the seed, this section, the §4.2 reference. Not changed: the
summary is written after `screen.json` (recoverable from the JSON), and the one-shot check is per `--runs-dir`,
as in trend and carry.

**Freeze and screen (2026-10-24 UTC, on the user's go).** Frozen at 570c809 (lock: `frozen_on` 2026-10-24,
`prereg_hash` `46ba8b847fc966d3e5b8969b1de3ea869e06c20540388cde88415916693c319d`, git 31f57a9), tagged
`etf-prereg-v1`. The screen ran once (3db910d):

| | value |
|---|---|
| return days | 2005-08-01 .. 2017-12-29 (3,127; first decision day 2005-07-29) |
| observed gross excess Sharpe | 0.663 |
| 1,000 random-days paths | mean 0.680, std 0.110, 5/50/95% 0.513 / 0.678 / 0.844 |
| **p_screen** | **0.5455 ≥ 0.10 → STOP** |
| vol-targeted buy-and-hold (descriptive) | 0.795 |
| 100% SPY (descriptive) | 0.468 |
| per ETF, strategy / buy-and-hold | SPY 0.535 / 0.627, QQQ 0.678 / 0.769, IWM 0.203 / 0.470, GLD 0.579 / 0.567 |
| mean T-bill return | 1.13% a year |

The timing is no better than random timing with the same time in market. The positive Sharpe comes from being
long rising assets, and the vol-targeted buy-and-hold did better. By §2.3, Phases 3–7 are not built: the ETF
project is stopped. `tests/etf/test_real_screen.py` reproduces the committed `screen.json`.
