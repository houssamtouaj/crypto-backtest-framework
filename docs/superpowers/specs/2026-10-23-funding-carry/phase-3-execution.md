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
