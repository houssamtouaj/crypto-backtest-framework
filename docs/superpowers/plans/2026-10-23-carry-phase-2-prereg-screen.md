# Carry Phase 2 — Pre-registration and Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `configs/carry/prereg.yaml` with its loader, hash and JSON lock; the simplified net screen (`carry/screen.py`) with its one-shot CLI; then, with the user's go-ahead, the freeze, the tag `carry-prereg-v1`, and one run of the screen that decides go or stop.

**Architecture:** `carry/prereg.py` copies the trend pattern (`trend/prereg.py`): exact key sets, `config.read_yaml_unique`, every block through a frozen `_Config` class, canonical hash without `periods.forward.start`, a JSON lock written once, `check_frozen` as the gate. `carry/screen.py` reads data only through `carry.frames.load_pairframe` (in-sample, no `allow_holdout`), takes `on` from `carry.signals.compute_signals`, builds per-pair net sleeve returns on the in-sample days and the portfolio mean over live pairs, and tests V2 (Phase 4 §4.2) with a moving-block bootstrap of the daily excess over the hurdle.

**Tech Stack:** Python 3.11, numpy, PyYAML, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-23-funding-carry/phase-2-prereg-screen.md`, read with `00-overview.md` (CD2–CD13), Phase 1 §1.6 (calls, data facts, pointers), Phase 4 §4.1–§4.2 (V1, V2). Pattern: `docs/superpowers/specs/2026-10-23-trend-following/phase-2-prereg-screen.md` §2.6, `perpbt/trend/{prereg,screen}.py`, `tests/trend/{test_prereg,test_screen,test_screen_cli}.py`.

## Global Constraints

- Branch `carry/2-prereg-screen` from `dev` (484a298).
- **Stops (user):** (1) before the freeze: show the final `configs/carry/prereg.yaml` with `master_seed`; (2) after the screen has run once: report `p_screen`, the decision and the descriptive rows; never re-run it; (3) before the `--no-ff` merge into `dev`. Every open design question: take the recommended option, label it *(my call)*, list it at the end.
- The prereg values are spec §2.1 verbatim. After the freeze only `periods.forward.start` may change.
- Data only through `carry.frames.load_pairframe(data_cfg, pair, end=insample_end)`, never `allow_holdout`. `tests/carry/test_frames.py` (only `frames.py`/`basis.py` read the stores; no `.ret`; `allow_holdout` scan) stays green and is not widened.
- `f̄` and `on` only from `carry.signals.compute_signals`; the prereg `signal` block is `CarrySignalConfig`.
- Perp leg price return `c_{d+1}/c_d − 1` from `pf.perp.c`; funding `F_{d+1}` = sum of `pf.perp.funding_rate` over day `d+1`'s events (`pf.perp.events(j+1)`). Never `DayFrame.ret`.
- Returns on 2020-07-01..2023-12-31 only; decisions on the day before each. Nothing on 2024-01-01 onward is loaded or computed.
- Before the screen run: no real-data return, P&L, funding statistic or per-pair share of days on is computed or printed (real-data tests check signals and structure only).
- Seeds: `stats.seeds.rng_for(master_seed, prereg_hash, purpose)`; the gating p uses purpose `"screen"`. `screen.json` byte-identical on rerun (same inputs, date and commit).
- Do not run any first-strategy command (`perpbt run`, `grid`, `baselines`, `holdout`, `stats`) or `perpbt trend screen`. Long jobs in the foreground, one at a time.

## My calls (labelled; listed in the hand-off)

1. **`f̄` is time-weighted across interval changes:** `f̄_d = Σ rate_e × 24 / Σ interval_h_e × 365` over the window's events (the realised funding per hour covered, annualised). Phase 1 used the per-event mean of per-day rates, which weights 4 h events twice per hour in a mixed window. Identical in steady state (all in-sample events are 8 h); only holdout/forward transition windows differ. Changed before the freeze, not tuned.
2. **`master_seed: 202610233`** (the carry spec date + 3 for the third strategy), written before the freeze.
3. **Screen start state:** every pair starts flat; the state before the first decision day counts as off, so a pair on at 2020-06-30 pays an entry cost on 2020-07-01. No exit cost is charged at the period end (not a delisting, Phase 1 §1.6).
4. **Live pairs:** a pair is live on decision day `d` when its PairFrame has `d` and `d+1`; an off or not-yet-warm sleeve contributes 0 and counts in the mean (`E / N_live`). The real screen refuses when a pair's frame does not end on `insample_end` (no in-sample delisting is expected; one would need Phase 3's rules).
5. **Descriptive seeds:** V1 and the `always_on` row use purposes `"screen/v1"`, `"screen/always_on/v2"`, `"screen/always_on/v1"`, so the gating stream `"screen"` is exactly the spec's.
6. **Annualised net return** (descriptive) = `365 × mean(ret)`, arithmetic like V2's statistic; its CI is the excess CI plus `365 × h_d`.
7. **Config classes now:** `SleeveConfig`, `MarginConfig`, `CarryCostConfig`, `CarryRebalanceConfig` in `carry/config.py` (the prereg validates every block); `block_bootstrap_mean` added to `stats/bootstrap.py` now (Phase 4 §4.2 names it; backward compatible).

## Review Focus

1. **The screen runs only on the frozen file** (missing lock, hash mismatch → refuse). Task 5: `test_screen_refuses_without_lock`, `test_screen_refuses_hash_mismatch`.
2. **One shot** (existing `screen.json` → refuse; a non-finite statistic → refuse and no file). Task 5: `test_screen_refuses_to_overwrite`; Task 4: `test_non_finite_refuses`, `test_write_screen_refuses_nan_and_leaves_no_file`.
3. **Funding counted once and with the short's sign** (positive rate earns). Task 4: `test_hand_three_days_two_pairs` (funding on the perp leg added, price return subtracted).
4. **No load past the in-sample end** (a prereg whose `insample_end` is after 2023-12-31, or a data config that differs, refuses before any read). Task 5: `test_screen_refuses_insample_mismatch`, Task 3: `test_periods_insample_end_is_carry_end`.
5. **The prereg and `universe.json` agree.** Task 3: `test_universe_must_match_universe_json`.

---

### Task 1: Time-weighted `f̄`

**Files:** Modify `perpbt/carry/signals.py` (`trailing_funding`, docstring); Test `tests/carry/test_signals.py`, `tests/carry/test_real_frames.py`.

- [ ] Step 1: Tests: `test_interval_change_time_weighted` — a window of 3 events at 8 h with rate `a` and 6 events at 4 h with rate `b` gives `(3a + 6b) × 24 / (3×8 + 6×4) × 365`; update the existing interval-change test to the time-weighted value; steady-state 8 h equals `mean(rate) × 3 × 365` to 1e-12. Slow real-data test: in-sample `on` from `compute_signals` equals `on_off` of a per-event reference `f̄` (inline in the test) for every pair, W ∈ {3, 7, 14} (signals only; nothing printed).
- [ ] Step 2: Run → the interval tests fail.
- [ ] Step 3: `trailing_funding`: window sums of `rate` and of `interval_h`; `f̄ = Σrate × 24 / Σinterval_h × 365` where the window has events.
- [ ] Step 4: `python -m pytest tests/carry -q` (slow included) → green. Commit.

### Task 2: Carry config classes and `block_bootstrap_mean`

**Files:** Modify `perpbt/carry/config.py`, `perpbt/stats/bootstrap.py`; Test `tests/carry/test_config.py`, `tests/test_bootstrap.py` (or the existing stats test file).

**Produces:**
- `SleeveConfig(perp_leverage: float)` (> 0).
- `MarginConfig(rebalance_below: float, mmr: dict[str, float], liquidation_fee: float)`; `rebalance_below` in (0, 1]; `mmr` needs `default`, values > 0; `mmr_for(pair)`.
- `CarryCostConfig(perp_taker: float, spot_taker: float, slippage: dict[str, float])`; non-negative; `slippage` needs `default`; `slippage_for(pair)`.
- `CarryRebalanceConfig(fill_offset_min: int)` in `trend.daily.FILL_OFFSETS_MIN`.
- `block_bootstrap_mean(x, B, rng, block_len=10) -> np.ndarray` — `B` means of moving-block resamples via `block_indices`; empty `x` → `np.full(B, nan)`.

- [ ] Step 1: Tests: validation errors for each bound; `slippage_for("BTCUSDT")`/default; `block_bootstrap_mean` equals `x[k].mean(axis=1)` for the same seed's `block_indices`, chunk-independent, constant `x` → all replicates equal.
- [ ] Step 2–4: implement, run `tests/carry tests/test_bootstrap*.py` → green. Commit.

### Task 3: `carry/prereg.py` and `configs/carry/prereg.yaml`

**Files:** Create `perpbt/carry/prereg.py`, `configs/carry/prereg.yaml`; Test `tests/carry/test_prereg.py`.

**Produces:** `CarryPrereg` (fields `registered_on, universe, periods, signal, sleeve, margin, rebalance, costs, exploratory, reprice, hurdle, stats, screen, verdict_rule`; `content()`, `prereg_hash()`), `parse_prereg`, `load_prereg`, `check_universe(prereg, universe)` (`um` only), `lock_path`, `freeze(prereg_path, *, universe_path, today=None) -> dict`, `read_lock`, `check_frozen(prereg_path) -> CarryPrereg`, `LOCK_KEYS` = trend's. `Periods` (warmup, insample, holdout start/end, forward start/reviews): `warmup.start ≤ warmup.end < insample.start ≤ insample.end < holdout.start ≤ holdout.end`, `insample_end == frames.CARRY_INSAMPLE_END`, holdout start the day after insample end. `Exploratory(name, signal: CarrySignalConfig, sleeve: SleeveConfig)` stored merged; may override only `signal`, `sleeve`. `Hurdle(annual, descriptive)`, `CarryStats(alpha, bootstrap_n, block_len_days, random_timing_runs, shift_margin_days, master_seed)`, `ScreenConfig(bootstrap_n, stop_if_p_at_least)`, `RepriceConfig(slippage, spot_taker)`, verdict keys `v1, v2, edge, reported_for, forward_12m`.

- [ ] Step 1: Tests (copy trend's set): round trip of the committed file (values of §2.1; `always_on` signal entry −1e9/exit −1e9, `lev_1` sleeve 1.0 with the primary signal, `lookback_3d` keeps entry/exit), hash stable under key order / comments / `0.10` vs `0.1`, hash changes on content, forward start outside the hash, unknown/missing/invalid keys (parametrised, incl. exploratory overriding `costs`), duplicate YAML key, universe mismatch, `test_periods_insample_end_is_carry_end`, freeze writes the JSON lock once and refuses a universe mismatch, `read_lock` exact keys, `check_frozen` (missing lock, hash mismatch, forward start edit passes).
- [ ] Step 2: Run → fail (no module).
- [ ] Step 3: Implement; write `configs/carry/prereg.yaml` (§2.1 values, `registered_on: 2026-10-23`, `master_seed: 202610233`, `universe.um` copied from `configs/trend/universe.json`).
- [ ] Step 4: Run → green; `python -m perpbt carry prereg validate` comes in Task 5. Commit.

### Task 4: The screen model (`carry/screen.py`, pure part)

**Files:** Create `perpbt/carry/screen.py`; Test `tests/carry/test_screen.py`.

**Produces:**
- `ScreenInputs(pairs, day_ms, r_spot, r_perp, funding, live)` — `(pairs, days)` arrays on decision days `start−1 .. end−1`; NaN/False where not live.
- `screen_inputs(frames: dict[str, PairFrame], *, start: str, end: str) -> ScreenInputs`.
- `state_matrix(signals: dict[str, CarrySignals], frames, inputs) -> np.ndarray` — bool `on_d` per pair and decision day (False where not live).
- `sleeve_returns(on, inputs, *, leverage, cost_per_switch: np.ndarray) -> np.ndarray` — `x × (r_spot − r_perp + F)` when on, minus `x × cost_pair` where `on_d ≠ on_{d−1}` (previous = False before the first decision day and before a pair's first live day); `x = L/(L+1)`; 0 where off; NaN where not live.
- `portfolio_returns(sleeve, inputs) -> np.ndarray` — mean over live pairs; 0 when none.
- `v2_stats(ret, *, hurdle, B, block_len, rng) -> dict` and `v1_stats(ret, *, B, block_len, rng) -> dict`.
- `run_screen(inputs, on_primary, on_always, cfg pieces..., rngs) -> dict` with `p_screen`, `decision`, rows `primary`, `always_on`; refuses (ValueError) on any non-finite statistic.

- [ ] Step 1: Tests: `test_hand_three_days_two_pairs` (hand numbers incl. a funding day and a negative-rate day), `test_costs_on_transitions_only` (entry and exit charged once each; first day on → entry), `test_off_sleeve_contributes_zero`, `test_delisted_pair_leaves_mean`, `test_constant_30pct_funding_passes_v2` / `test_constant_2pct_funding_fails_v2` (synthetic PairFrames from `build_pairframe`, equal perp/spot prices with small noise, signals via `compute_signals`), `test_v2_p_formula` (p = (1+#≤0)/(B+1), CI and reading), `test_deterministic`, `test_non_finite_refuses`, `test_write_screen_refuses_nan_and_leaves_no_file`.
- [ ] Step 2–4: implement, run → green. Commit.

### Task 5: Store wiring, writer, CLI

**Files:** Modify `perpbt/carry/screen.py` (`screen_from_store`, `screen_path`, `write_screen`, `_summary_md`), `perpbt/cli.py` (`carry prereg validate|freeze`, `carry screen`), `.gitignore` (admit `runs/carry/screen.json`, `runs/carry/summary.md`); Test `tests/carry/test_screen_cli.py`.

`screen_from_store(prereg, data_cfg)`: refuse unless `data_cfg.insample_end == prereg insample_end == CARRY_INSAMPLE_END` and `warmup_start` equal and every member in `listing`; `load_pairframe(..., end=insample_end)` per member; refuse when a frame's last day ≠ `insample_end`; signals via `compute_signals(pf, prereg.signal)` and the `always_on` exploratory signal; costs per pair `spot_taker + perp_taker + 2 × slippage_for(pair)`; record `prereg_hash`, `data_config_hash`, `period`, `pairs`, `pair_last_day`.

- [ ] Step 1: CLI tests on a synthetic perp+spot+funding store in `tmp_path` (copy trend's `env`): validate prints the hash; universe mismatch exit 2; freeze once; screen refuses without lock / on hash mismatch; writes json then refuses to overwrite; stop writes summary.md, go does not; refuses insample/warmup mismatch; refuses a pair ending before the period end; `test_screen_json_byte_identical` (two runs in separate run dirs with the same `computed_on`/git → identical bytes).
- [ ] Step 2–4: implement, run `tests/carry` and `tests/trend` → green. Commit.

### Task 6: Notes, suite, stop before the freeze

- [ ] Step 1: Spec §2.6 implementation notes (the calls above); progress log here.
- [ ] Step 2: Full suite `python -m pytest -q` → green; `python -m perpbt carry prereg validate` prints the hash. Commit.
- [ ] Step 3: **STOP** — show the user `configs/carry/prereg.yaml`.

### Task 7: Freeze and screen (after the user's go)

- [ ] Step 1: `python -m perpbt carry prereg freeze`; commit `prereg.yaml` + `prereg.lock`; tag `carry-prereg-v1`.
- [ ] Step 2: `python -m perpbt carry screen` once, foreground. Commit `runs/carry/screen.json` (and `summary.md` on stop).
- [ ] Step 3: **STOP** — report `p_screen`, decision, descriptive rows.

### Task 8: Review and merge stop

- [ ] Step 1: Fresh whole-branch review (most capable model); fix; suite green.
- [ ] Step 2: **STOP** before `git merge --no-ff carry/2-prereg-screen` into `dev`.

## Progress log

Spec: docs/superpowers/specs/2026-10-23-funding-carry/phase-2-prereg-screen.md
