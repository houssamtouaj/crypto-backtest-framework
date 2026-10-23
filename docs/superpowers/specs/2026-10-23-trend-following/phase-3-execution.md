# Phase 3 — Execution

Read with `00-overview.md`. Built only if the Phase 2 screen said go.
Delivers the daily target-weight executor in two forms: a plain reference
loop over one path, and a batched form over many paths (for T1 and
re-pricing), proven equal. The first strategy's 15m bracket simulator is
not used or changed.

**Branch:** `trend/3-execution`, from `dev` after Phase 2 is merged.

## 3.1 Inputs and outputs

```python
def run_path(frames: dict[str, DayFrame], exposure: dict[str, np.ndarray], cfg: ExecSpec) -> PathResult
def run_batch(frames, exposure_batch: dict[str, np.ndarray],  # shape (paths, days) per pair
              cfg: ExecSpec) -> BatchResult                   # daily portfolio returns (paths, days) + counters
```

`ExecSpec` carries sizing, rebalance, costs, and the period. Exposure is an
input (Phase 1 or a shifted path), so the executor never computes signals.

`PathResult` tables: `daily` (date, equity, ret, gross_ret, fees, slippage,
funding, gross_exposure, n_active), `daily_pair` (date, pair, weight held,
target, pnl, funding), `fills` (date, pair, qty, price, reason, fee,
slippage), `events` (liquidation, delisting, late fill, band skips).

## 3.2 One account

One cross-margin account, starting equity 10,000 (only the scale; results
are weights). On each day, `N_active` is the number of pairs whose signal
is valid and that are not delisted. The target notional of pair `i` is
`w*_i × E / N_active`, with `E` the equity at the decision (day `d`
close). Gross notional is capped at `max_gross × E` by scaling all targets
down proportionally. Per-pair runs (secondary results) use the same code
with a one-pair universe.

## 3.3 Day loop (for day `d+1`, decisions from day `d`)

1. **Funding** events of day `d+1` before the fill time are charged on the
   quantity held then; events after it on the new quantity.
   `amount = rate × qty × px` (longs pay positive rates).
2. **Trade decision** per pair, from the decision at day `d`'s close:
   trade if the exposure differs from the exposure at the pair's last trade (`signal`), or the target is 0 and the
   pair holds a position (`exit`), or
   `|w_held − w*| > drift_band × |w*|` (`drift`). Otherwise `band_skip`.
   `w_held = qty × c_d / (E / N_active)`.
3. **Fill** at `fill_px[offset]` of day `d+1` (default the 00:15 close).
   New quantity `= target notional / fill price`. Cost on the traded
   notional `|Δq| × px`: `fee_taker` plus the pair's `slippage`, booked as
   cash costs (the fill price itself is not moved, so costs are linear in
   the rates).
4. **Mark** at `c_{d+1}`; daily return `= E_{d+1} / E_d − 1`.
5. **Liquidation check** (conservative): with every held pair at its day
   low simultaneously, if equity `≤ Σ mmr_i × notional_i`, the account is
   liquidated at those prices: close all, record the event, the path ends
   with its remaining equity, later days have return 0 and the run is
   flagged. Not expected for the portfolio at ≤ 2× gross; possible for a
   per-pair run on a −50% day.

Weekly variant: steps 2–3 happen only on the configured weekday; funding
and marks every day.

## 3.4 Buy-and-hold benchmark

The same executor with `e ≡ 1` for every active pair from its first valid
day: identical vol targeting, cap, drift band, costs, and funding. This is
the T2 benchmark. The first strategy's ex-post scaled buy-and-hold
(`stats/buyhold.py`) is shown in the report as a reference line only.

## 3.5 Gross mode

`ExecSpec(gross=True)` zeroes fees, slippage, and funding. Used for the
gross columns of the report (not for the screen, which has its own model).

## 3.6 Gaps and delistings

- **Missing fill candle.** Fill at the close of the next available 15m
  candle of day `d+1`; count `late_fill`. If day `d+1` has none, the
  trade waits for the next day's fill time (the decision is re-evaluated
  then).
- **Delisting.** A pair is delisted if its data ends more than 7 days
  before the period end. Its position is closed at the close of its last
  15m candle with fee and slippage; it leaves `N_active` from the next
  decision. Remaining targets are re-spread over the active pairs by the
  normal rule (no forced trade beyond the band).
- **Filled days** (no candle all day) carry the position; no fill.

## 3.7 Batched form

`run_batch` runs the same rules over `P` paths at once, vectorised along
paths and pairs, looping over days. It must equal `run_path` path by path.

## 3.8 Tests

- Hand-checked days: a signal change, an exit, a drift trade, a band skip,
  funding before and after the fill, a late fill, a delisting.
- Equivalence: `run_batch` on 50 random exposure paths equals `run_path`
  on each to 1e-12 (daily returns, costs, counters).
- Gross mode with costs zeroed equals a hand-computed weight × return
  path when the band is 0 and fills are at the close (sanity link to the
  screen model).
- Liquidation: a synthetic −60% day at 2× triggers it; a −40% day does
  not.
- Exposure 0 throughout: zero trades, zero returns, zero funding.
- Byte-identical outputs on rerun.

## 3.9 Exit criterion

Tests green; one real in-sample path runs in under a second; a batch of
5,000 paths in under a minute. **No performance number is reported in
this phase**; real-data runs are only for timing and sanity checks
(no NaNs, counters plausible).
