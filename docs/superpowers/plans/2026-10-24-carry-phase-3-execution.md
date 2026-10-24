# Carry Phase 3 — Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `perpbt/carry/executor.py`: the two-leg daily executor. `run_path` is a plain reference loop over one on/off path; `run_batch` runs the same rules over `P` paths, vectorised over paths and pairs. The two are proven equal to 1e-12. Also the margin, liquidation, rebalance, late-fill, delisting and funding rules of spec §3.2–§3.4.

**Architecture:** `ExecSpec` holds the frozen prereg's `sleeve`, `margin`, `costs` and `rebalance` blocks plus the return period (`exec_spec(prereg, ...)` builds it, with no new constants except the spec's starting equity of 10,000). The money formulas are small kernels (entry, close, rebalance quantity and flows, liquidation price, rebalance trigger) that work on floats and on arrays alike. `run_path` reads the PairFrames row by row with its own control flow. `run_batch` aligns every pair on the return-day grid once (padded funding events), then loops over days with `(paths, pairs)` masks. Sums over pairs and over funding events go in the same order in both, so the results agree bit for bit in practice.

**Tech Stack:** Python 3.11, numpy, pandas, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-23-funding-carry/phase-3-execution.md` with `00-overview.md` (CD3, CD6, CD7, §5, §7), Phase 1 §1.6 (Phase 3 pointers), Phase 2 §2.6, and trend Phase 3 §3.6 (the late-fill rule).

## Global Constraints

- Branch `carry/3-execution` from dev 44b318f.
- Data only through `carry.frames.load_pairframe` (in-sample, never `allow_holdout`). The AST guards in `tests/carry/test_frames.py` stay green and are not widened: only `frames.py`/`basis.py` touch the stores; no `.ret`; no `allow_holdout` other than False or pass-through; no `**` keyword splat in a call.
- The executor never computes signals: it takes the on/off arrays per pair (PairFrame rows), from `compute_signals` or a shifted path.
- No performance number (spec §3.7): real-data tests check structure, runtime, causality and invariants only. Never compute, assert or print a return, Sharpe, P&L or funding total.
- `tests/carry/test_real_screen.py` stays green (screen.json byte for byte). The screen, `configs/carry/prereg.yaml`, its lock and `runs/carry/` are unchanged.
- Shared modules change only backward compatibly; the first strategy's and trend's suites stay green.
- Do not run any first-strategy or trend run command, nor `perpbt carry screen` or `perpbt carry prereg freeze`. Long jobs in the foreground, one at a time.

## My calls (labelled; listed in the hand-off)

1. **Delisting** = the pair's frame ends on a day before the period's last return day. A frame that ends on the last return day is the window's end, even mid-day: it is marked, with no exit cost, as in the screen. In-sample, every pair ends 2023-12-31 23:45, so nothing is delisted. Trend's "more than 7 days" threshold is not used: a PairFrame has no rows after its last day, so an earlier end cannot be held.
2. **Funding at a fill:** an event strictly before the perp leg's fill time is charged on the old quantity, and one at or after it on the new quantity. The fill time is the candle's close, `open + 15 min`; the exchange closes the candle 1 ms earlier, so a fill "at" 08:00 comes before the 08:00 event. Only the perp leg's fill time matters, because funding is paid on the perp. A late perp fill after the 08:00 event therefore leaves that event on the old quantity.
3. **`funding_px` NaN when a quantity is held:** the event is priced at the close of the last perp candle before it (new PairFrame field `funding_px_prev`; backward compatible). Each use is counted as an event. There is none in-sample. An event with no quantity held is never priced, so XLM's pre-listing event is never charged.
4. **One settlement per day; the entry spends the target, costs included** (changed while writing the tests: with costs on top, every entry would be capped whenever all pairs enter at once). The day's trades settle together at the fill, in this order:
   1. exits and post-liquidation spot sales;
   2. rebalances;
   3. entries.

   When free cash cannot cover every entry at `E / N_live` plus its costs, the entries share the cash pro rata, so the pair order does not matter. Each such entry counts as `capped_entry`. Entry costs come out of the free cash, so cash never goes negative. A rounding residue below 0 after a capped day is set to 0. An entry capped to 0 does not trade and is tried again the next day.
5. **Costs** are fees and slippage on traded notional at the raw fill price, booked as cash (not a price adjustment). Rebalance costs are paid from the freed spot cash, inside the sleeve. The liquidation fee goes in `fees`.
6. **Rebalance:** at the fill, the trade size `δ` is solved so that the perp wallet equity after the trade and its costs equals `q'·Pp/L`. There is no trade when:
   - `δ ≤ 0` (the wallet recovered before the fill), or
   - the perp fill is at or above the liquidation price (that day's high check then liquidates).

   The trigger is re-evaluated at every close.
7. **Liquidation:**
   - **Price:** where equity equals maintenance, `(W + q·P0) / (q(1 + mmr))`. The spec's "that price" is read as CD7's liquidation price, and it is always ≤ the day high.
   - **Fee:** charged in full even when it exceeds the remaining wallet. With mmr < 1% the wallet goes slightly negative and is settled by the spot sale. Binance caps an isolated loss at the margin, so this is the conservative reading.
   - **Timing:** the day's funding is booked as if the position were held all day, because the time of the high is unknown and the liquidation is placed at the day's end.
   - **Entry day:** the whole day's high is checked, which is conservative.
   - **Block:** the pair stays blocked until a decision is off.
8. **The spot sale after a liquidation** needs only the spot leg's fill and has reason `liquidation`. It is postponed like any other trade.
9. **A postponed trade** (either needed leg has no fill candle that day) is re-evaluated the next day from that day's decision.
10. **Tables:** `daily_pair.state` is the input decision applied that day; `perp_qty` is signed (short < 0). `n_on` is the number of hedged sleeves at the close. `daily` also has `cash`. `events` has `date, pair, kind, leg, detail, value`.
11. **Equivalence tolerance:** money columns within 1e-12 × starting equity (spec §7: returns are fractions of equity; the starting equity is only a scale), `ret` within 1e-12, counts exactly equal.
12. **Shared kernels:** `run_path` and `run_batch` share the money formulas. Their control flow, alignment and funding gathering are separate code. The hand-checked tests pin the formulas.
13. **Start:** as in the screen, every pair starts flat, and a pair is live on decision day `d` when its frame has `d` and `d+1`. An entry on a delisting day is allowed, because it is decided the day before.

## Review Focus

1. Funding never charged before a position exists; split at the perp fill time. Tests: `test_funding_before_and_after_fill`, `test_pre_listing_event_not_charged`, `test_late_perp_fill_after_8h_event`.
2. Cash never negative; entries capped pro rata. `test_capped_entries_pro_rata`, invariant test on random paths.
3. Liquidation at +60% high (L = 2), none at +30%; rebalance restores the wallet. `test_liquidation_60pct`, `test_no_liquidation_30pct_rebalances`.
4. Equity identity `ΔE = funding − fees − slippage + basis_pnl` on random paths; delta neutrality.
5. `run_batch == run_path` on 50 random paths (synthetic with gaps, late fills, a delisting) and a few real paths.
6. The in-sample end is not a delisting; a frame ending earlier is.

---

### Task 1: `funding_px_prev` on the PairFrame

**Files:** Modify `perpbt/carry/frames.py`; Test `tests/carry/test_frames.py`.

- [ ] Step 1: Test `test_funding_px_prev`: the close of the last perp candle opening before each event; NaN for an event before the first candle (XLM shape); equals the previous candle's close when the event candle is missing.
- [ ] Step 2: Run → fail. Step 3: implement (field last; docstring). Step 4: `tests/carry` green. Commit.

### Task 2: `ExecSpec`, kernels, `run_path`

**Files:** Create `perpbt/carry/executor.py`; Test `tests/carry/test_executor.py` (helper `tests/carry/execframes.py` building PairFrames from per-day prices).

**Produces:** `START_EQUITY`, `ExecSpec(sleeve, margin, costs, rebalance, start, end, equity0)`, `exec_spec(prereg, *, sleeve=None, start=None, end=None)`, `PathResult(daily, daily_pair, fills, events, counts)`, `run_path(frames, state, cfg)`, `COUNTS`.

- [ ] Step 1: Hand-checked tests: entry (quantities, wallet, costs, cash); exit; funding before/after the fill (entry and exit days); a negative funding event; a rebalance (+30% close: wallet back at target, fees); a liquidation at +60% high, none at +30% high; a delisting (closed at the last close with costs; leaves `N_live` next decision); the in-sample-style end is not a delisting; a late fill (per leg; the late perp fill after the 08:00 event); postponement (no fill candle on a leg); pre-listing funding event not charged; `funding_px` fallback; capped entries pro rata; always off → zero trades and zero returns; delta neutrality; equity identity; byte-identical rerun; causality (perturb data or state after `t` → rows ≤ `t` unchanged).
- [ ] Step 2: Run → fail. Step 3: implement. Step 4: green. Commit.

### Task 3: `run_batch`

**Files:** Modify `perpbt/carry/executor.py`; Test `tests/carry/test_executor_batch.py`.

**Produces:** `BatchResult(day_ms, n_live, equity, ret, cash, funding, fees, slippage, basis_pnl, n_on, counts)`, `run_batch(frames, state_batch, cfg)`.

- [ ] Step 1: Tests: 50 random paths on synthetic frames (three pairs: a late-listed pair, a delisted pair, gaps that force late fills and postponements, mixed funding signs, big moves that rebalance and liquidate) equal `run_path` path by path; one-path batch equals the path; shape and dtype refusals.
- [ ] Step 2: Run → fail. Step 3: implement. Step 4: green. Commit.

### Task 4: Real-data checks (slow)

**Files:** Test `tests/carry/test_real_executor.py`.

- [ ] Step 1: Ten in-sample PairFrames through `load_pairframe`; primary path from `compute_signals`:
  - structure: 1,279 daily rows, `n_live` 10, columns, no delisting, cash ≥ 0, identity;
  - `run_path` under 1 s;
  - 1,000 random paths through `run_batch` under 60 s;
  - `run_batch` equals `run_path` on 5 random paths and the primary;
  - causality on a state perturbation;
  - always off gives zero trades.

  Nothing printed; no return asserted.
- [ ] Step 2: Run slow; record timings in the log. Commit.

### Task 5: Notes, suite, review, merge stop

- [ ] Step 1: Spec §3.8 implementation notes (the calls above, real-data facts); progress log here.
- [ ] Step 2: Full suite `python -m pytest -q` (slow included) → green. Commit.
- [ ] Step 3: Fresh whole-branch review (most capable model); fix; suite green.
- [ ] Step 4: **STOP** before `git merge --no-ff carry/3-execution` into `dev`.

## Progress log

Spec: docs/superpowers/specs/2026-10-23-funding-carry/phase-3-execution.md
- Real-data structure (2020-06-30..2023-12-31, before any code): no NaN `funding_px`, 3 events every day, no late or missing 00:15 fill on either leg, no filled day, for all ten pairs. The fallback, late-fill and postponement rules are exercised on synthetic data only.
- Ruling (my call, during Task 2): an entry's outlay, costs included, is min(target, free-cash share); the sleeve amount is net of costs. Cost if wrong: sleeves start one round of costs (~0.2%) below the target instead of at it.
Task 1: complete (6889e27; tests/carry 137 passed).
Task 2: complete (39936b7; test_executor 26 passed). Mutations: pre-fill funding on the new quantity → 3 failures; liquidation at the high → 1; no delisting → 2; delisting at the period end → 7; late fills ignored → 3; no block after liquidation → 1; funding sign → 4; no price fallback → 1; never rebalance → 3.
Task 3: complete (ef2a085; test_executor_batch 4 passed; bit for bit on 10 paths).
Task 4: complete (4e23567; test_real_executor 6 passed: one path ≈ 0.17 s, 1,000 paths ≈ 11.4 s; no performance number computed or printed).
