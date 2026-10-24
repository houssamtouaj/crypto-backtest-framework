# ETF Phase 2 — Pre-registration and Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `configs/etf/prereg.yaml` with exact-key validation, a canonical hash and a JSON lock written once; the
one-shot gross screen (`perpbt etf screen`) on the in-sample basket with its go/stop decision.

**Architecture:** `perpbt/etf/config.py` gains the remaining prereg blocks (`EtfRebalanceConfig`,
`EtfCostConfig`, `EtfCashConfig`, `EtfRepriceConfig`). `perpbt/etf/prereg.py` parses the YAML into an
`EtfPrereg` (signal block `trend.config.SignalConfig`, sizing block `EtfSizingConfig`), hashes it without
`periods.forward.start`, checks it against `configs/etf/data.yaml` and the manifest hash, and freezes it.
`perpbt/etf/screen.py` aligns the four frames on decision days, computes the gross excess Sharpe at √252, and
compares it with 1,000 shared-offset circular-shift paths. `cli.py` gains `perpbt etf prereg validate|freeze`
and `perpbt etf screen`.

**Tech Stack:** Python 3.11, numpy, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-24-etf-trend/phase-2-prereg-screen.md`, with `00-overview.md`,
`phase-0-data.md` §0.9 and `phase-1-signals.md` §1.8. Code reused (imported, never edited): `trend/prereg.py`
(`LOCK_KEYS`, `_exact_keys`, `_iso`, `_mapping`, `_verdict`, `TrendStats`, `ScreenConfig`), `trend/config.py`
(`SignalConfig`), `etf/frame.py` (`load_frames`), `etf/signals.py` (`compute_signals`, `etf_target_weight`,
`summarise`, `ANNUALISE`), `etf/store.py` (`EtfStore.manifest_sha256`, `ETF_HOLDOUT_START`), `stats/seeds.py`
(`rng_for`), `config.py` (`read_yaml_unique`, `config_hash`, `from_dict`, `to_dict`).

## Global Constraints

- Branch `etf/2-prereg-screen` from `dev` (a702ea8). Stops: before the freeze (show `prereg.yaml` and its hash),
  after the screen (report `p_screen` and go/stop, nothing further), before the merge.
- Never edit any file in `CARRY_CODE_PATHS` or `perpbt/trend/`; `tests/etf/test_guards.py` keeps passing.
- Data only through `EtfStore`; no `allow_holdout` in the new modules. The screen is in-sample only.
- Do not re-fetch `data/etf`; the prereg pins manifest sha256 `308f5012…60658`.
- Screen model: ETF return on `d+1` is `w*_d × ret_{d+1} + (1 − w*_d) × cash_{d+1}`; the portfolio is the mean
  over the four; excess is minus `cash_{d+1}`; Sharpe `√252 × mean / std (ddof 1)`; no fills, band or costs.
- Random paths: one offset `k` uniform on `[60, n − 60]` per path, the same for every ETF, applied to the exposure
  series; `w*` recomputed with the actual `σ̂`. `p = (1 + #{paths ≥ observed}) / 1001`.
  Seed `rng_for(master_seed, prereg_hash, "screen")`.
- **The real screen is not run before the user's go on the freeze.** No test computes a strategy return on the
  real store before `runs/etf/screen.json` exists.

## Builder's calls (on the standing instruction)

1. The prereg helpers and the `stats`/`screen` blocks reuse `trend/prereg.py` (`TrendStats`, `ScreenConfig`,
   `_verdict`, `LOCK_KEYS`); their key sets are the ETF spec's exactly.
2. New blocks in `etf/config.py`: `EtfRebalanceConfig` (`frequency` daily/weekly, `weekday`, `drift_band ≥ 0`,
   `fill` next_open/next_close), `EtfCostConfig` (`commission`, `slippage` table with `default`, each in
   [0, 0.1)), `EtfCashConfig` (`series`, `day_count` 360), `EtfRepriceConfig` (`slippage`, `commission`).
3. Weekly rebalancing accepts only `weekday: friday`, read as the week's last trading day (the spec's comment);
   Phase 3 defines the holiday weeks.
4. Every signal block, primary and exploratory, must be `long_flat` (ED13: a cash account cannot short).
5. `cash.series` must equal `data.yaml`'s `rates.series` and `day_count` must be 360 (what `frame.cash_returns`
   computes).
6. The prereg must agree with `data.yaml`: tickers (in order), `warmup.start`, `insample`, `holdout.end`,
   `data.download_date`; `holdout.start` must be the store's constant 2018-01-01. Checked by freeze, validate
   and the screen (`check_snapshot`), not by the pure parser.
7. `check_snapshot` also refuses a manifest whose sha256 differs from `data.manifest_sha256`.
8. The screen's start check is `etf.signals.summarise(…, insample_start, insample_end)` (it needs the decision
   day before the start); the frames must end within 5 calendar days of `insample_end`.
9. Descriptive extras in `screen.json`: per-ticker strategy and buy-and-hold excess Sharpe and mean `w*`; the
   cash return's annualised mean. None gates.
10. `summary.md` is written only on stop (as in trend); on go only `screen.json`.
11. A slow test pins the committed `screen.json` byte for byte (except `computed_on`, `git_commit`); it skips
    until the file exists, so nothing is computed on the real store before the one-shot run.

## Review Focus

1. The return on day `d+1` uses `w*_d` (no lookahead), and the cash leg is credited on `1 − w*_d`.
2. The shift is one shared offset across ETFs and preserves each series' values and change count.
3. The hash excludes only `periods.forward.start`; the `data` block is inside it.
4. The screen refuses: no lock, a hash mismatch, a manifest mismatch, a non-finite Sharpe, an existing
   `screen.json`; the file is serialised before it is opened.

---

### Task 1: prereg blocks (`etf/config.py`) and `etf/prereg.py`

- [ ] Tests `tests/etf/test_prereg.py`: round trip of `configs/etf/prereg.yaml`; hash stable under key order,
  comments, `2` vs `2.0`; hash changes on content; forward start excluded; unknown/missing/invalid keys;
  duplicate YAML key; exploratory rows merged and limited to `signal`, `sizing`, `rebalance`; long_short
  refused; snapshot checks (tickers, dates, manifest); freeze once, refuses a mismatch and leaves no lock;
  `check_frozen`.
- [ ] Implement; `python -m pytest tests/etf -q`.
- [ ] Commit.

### Task 2: `etf/screen.py`

- [ ] Tests `tests/etf/test_screen.py`: decision-day alignment (first decision day is the trading day before the
  start, returns on the in-sample days); hand-computed portfolio excess return with the cash leg; `w*_d`
  causality (perturbing returns from `d+1` on leaves the decision weights unchanged); one shared offset;
  the shift preserves values and change counts; trending series beats the paths; random walk p roughly uniform;
  determinism and the p formula; NaN refuses; `write_screen` once, serialised first, `summary.md` on stop only.
- [ ] Implement; commit.

### Task 3: CLI and the real-store checks

- [ ] `perpbt etf prereg validate|freeze`, `perpbt etf screen` with exit codes 0/1/2; tests on a synthetic store.
- [ ] Slow tests: the real prereg passes `check_snapshot` against `data.yaml` and the store; the real screen
  inputs build (3,127 return days, decision day 2005-07-29) without computing a return; the committed
  `screen.json` is pinned once it exists.
- [ ] Full suite; fresh review; fixes.
- [ ] **Stop:** show `prereg.yaml` and its hash. Freeze, commit, tag `etf-prereg-v1` on the user's go.
- [ ] Run the screen once; commit `runs/etf/screen.json` (and `summary.md` on stop). **Stop:** report.

## Progress log


- 2026-10-24: Task 1 (8599d8f), Task 2 (2c5ce62), Task 3 CLI and real-store checks (0a12b65). Full suite
  1,705 passed, 1 skipped (the screen pin, by design). Real prereg validates against data.yaml and the store.
- 2026-10-25: fresh review (the first reviewer stalled; a second one reported): freeze-ready after one fix.
  Fixed: `.gitignore` admits `runs/etf/screen.json` and `summary.md`; minors (spec §2.6).
