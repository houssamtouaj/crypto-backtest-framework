# Phase 3 — Execution

Read with `00-overview.md`. Built only if the Phase 2 screen said go.
Delivers the cash-account executor in two forms, a plain reference loop
over one path and a batched form over many paths (for T1 and re-pricing),
proven equal.

**Branch:** `etf/3-execution`, from `dev` after Phase 2 is merged.

## 3.1 Inputs and outputs

```python
def run_path(frames: dict[str, EtfFrame], exposure: dict[str, np.ndarray], cfg: ExecSpec) -> PathResult
def run_batch(frames, exposure_batch: dict[str, np.ndarray],   # (paths, days) per ticker
              cfg: ExecSpec) -> BatchResult                    # daily portfolio returns (paths, days) + counters
```

`ExecSpec` carries sizing, rebalance, costs and the period. Exposure is an
input (Phase 1 or a shifted path); the executor never computes signals.
Target weights are recomputed inside from exposure and the frame's σ̂.

`PathResult` tables: `daily` (date, equity, ret, cash_ret, excess_ret,
gross_ret, slippage, commission, dividends, interest, invested_share),
`daily_etf` (date, ticker, qty, weight held, target, pnl, dividend),
`fills` (date, ticker, qty, price, reason, slippage, commission), `events`
(band skips, cash-cap scalings).

## 3.2 One cash account

Starting equity 10,000 (scale only). Fractional shares are allowed, so
results are weights *(my call)*. `N` is the number of tickers whose signal
is valid (all four throughout the periods). Ticker `i`'s target value is
`w*_i × E_d / N`, with `E_d` the equity at day `d`'s close. Since
`w* ≤ 1`, total targets never exceed `E_d`. There is no borrowing: cash
never goes below 0 (§3.3 step 3).

## 3.3 Day loop (day `d+1`, decisions from day `d`'s close)

1. **Overnight.** Each held quantity is marked from `C_d` to `O_{d+1}`;
   dividends going ex on `d+1` are credited in cash
   (`qty × D_{d+1}`). The dividend is **reinvested** at `O_{d+1}` into the
   same ETF (with slippage, reason `dividend`, not counted as a trade),
   unless today's trade exits or resizes that ETF, in which case it simply
   joins the cash used by the trade *(my call: matches the frame's total
   return, and keeps the 100% SPY benchmark from holding dividend cash for
   years under the band)*.
2. **Decide (made at `d`'s close, applied now).** For each ticker, a trade
   is due when: its exposure differs from its exposure at its last trade
   (`signal`); its target is 0 and it holds shares (`exit`); or
   `|w_held_d − w*_d| / w*_d > drift_band` with `w*_d > 0` (`drift`).
   Otherwise a needed-but-suppressed trade counts as `band_skip`. The
   target quantity is `w*_d × E_d / N / C_d` (sized on the decision close,
   filled at the open).
3. **Fill at `O_{d+1}`** (or `C_{d+1}` for `fill: next_close`, in which
   case steps 1 and 4 use the old quantities for the whole day and the
   trade happens at the close). Sells first, then buys. Buy price
   `O × (1 + s)`, sell price `O × (1 − s)`, `s` the ticker's slippage;
   commission `c × traded value`. If the buys cost more than the cash after
   sells (an overnight gap up), all buys of that day are scaled down by
   the same factor and a `cash_cap` event is recorded.
4. **Intraday.** Mark every quantity from `O_{d+1}` to `C_{d+1}`.
5. **Interest.** Cash held at day `d`'s close earns `cash_{d+1}` (Phase 1
   §1.3) *(my call: interest on the prior close's balance; the
   difference from intraday timing is negligible)*.
6. `E_{d+1}` = cash + Σ qty × `C_{d+1}`. `ret = E_{d+1}/E_d − 1`;
   `excess_ret = ret − cash_{d+1}`.

Weekly rebalancing: decisions only on each week's last trading day; fills
on the next trading day's open; exits too wait for the decision day.

## 3.4 Benchmarks (same executor)

- **Vol-targeted buy-and-hold** (T2): exposure fixed at 1 for every
  ticker, same sizing, band and costs.
- **100% SPY** and **equal-weight buy-and-hold** (descriptive): weights
  fixed at 1 of their sleeve, no vol targeting; the equal-weight one
  rebalanced on the first trading day of each month.

## 3.5 Batched executor

Same arithmetic vectorised over paths (shape `(paths, days)` per ticker).
Equality test: for 20 random exposure paths, batched results equal the
reference loop to 1e-12 on every daily return and counter.

## 3.6 Tests

- Hand-computed small cases: one buy and one sell with slippage; a
  dividend held overnight vs bought at the ex-date open; the cash cap on a
  gap up; weekend interest.
- Band: a drift of 19% does not trade, 21% does; an exposure change always
  trades.
- `fill_close` path: no overnight change of quantity.
- Zero costs, exposure 1, `no_vol_target` and band 0 reproduce each
  ticker's total return (Phase 1 §1.2) to 1e-10 **from the day after the
  first fill** (the first buy is sized on a close and filled at an open, so
  it can be cash-capped).
- Perturbation: changing any input after day `d` leaves `daily` up to `d`
  unchanged.

## 3.7 Exit criterion

Tests green, equivalence proven; a timing note (one path and 1,000 paths on
the real in-sample data) in this file's notes. **No statistic on real
strategy returns is reported in this phase** beyond the timing run.
