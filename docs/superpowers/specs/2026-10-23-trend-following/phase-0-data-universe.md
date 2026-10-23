# Phase 0 — Data and universe

Read with `00-overview.md`. Delivers spot-archive support in the shared
data layer, the point-in-time universe for perps and for spot, the trend
data config, and the fetched data. No strategy logic.

**Branch:** `trend/0-data-universe`, from `dev`.

## 0.1 Spot support in the data layer

The first strategy's data layer is perp-only (`bulk.BASE_URL` is
`data/futures/um`, `ARCHIVE_START` is 2020-01). Extend it, backward
compatibly:

- `bulk.py`: a `market` argument, `"um"` (default, unchanged behaviour)
  or `"spot"`. Spot klines live at
  `https://data.binance.vision/data/spot/{monthly|daily}/klines/<SYM>/<tf>/<SYM>-<tf>-<period>.zip`
  (verified 2026-10-23: `BTCUSDT-15m-2017-08` and `BCCUSDT-15m-2018-01`
  return 200). Spot has no funding files. The spot archive's first month
  is per symbol; the archive-start check becomes per market. Spot files
  after 2025 use microsecond timestamps; the existing ms/µs detection
  applies.
- `store.py`: the store root depends on the market: perps stay at
  `data/candles/<PAIR>/<tf>` (unchanged paths, so the existing BTC/ETH/SOL
  15m files are reused), spot goes to `data/spot/candles/<PAIR>/<tf>`.
  Manifests as today.
- `fetch.py`: `run_fetch(..., market=...)`; spot skips funding and ccxt.
  `--ccxt-tail` and `--ccxt-gaps` skip delisted perps (ccxt may not know
  them); their archive data is complete up to delisting.
- Validation (`validate.py`) runs unchanged on spot 15m.

The holdout guard for spot is a separate flag: a `CandleStore` for the spot
market refuses every load unless `allow_holdout=True`, regardless of dates
(the entire spot store is holdout data).

Universe ranking (§0.3, §0.4) is new code, not reuse: it lists the
archive's S3 contents and reads the archive's **1d** klines in memory
(never written to the store, which only knows 1m and 15m), returning
volume and coverage only, never prices.

## 0.2 Trend data config

`configs/trend/data.yaml` is a `DataConfig`:

- `insample_start: 2020-01-01`, `insample_end: 2026-10-20` (the store
  guard then blocks forward data except through `trend/forward.py`),
  `holdout_start: 2026-10-21` (the first day after the in-sample period;
  it is the earliest possible forward day, `DataConfig` requires a date),
  `holdout_end: null`; `warmup_start: 2020-01-01`. The forward period's
  real start lives in the pre-registration (`periods.forward.start`, set
  by the code lock); `data.yaml` is not edited by the lock.
- `listing`: every perp in the resolved universe.

`configs/trend/data-spot.yaml` is a second `DataConfig` for the spot
members: `listing` holds their first spot day, `insample_*` cover
2017-08-01 – 2019-12-31, and the store guard for spot is the market flag
above.

The first strategy's `configs/data.yaml` is not edited.

## 0.3 Universe rule — perps (in-sample and forward)

1. **Candidates.** Every USDT-M perpetual with an archive 1d kline for
   every day of 2020-01-01 – 2020-06-30 (so it can be warmed up by the
   first trading day). Holes in the 15m archive inside those days do not
   disqualify a pair; they are filled by `--ccxt-gaps` at fetch and
   reported. *(builder's call)* Delisted symbols count; the archive keeps most of
   them (verified 2026-10-23: LUNAUSDT and SRMUSDT present for 2021-06).
   Symbol list from the archive's S3 listing
   (`data.binance.vision/?prefix=data/futures/um/monthly/klines/`).
2. **Rank** by total quote volume over 2020-01-01 – 2020-06-30, from the
   archive's 1d klines (`quote_volume` column).
3. **Keep the top 10.** Exclude stablecoin bases and leveraged tokens (none
   expected among 2020 perps; the rule is stated for symmetry with spot).
4. **Trading starts 2020-07-01** for every member.

Every input is dated before the first trading day, so the choice uses no
information from the trading period. SOLUSDT (listed 2020-09-14) is not a
candidate under this rule; BTC, ETH, and SOL are reported per pair in any
case where they are members, and SOL is not added.

**Open with the user:** the rule approved in conversation ranked perps
listed by 2020-12-31 on full-year 2020 volume, which uses information from
the first six months of trading (volume tracks the 2020 rallies). This
draft proposes the stricter rule above. See the question list in the
hand-off message; the spec is updated with the answer.

## 0.4 Universe rule — spot (holdout)

1. **Candidates.** Spot `*USDT` pairs with 15m archive data on every day of
   2017-12-01 – 2017-12-31. Exclude stablecoin bases (USDC, TUSD, PAX,
   BUSD, USDS, …) and leveraged tokens.
2. **Rank** by quote volume over 2017-12-01 – 2017-12-31.
3. **Keep up to 10** (fewer exist; that is expected).
4. Each member enters the holdout portfolio on the first day its signals
   and vol forecast are warm (Phase 1 §1.5), and the holdout ends
   2019-12-31. Members delisted before then exit by the delisting rule
   (Phase 3 §3.6), e.g. BCCUSDT in 2018-11.

## 0.5 Output

`perpbt trend universe --market um|spot` writes `configs/trend/universe.json`:

```json
{"um":   {"rule": "...", "ranked": [{"pair": "...", "quote_volume": 0.0, "first_day": "...", "last_day": "..."}],
          "members": ["..."], "computed_on": "YYYY-MM-DD"},
 "spot": {...}}
```

The full ranking is kept (not only the members) so the choice can be
audited. The file is committed and its content goes into the
pre-registration hash (Phase 2).

## 0.6 Fetch

- Perps: `perpbt data fetch --config configs/trend/data.yaml --tf 15m
  --pairs <members> --ccxt-tail` for 15m and funding. No 1m. BTC/ETH
  already exist and are only extended.
- Spot: `perpbt data fetch --config configs/trend/data-spot.yaml --market spot --tf 15m --pairs <members>`.
- `perpbt data validate` on both; manifests committed.

## 0.7 Tests

- Spot URL building, per-market archive start, store roots, and that the
  perp paths are unchanged (existing tests stay green).
- Spot guard: any price load without `allow_holdout=True` raises.
- Universe ranking on a synthetic archive listing: ties broken by symbol
  name, incomplete-coverage candidates dropped, exclusions applied,
  delisted symbols kept.
- The full first-strategy test suite stays green.

## 0.8 Exit criterion

Universe resolved and committed for both markets, data fetched and
validated, gap counts reviewed, all tests green.
