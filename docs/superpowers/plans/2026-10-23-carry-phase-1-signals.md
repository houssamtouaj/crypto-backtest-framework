# Carry Phase 1 — Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The paired daily frame (perp + spot on one calendar, cut at the last common candle, with basis, fill availability and late-fill prices), the trailing annualised funding `f̄`, and the on/off state with hysteresis. No execution, no returns.

**Architecture:** `perpbt/carry/frames.py` loads both legs' 15m candles and the perp funding through the guarded stores, finds `last_common_ms`, truncates both legs there and builds each leg with the trend `trend.daily.build_dayframe` (the builder `load_dayframe` uses), so a PairFrame holds two ordinary trend DayFrames. `perpbt/carry/signals.py` computes `f̄` from the perp leg's funding events and the on/off state. `perpbt/carry/config.py` holds `CarrySignalConfig` (the prereg `signal` block).

**Tech Stack:** Python 3.11, numpy, pytest; `perpbt.trend.daily`, `perpbt.data.store`, `perpbt.config`.

**Spec:** `docs/superpowers/specs/2026-10-23-funding-carry/phase-1-signals.md` with `00-overview.md`; Phase 0 §0.2/§0.7; trend Phase 1 §1.8.

## Global Constraints

- `CARRY_INSAMPLE_END = "2023-12-31"` in `carry/frames.py`; `load_pairframe` raises `HoldoutAccessError` for `end > CARRY_INSAMPLE_END` unless `allow_holdout=True`, whatever `DataConfig` it is given, before any I/O.
- Never write `allow_holdout=True` in `perpbt/` (user instruction); Phase 5's `carry/holdout.py` and `carry/forward.py` are the only future exceptions.
- No second DayFrame type: the legs are `trend.daily.DayFrame`s built by `trend.daily.build_dayframe`. The perp leg's return is its price return; `DayFrame.ret` is never used by carry code.
- `f̄_d = 365 × Σ_e rate_e × 24/interval_h_e / n_events` over the events of days `d−W+1..d`; NaN for `d < W−1` and for a window with no event.
- On/off: on if `f̄ > entry`, off if `f̄ < exit`, else previous; initial off; NaN → off. Prereg values: W 7, entry 0.10, exit 0.0.
- Real-data tests: `@pytest.mark.slow`, in-sample loads only (end ≤ 2023-12-31); EOS beyond the in-sample read from manifests only (timestamps).
- Compute and print no return, P&L or funding statistic beyond `f̄`/`on` themselves; never report the per-pair share of days on.
- Do not run any first-strategy or trend run command (`perpbt run`, `grid`, `baselines`, `holdout`, `stats`, trend screen).

## Review Focus

1. A leg ending mid-day (EOS perp 09:00) while the other continues → both legs' last candle is `last_common_ms`, the frame's last day is that day, funding after it dropped; pinned in Task 2 (`test_spot_ends_first`, `test_perp_ends_first_mid_day_eos_shape`).
2. A common last slot that is not `min(last perp, last spot)` (the earlier-ending leg's last candle missing in the other leg) → `last_common_ms` is the last slot present in both; Task 2 `test_last_common_skips_one_leg_gap`.
3. `end` past 2023-12-31 under `configs/trend/data.yaml` (whose stores allow perps to 2026-10-20) → refused before I/O; Task 2 `test_load_refuses_past_carry_insample_under_both_configs`.
4. A funding interval change (8 h → 4 h) inside a window → per-event normalisation, no jump from the event count; Task 3 `test_interval_change_mid_series`.
5. A day with no funding event inside a warm window (a funding gap) → the mean over the events present; a window with none → NaN → off; Task 3 `test_window_without_events_is_nan_and_off`.

---

### Task 1: `CarrySignalConfig`

**Files:** Create `perpbt/carry/config.py`; Test `tests/carry/test_config.py`.

**Interfaces — Produces:** `CarrySignalConfig(lookback_days: int, entry_annual: float, exit_annual: float)`, a frozen `_Config`, no defaults.

- [ ] **Step 1:** Tests: `from_dict` round trip with `{lookback_days: 7, entry_annual: 0.10, exit_annual: 0.0}`; `lookback_days` 0 → `ConfigError`; `exit_annual > entry_annual` → `ConfigError`; `entry == exit` (always_on's −1e9/−1e9) accepted; non-finite threshold → `ConfigError`; unknown key → `ConfigError`.
- [ ] **Step 2:** Run → FAIL (module missing). **Step 3:** implement. **Step 4:** PASS. **Step 5:** commit `Carry phase 1: CarrySignalConfig`.

### Task 2: PairFrame (`carry/frames.py`)

**Files:** Create `perpbt/carry/frames.py`; Test `tests/carry/test_frames.py`.

**Interfaces — Produces:**
- `CARRY_INSAMPLE_END = "2023-12-31"`; `LEGS = ("perp", "spot")`.
- `PairFrame` (frozen, `eq=False`, read-only arrays): `pair`, `day_ms`, `perp: DayFrame`, `spot: DayFrame`, `basis`, `filled`, `fill_ok: dict[int, ndarray[bool]]`, `fill_px_late: dict[str, dict[int, ndarray]]`, `fill_late_ms: dict[str, dict[int, ndarray]]` (float64, the fill time = the candle's close = open + 15 min; NaN when none), `funding_interval_h: ndarray` (aligned with `perp.funding_ts`), `last_common_ms: int`; `__len__`.
- `build_pairframe(perp: Candles, funding: Funding, spot: Candles, *, first_day: str, fill_offsets_min=FILL_OFFSETS_MIN) -> PairFrame`.
- `load_pairframe(data_cfg, pair, *, end, allow_holdout=False, fill_offsets_min=FILL_OFFSETS_MIN) -> PairFrame`.

- [ ] **Step 1: tests** (synthetic, `tests.synthetic.random_walk`, 96 candles a day):
  - `test_legs_equal_load_dayframe`: both legs end together → `pf.perp`/`pf.spot` arrays equal `trend.daily.load_dayframe(cfg, pair, end, market=…)`.
  - `test_spot_ends_first`, `test_perp_ends_first_mid_day_eos_shape` (perp to day 3 09:00, spot 3 days longer, flat funding after): `last_common_ms`, both legs' `last_candle_ms == last_common_ms`, `day_ms[-1]` the cut day, `perp.funding_ts.max() <= last_common_ms`, `len(funding_interval_h) == len(perp.funding_ts)`.
  - `test_last_common_skips_one_leg_gap`.
  - `test_basis_filled_fill_ok`: basis = `perp.c/spot.c − 1`; a spot-only missing day → pair `filled`; a missing 00:00 spot candle → `fill_ok[15]` False that day, True elsewhere.
  - `test_fill_px_late`: on time = `fill_px[h]` and `fill_late_ms = day + h`; missing candle → next candle of the day; none left in the day → NaN.
  - `test_pair_mismatch_and_no_common_raise`.
  - `test_load_refuses_past_carry_insample_under_both_configs` (parametrised over `configs/carry/data.yaml` and `configs/trend/data.yaml`, `data_dir` = tmp): `end="2024-01-01"` → `HoldoutAccessError` matching `2023-12-31` with an empty store (no I/O); `end="2023-12-31"` → `FileNotFoundError` (passes the guard).
  - `test_load_allow_holdout_passes_through`: trend config, synthetic perp+funding+spot 2023-12-30..2024-01-02, `allow_holdout=True` loads through 2024-01-02.
  - `test_no_allow_holdout_true_in_carry`: AST scan of `perpbt/carry/*.py` for a keyword `allow_holdout=True`, except `holdout.py`/`forward.py`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** implement (common slots by `np.intersect1d` from the first day; truncate with `Candles.slice`; late fill by `searchsorted` of `day + h − 15 min` bounded by the day's end). **Step 4:** PASS. **Step 5:** commit `Carry phase 1: PairFrame`.

### Task 3: trailing funding and on/off (`carry/signals.py`)

**Files:** Create `perpbt/carry/signals.py`; Test `tests/carry/test_signals.py`.

**Interfaces — Consumes:** `PairFrame`, `CarrySignalConfig`. **Produces:** `trailing_funding(ptr, rate, interval_h, lookback_days) -> ndarray`; `on_off(fbar, entry, exit) -> ndarray[bool]`; `CarrySignals(fbar, on)` frozen; `compute_signals(pf, cfg) -> CarrySignals`.

- [ ] **Step 1: tests:** hand-built series — warmup NaN → off; entry crossing; exit crossing; band keeps state both ways; ties (`= entry` stays off, `= exit` stays on); `test_interval_change_mid_series` (8 h 1e-4 → 4 h 5e-5 is the same per-day rate, `f̄` constant 0.1095); `test_window_without_events_is_nan_and_off`; NaN mid-series drops a held state to off; always_on thresholds → on from the first warm day. Perturbation (`tests/synthetic.assert_causal`, `confirmed_at` = last candle of the day): perp candles and spot candles at day-end cuts with `truncate=True` and mid-day cuts; funding events changed after a cut time leave days ending at or before it unchanged.
- [ ] **Step 2:** FAIL. **Step 3:** implement (per-day sums of normalised rates and counts, window sums with `sliding_window_view`; plain loop for the state). **Step 4:** PASS. **Step 5:** commit `Carry phase 1: trailing funding and on/off state`.

### Task 4: real data, notes

**Files:** Test `tests/carry/test_real_frames.py` (slow); Modify spec `phase-1-signals.md` (§1.6 implementation notes); this plan's progress log.

- [ ] **Step 1:** Slow tests under `configs/carry/data.yaml`, every pair, `end="2023-12-31"`: first day = listing; last day 2023-12-31 and `last_common_ms` 23:45; no pair filled run > 5; basis finite; `fill_ok[15]` all True; intervals in {1, 2, 4, 8}; `f̄` NaN exactly on the first W−1 days and finite after, for W in {3, 7, 14}; `on` boolean. EOS from manifests only: perp last open 2025-05-21 09:00 < spot last open → the common last day is the perp's.
- [ ] **Step 2:** `python -m pytest tests/carry -q` (slow included) and the full suite → green. Commit.
- [ ] **Step 3:** Spec §1.6 notes and the progress log; commit.
- [ ] **Step 4:** Fresh whole-branch review (most capable model); fix; STOP before the `--no-ff` merge into dev.

## Progress log

Spec: docs/superpowers/specs/2026-10-23-funding-carry/phase-1-signals.md
- Ruling (my call): `load_pairframe` loads the 15m candles and funding itself and builds each leg with `trend.daily.build_dayframe`, not `load_dayframe`: the cut at `last_common_ms` and the late-fill prices need the 15m candles, which a DayFrame does not keep. A test pins that the legs equal `load_dayframe`'s output when no cut is needed — cost if wrong: four duplicated lines of loading.
- Ruling (my call): both legs are truncated at `last_common_ms` before building, so the last day's o/h/l/c of both legs stop at the last common candle (consistent with the funding cut and Phase 3's close at that candle) — cost if wrong: the last day's bar differs from the uncut DayFrame.
- Ruling (my call): `fill_late_ms` is the fill time (the candle's close, open + 15 min), so it equals `day + h` when on time — cost: none.
- Ruling (my call): `funding_interval_h` is added to the PairFrame (the DayFrame keeps no interval) — cost: none.
Task 1: complete (cb16830; tests/carry/test_config.py → 7 passed)
Task 2: complete (b50c7bc; tests/carry/test_frames.py → 12 passed)
Task 3: complete (506c494; tests/carry/test_signals.py → 16 passed). Fixture: the sine period was lengthened (k/15, 60 days) so the 7-day f̄ crosses both thresholds. Mutation check: an f̄ that reads the next day's funding fails the truncation and funding-perturbation tests.
Task 4: complete (558e154; real data 13 passed in 1 s; full suite 1177 passed in 2 min 40 s).
- Task 4: Ruling: seven perps' listing days start mid-day (08:00; XRP 08:15), so `fill_ok` is asserted from day 1. The late fill is asserted on every day — cost: none (trading starts 2020-07-01).
Final review: fresh reviewer (opus) — "merge"; 0 Critical, 0 Important, 10 Minor; tests/carry 64 passed; facts in §1.6 confirmed; mutation runs confirmed that the causality, late-fill and guard tests are not vacuous.
- Final: fixed (Minor) the guard tests match "carry in-sample end", so the store's own refusal cannot satisfy them (mutation: carry guard off → 3 failures); the real-data refusal too.
- Final: fixed (Minor) the allow_holdout scan flags any value but False / the pass-through parameter, and any `**` kwargs; new AST tests: no `.ret` read in perpbt/carry; only frames.py and basis.py read the stores.
- Final: fixed (Minor) real-data intervals pinned to {8}; §1.6: all in-sample events 8 h, XLM pre-candle event, §1.4 wording, Phase 3 pointers; a test keeps an event at exactly last_common_ms.
- Final: Ruling (labelled, not changed) the warmup counts a partial listing day as one of W days, and annualisation is per event as §1.2 states (only interval-change windows differ, none in-sample) — cost if wrong: transition-window f̄ in the holdout/forward weights 4 h events more; can be changed before the Phase 2 freeze.
