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
   every day from its first archived day through 2020-06-30, that first
   day being no later than 2020-01-23, so the 160-day members are warm on
   the first trading day (Phase 1 §1.5: `d − 160 ≥` first day). Holes in the 15m archive inside those days do not
   disqualify a pair; they are filled by `--ccxt-gaps` at fetch and
   reported. *(builder's call)* Delisted symbols count; the archive keeps most of
   them (verified 2026-10-23: LUNAUSDT and SRMUSDT present for 2021-06).
   Symbol list from the archive's S3 listing
   (`data.binance.vision/?prefix=data/futures/um/monthly/klines/`).
2. **Rank** by total quote volume over 2020-01-01 – 2020-06-30, from the
   archive's 1d klines (`quote_volume` column). A pair listed in January
   is ranked on the days it existed.
3. **Keep the top 10.** Exclude stablecoin bases and leveraged tokens (none
   expected among 2020 perps; the rule is stated for symmetry with spot).
4. **Trading starts 2020-07-01** for every member.

Every input is dated before the first trading day, so the choice uses no
information from the trading period. SOLUSDT (listed 2020-09-14) is not a
candidate under this rule; BTC, ETH, and SOL are reported per pair in any
case where they are members, and SOL is not added.

**Decided with the user (2026-10-23).** The rule first discussed ranked
perps listed by 2020-12-31 on full-year 2020 volume; that uses information
from the first six months of trading (volume tracks the 2020 rallies), so
the stricter rule above was chosen, accepting that SOLUSDT is excluded.

**Amended with the user (2026-10-23, Phase 0 build).** The first version
required a kline on every day of 2020-01-01 – 2020-06-30. Run against the
archive it left 3 members (BTCUSDT, ETHUSDT, BCHUSDT): most 2020 perps were
listed during January 2020 (XRP 01-06 … XLM 01-20, ADA 01-31). The user
chose the warm-by-2020-07-01 rule above, which keeps every input before
the first trading day and the trading start unchanged.

## 0.4 Universe rule — spot (holdout)

1. **Candidates.** Spot `*USDT` pairs with an archive 1d kline on every
   day from the later of their first archived day and 2017-12-01 through
   2017-12-31. Exclude stablecoin bases (USDC, TUSD, PAX,
   BUSD, USDS, …) and leveraged tokens.
2. **Rank** by quote volume over 2017-12-01 – 2017-12-31.
3. **Keep up to 10** (fewer exist; that is expected).

   *Amended with the user (2026-10-23, Phase 0 build):* the first version
   required every day of 2017-12, which dropped LTCUSDT (spot listing
   2017-12-13). Spot members enter the holdout when warm anyway, so the
   rule now matches the perp one without a warmup cutoff.
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

## 0.9 Implementation notes (Phase 0 build, 2026-10-23)

Builder's calls taken while building this phase; none changes a trading
rule (the universe-rule amendments in §0.3 and §0.4 were the user's).

- **Coverage is read from 1d klines in both markets.** §0.4 said "15m
  archive data on every day"; a day has a 1d kline exactly when it has
  trades, which is what §0.1 already prescribes for ranking.
- **`universe.json`** also records each market's `window` and a `dropped`
  list (`pair`, `reason`) for every symbol that had data in the window but
  is not a candidate (missing month, missing days, first day too late,
  stablecoin, leveraged token), so the choice can be audited. Non-USDT
  pairs and dated futures are not listed. `first_day` is the first row of
  the symbol's earliest monthly 1d file, `last_day` the last row of its
  latest one (2026-09-30 for a live pair; the delisting day otherwise).
- **Exclusions:** stablecoin and fiat bases `USDC TUSD PAX USDP BUSD USDS
  USDSB DAI FDUSD UST USTC EUR GBP AUD`; a leveraged token is a base ending
  in `UP`, `DOWN`, `BULL` or `BEAR` with at least three characters left
  (`BTCUP` yes, `JUP` no). Neither occurs among the 2020 perps or the
  December 2017 spot pairs.
- **Delisted perps and ccxt:** `--ccxt-tail` and `--ccxt-gaps` skip a pair
  whose stored series ends more than 7 days before the fetch day
  (`fetch.DELISTED_AFTER_DAYS`); the summary shows `ccxt_skipped:
  delisted`. EOSUSDT (archive ends 2025-05-21) is such a pair.
- **Commands:** the CLI flag is `--tfs 15m` (§0.6 wrote `--tf`); the spot
  fetch adds `--to 2019-12-31`, the holdout end, so no later spot data is
  downloaded. `perpbt data validate --market spot` validates the spot store.
- **Runtime:** the archive listing is one request per symbol; with 8
  threads the perp ranking took about 7 minutes and the spot ranking about
  3.5 (sequential: 24 and 22 minutes).
- **Off-grid archive candles are snapped.** After the exchange restart of
  2018-02-09 the spot archive's 15m candles run on a shifted grid for 81
  candles (09:58:14.789 → 2018-02-10 05:58:14.789, 1m45s before a slot),
  in every spot member. `bulk.snap_to_grid` moves a candle within a
  quarter step of the grid to the nearest slot at ingest and records the
  count per archive file (`"snapped"` in the manifest); a collision or a
  larger offset still fails. This keeps the real restart prices instead of
  a 20-hour hole. Perp files have no off-grid candles and are unchanged.

**Data facts for later phases** (fetched 2026-10-23):

- Perps: 0 gaps in all ten members after the ccxt gap fill (the
  2022-02-26..28 and 2022-04-01..02 archive holes, 480 candles per pair;
  EOSUSDT has none); every funding interval is 8 h.
- EOSUSDT's last candle is 2025-05-21 09:00, but its archive funding files
  continue at a flat 0.01% per 8 h through 2026-09: artifacts of the dead
  contract. Funding must be cut at a pair's last candle (Phase 1 §1.1,
  Phase 3 §3.6).
- Spot gaps are exchange-wide outages, the same in every pair, all under
  5 days; the longest is the 2018-02-08 00:30 → 2018-02-09 10:00 halt.
  BCCUSDT is suspended 2018-11-15 16:45 → 2018-11-20 02:45 (the BCH fork),
  has one more candle and is delisted.
