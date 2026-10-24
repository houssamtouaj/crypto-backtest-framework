# Phase 3 — Execution

Read with `00-overview.md`. Built only if the Phase 2 screen said go.
Delivers the two-leg daily executor: a plain reference loop over one path
and a batched form over many on/off paths (for the random-timing
diagnostic and re-pricing), proven equal.

**Branch:** `carry/3-execution`, from `dev` after Phase 2 is merged.

## 3.1 Inputs and outputs

```python
def run_path(frames: dict[str, PairFrame], state: dict[str, np.ndarray], cfg: ExecSpec) -> PathResult
def run_batch(frames, state_batch: dict[str, np.ndarray], cfg: ExecSpec) -> BatchResult   # (paths, days)
```

`state` is the on/off series (Phase 1, or a shifted path); the executor
never computes signals. `PathResult` tables: `daily` (date, equity, ret,
funding, fees, slippage, basis_pnl, n_on, n_live), `daily_pair` (date,
pair, state, spot_qty, perp_qty, perp_wallet, sleeve_value, funding),
`fills` (date, pair, leg, qty, price, reason, fee, slippage), `events`
(rebalance, liquidation, delisting, late fill).

## 3.2 The account

Starting equity 10,000 (scale only). On each decision day the sleeve
target is `E / N_live` with `E` the equity at day `d`'s close. An entry
takes `min(E / N_live, free cash)`, where free cash is the equity not held
in on-sleeves. Cash never goes negative (that would be free borrowing),
and an entry cut short is counted as `capped_entry`. On-sleeves are not
resized to make room. *(my call, after the builder check)* Each "on"
sleeve holds spot quantity `q` and perp quantity `−q`, with spot notional
`q × spot_px = x × S` and perp wallet `x × S / L` at entry. The spot
wallet and perp wallet of a sleeve are tracked separately (no
cross-collateral). Sleeves are not resized while on except by the
rebalance rule (§3.3 step 4), so a winning sleeve is not trimmed daily
(turnover stays low). *(my call)*

## 3.3 Day loop (day `d+1`, decisions from day `d`)

1. **Funding** events of day `d+1` are credited to (or debited from) the
   perp wallet on the quantity held at the event: `amount = rate × q ×
   px_event` for the short (receives positive rates). Before the fill time
   on the old quantity, after it on the new one.
2. **Decision**: `off → on`: enter both legs; `on → off`: exit both legs;
   otherwise hold.
3. **Fill** both legs at `fill_px[offset]` of day `d+1` (late-fill rule
   as trend Phase 3 §3.6, using `fill_px_late`). Fees and slippage per leg
   on traded notional, booked as cash.
4. **Margin.** Mark the perp at the day high: if the perp wallet equity
   (`wallet + q × (entry_px − high)`) falls to the maintenance margin
   (`mmr × q × high`), the short is liquidated at that price with the
   liquidation fee, the spot leg is sold at the next fill, and the sleeve
   is off until the signal turns on again from off. Otherwise, at the day
   close, if the perp wallet equity is below `rebalance_below` of the
   margin needed at leverage `L` on the current notional, the sleeve is
   rebalanced at the next fill: both legs reduced equally and the freed
   spot cash moved to the perp wallet until the wallet is back at target.
5. **Mark** both legs at `c_{d+1}`; daily return `= E_{d+1} / E_d − 1`.

## 3.4 Delistings and gaps

A sleeve is closed at the last common candle of its two legs (PairFrame
`last_common_ms`) with costs; the pair leaves `N_live` from the next
decision. A day where either leg has no fill candle at all postpones the
trade to the next day's fill (counted).

## 3.5 Batched form

`run_batch` runs the same rules over `P` on/off paths at once, looping
over days and vectorised over paths and pairs. It must equal `run_path`
path by path to 1e-12.

## 3.6 Tests

- Hand-checked days: an entry, an exit, funding before and after the fill,
  a negative funding event, a rebalance, a liquidation on a +60% day high
  at L = 2 (none at +30%), a delisting, a late fill.
- Delta neutrality: with both legs at the same price path and zero funding
  and costs, the sleeve value is constant.
- Equivalence of `run_batch` and `run_path` on 50 random paths.
- Always off: zero trades, zero returns. Byte-identical outputs on rerun.

## 3.7 Exit criterion

Tests green; one real in-sample path in under a second; 1,000 paths in
under a minute. **No performance number is reported in this phase.**

## 3.8 Implementation notes (Phase 3 build, 2026-10-24)

Builder's calls taken while building this phase. None was tuned on a
return: no return, P&L or funding total was computed on real data. They
are in `perpbt/carry/executor.py`'s docstring.

- **Inputs.** `ExecSpec` holds the frozen prereg's `sleeve`, `margin`,
  `costs` and `rebalance` blocks, the return days and `equity0`
  (`START_EQUITY` 10,000, §3.2). `exec_spec(prereg, sleeve=, start=, end=)`
  builds it; the in-sample period is the default. `state[pair]` is bool per
  PairFrame row (the decision at that row's close); `run_batch` takes
  `(P, rows)`.
- **Liveness and start** *(my call, as the screen)*. Every pair starts flat.
  A pair is live on decision day `d` when its frame has `d` and `d+1`;
  `N_live` counts those pairs. An entry on a delisting day is allowed,
  because it is decided the day before.
- **Delisting** *(my call)*. A frame that ends on a day before the period's
  last return day is a delisting: the sleeve closes at the last common
  candle's close with costs, and the pair leaves `N_live` from the next
  decision. A frame that ends on the last return day is the window's end,
  even a partial day: it is marked, with no exit cost. The in-sample end
  (`last_common_ms` 2023-12-31 23:45) is therefore never a delisting. Trend's
  "more than 7 days" threshold is not used, because a PairFrame has no rows
  after its last day and an earlier end cannot be held.
- **Funding quantity at a fill** *(my call)*. An event strictly before the
  perp leg's fill time is charged on the old quantity, and one at or after
  it on the new quantity. The fill time is the candle close (`open + 15 min`;
  the exchange's close time is 1 ms earlier, so a fill at 08:00 precedes the
  08:00 event). Only the perp leg's time matters. A late perp fill after the
  08:00 event leaves that event on the old quantity. An event with no
  quantity held is never priced, so XLM's pre-listing 00:00 event cannot be
  charged.
- **Missing `funding_px`** *(my call)*. The event is priced at the close of
  the last perp candle before it, from a new PairFrame field
  `funding_px_prev` (backward compatible; the screen does not read it). Each
  use is counted as `funding_px_fallback`.
- **Entry size** *(my call)*. An entry spends the target `E_d / N_live`,
  costs included. The sleeve amount `A` satisfies `A` + both legs' costs =
  the spend; spot notional `x·A`, perp wallet `x·A / L`. §3.2's
  `min(E / N_live, free cash)` is the spend. Reading the costs as additional
  would cap nearly every entry whenever all pairs enter together.
- **One settlement per day** *(my call)*. The day's trades settle at the fill
  in this order:
  1. exits and post-liquidation spot sales;
  2. rebalances;
  3. entries.

  When free cash cannot cover every entry at the target, the entries share
  it pro rata, so the order of the pairs does not matter, and each counts as
  `capped_entry`. An entry capped to 0 does not trade and is tried again the
  next day. After the entries, a rounding residue below 0 is set to 0, so
  cash never goes negative.
- **Costs** are fees and slippage on traded notional at the raw fill price,
  booked as cash. Rebalance costs are paid out of the freed spot cash, inside
  the sleeve. The liquidation fee is reported under `fees`.
- **Rebalance** *(my call)*. At the fill, `δ` is solved so that the perp
  wallet equity after the trade and its costs equals `q'·Pp / L`. There is no
  trade when:
  - `δ ≤ 0` (the wallet recovered before the fill);
  - the perp fill is at or above the liquidation price (that day's high check
    then liquidates).

  The trigger is re-evaluated at every close.
- **Liquidation** *(my call)*.
  - **Price:** CD7's liquidation price `(W + q·P0) / (q(1 + mmr))`, which is
    ≤ the day high. That is the reading of §3.3's "that price".
  - **Fee:** charged in full, even when it exceeds the remaining wallet (with
    mmr < 1% the wallet ends slightly negative and is settled by the spot
    sale). This is conservative: Binance caps an isolated loss at the margin.
  - **Funding that day:** booked as if the position were held all day, because
    the time of the high is unknown.
  - **Entry day:** the whole day's high is used.
  - **Spot sale:** at the next fill, which needs only the spot leg; reason
    `liquidation`.
  - **Re-entry:** the pair stays out until a decision is off.
- **Postponement.** A trade whose needed legs lack a fill candle that day is
  postponed (counted) and re-decided the next day from that day's decision.
- **Tables.**
  - `daily` adds `day_ms` and `cash` to §3.1's columns.
  - `daily_pair.state` is the input decision applied that day, and
    `perp_qty` is signed (short < 0).
  - `n_on` is the number of hedged sleeves at the close.
  - `events` columns are `date, pair, kind, leg, detail, value`. Kinds:
    `postponed`, `late_fill`, `funding_px_fallback`, `rebalance`,
    `liquidation`, `capped_entry`, `delisting`.
  - `basis_pnl` is the legs' price P&L, so `ΔE = funding − fees − slippage +
    basis_pnl` (tested).
- **Two implementations, shared formulas** *(my call)*. `run_path` (plain
  loop, reads the PairFrames row by row) and `run_batch` (aligned arrays,
  `(paths, pairs)` masks) share only the money kernels. Sums over pairs and
  funding events run in the same order in both. On 50 random synthetic paths
  that exercise every rule they agree to 1e-12 of the starting equity, and
  in practice bit for bit.

**Real data (in-sample, 2026-10-24; structure only).** From 2020-06-30 all
ten pairs have:
- no NaN `funding_px`;
- three 8 h events every day;
- no late or missing 00:15 fill on either leg;
- no filled day.

So the late-fill, postponement and fallback rules are exercised only on
synthetic data in-sample. With the primary states there are 1,279 return
days and `n_live` is 10 throughout, with no delisting, cash ≥ 0, and the
equity identity holding to 1e-9 of the starting equity. One path runs in
about 0.17 s and 1,000 random paths through `run_batch` in about 11 s.
`run_batch` equals `run_path` on the primary and five random real paths.
