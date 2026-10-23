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
