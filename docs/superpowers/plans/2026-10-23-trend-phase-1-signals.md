# Trend Phase 1 — Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-pair daily arrays (`DayFrame`) built from 15m candles and funding, the 12 ensemble members, the ensemble exposure, the 60-day vol forecast and the vol-targeted target weights, all causal, computed for every perp universe pair on real data.

**Architecture:** `perpbt/trend/daily.py` turns one pair's 15m candles and funding into calendar-day arrays starting at the pair's configured first day (`max(listing, warmup_start)`) and ending at its last candle, reusing `indicators.daily.daily_bars`. `perpbt/trend/config.py` holds the `SignalConfig` and `SizingConfig` frozen dataclasses (the Phase 2 pre-registration's `signal` and `sizing` blocks). `perpbt/trend/signals.py` holds the member functions (pure numpy on daily `c, h, l`), the exposure, `σ̂`, `w*`, and `compute_signals(frame, signal, sizing) -> Signals`.

**Tech Stack:** Python 3.11, numpy, pandas (only through the stores), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-23-trend-following/phase-1-signals.md`, read with `00-overview.md` (TD1–TD3, §7, §11) and Phase 0 §0.9 (data facts). Code this builds on: `perpbt/indicators/daily.py` (`daily_bars`), `perpbt/stats/buyhold.py` (`bh_daily`, the `ret` reference), `perpbt/data/store.py` (`CandleStore`, `FundingStore`, `Candles`, `Funding`, `DAY_MS`, `date_ms`), `perpbt/config.py` (`_Config`, `_check_*`, `ConfigError`), `tests/synthetic.py` (`random_walk`, `perturb_after`, `assert_causal`, `candles_from_rows`).

## Global Constraints

- Branch `trend/1-signals` from `dev` (bc68d09). Ask the user before the `--no-ff` merge into `dev`.
- A pair's first day is `max(listing[pair], warmup_start)` from its `DataConfig`, never the first stored candle (the shared BTC/ETH 15m store starts 2019-11-01; the trend listing says 2020-01-01).
- A DayFrame ends on the day of the pair's last 15m candle; funding events after that candle's open time are dropped (EOSUSDT: last candle 2025-05-21 09:00, artifact funding at a flat 1e-4 per 8 h continues to 2026-09).
- `ret_d = c_d / c_{d−1} − 1 − funding_d`; an event at exactly 00:00 belongs to the new day.
- Members on completed daily closes, `N ∈ {20, 40, 80, 160}`; ties (`=`) are "not above" (0). Donchian: long on `c_d > max(h_{d−N..d−1})`, off `c_d < min(l_{d−N/2..d−1})`; short mirrored; initial state off.
- `e_d = mean of the 12 members`; `σ̂_d = std(ret_{d−59..d}, ddof=1) × √365`; `w*_d = e_d × min(0.30 / σ̂_d, 2.0)`. Parameters come from `SignalConfig` / `SizingConfig`, not module constants.
- Valid (active) on day index `d` only if `d ≥ max(lookbacks)` (index 0 = first day) and `σ̂_d` is finite; otherwise `e_d` and `w*_d` are NaN.
- More than 5 consecutive `filled` days in a pair logs a warning (`logging`, the codebase's convention) and is recorded on the DayFrame.
- No `allow_holdout=True` anywhere under `perpbt/` (the grep test in `tests/test_holdout.py`); the loader passes the caller's flag through. Spot tests use synthetic data in a temporary store; the real spot check reads manifests only, never prices.
- **No performance number of any kind** is computed or printed in this phase (no returns of weights × prices, no Sharpe).
- Do not run any first-strategy command (`perpbt run`, `grid`, `baselines`, `holdout`, `stats`) (overview §11 item 9). Long jobs in the foreground, one at a time.

## Review Focus

1. **The first day comes from the config, not the store.** Candles from 2019-11-01 with listing 2020-01-01 must give `day_ms[0] == 2020-01-01` and `ret[0]` NaN (the 2019-12-31 close is not used). Pinned in Task 1 by `test_first_day_from_config_not_store`.
2. **Dead-contract funding.** Funding events after the last candle must not reach `funding`, `ret` or the event lists, and the frame must not extend past the last candle's day. Pinned in Task 1 by `test_funding_after_last_candle_dropped`.
3. **Zero vol.** Sixty flat days (filled days or a constant price, no funding) give `σ̂ = 0`; `w*` must be `e × max_weight`, finite, with no `RuntimeWarning`. Pinned in Task 3 by `test_zero_vol_hits_cap`.
4. **A candle inside day `d`** moves day `d`'s close but must change nothing on earlier days, while a cut at a day's last candle freezes that day too. Pinned in Task 3 by `test_signals_causal_mid_day` and `test_signals_causal_day_end` (truncation included).
5. **Donchian uses highs to enter and lows to exit.** A close above the prior closes but not the prior highs must not enter. Pinned in Task 2 by `test_donchian_uses_highs_and_lows`.

---

### Task 1: `DayFrame` and its loader (`trend/daily.py`)

**Files:**
- Create: `perpbt/trend/daily.py`, `tests/trend/test_daily.py`

**Interfaces:**
- Consumes: `daily_bars(candles15) -> DailyBars`; `Candles`, `Funding`, `CandleStore(cfg, market=)`, `FundingStore(cfg)`, `DAY_MS`, `date_ms`; `DataConfig`.
- Produces:
  - `FILL_OFFSETS_MIN: tuple[int, ...] = (15, 60, 240, 480)`; `MAX_FILLED_RUN = 5`.
  - `@dataclass(frozen=True, eq=False) class DayFrame` with fields `pair: str`, `day_ms: int64[D]`, `o, h, l, c: float64[D]` (`l` is the daily low used by the liquidation check), `filled: bool[D]`, `funding: float64[D]` (sum of the day's rates), `ret: float64[D]` (`ret[0]` NaN), `fill_px: dict[int, float64[D]]` (offset minutes → close of the 15m candle closing at `day + offset`; NaN when missing or on a filled day), `funding_ts: int64[F]`, `funding_rate: float64[F]`, `funding_px: float64[F]` (open of the 15m candle starting at `ts`, NaN when missing), `funding_ptr: int64[D+1]` (day `k`'s events are `[ptr[k], ptr[k+1])`), `last_candle_ms: int`, `filled_runs: tuple[tuple[int, int], ...]` (`(first day_ms, length)` of every run of filled days). `__len__` = D; `events(k) -> slice`.
  - `filled_runs(day_ms: np.ndarray, filled: np.ndarray) -> tuple[tuple[int, int], ...]`.
  - `build_dayframe(candles15: Candles, funding: Funding, *, first_day: str, fill_offsets_min: tuple[int, ...] = FILL_OFFSETS_MIN) -> DayFrame`.
  - `first_day(data_cfg: DataConfig, pair: str) -> str` = `max(listing[pair], warmup_start)`; `ValueError` naming the config when the pair is not listed.
  - `load_dayframe(data_cfg: DataConfig, pair: str, *, end: str, market: str = "um", allow_holdout: bool = False, fill_offsets_min=FILL_OFFSETS_MIN) -> DayFrame`: loads 15m candles and (perps only) funding over `[date_ms(first_day), date_ms(end) + DAY_MS)`; spot gets an empty `Funding`.

- [ ] **Step 1: Failing tests** in `tests/trend/test_daily.py` (synthetic candles from `candles_from_rows` / `random_walk` at `T0_MS`, funding built with `Funding(...)`):
  - `test_calendar_days_and_filled`: candles on days 0, 1, 3 (none on day 2) → `len == 4`, `filled == [F, F, T, F]`, day 2 `o == h == l == c == c[1]`, `fill_px[15][2]` NaN, `ret[2] == -funding[2]`.
  - `test_funding_day_boundary`: an event at exactly day 1 00:00 is in `funding[1]` and in `events(1)`, not day 0; an event at 23:59 of day 0 is in day 0.
  - `test_ret_matches_bh_daily`: 5 full days of `random_walk` plus funding every 8 h → `ret[1:]` equals `bh_daily(candles, funding, first_idx=96, last_idx=n-1)["ret"]` to 1e-15; `ret[0]` NaN.
  - `test_fill_px_per_offset`: `fill_px[15][k]` is the close of the candle opening at day `k` 00:00; `fill_px[60][k]` of the candle opening at 00:45; `fill_px[480][k]` at 07:45; a removed 00:45 candle gives `fill_px[60][k]` NaN.
  - `test_funding_event_px`: `funding_px` is the open of the candle starting at the event; an event at a missing candle gives NaN.
  - `test_intraday_gap_uses_present_candles`: a day missing its 10:00–12:00 candles is not `filled`; its `h, l, c` come from the present candles.
  - `test_first_day_from_config_not_store`: candles from two days before `first_day` → `day_ms[0] == date_ms(first_day)`, `ret[0]` NaN, `c[0]` = close of `first_day`'s last candle.
  - `test_first_day_without_candle_raises`: no candle on `first_day` → `ValueError`.
  - `test_funding_after_last_candle_dropped`: candles end on day 3 at 09:00; events at day 3 08:00, day 3 16:00 and day 5 00:00 → `len == 4`, `funding_ts.max()` is day 3 08:00, `funding[3]` is that one rate, `last_candle_ms` is day 3 09:00.
  - `test_long_filled_run_warns`: 6 consecutive filled days → one `WARNING` from `perpbt.trend.daily` naming the pair and the first filled date (caplog), `filled_runs` contains `(day_ms of the first, 6)`; 5 filled days → no warning.
  - `test_empty_funding`: an empty `Funding` gives `funding == 0`, `funding_ptr` all zeros, `ret` the pure price return.
  - `test_first_day_is_max_of_listing_and_warmup`: listing `2020-01-17`, warmup `2020-01-01` → `"2020-01-17"`; listing `2017-08-17`, warmup `2017-08-01` → `"2017-08-17"`; listing before warmup → warmup; unknown pair → `ValueError`.
  - `test_load_dayframe_perp` (tmp store via the `data_cfg` fixture, rows written with `CandleStore.write` / `FundingStore.write`): 15m rows from 2019-11-01, listing 2020-01-01 → `day_ms[0] == date_ms("2020-01-01")`, last day `end`.
  - `test_load_dayframe_spot_needs_allow_holdout`: rows in the spot store → `load_dayframe(..., market="spot")` raises `HoldoutAccessError`; with `allow_holdout=True` (in the test only) it returns a frame with zero funding.
- [ ] **Step 2:** `python -m pytest tests/trend/test_daily.py -q` → fails (module missing).
- [ ] **Step 3:** Implement. Day grid: `day_ms = first_day_ms + DAY_MS × arange(D)` with D up to the last candle's day; `daily_bars` on the candles from `first_day_ms`, scattered onto the grid; filled days forward-filled from the previous close. Fill offsets must be multiples of 15 in `[15, 1440]` (`ValueError`). Per-day funding sums via `np.add.at`; `funding_ptr` via `searchsorted` of the day starts on `funding_ts`.
- [ ] **Step 4:** `python -m pytest tests/trend/test_daily.py tests/test_holdout.py -q` → pass. Commit `Trend phase 1: DayFrame (daily arrays from 15m candles and funding) and its loader`.

### Task 2: `SignalConfig`, `SizingConfig` and the 12 members

**Files:**
- Create: `perpbt/trend/config.py`, `perpbt/trend/signals.py`, `tests/trend/test_config.py`, `tests/trend/test_signals.py`

**Interfaces:**
- Consumes: `perpbt.config._Config`, `ConfigError`, `_check_literal`, `_check_positive`, `from_dict`, `config_hash`.
- Produces:
  - `config.py`: `MEMBERS = ("return_sign", "ma_filter", "donchian")`; `@dataclass(frozen=True) class SignalConfig(_Config)`: `members: tuple[str, ...]` (non-empty, each in `MEMBERS`, no duplicates), `lookbacks: tuple[int, ...]` (non-empty, unique, each even and `>= 2`), `donchian_exit: str` (`"half"` only), `direction: str` (`"long_flat"` | `"long_short"`); property `long_short: bool`. `@dataclass(frozen=True) class SizingConfig(_Config)`: `target_vol: float` (> 0), `vol_window: int` (>= 2), `max_weight: float` (> 0), `max_gross: float` (> 0; used from Phase 3).
  - `signals.py`: `return_sign(c, n, *, long_short=False) -> float64[D]` (NaN for `d < n`); `ma_filter(c, n, *, long_short=False)` (NaN for `d < n − 1`); `donchian_states(c, h, l, n) -> (bool[D], bool[D])` (long, short; False before day `n`); `donchian(c, h, l, n, *, long_short=False)` (NaN for `d < n`; long/flat = long state, long/short = long − short).
- [ ] **Step 1: Failing tests.**
  - `tests/trend/test_config.py`: `from_dict` round trip of the primary (`members` all three, `lookbacks [20, 40, 80, 160]`, `"half"`, `"long_flat"`; sizing `0.30, 60, 2.0, 2.0`); `vol_window: 60` vs `60.0` → `ConfigError` (int field); `target_vol: 0.3` and `0.30` hash equal; odd lookback `21`, duplicate lookback, unknown member, `direction: "short_only"`, `donchian_exit: "full"`, `target_vol: 0`, unknown key → `ConfigError`.
  - `tests/trend/test_signals.py` (hand-worked):
    - `test_return_sign`: `c = [1, 2, 3, 3, 2]`, `n = 2` → `[nan, nan, 1, 1, 0]`; long/short `[nan, nan, 1, 1, -1]`; `c = [1, 2, 1]` → day 2 is `0` in both (tie).
    - `test_ma_filter`: `c = [1, 3, 2, 2]`, `n = 2` → `[nan, 1, 0, 0]`; long/short `[nan, 1, -1, 0]` (day 3 is a tie with its mean).
    - `test_donchian_state_machine`: `c = h = l = [10, 10, 10, 10, 11, 12, 11.5, 10.5, 9, 9, 10.6]`, `n = 4` → long/flat `[nan]*4 + [1, 1, 1, 0, 0, 0, 0]`, long/short `[nan]*4 + [1, 1, 1, 0, -1, -1, 0]` (days 5–6 and 9 carry the state).
    - `test_donchian_tie_does_not_enter`: `c = h = l = [10] * 5`, `n = 4` → day 4 is `0`.
    - `test_donchian_uses_highs_and_lows`: `c = l = [10, 10, 10, 10, 10.5]`, `h = [11, 10, 10, 10, 10.5]`, `n = 4` → day 4 is `0`.
    - `test_donchian_never_both_states`: on 20 seeded `random_walk` daily series (2,000 15m candles aggregated with `daily_bars`), for each `n` in `(20, 40, 80, 160)`, `not (long & short).any()`, and the long/short member takes only `{-1, 0, 1}` after warmup.
- [ ] **Step 2:** `python -m pytest tests/trend/test_config.py tests/trend/test_signals.py -q` → fail.
- [ ] **Step 3:** Implement. Rolling max/min/mean via `sliding_window_view` over the prior `N` (or `N // 2`) days; the Donchian state is a plain loop over days (about 2,400 days per pair).
- [ ] **Step 4:** Run the two files → pass. Commit `Trend phase 1: signal and sizing configs; return-sign, MA-filter and Donchian members`.

### Task 3: Exposure, vol forecast, target weights, `compute_signals`, causality

**Files:**
- Modify: `perpbt/trend/signals.py`
- Test: `tests/trend/test_signals.py`

**Interfaces:**
- Consumes: Task 1 `DayFrame`, `build_dayframe`; Task 2 members and configs.
- Produces:
  - `member_matrix(frame: DayFrame, signal: SignalConfig) -> tuple[tuple[str, ...], np.ndarray]`: names `f"{member}_{n}"` in `signal.members` × `signal.lookbacks` order, values shape `(K, D)`.
  - `warmup_days(signal: SignalConfig, sizing: SizingConfig) -> int` = `max(max(lookbacks), vol_window)` (the first valid index; `ret[0]` is NaN so `σ̂` is first finite at index `vol_window`).
  - `vol_forecast(ret: np.ndarray, window: int) -> np.ndarray`: annualised (`√365`), NaN until `window` finite returns.
  - `target_weight(e: np.ndarray, sigma: np.ndarray, sizing: SizingConfig) -> np.ndarray`: `e × min(target_vol / σ, max_weight)`; `σ = 0` gives `max_weight`; NaN where `e` or `σ` is NaN; no numpy warnings. Public because the Phase 2 screen recomputes weights for shifted exposures.
  - `@dataclass(frozen=True, eq=False) class Signals`: `pair`, `day_ms`, `names`, `members` `(K, D)`, `exposure`, `sigma`, `weight`, `valid: bool[D]`.
  - `compute_signals(frame: DayFrame, signal: SignalConfig, sizing: SizingConfig) -> Signals`: `valid = (arange(D) >= max(lookbacks)) & isfinite(sigma)`; `exposure` and `weight` NaN where not valid.
- [ ] **Step 1: Failing tests** (append to `tests/trend/test_signals.py`; a test helper `frame_from_closes(closes, *, funding=None)` builds a DayFrame from one 15m candle per day at 23:45 with `o = h = l = c`):
  - `test_exposure_ramp_and_flat`: 200 days of a strictly rising close, primary config → `exposure[160:] == 1.0` (long/flat and long/short); a constant close → `exposure[160:] == 0.0` in both (every member ties).
  - `test_exposure_steps_of_one_twelfth`: on a `random_walk` frame of 400 days, `exposure[valid] * 12` is integral, within `[0, 12]` long/flat and `[-12, 12]` long/short.
  - `test_warmup`: a 300-day frame → `valid[:160]` all False, `valid[160:]` all True, `exposure[159]` NaN; with `vol_window = 200` the first valid index is 200 (`warmup_days` agrees).
  - `test_vol_forecast`: `window = 3`, `ret = [nan, .01, -.02, .03, .00]` → `[nan, nan, nan, std([.01, -.02, .03], ddof=1) × √365, std([-.02, .03, .00], ddof=1) × √365]`.
  - `test_target_weight`: `e = 0.5, σ = 0.6 → 0.25`; `e = 0.5, σ = 0.1 → 1.0`; `e = -1, σ = 0.3 → -1.0`; `e = nan → nan`; `σ = nan → nan` (sizing `0.30, …, max_weight 2.0`).
  - `test_zero_vol_hits_cap`: a constant close for 250 days, `e` forced to `0.5` → `target_weight` gives `1.0`, finite, under `warnings.simplefilter("error")`; `compute_signals` on that frame runs under the same filter.
  - `test_ret_includes_funding_in_sigma`: the same prices with and without a constant 1e-4 funding per 8 h give the same `σ̂`; alternating ±1e-3 funding changes it.
  - `test_signals_causal_day_end`: a 15m `random_walk` of 260 days; `fn(c) = (values, confirmed_at)` where values stack `members`, `exposure`, `sigma`, `weight` per day and `confirmed_at[k]` is the index of day `k`'s last candle; `assert_causal(fn, candles, cuts=[day-end candles of days 159, 160, 200, 259], seeds=[1, 2], truncate=True)`.
  - `test_signals_causal_mid_day`: same `fn`, cuts at candles 37 and 80 of days 170 and 230, `truncate=False` (rows of day `k` are compared only when its last candle is at or before the cut).
  - `test_funding_after_cut_changes_nothing_before`: adding an event on day 200 leaves every output on days `< 200` unchanged.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement. `np.errstate(divide="ignore", invalid="ignore")` around the division only; the cap applied with `np.where(sigma > 0, …, max_weight)`.
- [ ] **Step 4:** `python -m pytest tests/trend -q` → pass. Commit `Trend phase 1: exposure, vol forecast, target weights; causality tests`.

### Task 4: Real data, spec notes, full suite, review

**Files:**
- Create: `tests/trend/test_real_signals.py` (`pytestmark = pytest.mark.slow`; skip when `data/candles` is absent)
- Modify: `docs/superpowers/specs/2026-10-23-trend-following/phase-1-signals.md` (new §1.8 Implementation notes), this plan's progress log

- [ ] **Step 1: Tests:**
  - `test_perp_signals_warm_and_complete` (parametrised over `configs/trend/universe.json` `um.members`): `load_dayframe(trend cfg, pair, end="2026-10-20")` with `data_dir` rebased to the repo root; `day_ms[0] == date_ms(listing[pair])`; no `filled` day; `compute_signals` with the primary configs → `valid` True and `weight` finite on every day from 2020-07-01 to the frame's last day; `abs(weight) <= 2.0`.
  - `test_eos_frame_ends_at_last_candle`: `EOSUSDT` → `day_ms[-1] == date_ms("2025-05-21")`, `funding_ts.max() <= last_candle_ms`.
  - `test_btc_store_head_not_used`: `BTCUSDT` and `ETHUSDT` start 2020-01-01 although the store starts 2019-11-01 (`CandleStore(cfg).manifest(...)["first_open_ms"] < date_ms("2020-01-01")`).
  - `test_spot_filled_runs_from_manifests` (parametrised over `spot.members`; reads `data/spot/candles/<PAIR>/15m/manifest.json`, never prices): rebuild the 15m timestamp grid from `first_open_ms`, `last_open_ms` and `gaps`; per calendar day from `listing[pair]` mark days without a timestamp; `filled_runs(...)` has no run longer than `MAX_FILLED_RUN`.
- [ ] **Step 2:** `python -m pytest tests/trend/test_real_signals.py -q` (foreground) → pass. Record in the progress log per pair: first day, first valid date, last day, filled-run summary, and the spot members' warm dates (first day + 160) from their listings. No weights, returns or other performance figures in the log.
- [ ] **Step 3:** Write §1.8 Implementation notes in the Phase 1 spec: `ret[0]` NaN; `l` is the low; funding kept through the last candle's open; `fill_px` NaN when missing (the late-fill rule's "next available candle" is Phase 3's to add); spot DayFrames carry no funding (the assumed 1e-4/8 h is a constant per day and does not move `σ̂`; Phase 3 charges it); the warning goes through `logging`; spot real-data check from manifests only; `warmup_days = max(max(lookbacks), vol_window)`. Commit `Trend phase 1: real-data checks; spec implementation notes`.
- [ ] **Step 4:** `python -m pytest -q` (full suite, slow included, foreground) → all green; paste the tail into the progress log.
- [ ] **Step 5:** Fresh whole-branch review (one reviewer, most capable model) against the spec and this plan; fix findings; commit.
- [ ] **Step 6:** Ask the user to merge `trend/1-signals` into `dev` (`--no-ff`).

## Progress log

Spec: docs/superpowers/specs/2026-10-23-trend-following/phase-1-signals.md
Pre-flight:
- T1 -> T3: DayFrame fields (c, h, l, ret, day_ms) and build_dayframe — consistent.
- T2 -> T3: SignalConfig/SizingConfig and the member functions — consistent.
- Task 1: Ruling: DayFrame arrays are made read-only (as CandleStore.load's) — later phases share one frame across many paths — cost if wrong: none.
- Task 1: Ruling: added test_fill_offset_validation beyond the brief — pins the offset check — cost: none.
Task 1: complete (commits 0de5f81..2f2d603, tests: python -m pytest tests/trend/test_daily.py tests/test_holdout.py -q → 24 passed)
- Task 2: Ruling: test_donchian_uses_highs_and_lows also pins the exit on lows (a close above the prior closes but below the prior N/2 lows exits) — the brief pinned only the entry — cost: none.
Task 2: complete (commits 2f2d603..68259d9, tests: python -m pytest tests/trend/test_config.py tests/trend/test_signals.py -q → 43 passed)
- Task 3: Ruling: added test_member_matrix_names_and_shape and a "the walk moves the ensemble" guard in test_exposure_steps_of_one_twelfth — keeps the step test from passing on a constant exposure — cost: none.
- Task 3: Ruling: σ̂ annualised with √365 (calendar days, as the spec's formula) as module constant ANNUALISE — a fixed part of the definition, not a tunable — cost if wrong: none (the spec fixes it).
Task 3: complete (commits 68259d9..40fe9d0, tests: python -m pytest tests/trend -q → 90 passed in 1.72s)
- Task 4: Ruling: the real-data tests check code already written in Tasks 1-3 and were not run RED (as Phase 0's config checks) — each asserts concrete dates and counts — cost if wrong: a check that cannot fail.
- Task 4: Ruling: real-data perp test also runs long/short and checks all 12 members finite from 2020-07-01; spot check also asserts the listing day holds the first candle — cost: none.
- Task 4: real data: 10 perps, 0 filled days, valid from 2020-06-09 (BTC ETH BCH) .. 2020-06-28 (XLM); EOS last day 2025-05-21, 5881 funding events kept; each pair loads + signals in < 0.1 s. Spot (manifests only): no filled run > 5; warm BTC/ETH 2018-01-24, BNB 04-15, BCC 04-20, NEO 04-29, LTC 05-22.
Task 4: complete (commits 40fe9d0..56e7492, tests: python -m pytest -q (full, slow included) → 1053 passed in 154.04s)
