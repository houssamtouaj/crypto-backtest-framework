# Phase 3 — Strategy

Read with `00-overview.md`. Delivers the strategy plug-in interface and the
ICT order-block rule as a pure function from `MarketView` to intents, with
every skip reason counted. Depends on Phases 1 and 2. Execution of intents
is Phase 4.

This section is the contract for the rule. If anything here differs from
the intended strategy, that is an ambiguity to raise before Phase 4.

Notation: 15m candles indexed `i`, open time `τ_i`, close time `τ_i + 15m`.
Decision time for candle `i` is its close. "As of `i`" means using candles
`≤ i` only. `t` is the current (impulse) candle, `s` a swing-high candle,
`c` the order-block candidate, `d` the displacement candle.

## 3.1 Plug-in interface (`strategy/base.py`)

```python
@dataclass(frozen=True)
class PlaceBracketLimit:
    side: str                    # "long" (short is deferred but carried)
    price: float                 # entry limit
    stop: float
    target: float
    expires_ms: int              # cancel if unfilled by this instant (window end)
    hold_rule: HoldRule          # simulator derives the deadline at fill
    tag: dict                    # audit metadata, copied to orders and trades

@dataclass(frozen=True)
class CancelOrder:  order_id: int
@dataclass(frozen=True)
class ClosePosition: position_id: int; reason: str
Intent = PlaceBracketLimit | CancelOrder | ClosePosition

class Strategy(Protocol):
    name: str
    params: StrategyParams
    warmup_bars: int
    def on_candle(self, view: MarketView, account: AccountView) -> list[Intent]
    def on_event(self, event: SimEvent) -> None        # fills, cancels, closes, skips
    def skip_counts(self) -> dict[str, int]
```

`tag` for this strategy: `candidate_idx, impulse_idx, displacement_idx,
swing_idx, swing_level, zone_low, zone_high, entry_kind, atr, stop_dist,
session_id`.

## 3.2 Swing high (k)

From Phase 2: candle `s` is a swing high iff `high[s] > high[s ± j]` for
`j = 1..k`, strict both sides; confirmed as of `s + k`; level `high[s]`.

## 3.3 Live levels, reference, impulse (D2)

The strategy keeps a set of *live* levels: confirmed swing highs not yet
closed above.

At the close of candle `t`, in this order:
1. Add every swing high with `confirmed_at == t` to the live set. (It
   cannot be broken on `t`: `close[t] ≤ high[t] < high[s]`.)
2. The **reference** is the live level with the greatest `confirmed_at`.
3. Candle `t` is an **impulse** iff a reference exists and
   `close[t] > reference.level`.
4. Retire every live level with `level < close[t]`, reference or not. An
   older, higher live level that survives becomes the reference at `t+1`.

Consequences: "live since confirmation" and "unbroken since formation"
coincide, because no candle in `s+1..s+k` can close above `high[s]`. A
candle that closes above an older level but not above the reference is
not an impulse; the older level still retires.

With `structure_break = "literal"` (not in the grid) step 4 is skipped and
the reference is the most recently confirmed swing high regardless of
breaks.

## 3.4 Candidate (N)

For an impulse at `t`, the candidate is the most recent bearish candle `c`
(`close[c] < open[c]`) with `t − N ≤ c ≤ t − 1`. No such candle → the
impulse yields no block (`no_candidate`). One impulse yields at most one
candidate; if it is rejected below, the search continues with the next
impulse, not with an earlier bearish candle.

## 3.5 Session eligibility (D1, D3)

The current session is `view.session`. The candidate is eligible iff
`τ_c ≥ session.open_ms` and `session.in_window` is true at `t` (which,
with windows on the 15m grid, is the same as `τ_t + 15m ≤ session.end_ms`).
Otherwise `ineligible`.

At most **one intent per session**: once the strategy emits a
`PlaceBracketLimit` in a session, it emits no more in that session,
whatever happens to that order (fill, stop-out, expiry) and whether or not
the simulator accepts it (a leverage skip in Phase 4 still consumes the
session). This keeps the trade list independent of account state.

## 3.6 Zone, entry, stop, target

- Zone: `full` → `[low[c], high[c]]`; `body` → `[close[c], open[c]]`.
- Entry: `top` → zone top; `mid` → zone midpoint.
- Stop: `low[c] − buffer`, anchored at the candle's true low in both zone
  modes. Buffer is `value × ATR14[t]` (Wilder, 15m, as of `t`) for `atr`,
  or `value × entry` for `pct`.
- `stop_dist = entry − stop` (must be `> 0`; otherwise `degenerate`, counted).
- Target: `entry + r_target × stop_dist`.
- Pierce: `pierce_abs = pierce × entry`.

## 3.7 Displacement and mitigation (D12)

This replaces v1 §6.5.

- **Displacement.** `d` is the first candle in `c+1 .. t` with
  `close[d] > high[c]` (the candle's true high, in both zone modes). If
  none exists the block is rejected (`no_displacement`). Note that `d` may
  equal `t`.
- **Mitigation.** If `d < t` and `min(low[d+1 .. t]) ≤ entry − pierce_abs`,
  price has already traded back into the entry after leaving the block:
  rejected (`mitigated`). With `d == t` there is nothing to check. This
  uses the same predicate as the Phase 4 fill rule, so "traded back into
  the zone" and "would have filled" are the same test.
- **Price above entry.** If `close[t] ≤ entry`, the limit would sit at or
  above the market: rejected (`entry_above_price`). With `pierce = 0` this
  is implied by the two rules above; with `pierce > 0` it closes the gap
  `close[t] ∈ (entry − pierce_abs, entry]`.

Why v1 was wrong: candle `c+1` opens at `close[c] < high[c]`, so the v1
test "any candle in `c+1..t` would have filled" fired on every block,
including the textbook pattern of a bearish candle followed immediately
by the impulse.

## 3.8 Trend filter (D7)

When `trend_filter` is on, the block is rejected (`trend`) unless
`close[t] > daily_sma(t)`, the SMA(50) of the 50 completed UTC daily
closes before the day of `t`. NaN SMA (warmup) counts as rejected.

## 3.9 Intent

If the block survives 3.4–3.8 and the session has no intent yet:

```
PlaceBracketLimit(side="long", price=entry, stop=stop, target=target,
                  expires_ms=session.end_ms, hold_rule=params.hold_rule, tag=...)
```

placed at the close of `t`; it can fill from `t+1`. Sizing and the
leverage check happen in the simulator at placement (Phase 4).

## 3.10 Hold rules (semantics fixed here, enforced in Phase 4)

`none`; `session_end` (deadline = end of the window in which the order was
placed); `max_hold` (deadline = fill candle close + `hours`). Measured from
the fill, not from placement.

## 3.11 Skip accounting

`skip_counts()` returns counts of `no_candidate, ineligible,
no_displacement, mitigated, entry_above_price, degenerate, trend,
session_used` (an impulse seen after the session's intent) plus
`impulses` and `blocks_seen`. The simulator adds `leverage`. All appear in
the results row and the report.

## 3.12 Tasks and tests

Scenario tests use `tests/synthetic.py` builders with hand-placed candles
and assert the exact intent list (prices, expiry, tag) or the skip reason.

- **3.1 Live levels and impulse.**
  Tests: a level retires on the first close above it; the most recently
  confirmed live level is the reference even when an older higher level
  exists; a level confirmed on `t` cannot be broken on `t`; a close above
  an older level but below the reference retires the older one and is not
  an impulse; after the reference breaks, the surviving older level is the
  reference next candle; `literal` mode ignores retirement.
- **3.2 Block selection.**
  - S1 basic: bearish `c`, impulse `t = c+1` closing above the level and
    above `high[c]` → one intent with `price = high[c]`,
    `stop = low[c] − 0.1 × ATR`, `target = entry + 2 × stop_dist`,
    `expires_ms = window end`.
  - S2 impulse at `c + N + 1` → `no_candidate`.
  - S3 two bearish candles inside `N` → the later one is the block.
  - S4 level already closed above before `t` → no impulse (D2).
  - S5 candidate before the session open, impulse after → `ineligible`;
    the next block in the session is taken.
  - S6a the immediate pattern (S1 shape) is **not** mitigated (`d == t`).
  - S6b `d < t` and a later candle's low at or below entry → `mitigated`;
    the next block in the session is taken (D4).
  - S6c with `pierce > 0`, a low exactly at entry is not mitigation; a low
    at `entry − pierce_abs` is.
  - S7 one intent per session: later blocks are `session_used` after an
    intent, including after that order expired or was stopped out, and
    after the simulator reported a leverage skip.
  - S8 body zone: `price = open[c]`, stop still from `low[c]`; `mid` entry
    on both zone modes.
  - S9 trend filter flips a known block; NaN SMA rejects.
  - S10 expiry equals the window end for all three sessions on a DST day.
  - S11 no displacement: candidate wick above every close through `t` →
    `no_displacement`.
  - S12 `pierce > 0` and `close[t]` in `(entry − pierce_abs, entry]` →
    `entry_above_price`.
  - S13 `degenerate`: a zero-range candle with zero buffer.
- **3.3 Strategy-level look-ahead test.**
  On a seeded random walk (`n = 5,000`, UTC session), the list of intents
  with decision index `≤ cut` is identical between the full series, the
  series truncated at `cut`, and `perturb_after(series, cut)`. 20 seeds ×
  3 cuts. Repeated for NY with weekday rule.

Exit criterion: all scenarios green; the rule text above and the code
agree line by line (reviewer reads both).
