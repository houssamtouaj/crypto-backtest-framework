# Phase 2 — Pre-registration and screen

Read with `00-overview.md`. Delivers the carry pre-registration and its
freeze, then the simplified net screen and its go/stop decision. The
freeze comes first; the screen can stop the project but cannot change
anything in the frozen file.

**Branch:** `carry/2-prereg-screen`, from `dev` after Phase 1 is merged.

## 2.1 `configs/carry/prereg.yaml`

```yaml
registered_on: <freeze day>
universe:                    # copied from configs/trend/universe.json "um"; part of the hash
  um: {rule: "...", members: [...]}
periods:
  warmup:   {start: 2020-01-01, end: 2020-06-30}
  insample: {start: 2020-07-01, end: 2023-12-31}   # returns on these days; decisions on the day before each
  holdout:  {start: 2024-01-01, end: 2026-10-20}
  forward:  {start: null, reviews: [12, 36]}        # start set at the code lock; excluded from the hash
signal:   {lookback_days: 7, entry_annual: 0.10, exit_annual: 0.0}
sleeve:   {perp_leverage: 2.0}
margin:
  rebalance_below: 0.5       # perp wallet equity / margin at target leverage
  mmr: {default: 0.01, BTCUSDT: 0.004, ETHUSDT: 0.005}
  liquidation_fee: 0.01
rebalance: {fill_offset_min: 15}
costs:
  perp_taker: 0.0005
  spot_taker: 0.0010
  slippage: {default: 0.0005, BTCUSDT: 0.0002, ETHUSDT: 0.0002}   # per leg
exploratory:                 # reported; no say in the verdict; DSR trials
  - {name: always_on,    signal: {entry_annual: -1.0e9, exit_annual: -1.0e9}}
  - {name: entry_5,      signal: {entry_annual: 0.05}}
  - {name: entry_20,     signal: {entry_annual: 0.20}}
  - {name: lookback_3d,  signal: {lookback_days: 3}}
  - {name: lookback_14d, signal: {lookback_days: 14}}
  - {name: lev_1,        sleeve: {perp_leverage: 1.0}}
reprice: {slippage: [0.0, 0.0002, 0.0005, 0.001], spot_taker: [0.00075, 0.0010]}
hurdle:  {annual: 0.05, descriptive: [0.0, 0.03, 0.05, 0.08]}
stats:
  alpha: 0.05
  bootstrap_n: 10000
  block_len_days: 30
  random_timing_runs: 1000   # descriptive only (Phase 4 §4.4)
  shift_margin_days: 60
  master_seed: <a new fixed integer, written before the freeze>
screen: {bootstrap_n: 10000, stop_if_p_at_least: 0.10}
verdict_rule:
  v1: "one-sided moving-block bootstrap p of the portfolio net Sharpe > 0 < alpha"
  v2: "one-sided moving-block bootstrap p of the annualised net return minus the hurdle > 0 < alpha"
  edge: "v1 and v2 (intersection-union; no multiplicity adjustment)"
  reported_for: [insample, holdout, forward_36m]
  forward_12m: descriptive only
```

As in trend Phase 2: exact key sets (unknown or missing keys raise), the
duplicate-key loader (`config.read_yaml_unique`), canonical JSON hash over
the normalised content without `periods.forward.start`, exploratory rows
stored merged and limited to overriding `signal` and `sleeve`, the
universe checked against `universe.json` on load and freeze. `always_on`
uses thresholds that every finite `f̄` passes (the state is on from the
first warm day).

## 2.2 Freeze

`perpbt carry prereg validate | freeze` writes `configs/carry/prereg.lock`
(JSON, the trend lock's keys). Commit `prereg.yaml` and the lock together;
tag `carry-prereg-v1`. After the freeze only `periods.forward.start` may
change. **Stop for the user before the freeze** (they see the final file)
and **after the screen** (they see the result before anything else).

## 2.3 The screen (`carry/screen.py`)

A deliberately simple net model, independent of the Phase 3 executor:

- Sleeve return on day `d+1` when `on_d`: `x × (r^spot_{d+1} − r^perp_{d+1}
  + F_{d+1})`, with `x = L / (L + 1)` (2/3), `r` the close-to-close price
  returns of each leg, and `F_{d+1}` the sum of the perp's funding rates on
  day `d+1` (received by the short when positive).
- Costs: on a day where the state changes, `x × (spot_taker + perp_taker
  + 2 × slippage_pair)` is subtracted (one entry or one exit of both legs).
- No fill delay, no margin, no rebalancing, no liquidation.
- Portfolio return: the mean over the pairs live on `d` (an "off" sleeve
  contributes 0).
- Statistic: V2's one-sided p (Phase 4 §4.2) on the in-sample days, with
  `screen.bootstrap_n` resamples, `stats.block_len_days`, seed
  `(master_seed, prereg_hash, "screen")`.

**Decision.** If `p_screen ≥ 0.10`, stop: write `runs/carry/screen.json`
and `runs/carry/summary.md`, merge, do not build Phases 3–7. Otherwise
continue; nothing in the pre-registration changes. The screen also
reports, descriptively, the annualised net return, its CI, V1's p, and the
always-on row under the same model.

## 2.4 Tests

- Prereg: round trip, hash stability, unknown/missing keys, forward start
  outside the hash, freeze once, the universe check.
- Screen: a hand-computed three-day, two-pair case; costs on transitions
  only; an "off" sleeve contributes 0; a delisted pair leaves the mean; a
  synthetic series with constant funding of 30%/yr and no basis passes
  V2; one with 2%/yr does not.
- One shot: refuses without a matching lock, refuses to overwrite
  `screen.json`, refuses a non-finite statistic.

## 2.5 Exit criterion

Lock committed and tagged; `runs/carry/screen.json` committed with
`p_screen`, the annualised net return and CI, and the decision. Report the
decision to the user before starting Phase 3.

## 2.6 Implementation notes (Phase 2 build, 2026-10-23)

Builder's calls taken while building this phase, before the freeze; none
was tuned on a return.

- **`f̄` is time-weighted** *(my call; replaces Phase 1 §1.6's per-event
  mean)*. `f̄_d = Σ rate_e × 24 / Σ interval_h_e × 365` over the window's
  events: the funding a short receives per hour covered, annualised. In a
  window that mixes 8 h and 4 h events, the per-event mean of per-day
  rates weighted each 4 h event like an 8 h one, i.e. twice per hour. Both
  are identical in steady state, and every in-sample event is 8 h; a slow
  test pins that the in-sample `on` of every pair (W = 3, 7, 14) equals the
  per-event formula's. Only holdout/forward transition windows differ.
- **`master_seed: 202610233`** *(my call)*: the carry spec date followed by
  3 (the third strategy), written before the freeze and before any return
  was computed.
- **Prereg checks.** As trend §2.6 (exact keys, duplicate-key loader,
  merged exploratory rows limited to `signal` and `sleeve`, the universe
  `um` block checked against `universe.json` on load and freeze, JSON lock
  with the trend lock's keys). Also: `insample.end` must be
  `CARRY_INSAMPLE_END` (2023-12-31) and `holdout.start` the day after.
  `always_on`'s thresholds are written `-1.0e+9` (PyYAML reads `-1.0e9`
  as a string). `CarrySignalConfig` is the `signal` block; `SleeveConfig`,
  `MarginConfig`, `CarryCostConfig`, `CarryRebalanceConfig` (in
  `carry/config.py`) and `Hurdle`, `CarryStats`, `ScreenConfig`,
  `RepriceConfig` (in `carry/prereg.py`) validate the other blocks.
- **Screen data.** Only `carry.frames.load_pairframe(data_cfg, pair,
  end=insample_end)`; `on` from `carry.signals.compute_signals`. Decisions
  on 2020-06-30 .. 2023-12-30, returns on 2020-07-01 .. 2023-12-31 (1,279
  days). The perp leg's return is `perp.c[j+1] / perp.c[j] − 1`; `F_{d+1}`
  sums `perp.funding_rate` over day `d+1`'s events.
- **Start and end** *(my call)*. Every pair starts off, so a pair on at
  2020-06-30 pays an entry cost on 2020-07-01; no exit cost at the period
  end (not a delisting). A pair is live on `d` when its PairFrame has `d`
  and `d+1`; an off or not-yet-warm sleeve contributes 0 and counts in the
  mean (`E / N_live`). The screen refuses when a pair's frame does not end
  on 2023-12-31 (no in-sample delisting is handled by this model), when
  `data.yaml`'s `insample_end` or `warmup_start` differs from the prereg's,
  or when a member has no listing date.
- **Statistics.** `stats.bootstrap.block_bootstrap_mean` (Phase 4 §4.2's
  function, added now, backward compatible). V2 replicates `365 ×
  mean(e*)`, `h_d = 1.05^(1/365) − 1`, `p = (1 + #{≤ 0}) / (B + 1)`; reading
  `above_hurdle` (passes), `below_hurdle` (CI upper < 0) or `not_proven`.
  The annualised net return is `365 × mean(ret)` (arithmetic, like V2's
  statistic); its CI is the excess CI plus `365 × h_d`. V1 (Sharpe, block
  bootstrap, `p` over finite replicates) and the `always_on` row are
  descriptive; a descriptive Sharpe that cannot be computed (zero variance)
  is `null`, while a non-finite V2 statistic refuses the run.
- **Seeds** *(my call)*. The gating V2 uses `rng_for(master_seed,
  prereg_hash, "screen")`; the descriptive ones `"screen/v1"`,
  `"screen/always_on/v2"`, `"screen/always_on/v1"`.
- **One shot.** `perpbt carry screen` refuses without a matching lock and
  when `runs/carry/screen.json` exists (also opened with `"x"`); the JSON is
  serialised before the file is opened. On stop it also writes
  `runs/carry/summary.md`; `.gitignore` admits both.
