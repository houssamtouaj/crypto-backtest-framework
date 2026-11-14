# Phase 1 — Signals

Read with `00-overview.md`. Delivers the ETF day frame, the 12 members
(reused), the exposure, the vol forecast and the target weights, with
causality tests.

**Branch:** `etf/1-signals`, from `dev` after Phase 0 is merged.

## 1.1 `EtfFrame` (`etf/frame.py`)

One ticker's read-only arrays, one row per trading day from `warmup_start`
to the period end:

| Field | Meaning |
|---|---|
| `day_ms` | the date at 00:00 UTC |
| `o, h, l, c` | split-adjusted open, high, low, close (Yahoo) |
| `div` | cash dividend with its ex-date on this day (0 if none) |
| `ret` | close-to-close total return (§1.2); `ret[0]` NaN |
| `r_on` | overnight total return, previous close to this open |
| `r_id` | intraday return, this open to this close |
| `cash` | the T-bill return from the previous trading day's close to this one (§1.3) |

`h` and `l` feed the Donchian member only; nothing in this strategy marks
to an intraday low (no leverage, no liquidation).

## 1.2 Total return

With `C_{d−1}` the previous close, `O_d`, `C_d` today's open and close and
`D_d` the dividend going ex today:

- `r_on_d = (O_d + D_d) / C_{d−1} − 1` — the holder at yesterday's close
  owns the share at the ex-date's open and is owed the dividend;
- `r_id_d = C_d / O_d − 1`;
- `ret_d = (1 + r_on_d)(1 + r_id_d) − 1 = (C_d + D_d·C_d/O_d) / C_{d−1} − 1`.

Note the last form is not exactly the usual `(C_d + D_d)/C_{d−1} − 1`;
they differ by `D_d (C_d/O_d − 1) / C_{d−1}`, far below a basis point.
**Signals and σ̂ use `ret` as defined here** so the screen, executor and
signals agree *(my call)*. The dividend is credited in cash at the
ex-date's open; it is not reinvested by the frame (the executor decides).
A position bought at day `d`'s open earns only `r_id_d` that day.

## 1.3 Cash rate

`cash_d = DTB3_{d−1} / 100 × days(d−1 → d) / 360`, where `DTB3_{d−1}` is the
last published rate on or before the previous trading day and `days` is the
calendar-day count (3 over a normal weekend). Discount-basis to
money-market conversion is ignored (under 0.1% of the rate at these levels)
*(my call)*.

## 1.4 Members and exposure

Imported from `perpbt/trend/signals.py`, not copied or edited:
`return_sign`, `ma_filter`, `donchian` (with `donchian_states`), each on
the frame's arrays. They are pure functions of `c, h, l, n`.

- Members are computed on the **split-adjusted close** `c` (price trend),
  not on a total-return index. Dividends of about 0.3% a quarter barely move
  a 20–160 day trend; using price keeps the Donchian member's high/low
  consistent with its close *(my call)*.
- `exposure_d` = mean of the 12 members, in {0, 1/12, …, 1}; long/flat
  only.
- Valid from the day every lookback has `N` prior bars and σ̂ its window:
  `warmup_bars = max(160, 60) = 160` bars after `warmup_start`, the first
  valid decision about 2005-07-13. The in-sample period starts on
  2005-08-01 so that every ticker is valid on the decision day before it
  (2005-07-29); Phase 2 checks this and refuses otherwise.

## 1.5 Vol forecast and target weight

- `σ̂_d = std(ret_{d−59..d}, ddof=1) × √252` — the ETF module's own
  `vol_forecast252`, because `trend/signals.vol_forecast` hard-codes √365.
- `w*_d = exposure_d × min(0.15 / σ̂_d, 1.0)` via
  `trend/signals.target_weight` with an `EtfSizingConfig` that carries
  `target_vol = 0.15`, `max_weight = 1.0`. `target_weight` reads only those
  two fields (checked), so no `trend.config.SizingConfig` is built.
- The `no_vol_target` row (`target_vol: null`) cannot go through
  `target_weight` (it would divide `None`); the ETF wrapper branches:
  `w* = exposure × max_weight`.

## 1.6 Tests

- `EtfFrame`: total-return identities on a synthetic series with a
  dividend; split continuity; refusal of misaligned dates across tickers.
- Cash: weekend accrual (3 days), holiday gap, rate forward-fill.
- **Causality (perturbation):** changing any input on day `d+1` or later
  leaves members, exposure, σ̂ and `w*` on days ≤ `d` unchanged, for every
  field of the frame.
- √252: `vol_forecast252` against a direct numpy computation.
- Members reused: a test asserts the imported functions are the objects in
  `perpbt.trend.signals` (no copy).

## 1.7 Exit criterion

Tests green; on the real store, the first valid day per ticker, exposure
histogram, mean exposure and mean `w*` per ticker over the in-sample period
recorded in this file's notes. **No return of the strategy is computed in
this phase.**

## 1.8 Implementation notes (2026-10-24, branch `etf/1-signals`)

**Code.** `perpbt/etf/frame.py` (`EtfFrame`, `cash_returns`, `build_frame`, `check_aligned`, `load_frames`),
`perpbt/etf/signals.py` (`vol_forecast252`, `first_valid_index`, `member_matrix`, `etf_target_weight`,
`compute_signals`, `summarise`), `EtfSizingConfig` in `perpbt/etf/config.py`. Nothing under `perpbt/trend/` or in
`CARRY_CODE_PATHS` was edited; the carry code version is still `7ce43816…`. Plan and log:
`docs/superpowers/plans/2026-10-24-etf-phase-1-signals.md`.

**Builder's calls** (on the standing instruction to take the recommended option; the user can overturn any):

1. `EtfFrame` also carries `ticker` and a `stale_open` flag (`O_d = C_{d−1}`). Prices are not changed; the flag lets
   Phases 2–3 count the stale opens.
2. `EtfSizingConfig` is the Phase 2 `sizing` block one to one: `target_vol` (float or null), `vol_window`,
   `max_weight` (in (0, 1]: no leverage), `annualise_days` (must be 252).
3. The `signal` block reuses `trend.config.SignalConfig` (imported). `compute_signals` refuses `long_short`.
   `trend.signals.member_matrix` is imported too: it reads only `c`, `h` and `l`, so it runs on an `EtfFrame`.
4. A frame refuses a first row other than `warmup_start`, an `end` after the store's `holdout_end`, and a cash day
   whose rate is more than 10 calendar days old. Rule 7 makes the last unreachable on this store; it guards a
   short or synthetic one. Rates are loaded from 14 calendar days before `warmup_start`.
5. `no_vol_target` has the same valid days as the primary (it needs a finite σ̂ too), so every row starts on the
   same day.
6. Frame arrays are read-only copies; a later change to the source rows does not reach them. `ret` is
   `validate.total_return`, the formula rule 5 already checks.
7. `load_frames` passes `allow_holdout` through to the store (default False), as the carry frame loader does.
8. **"Published on or before `d−1`" means the DTB3 observation date.** FRED releases the rate dated `d−1` on day
   `d`. That is not lookahead: no decision reads `cash`. It is the interest accrued over `d−1 → d`.

**Real store, in-sample** (`tests/etf/test_real_signals.py`, slow; `end = 2017-12-31`, no holdout flag;
manifest sha256 still `308f5012…`):

- Four aligned frames, 3,302 rows each, 2004-11-18..2017-12-29. `ret`, `r_on`, `r_id` and `cash` are finite from
  row 1. The largest |`ret`| is 14.5% (SPY, 2008-10-13).
- **First valid day: 2005-07-11 for all four** (row 160; the spec's "about 2005-07-13" was an estimate). Every
  ticker is valid on 2005-07-29, the decision day before `insample_start`. All 12 members are finite from then on.
- **Cash:** negative on six days, all from the 2015 DTB3 prints: 2015-09-21, 09-23, 09-28, 10-01, 10-02, 10-09.
  The annualised rate stays within −0.1%..6%.
- **IWM split** (2005-06-09): `ret` that day 0.98%, continuous.
- **QQQ corrections:** the 2010-06-18 dividend (0.089) is there; the 2010-06-25 duplicate is gone. Dividend days:
  53 each for SPY, QQQ and IWM, none for GLD. The 2020-09-21 QQQ fix is in the holdout and not loaded here.
- **Stale opens** over 2004-11-18..2017-12-29: SPY 42, QQQ 55, IWM 42, GLD 34. On 14 of them the stale open is
  also the day's high or low (SPY 4, QQQ 1, IWM 5, GLD 4), so that extreme may be stale too. Only the Donchian
  member reads `h` and `l`, and nothing was changed.

**Signals over the in-sample period** (2005-08-01..2017-12-31, 3,127 days; primary sizing 0.15 / 60 / 1.0):

| Ticker | Mean exposure | Mean `w*` | Mean σ̂ | Days with σ̂ ≤ 15% (`w* = e`) | Exposure histogram, k/12 for k = 0..12 |
|---|---|---|---|---|---|
| SPY | 0.649 | 0.611 | 0.160 | 64.8% | 246, 159, 122, 130, 132, 136, 165, 182, 195, 216, 230, 296, 918 |
| QQQ | 0.672 | 0.610 | 0.181 | 47.2% | 206, 159, 100, 110, 123, 143, 141, 184, 173, 259, 291, 298, 940 |
| IWM | 0.612 | 0.518 | 0.214 | 28.1% | 279, 178, 165, 118, 115, 139, 179, 238, 189, 247, 286, 313, 681 |
| GLD | 0.540 | 0.455 | 0.181 | 40.4% | 350, 230, 179, 165, 223, 186, 189, 206, 147, 218, 260, 239, 535 |

With `no_vol_target`, mean `w*` equals mean exposure. No return of the strategy was computed.

**For later phases:**

- Phase 2: build the prereg's `signal` block as a `trend.config.SignalConfig` and its `sizing` block as an
  `EtfSizingConfig`. The screen reads `EtfSignals.weight` and the frame's `ret` and `cash`. The insample check is
  `summarise(…, insample_start, insample_end)`. It raises on any invalid day in the window, and when the
  decision day before `insample_start` is missing or invalid (day `d+1`'s return uses `w*_d`).
- Phase 5: `ETF_CODE_PATHS` must include `perpbt/trend/signals.py` and `perpbt/trend/config.py` (and what they
  import, `perpbt/trend/daily.py`), or the ETF code lock would not cover the member code.
- Phase 3: a fill at day `d`'s open earns `r_id[d]` that day. The dividend is in `r_on[d]`, owed to the holder at
  `d−1`'s close. `stale_open` marks the fills that carry Yahoo's stale opens.
