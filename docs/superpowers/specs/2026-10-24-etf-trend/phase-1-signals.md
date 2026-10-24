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
