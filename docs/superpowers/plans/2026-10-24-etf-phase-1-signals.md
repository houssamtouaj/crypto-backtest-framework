# ETF Phase 1 — Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-ETF trading-day arrays (`EtfFrame`: prices, dividends, total return split into overnight and intraday, T-bill cash return), the 12 crypto-trend members reused unchanged, the exposure, the √252 vol forecast and the vol-targeted target weights, all causal, computed for SPY, QQQ, IWM and GLD on the real in-sample store.

**Architecture:** `perpbt/etf/frame.py` turns one ticker's `EtfStore.load` rows and the DTB3 rates into read-only numpy arrays from `warmup_start` to the period end; `load_frames` builds all four and refuses misaligned calendars. `perpbt/etf/config.py` gains `EtfSizingConfig` (the Phase 2 prereg `sizing` block). `perpbt/etf/signals.py` imports the member functions and `target_weight` from `perpbt/trend/signals.py`, adds `vol_forecast252`, the `no_vol_target` branch, `compute_signals` and a descriptive `summarise` (no returns).

**Tech Stack:** Python 3.11, numpy, pandas (only through `EtfStore`), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-24-etf-trend/phase-1-signals.md`, read with `00-overview.md` (ED1, ED3, ED4, §7) and `phase-0-data.md` §0.9. Code this builds on: `perpbt/etf/store.py` (`EtfStore.load`, `load_rates`, `manifest`), `perpbt/etf/config.py` (`EtfDataConfig`), `perpbt/etf/validate.py` (`total_return`, the §1.2 formula already used by rule 5), `perpbt/trend/signals.py` (`return_sign`, `ma_filter`, `donchian`, `target_weight`), `perpbt/trend/config.py` (`SignalConfig`, `MEMBERS`), `perpbt/config.py` (`_Config`, `ConfigError`, `from_dict`), `perpbt/data/store.py` (`DAY_MS`, `date_ms`), `tests/etf/synth.py` (`make_data`, `trading_days`).

## Global Constraints

- Branch `etf/1-signals` from `dev` (88e0b6b). Ask the user before the `--no-ff` merge into `dev`.
- **Never edit** any file in `CARRY_CODE_PATHS` or under `perpbt/trend/`; `tests/etf/test_guards.py` and `tests/test_holdout.py` keep passing unchanged.
- Data only through `EtfStore`; no `allow_holdout=True` outside `etf/validate.py`, `etf/holdout.py`, `etf/forward.py`. The frame loader passes the caller's flag through (default False).
- Do not re-fetch `data/etf` (manifest sha256 `308f5012…` will be pinned by the Phase 2 prereg).
- `r_on_d = (O_d + D_d)/C_{d−1} − 1`; `r_id_d = C_d/O_d − 1`; `ret_d = (1+r_on_d)(1+r_id_d) − 1`; index 0 of each is NaN.
- `cash_d = DTB3_{d−1}/100 × days(d−1 → d)/360`, DTB3 the last published rate on or before the previous trading day, used as published (negative prints included); `cash[0]` NaN.
- Members on the split-adjusted close `c` (and `h`, `l` for Donchian), `N ∈ {20, 40, 80, 160}` bars; exposure = mean of the 12; long/flat only.
- `σ̂_d = std(ret_{d−59..d}, ddof=1) × √252`; `w* = e × min(0.15/σ̂, 1.0)` through `trend.signals.target_weight`; `target_vol: null` gives `w* = e × max_weight`.
- Valid on day index `d` iff `d ≥ max(max(lookbacks), vol_window)` (index 0 = `warmup_start`) and `σ̂_d` finite; elsewhere exposure and `w*` are NaN.
- **No return of the strategy** (no weight × return, no Sharpe) is computed or printed in this phase.

## Builder's calls (my calls, on the standing instruction)

1. `EtfFrame` also carries `ticker` and `stale_open` (`o_d == c_{d−1}`, a flag only; prices are not changed) so Phases 2–3 can count the ~1% stale opens.
2. `EtfSizingConfig` mirrors the Phase 2 `sizing` block exactly: `target_vol: float | None`, `vol_window: int`, `max_weight: float`, `annualise_days: int` (must be 252).
3. The signal block reuses `trend.config.SignalConfig` (imported); `compute_signals` refuses `direction: long_short` (a cash account cannot short, ED13).
4. A frame refuses a first row other than `warmup_start`, an `end` past the manifest's `holdout_end`, and a cash day whose rate is more than 10 calendar days old (rule 7 makes this unreachable on the real store; it guards a short or synthetic one). Rates are loaded from 14 calendar days before `warmup_start`.
5. The `no_vol_target` row has the same valid days as the primary (needs finite `σ̂`), so every row starts on the same day.
6. Frame arrays are read-only (one frame is shared across many paths later).
7. `total_return` is imported from `etf/validate.py` (the formula rule 5 already checks), not rewritten.

## Review Focus

1. **The dividend lands at the ex-date open, not before.** A dividend on day `d` must appear in `r_on[d]` and `ret[d]`, never in `r_id[d]` or day `d−1`. Pinned in Task 1 by `test_dividend_in_overnight_only`.
2. **Weekend and holiday accrual uses the previous trading day's rate.** Friday→Monday is 3 days at Friday's rate; a Monday holiday makes Friday→Tuesday 4 days; a rate published after `d−1` (a Saturday print, say) is not used for day `d`. Pinned in Task 1 by `test_cash_weekend_holiday_and_ffill`.
3. **A rate published on day `d` cannot reach `cash_d`.** Pinned in Task 1 by `test_frame_causal` (rates perturbed from day `d` on leave `cash[:d+1]` unchanged).
4. **Zero vol and the null target.** Sixty constant closes give `σ̂ = 0` → `w* = e × max_weight`, finite, no warning; `target_vol: None` never calls `target_weight`. Pinned in Task 2 by `test_zero_vol_hits_cap` and `test_no_vol_target_branch`.
5. **The split inside the warmup.** A recorded split with a continuous split-adjusted close must give an ordinary return that day (not −50%). Pinned in Task 1 by `test_split_day_is_continuous`; on real data, IWM 2005-06-09 in Task 3.

---

### Task 1: `EtfFrame` and its loader (`etf/frame.py`)

**Files:**
- Create: `perpbt/etf/frame.py`, `tests/etf/test_frame.py`

**Interfaces:**
- Consumes: `EtfStore.load(ticker, start, end, *, allow_holdout)` → columns `day_ms, open, high, low, close, volume, adj_close, dividend, split, capital_gain`; `EtfStore.load_rates(start, end, *, allow_holdout)` → `day_ms, rate_pct`; `EtfStore.manifest()["holdout_end"]`; `EtfDataConfig` (`tickers`, `warmup_start`); `validate.total_return(o, c, div)`.
- Produces:
  - `MAX_RATE_AGE_DAYS = 10`, `RATE_LOOKBACK_DAYS = 14`.
  - `@dataclass(frozen=True, eq=False) class EtfFrame`: `ticker: str`, `day_ms: int64[D]`, `o, h, l, c, div, ret, r_on, r_id, cash: float64[D]`, `stale_open: bool[D]`; `__len__`; all arrays read-only.
  - `cash_returns(day_ms: np.ndarray, rates: pd.DataFrame) -> np.ndarray` (`cash[0]` NaN; `ValueError` when no rate on or before `day_ms[d−1]` or the rate is older than `MAX_RATE_AGE_DAYS`).
  - `build_frame(ticker: str, prices: pd.DataFrame, rates: pd.DataFrame, *, first_day: str) -> EtfFrame` (`ValueError` when the first row is not `first_day` or prices are not positive/finite).
  - `check_aligned(frames: Mapping[str, EtfFrame]) -> None` (`ValueError` naming the ticker and first differing date).
  - `load_frames(store: EtfStore, cfg: EtfDataConfig, *, end: str, allow_holdout: bool = False) -> dict[str, EtfFrame]` (tickers in config order; `ValueError` when `end` is after the manifest's `holdout_end`).

- [ ] **Step 1: Failing tests** in `tests/etf/test_frame.py` (small hand-built frames; `make_data` written to a tmp `EtfStore` for the loader):
  - `test_total_return_identities`: three days, `C = 100, 101, 102`, `O_2 = 101.5`, `D_2 = 0.5` → `r_on[2] == (101.5+0.5)/101 − 1`, `r_id[2] == 102/101.5 − 1`, `(1+r_on)(1+r_id) − 1 == ret` (1e-15) on every day, and `ret` equals `validate.total_return`; index 0 NaN in all three.
  - `test_dividend_in_overnight_only`: same series with and without `D_2` → `r_id` identical, `ret[1]`/`r_on[1]` identical, `r_on[2]` and `ret[2]` larger.
  - `test_split_day_is_continuous`: a row with `split = 2.0` and a close 0.4% above the day before → `ret` that day ≈ 0.004 (prices are already split-adjusted; the frame ignores the column).
  - `test_stale_open_flag`: `o_d == c_{d−1}` sets `stale_open[d]`; `stale_open[0]` False.
  - `test_cash_weekend_holiday_and_ffill`: Thu, Fri, Mon, (Mon holiday) Tue days; rates 2.0 Thu, 3.0 Fri, 9.0 Sat, 4.0 Mon → `cash[Fri] == 0.02/360`, `cash[Mon] == 0.03×3/360` (Saturday's 9.0 is after Monday's previous trading day, so unused), Tuesday after a Monday holiday `== rate_Fri × 4/360`; a rate missing on `d−1` forward-fills the last earlier one.
  - `test_negative_rate_used_as_is`: rate −0.05 → `cash` negative.
  - `test_stale_or_missing_rate_raises`: no rate on or before the first `d−1`, or last rate 11 days old → `ValueError`.
  - `test_first_row_must_be_first_day`: prices starting a day after `first_day` → `ValueError`.
  - `test_frame_arrays_read_only`: writing into `frame.c` raises `ValueError`.
  - `test_frame_causal`: for cuts `d ∈ {5, 40, 200}` and every input column (`open, high, low, close, dividend`) and the rates, perturbing values on days `> d` (prices) or calendar days `≥ day_ms[d]` (rates) leaves every frame array on `[:d+1]` equal (NaN == NaN); a frame built from rows truncated after `d` equals the full frame's `[:d+1]`.
  - `test_load_frames_aligned_and_guarded` (tmp store of `make_data()`): `load_frames(store, cfg, end="2006-12-29")` → four frames, `day_ms[0] == date_ms("2004-11-18")`, equal `day_ms`, `cash` finite from index 1; `end="2018-01-02"` without the flag → `HoldoutAccessError`.
  - `test_check_aligned_refuses_misaligned`: drop one row from one frame's inputs → `ValueError` naming that ticker.
  - `test_end_after_holdout_end_refused`: manifest `holdout_end` 2026-10-23, `end="2026-10-26"` with `allow_holdout=True` (in the test only) → `ValueError`.
- [ ] **Step 2:** `python -m pytest tests/etf/test_frame.py -q` → fails (module missing).
- [ ] **Step 3:** Implement. `cash_returns`: `searchsorted(rate_ms, day_ms[:-1], side="right") − 1` gives the last rate on or before each `d−1`; `days = diff(day_ms) / DAY_MS`.
- [ ] **Step 4:** `python -m pytest tests/etf tests/test_holdout.py -q` → pass. Commit `ETF phase 1: EtfFrame (total return, overnight/intraday split, T-bill cash) and its loader`.

### Task 2: `EtfSizingConfig` and the signals (`etf/config.py`, `etf/signals.py`)

**Files:**
- Modify: `perpbt/etf/config.py` (add `EtfSizingConfig`), `tests/etf/test_config.py`
- Create: `perpbt/etf/signals.py`, `tests/etf/test_signals.py`

**Interfaces:**
- Consumes: Task 1 `EtfFrame`, `build_frame`; `trend.signals.return_sign, ma_filter, donchian, target_weight`; `trend.config.SignalConfig`.
- Produces:
  - `@dataclass(frozen=True) class EtfSizingConfig(_Config)`: `target_vol: float | None` (> 0 when set), `vol_window: int` (≥ 2), `max_weight: float` (in (0, 1]: no leverage), `annualise_days: int` (== 252).
  - `ANNUALISE = np.sqrt(252.0)`; `vol_forecast252(ret, window) -> float64[D]`.
  - `first_valid_index(signal: SignalConfig, sizing: EtfSizingConfig) -> int` = `max(max(lookbacks), vol_window)`.
  - `member_matrix(frame: EtfFrame, signal: SignalConfig) -> tuple[tuple[str, ...], np.ndarray]` (names `f"{member}_{n}"`, shape `(K, D)`).
  - `etf_target_weight(e, sigma, sizing: EtfSizingConfig) -> np.ndarray`.
  - `@dataclass(frozen=True, eq=False) class EtfSignals`: `ticker, day_ms, names, members, exposure, sigma, weight, valid`; property `first_valid_ms: int`.
  - `compute_signals(frame: EtfFrame, signal: SignalConfig, sizing: EtfSizingConfig) -> EtfSignals` (`ValueError` on `long_short`).
  - `summarise(signals: Mapping[str, EtfSignals], start: str, end: str) -> dict[str, dict]`: per ticker `first_valid` (ISO), `days`, `exposure_hist` (counts of `k/12`, k = 0..12), `mean_exposure`, `mean_weight`; `ValueError` when any day in `[start, end]` is not valid.

- [ ] **Step 1: Failing tests.** `tests/etf/test_config.py`: `test_sizing_config_round_trip_and_null_target` (the Phase 2 block, and `target_vol: null`), `test_sizing_config_refuses` (`annualise_days 365`, `max_weight 1.5`, `target_vol 0`, `vol_window 1`, unknown key). `tests/etf/test_signals.py`:
  - `test_members_are_the_trend_objects`: `etf.signals.return_sign is trend.signals.return_sign` (and `ma_filter`, `donchian`, `target_weight`).
  - `test_target_weight_reads_only_two_fields`: the `sizing.<attr>` names in `inspect.getsource(trend.signals.target_weight)` are exactly `{"target_vol", "max_weight"}`.
  - `test_vol_forecast252_against_numpy`: on a seeded `ret` (NaN at 0), `σ̂[d] == np.std(ret[d−59:d+1], ddof=1) × √252` for d ≥ 60 (1e-12), NaN for d < 60.
  - `test_exposure_steps_and_member_names`: 12 names, exposure in `{k/12}` on valid days and not constant on a seeded walk.
  - `test_valid_from_first_valid_index`: `valid[:160]` all False, `valid[160:]` all True on a 400-day walk; exposure/weight NaN where not valid.
  - `test_weight_formula`: on valid days `weight == exposure × min(0.15/σ̂, 1.0)`.
  - `test_zero_vol_hits_cap`: 200 constant closes then a walk → where `σ̂ == 0` the weight is `e × 1.0`, finite, under `warnings.simplefilter("error")`.
  - `test_no_vol_target_branch`: `target_vol=None` → `weight == exposure × max_weight` on valid days, same `valid` as the primary.
  - `test_long_short_refused`: `direction: long_short` → `ValueError`.
  - `test_signals_causal`: frames from `build_frame` on a 400-day seeded series; for cuts `d ∈ {159, 160, 250}`, perturbing every price column and the dividend on days `> d` and the rates from `day_ms[d]` leaves `members`, `exposure`, `sigma`, `weight`, `valid` on `[:d+1]` unchanged, and truncating after `d` gives the same rows.
  - `test_summarise`: hand-made two-ticker signals → histogram counts sum to `days`, means match numpy; a start before the first valid day → `ValueError`.
- [ ] **Step 2:** `python -m pytest tests/etf/test_config.py tests/etf/test_signals.py -q` → fails.
- [ ] **Step 3:** Implement as the Interfaces say; the member loop calls the three imported functions with `long_short=False`.
- [ ] **Step 4:** `python -m pytest tests/etf tests/test_holdout.py -q` → pass. Commit `ETF phase 1: EtfSizingConfig, signals (trend members reused, sqrt-252 vol, target weights)`.

### Task 3: Real data, spec notes, full suite

**Files:**
- Create: `tests/etf/test_real_signals.py` (`pytestmark = pytest.mark.slow`; skip without `data/etf/manifest.json`)
- Modify: `docs/superpowers/specs/2026-10-24-etf-trend/phase-1-signals.md` (§1.8 implementation notes), this plan's progress log

- [ ] **Step 1:** Real tests on the store (in-sample only, `end = insample_end`, no flag): the store's manifest sha256 is still `308f5012…`; four aligned frames from 2004-11-18 to 2017-12-29; `cash` finite from index 1 and negative on the 2015 negative-print days; IWM `|ret|` on 2005-06-09 under 0.05; QQQ has no dividend on 2010-06-25; every ticker valid on 2005-07-29 (the decision day before `insample_start`) — `summarise` over `[2005-08-01, 2017-12-31]` does not raise; all 12 members finite from the first valid day; stale-open counts per ticker in-sample recorded.
- [ ] **Step 2:** `python -m pytest tests/etf/test_real_signals.py -q` → pass. Record per ticker: first valid day, exposure histogram, mean exposure, mean `w*` (in-sample), stale-open count. No returns.
- [ ] **Step 3:** Write §1.8 (data facts, numbers, builder's calls); commit `ETF phase 1: real-data checks; spec implementation notes`.
- [ ] **Step 4:** Full suite `python -m pytest -q` (slow included) → pass; `git diff dev --stat -- perpbt/trend perpbt/carry perpbt/stats` empty.
- [ ] **Step 5:** One fresh whole-branch reviewer; fix its findings; full suite again; push the branch; stop before the merge.

---

## Progress log

- Task 1: Ruling: the end-after-holdout_end test uses a synthetic store whose holdout_end is 2006-12-29, so it needs no holdout flag — cost: none.
- Task 1: Ruling: the causal test also asserts the perturbation moved something after the cut; a mutation (cash from the rate on day d instead of d−1) fails 4 tests — cost: none.
- Task 1: Ruling: added test_bad_prices_refused (non-positive prices) and test_columns_are_the_store_columns beyond the brief — cost: none.
Task 1: complete (b803652; python -m pytest tests/etf tests/test_holdout.py -q → 107 passed)
- Task 2: Ruling: the frame-input helpers moved to tests/etf/synth.py (load_rows, walk_inputs) so test_frame and test_signals share them — cost: none.
- Task 2: Ruling: test_zero_vol_hits_cap also calls etf_target_weight directly (e = 1, σ̂ = 0 → 1.0), because flat closes give exposure 0 and the cap would otherwise be checked only on zeros — cost: none.
Task 2: complete (fc280c2; python -m pytest tests/etf tests/test_holdout.py -q → 131 passed)
- Task 3: real store: 3,302 rows per ticker to 2017-12-29; first valid 2005-07-11 (row 160) for all four; six negative cash days (2015); IWM split day ret 0.98%; QQQ 2010-06-25 duplicate absent; stale opens SPY 42, QQQ 55, IWM 42, GLD 34. Numbers in spec §1.8. No strategy return computed.
Task 3: complete (11e350f; full suite python -m pytest -q → 1618 passed in 559 s; git diff dev -- perpbt/trend perpbt/carry perpbt/stats empty)
- Final review (fresh opus reviewer, whole branch): verdict "merge"; no Critical, no Important. Minors fixed: (1) summarise also requires the decision day before start to be valid (test_summarise_needs_the_decision_day_before_start); (2) build_frame copies the source arrays (to_numpy returned views; a mutation to view fails test_frame_arrays_read_only); (3) summarise bins by the member count K, not 12 (test_summarise_bins_by_the_member_count); (4) member_matrix is imported from trend.signals instead of re-implemented (identity test). Recorded in spec §1.8, not code: (5) Phase 5's ETF_CODE_PATHS must include trend/signals.py, trend/config.py, trend/daily.py; (6) "published" in §1.3 means the DTB3 observation date (call 8).
