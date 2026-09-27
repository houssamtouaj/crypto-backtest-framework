# Phase 2 — Indicators and the look-ahead guard

Read with `00-overview.md`. Delivers the three causal indicators the
strategy uses and the `MarketView` guard that makes look-ahead impossible in
strategy code. Depends on Phase 0 (synthetic builders); real data is used
only in slow tests.

**Branch:** `phase/2-indicators`, created from `dev` after Phase 0 is
merged; may run in parallel with `phase/1-data` in a separate worktree;
merged into `dev` with `--no-ff` when the exit criterion below is met
(overview §8.1).

Every indicator returns arrays aligned to the 15m index, NaN (or -1 for
integer arrays) before warmup, and states its confirmation lag: the number
of candles after `i` that must be closed before the value at `i` is known.
A value with lag 0 is usable at the close of `i`.

## 2.1 Wilder ATR(n) — `indicators/atr.py`

```python
def atr(candles: Candles, n: int = 14) -> np.ndarray     # lag 0
```

`TR_i = max(h_i − l_i, |h_i − c_{i−1}|, |l_i − c_{i−1}|)` with `TR_0 = h_0 − l_0`.
`ATR_{n−1} = mean(TR_0..TR_{n−1})`; then `ATR_i = (ATR_{i−1} × (n−1) + TR_i) / n`.
NaN for `i < n−1`. Gaps in the series are ignored (the previous available
candle is "previous").

## 2.2 Swing highs — `indicators/swings.py`

```python
@dataclass(frozen=True)
class Swings:
    idx: np.ndarray            # candle index s of each swing high
    level: np.ndarray          # high[s]
    confirmed_at: np.ndarray   # s + k
    # arrays sorted by confirmed_at (equivalently by idx)

def swing_highs(candles: Candles, k: int) -> Swings       # lag k
```

Candle `s` is a swing high iff `high[s] > high[s ± j]` for all `j = 1..k`,
strict on both sides; equal highs do not qualify. It is confirmed as of
`i = s + k`. Candles within `k` of either end of the series cannot be swing
highs. No two swing highs share an index, and because `s + k` is injective
no two share a `confirmed_at`.

Consumers never index `Swings` directly; they go through
`MarketView.swings_confirmed_by(i)`.

## 2.3 Daily SMA and ADX aligned to 15m — `indicators/daily.py`

```python
def daily_bars(candles15: Candles) -> DailyBars     # UTC days; o/h/l/c/v from the day's 15m candles
def daily_sma_aligned(candles15: Candles, n: int = 50) -> np.ndarray    # lag 0 at the 15m level
def daily_adx_aligned(candles15: Candles, n: int = 14) -> np.ndarray    # lag 0 at the 15m level
```

A UTC day `D` is *completed* once any candle of a later day exists; the
last day of the series is never completed. (Earlier wording said "once the
candle opening at `D+1 00:00` exists"; that would leave a day uncompleted
forever when that one candle is missing.) The daily close of `D` is the
close of the last 15m candle of `D` present in the data (outage gaps do
not disqualify a day); a day with no candle at all has no bar and is
skipped. For any 15m candle `i` on day `D`, the aligned value is the
indicator computed on the bars of the days `< D` only. So the value is
constant across all candles of a day and changes at 00:00 UTC. Days before
the first `n` completed days give NaN.

ADX is the standard Wilder construction: `+DM`, `−DM`, `TR` per day; Wilder
RMA(n) smoothing of each; `DI± = 100 × RMA(±DM) / RMA(TR)`;
`DX = 100 × |DI+ − DI−| / (DI+ + DI−)`; `ADX = RMA(n)(DX)`. Warmup is `2n − 1`
days; with the 2019-11-01 backfill both SMA(50) and ADX(14) are valid on
2020-01-01 for BTC. ETHUSDT listed on 2019-11-27, so only 35 daily bars
precede 2020-01-01: ADX(14) is valid from 2019-12-25 but SMA(50) only from
2020-01-16, and the trend filter (Phase 3 §3.8) rejects ETH blocks before
then. For SOL both are valid from listing plus warmup.

## 2.4 MarketView and AccountView — `strategy/base.py`

```python
class LookaheadError(RuntimeError): ...

@dataclass(frozen=True)
class SessionInfo:
    id: int; open_ms: int; end_ms: int; in_window: bool; is_last: bool

class MarketView:
    """Window ending at candle i. Any access beyond i raises LookaheadError."""
    def __init__(self, candles, *, atr, daily_sma, daily_adx, swings, calendar, start_i=0)   # built once per simulation (Phase 4)
    i: int                             # the view never reveals how many candles follow i
    def ts(self, j) -> int
    def open(self, j) / high(self, j) / low(self, j) / close(self, j) -> float
    def lows(self, a, b) -> np.ndarray     # low[a..b] inclusive, b <= i; same for highs/closes/opens
    def atr(self, j) -> float
    def daily_sma(self, j) -> float
    def daily_adx(self, j) -> float
    def swings_confirmed_by(self, j) -> Swings      # only rows with confirmed_at <= j; j <= i
    session: SessionInfo
    def advance_to(self, i) -> None                 # simulator only; forward, < series length
    def is_bearish(self, j) -> bool                 # close[j] < open[j]

@dataclass(frozen=True)
class OrderView:    order_id: int; side: str; price: float; stop: float; target: float; expires_ms: int; placed_idx: int
@dataclass(frozen=True)
class PositionView: position_id: int; side: str; entry: float; stop: float; target: float; qty: float; fill_idx: int
@dataclass(frozen=True)
class AccountView:                     # read-only, this variant only
    equity_mtm: float
    open_positions: tuple[PositionView, ...]
    pending_orders: tuple[OrderView, ...]
```

`MarketView` is constructed once per simulation with references to the
full arrays and mutated by advancing `i`; it never copies. Every accessor
checks `j <= self.i` (and `a <= b <= i` for ranges) and raises
`LookaheadError` otherwise. Negative `j` is an error too, so warmup NaNs
are the only signal of "not yet available". Range accessors and
`swings_confirmed_by` return read-only views of exactly the requested rows.
A non-integer index raises `TypeError`. `SessionInfo` outside any window has
`id = open_ms = end_ms = -1` and both flags false.

## 2.5 Tasks and tests

- **2.1 ATR.** Tests: matches a hand-computed 6-candle example (n = 3) and a plain-Python reference implementation on a 20-candle example, both to 1e-12;
  NaN for the first `n−1`; `assert_causal` over 20 seeds × 3 cuts.
- **2.2 Swing highs.** Tests: synthetic series with known swing highs for
  `k = 1, 2, 3`; equal highs excluded; `confirmed_at == idx + k` exactly;
  candles within `k` of the series end are never swings; `assert_causal`
  on `(level, confirmed_at)` pairs (a swing confirmed at `≤ cut` is
  identical under perturbation; swings confirmed after `cut` may differ).
- **2.3 Daily SMA/ADX.** Tests: the value on any 15m candle equals the
  SMA/ADX of the previous `n` completed days computed independently with
  pandas; changing any candle of day `D` never changes the value on day
  `D`; a day with an outage gap still counts as completed with its last
  available close; `assert_causal`. Slow: BTC 2020-01-01 has a non-NaN
  SMA(50) and ADX(14) after the backfill.
- **2.4 MarketView guard.** Tests: `view.close(view.i + 1)` raises;
  `view.lows(i − 3, i + 1)` raises; `view.swings_confirmed_by(i)` never
  returns a swing with `confirmed_at > i`; the same view advanced to `i+1`
  then exposes exactly one more candle; a fuzz test over random `(i, j)`
  pairs confirms access succeeds iff `0 <= j <= i`.

Exit criterion: all tests green; every indicator's docstring states its lag.
Then merge into `dev` and delete the branch.
