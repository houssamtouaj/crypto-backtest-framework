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
