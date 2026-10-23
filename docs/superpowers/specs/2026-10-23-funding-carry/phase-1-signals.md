# Phase 1 — Signals

Read with `00-overview.md`. Delivers the paired daily frame (both legs on
one calendar), the trailing funding statistic, and the on/off state. Pure
functions of the past; no execution.

**Branch:** `carry/1-signals`, from `dev` after Phase 0 is merged.

## 1.1 PairFrame (`carry/frames.py`)

Built from two trend `DayFrame`s (`trend.daily.load_dayframe`, perp with
funding, spot without) by `load_pairframe(data_cfg, pair, *, end,
allow_holdout=False)`, which refuses `end > CARRY_INSAMPLE_END` without
`allow_holdout` (Phase 0 §0.2). The perp leg's return is always its
**price** return `c_{d+1}/c_d − 1`, never `DayFrame.ret` (which already
subtracts funding for a long): carry code adds funding explicitly, and
using `ret` would count it twice.

| Array (per calendar day `d`) | Definition |
|---|---|
| `day_ms` | from the pair's first day (`listing`, as trend) to the **last day both legs have a candle** |
| `perp`, `spot` | the two DayFrames cut to those days |
| `basis` | `perp.c / spot.c − 1` at the day close |
| `fill_ok[h]` | both legs have the fill candle at offset `h` on day `d` |
| `last_common_ms` | open time of the last 15m slot where both legs have a candle |

Funding events after `last_common_ms` are dropped (the perp DayFrame
already drops those after its own last candle). A day where either leg is
`filled` is marked `filled` for the pair. The trend DayFrame's late-fill
gap (trend Phase 1 §1.8: no "next available candle" price) is closed here:
`fill_px_late[leg][h]` is the close of the first candle of the day at or
after `d + h`, and `fill_late_ms` its time, NaN when the day has none
after `d + h`.

## 1.2 Trailing funding

`f̄_d = mean(rate of the pair's funding events on days d−W+1 .. d) ×
(events per day) × 365`, with `W = 7` days from the pre-registration and
"events per day" = `24 / interval_h` of each event (so a pair that moved
to 4 h funding is annualised correctly; the mean is over rates normalised
to per-day first: `Σ rate_e × 24/interval_h_e / n_events`). NaN until `W`
full days of events exist.

## 1.3 On/off state

`on_d` = `True` when `f̄_d > entry` (10%), `False` when `f̄_d < exit` (0%),
otherwise `on_{d−1}`; initial `False`. Ties: `=` counts as "not above"
(stays off at exactly `entry`, stays on at exactly `exit`). NaN `f̄` →
off. The state is the decision for the fill on `d+1`.

## 1.4 Tests

- Hand-built funding series: entry and exit crossings, the hysteresis band
  (a value between exit and entry keeps the state), ties, a 4 h interval
  change mid-series, warmup NaN → off.
- **Perturbation:** changing any candle or funding event after a cut leaves
  `f̄`, `on` and `basis` at days `≤` the cut's day unchanged (the trend
  helpers in `tests/synthetic.py`).
- PairFrame: the common last day when the spot leg ends first and when the
  perp ends first; funding after `last_common_ms` dropped; `fill_px_late`.
- Real data (slow, in-sample loads only): every pair builds through
  2023-12-31 with no filled run > 5 days.

## 1.5 Exit criterion

All frames and states computed for every pair on real in-sample data;
tests green. **No return, P&L, or funding statistic beyond the signal
itself is computed or printed in this phase** (the per-pair share of days
"on" is not reported either; it would preview the screen).

## 1.6 Implementation notes (Phase 1 build, 2026-10-23)

Builder's calls taken while building this phase; none changes a trading
rule.

- **Legs built, not loaded, as DayFrames** *(my call)*. `load_pairframe`
  checks `CARRY_INSAMPLE_END` before any read, loads both legs' 15m candles
  and the perp funding through the guarded stores (the same windows as
  `trend.daily.load_dayframe`), and builds each leg with
  `trend.daily.build_dayframe`, the builder `load_dayframe` uses. The cut at
  `last_common_ms` and the late-fill prices need the 15m candles, which a
  DayFrame does not keep. A test pins that the legs equal `load_dayframe`'s
  output when no cut is needed. There is no second DayFrame type.
- **The cut** *(my call)*. Both legs' candles are cut at `last_common_ms`
  before their DayFrames are built, so on the last day both legs' o/h/l/c
  stop at the last common candle, and the perp DayFrame's own rule drops the
  funding events after it. `last_common_ms` is the last slot present in both
  legs, not `min` of the two last candles.
- **Fields added.** `funding_interval_h` (aligned with `perp.funding_ts`; the
  DayFrame keeps no interval) feeds §1.2. `fill_late_ms[leg][h]` is the fill
  time, the candle's close (open + 15 min), so it equals `day + h` when on
  time. The late fill never reaches into the next day: NaN when the day has
  no candle closing at or after `day + h`.
- **`f̄`.** NaN for the first `W − 1` days of the frame and for any window
  with no event; otherwise the mean of the per-day-normalised rates of the
  window's events. A funding event at exactly 00:00 belongs to the new day,
  so `f̄_d` uses only events before `d`'s close.
- **Config.** `carry/config.py` holds `CarrySignalConfig` (`lookback_days`,
  `entry_annual`, `exit_annual`: the prereg `signal` block; no defaults;
  `exit ≤ entry`, so `always_on`'s −1e9/−1e9 is valid). The sleeve, cost and
  margin configs are left to the phases that use them.
- **Guard test.** `tests/carry/test_frames.py` pins the refusal of
  `end > 2023-12-31` under both `configs/carry/data.yaml` and
  `configs/trend/data.yaml` before any read, and scans `perpbt/carry/` for a
  literal `allow_holdout=True` (only Phase 5's `holdout.py` and `forward.py`
  are exempt).

**Real data (in-sample, 2026-10-23).** All ten pairs build through
2023-12-31 with `last_common_ms` 2023-12-31 23:45, no run of filled pair days
longer than 5, finite basis, and `f̄` finite from day `W − 1` for W = 3, 7 and
14. Seven perps (EOS, ETC, LINK, LTC, TRX, XLM 08:00; XRP 08:15) start
mid-day on their listing day, so day 0 has no on-time 00:15 or 01:00 fill
(only a late fill). From day 1 every pair has both legs' 00:15 and 01:00
candles on every in-sample day. Every in-sample funding interval is one of
1, 2, 4 or 8 h. EOSUSDT, from the manifests only: the perp's last candle is
2025-05-21 09:00, the spot's is on 2025-05-26, so the pair's common last day
is the perp's (outside the in-sample; the cut itself is tested on synthetic
data of the same shape). No return, P&L or funding statistic and no share of
days on was computed or printed.
