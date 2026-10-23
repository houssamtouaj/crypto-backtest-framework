# Phase 0 — Data

Read with `00-overview.md`. Delivers spot 15m candles for the basket from
2020, the split spot guard, and the carry data config. No strategy logic.

**Branch:** `carry/0-data`, from `strategy/funding-carry-spec` (= `dev` +
the spec), so the spec reaches `dev` with this phase's merge.

## 0.1 The spot guard split

Today every spot load needs `allow_holdout=True` (trend Phase 0: the whole
spot store was the trend holdout, 2017-08 – 2019-12). The trend project is
stopped, but its holdout window stays protected. Change, backward
compatibly:

- A spot `CandleStore.load` whose `start_ms` is before **2020-01-01**
  (`SPOT_TREND_HOLDOUT_END_EXCLUSIVE`, a constant in `store.py`) still
  needs `allow_holdout=True`, whatever the config says.
- A spot load starting on or after 2020-01-01 follows the same date guard
  as perps: refused past the config's `insample_end` unless
  `allow_holdout=True`.
- The trend test `test_spot_load_refused_without_allow_holdout` (2018
  dates) stays as it is and must stay green. A new test pins the 2020+
  behaviour and the boundary (a load from 2019-12-31 refuses; from
  2020-01-01 passes inside the in-sample).

## 0.2 Carry data config

`configs/carry/data.yaml` is a `DataConfig` used for **both** stores (perp
and spot) of the carry strategy:

- `insample_start: 2020-01-01`, `insample_end: 2023-12-31`,
  `holdout_start: 2024-01-01`, `holdout_end: 2026-10-20`,
  `warmup_start: 2020-01-01`.
- `listing`: the ten perps with their trend `listing` dates (the perp is
  always the later of the two legs; every spot pair predates 2020).

The trend and first-strategy configs are not edited. Neither config
alone guards the carry holdout: `configs/trend/data.yaml` lets perps, and
after §0.1 spot from 2020, load through 2026-10-20. The carry loader
(`carry/frames.load_pairframe`, Phase 1) therefore checks the requested
end itself, whatever config it is given. An end after `CARRY_INSAMPLE_END
= 2023-12-31` (a constant in `carry/frames.py`, equal to the
pre-registered in-sample end) raises unless `allow_holdout=True`, and only
`carry/holdout.py` and `carry/forward.py` (Phase 5) pass it. A test pins
the refusal under both the carry and the trend data configs. *(my call,
after the builder check)*

## 0.3 Fetch

- Spot: `perpbt data fetch --config configs/carry/data.yaml --market spot
  --tfs 15m --from 2020-01-01 --to 2026-10-20 --pairs <one pair>`, one pair
  per call, in the foreground (background jobs get killed under memory
  pressure).
- Perps: already fetched by trend Phase 0 through 2026-10-23 (15m and
  funding, 0 gaps); nothing to fetch.
- `perpbt data validate --config configs/carry/data.yaml --market spot`;
  manifests committed. Record per pair: rows, first/last candle, gaps,
  snapped candles, overlap mismatches.

## 0.4 Basis sanity check (no returns)

For each pair, from the stored closes only: the share of 15m slots where
both legs have a candle, and the distribution of `perp close / spot close
− 1` (median, 1st/99th percentile, max |·|) over **2020-07-01 –
2023-12-31 only** (loads past the in-sample end are refused anyway). This
catches a wrong symbol mapping (e.g. a re-denominated coin) before any
strategy code exists. It computes no return, P&L or funding statistic.
Anything with |median| > 0.5% or max |·| > 20% is reported to the user
before Phase 1.

## 0.5 Tests

- The guard split (§0.1), including the boundary and that the trend spot
  test still refuses.
- `configs/carry/data.yaml` loads; its `listing` equals the trend um
  members with the trend dates.
- The full suite stays green.

## 0.6 Exit criterion

Spot data fetched and validated for all ten pairs, the basis check
reviewed, tests green.

## 0.7 Implementation notes (Phase 0 build, 2026-10-23)

Builder's calls taken while building this phase; none changes a trading
rule.

- **Guard.** `store.SPOT_TREND_HOLDOUT_END_EXCLUSIVE` (2020-01-01 00:00 UTC)
  is compared with a spot load's `start_ms`: a load that starts in 2019 and
  ends in 2020 still needs `allow_holdout=True`. Spot loads under
  `configs/trend/data-spot.yaml` (`insample_end` 2019-12-31) stay refused
  from 2020 on by the ordinary date guard.
- **Spot ceiling at the carry holdout** *(my call, after the whole-branch
  review)*. A spot load ending after `store.SPOT_CARRY_HOLDOUT_START`
  (2024-01-01) also needs `allow_holdout=True`, whatever the config. §0.1
  alone let `configs/trend/data.yaml` (and `configs/data.yaml` to
  2025-12-31) read the spot leg of the carry holdout without the flag, and
  the carry loader's own check only comes in Phase 1. Under
  `configs/carry/data.yaml` the ceiling coincides with the date guard; trend
  reads spot only before 2020; perps are unchanged. Phase 5's
  `carry/holdout.py` and `carry/forward.py` pass the flag anyway.
- **Trend test moved.** `tests/trend/test_daily.py::test_load_dayframe_spot_needs_allow_holdout`
  loaded spot from 2020-01-01, which §0.1 makes legal; its data now starts
  2019-12-30 (same intent), and a new test pins that a 2020 spot DayFrame
  loads without the flag. `trend/daily.load_dayframe`'s docstring is
  updated; no trend behaviour changes for pre-2020 spot.
- **Basis check as code.** `perpbt/carry/basis.py` with `perpbt carry basis`
  (default `--config configs/carry/data.yaml`) writes the committed
  `runs/carry/basis_check.json`. The window 2020-07-01..2023-12-31 is a
  module constant, not a parameter, so no config can move it into the
  holdout; both legs load through the guarded stores without
  `allow_holdout`. Coverage is common slots over the window's 15m grid
  slots (122,784).
- **Fetch.** The spot fetch starts at `max(--from, listing)`, rounded to the
  month: every pair has spot from 2020-01-01 00:00 (LINK and the other
  January perps included).

**Data facts for later phases** (fetched 2026-10-23):

- Spot, all ten pairs, first candle 2020-01-01 00:00 (BTC, ETH, LTC: the
  store continues from 2017), last 2026-10-20 23:45, 238,408 rows from 2020
  per pair (EOSUSDT: last 2025-05-26 02:45, 189,172 rows; its 523 later
  daily files are not published). 0 overlap
  mismatches, 0 snapped candles from 2020 (the 81 snapped in BTC, ETH, LTC
  are the 2018-02-09 restart).
- The same 15 spot gaps from 2020 in every pair, exchange-wide, 152 slots,
  the longest 23 slots (2020-02-19 11:45 → 17:30); 10 of them (93 slots)
  fall in 2020-07-01..2023-12-31, none after 2023-03-24. None covers
  00:00–01:00 UTC, so no in-sample 00:15 fill lands in a spot gap. Perps
  have no gaps.
- EOSUSDT: the perp's last candle is 2025-05-21 09:00, the spot's
  2025-05-26 02:45; the pair's common last day is the perp's.

**Basis check (2020-07-01..2023-12-31).** Coverage 0.9992 in every pair
(perp 122,784 rows, spot 122,691, all spot slots also perp slots). Medians
−0.05% … 0.00%, 1st/99th percentiles within ±0.4%: no symbol mapping
error. Three pairs exceed the 20% max limit, all in the 2021-05-19 crash
(13:30 UTC; XLM 12:45): BCH −45.5%, XLM −27.2%, TRX −21.8% (ETC 18.4%, LTC
14.9%, EOS 14.6%, LINK 12.9%, XRP 10.8%, ETH 9.6%, BTC 4.0% at the same
slot; in every pair the maximum is the perp *below* spot, see
`basis_at_max_abs`). The perp falls far below spot during the liquidation
cascade; the basis is back to about 1–3% within 15–45 minutes in most pairs
and about 75 minutes in XLM (−16% at 13:30, about −1% at 14:00). These are real prices, kept as they are; they bear on the margin
rule (CD7: liquidation at the day high of the short) only on the up side,
and on basis P&L if a sleeve is entered or exited on that day.
Other days with any |basis| > 5% (all pairs checked):

- **BCH, 2020-11-13 … 11-15 (the BCH/BCHA fork).** The perp's discount to
  spot builds over about two days: −0.7% on 11-13 12:00, −1.4% on 11-14
  00:00, −3.7% by 11-14 18:00, then −4% … −6.9% until the snapshot at
  11-15 12:00, when it snaps back to about 0.
- **XRP, 2020-12-11 (the Spark/FLR airdrop snapshot).** The discount runs
  from −0.5% at 00:00 to −1.8% at 18:00 and −6.7% at 23:45, then is +0.15%
  at 2020-12-12 00:00.
- **XLM, 2023-08-17 21:30.** One slot, −5.7%, in a market-wide drop.

The first two are the same effect: spot carried an airdrop entitlement and
the perp did not. A long-spot sleeve held across the snapshot loses that
step without the airdrop credit, which the backtest does not model
(conservative for the carry). Phase 3 counts such days; it does not credit
airdrops.
