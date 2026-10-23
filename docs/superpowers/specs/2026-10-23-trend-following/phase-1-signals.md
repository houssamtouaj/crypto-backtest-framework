# Phase 1 — Signals

Read with `00-overview.md`. Delivers the per-pair daily arrays, the 12
ensemble members, the ensemble exposure, the vol forecast, and the target
weights. Pure functions of the past; no execution.

**Branch:** `trend/1-signals`, from `dev` after Phase 0 is merged.

## 1.1 DayFrame (`trend/daily.py`)

Built once per pair from the 15m candles and funding, reusing
`indicators.daily.daily_bars`:

| Array (per calendar day `d`) | Definition |
|---|---|
| `day_ms` | 00:00 UTC of `d` |
| `o, h, l, c` | daily bar from 15m candles |
| `filled` | true if the day had no 15m candle; then `o=h=l=c=` previous close |
| `funding` | sum of funding rates of events with timestamp in `[d, d+1)` |
| `ret` | `c_d / c_{d−1} − 1 − funding_d` (same as `stats.buyhold.bh_daily`) |
| `fill_px[h]` | close of the 15m candle that closes at `d + h` for each configured fill offset `h` (15 min, 1 h, 4 h, 8 h); NaN if that candle is missing (Phase 3 §3.6) |
| `funding_events` | ragged list per day: `(ts, rate, px)` with `px` = open of the 15m candle starting at `ts` |
| `low` | daily low (for the liquidation check) |

More than 5 consecutive `filled` days in a pair raises a warning and is
reported. A pair's DayFrame ends at its last candle (delisting, Phase 3
§3.6).

## 1.2 The 12 members (`trend/signals.py`)

For lookback `N ∈ {20, 40, 80, 160}` on day `d` (all inputs on days `≤ d`):

| Member | Long/flat value | Long/short value |
|---|---|---|
| Return sign `R_N` | `1` if `c_d > c_{d−N}` else `0` | `sign(c_d − c_{d−N})` |
| MA filter `M_N` | `1` if `c_d > mean(c_{d−N+1..d})` else `0` | `sign(c_d − mean(...))` |
| Donchian `D_N` | state machine below | long state − short state |

**Donchian.** Long state turns on when `c_d > max(h_{d−N..d−1})` and off
when `c_d < min(l_{d−N/2..d−1})`; otherwise it keeps its previous value.
Initial state off. For long/short, a short state mirrors it (on when
`c_d < min(l_{d−N..d−1})`, off when `c_d > max(h_{d−N/2..d−1})`), and the
member is `long − short`. The long state always exits before the short
state can trigger (the N/2 low is above the N low), so the two are never
both on.

Ties (`=`) count as "not above": flat for long/flat, 0 for long/short.

## 1.3 Exposure

`e_d = (1/12) Σ members` — in `{0, 1/12, …, 1}` for long/flat and
`[−1, 1]` in steps of 1/12 for long/short.

## 1.4 Vol forecast and target weight

- `σ̂_d = std(ret_{d−59..d}, ddof=1) × √365`.
- `w*_d = e_d × min(0.30 / σ̂_d, 2.0)`.

Parameters (lookbacks, vol window, target, cap) come from `SignalConfig` /
`SizingConfig` in the pre-registration, not constants.

## 1.5 Warmup

A pair's signal is valid on day `d` only if `d − 160 ≥` its first day
(the longest Donchian needs 160 prior days, the return sign needs
`c_{d−160}`) and 60 returns exist. Before that, `e_d` is NaN and the pair
is not active. For perps the universe guarantees every member is warm on
2020-07-01 (each member's first day is no later than 2020-01-23 =
2020-07-01 − 160 days; Phase 0 §0.3); the spot members warm at different
dates.

## 1.6 Tests

- Each member on hand-built series (a ramp, a step, a V) against values
  worked out by hand, including ties and the Donchian state carry.
- **Perturbation:** changing any candle after the cut leaves every
  member, `e`, `σ̂`, and `w*` at days `≤` the cut's day unchanged
  (the first strategy's causality helper in `tests/synthetic.py`).
- Changing a candle inside day `d` changes nothing before day `d`.
- DayFrame: `filled` days, funding assignment at day boundaries (an event
  at exactly 00:00 belongs to the new day), the fill price per offset, and
  `ret` equal to `bh_daily`'s on data without filled days (`bh_daily`
  covers only days with candles).
- Long/short members never return `+1` and `−1` from the two Donchian
  states at once.

## 1.7 Exit criterion

All members, exposure and weights computed for every universe pair on real
data without warnings other than reported gaps; tests green. **No
performance number of any kind is computed in this phase.**
