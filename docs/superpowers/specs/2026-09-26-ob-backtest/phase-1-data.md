# Phase 1 — Data

Read with `00-overview.md`. Delivers candles and funding in Parquet with a
manifest, validation, the session calendar, the holdout guard, and the ccxt
head/tail fetch. Depends on Phase 0.

**Branch:** `phase/1-data`, created from `dev` after Phase 0 is merged; may
run in parallel with `phase/2-indicators` in a separate worktree; merged
into `dev` with `--no-ff` when the exit criterion below is met (overview
§8.1).

## 1.1 Sources (verified 2026-09-26)

**Bulk archive** `https://data.binance.vision/data/futures/um/`:
- `monthly/klines/<PAIR>/<TF>/<PAIR>-<TF>-YYYY-MM.zip` and
  `daily/klines/<PAIR>/<TF>/<PAIR>-<TF>-YYYY-MM-DD.zip`, each with a
  `.CHECKSUM` (sha256) sibling. TF in {`1m`, `15m`}.
- `monthly/fundingRate/<PAIR>/<PAIR>-fundingRate-YYYY-MM.zip`.
- Coverage starts **2020-01** for USDT-M futures: `BTCUSDT-1m-2019-12.zip`
  and the 2019-12 funding file return 404; 2020-01 returns 200. Listing
  dates (BTCUSDT 2019-09-08, ETHUSDT 2019-11-27, SOLUSDT 2020-09-14) matter
  only for SOL, whose files start 2020-09.
- Kline CSV columns: `open_time, open, high, low, close, volume, close_time,
  quote_volume, count, taker_buy_volume, taker_buy_quote_volume, ignore`.
  2020 files have no header row; recent files do. Timestamps are
  milliseconds in older files and may be microseconds in newer ones:
  detect per file (`value > 1e14` → microseconds, divide by 1000).
- Funding CSV columns: `calc_time, funding_interval_hours, last_funding_rate`;
  `calc_time` carries 1–2 ms jitter and is rounded to the minute.
- Daily files exist for the current month up to yesterday; the monthly file
  for a month appears a few days after month end.

**ccxt** (`binanceusdm`, `fetch_ohlcv`, 1,500 candles per call) for:
- Head backfill (D14): 15m candles for `2019-11-01 .. 2019-12-31` for
  BTCUSDT and ETHUSDT. 15m only; 1m and funding are not needed before
  2020-01-01 because no position can exist before the first decision candle.
- Tail: candles and funding between the last published daily file and the
  download instant. Optional; the pipeline works without it.

## 1.2 Storage schema

All timestamps UTC `int64` ms. Prices `float64`. Parquet, one file per
`pair/tf/year`, under `data/candles/`, `data/funding/`.

### `candles`

| column | type | notes |
|---|---|---|
| pair | str | e.g. BTCUSDT |
| tf | str | `1m`, `15m` |
| open_ms | int64 | primary key with pair, tf; candle open time |
| open, high, low, close | float64 | |
| volume | float64 | base asset |
| quote_volume | float64 | |
| trades | int64 | trade count |
| taker_buy_volume | float64 | |
| source | str | `bulk_monthly`, `bulk_daily`, `ccxt` |

Precedence when sources overlap: `bulk_monthly` > `bulk_daily` > `ccxt`.
Overlaps are resolved at merge time; a row from a lower-precedence source
that disagrees with a higher one on OHLC by more than 1e-9 relative is
logged in the manifest.

### `funding`

| column | type | notes |
|---|---|---|
| pair | str | |
| funding_ms | int64 | `calc_time` rounded to the minute |
| rate | float64 | signed; longs pay when positive |
| interval_h | int8 | from the file; not assumed to be 8 |
| source | str | `bulk_monthly`, `ccxt` |

### `manifest.json` (one per pair/tf, committed to git)

```json
{
  "pair": "BTCUSDT", "tf": "15m",
  "download_date": "2026-09-27",
  "files": [{"name": "BTCUSDT-15m-2020-01.zip", "sha256": "...", "rows": 2976, "source": "bulk_monthly"}],
  "ccxt_ranges": [{"start_ms": 1572566400000, "end_ms": 1577836800000, "rows": 5856}],
  "first_open_ms": 1572566400000, "last_open_ms": 1790000000000,
  "gaps": [{"start_ms": ..., "end_ms": ..., "missing": 3}],
  "overlap_mismatches": 0,
  "consistency_1m_15m": {"months_checked": 81, "mismatching_candles": 0}
}
```

## 1.3 Interfaces

```python
@dataclass(frozen=True, eq=False)   # eq=False: the generated __eq__ raises on ndarray fields
class Candles:                 # one pair, one timeframe, UTC, sorted, unique
    pair: str; tf: str
    ts: np.ndarray             # int64 ms, open time
    o: np.ndarray; h: np.ndarray; l: np.ndarray; c: np.ndarray; v: np.ndarray   # float64
    def __len__(self) -> int
    def index_at(self, ts_ms: int, *, exact: bool = False) -> int   # searchsorted(left); raises if absent when exact
    def slice(self, start_ms: int, end_ms: int) -> Candles   # [start, end)
    def step_ms(self) -> int                            # 60_000 or 900_000

@dataclass(frozen=True)
class Funding:
    pair: str
    ts: np.ndarray             # int64 ms, minute-rounded
    rate: np.ndarray           # float64
    interval_h: np.ndarray     # int8
    def events_between(self, a_ms: int, b_ms: int) -> slice   # indices with a <= ts <= b (inclusive both ends)

class HoldoutAccessError(RuntimeError): ...

class CandleStore:
    def __init__(self, data_cfg: DataConfig)
    def load(self, pair: str, tf: str, start_ms: int, end_ms: int, *, allow_holdout: bool = False) -> Candles
        # raises HoldoutAccessError if end_ms > insample_end (exclusive end of 2025-12-31) and not allow_holdout
        # start may be before insample_start (warmup)
    def write(self, pair, tf, frame: pd.DataFrame)          # partitions by year, idempotent
    def manifest(self, pair, tf) -> dict

class FundingStore:                                          # same guard and layout
    def load(self, pair, start_ms, end_ms, *, allow_holdout=False) -> Funding
```

The guard is API-level. The Parquet files for 2026 exist on disk after
`data fetch`; the human rule is that nothing reads them except through
`load(..., allow_holdout=True)`, which only `experiments/holdout.py` calls.

### SessionCalendar (`perpbt/data/sessions.py`)

```python
class SessionCalendar:
    def __init__(self, spec: SessionSpec, ts: np.ndarray)     # ts: 15m open times
    session_id: np.ndarray[int64]    # ordinal of the session-local calendar day; -1 outside any window
    open_ms: np.ndarray[int64]       # window open for the candle's session; -1 outside
    end_ms: np.ndarray[int64]        # window end (exclusive)
    in_window: np.ndarray[bool]      # open_ms <= ts < end_ms
    is_last: np.ndarray[bool]        # ts + 15m == end_ms
    def sessions(self) -> list[tuple[int, int, int]]          # (session_id, open_ms, end_ms) in order
    def eligible_days(self, start_ms, end_ms) -> np.ndarray   # session open instants inside [start, end)
```

Construction: for every session-local calendar day in the range whose
weekday is in `spec.days`, compute `open` and `close` as local wall-clock
instants with `zoneinfo` and convert to UTC ms. `"24:00"` means 00:00 of the
next local day. Every window boundary must land on a 15m grid point; the
constructor asserts this. On a DST change day the window still opens at the
local wall-clock time (NY 09:30 ET is 14:30 UTC in winter, 13:30 UTC in
summer). For `utc` the window is the UTC day and every day qualifies.

## 1.4 Validation (`perpbt/data/validate.py`)

- Timestamps strictly increasing and unique, on the timeframe grid.
- Gap report: every missing grid slot, coalesced into runs. Gaps are kept
  as gaps (no forward-fill). A 15m candle whose 1m constituents are partly
  missing is still used as published.
- 1m→15m consistency per month: aggregate 1m (first open, max high, min
  low, last close, sum volume) and compare to the published 15m bar; count
  mismatches beyond 1e-9 relative; write to the manifest. Mismatches are
  reported, not "fixed".
- Funding: timestamps unique after rounding; intervals in {1, 4, 8}; report
  any other value.

## 1.5 CLI

```
perpbt data fetch   [--pairs ...] [--tfs 1m 15m] [--from 2019-11-01] [--to today] [--ccxt-tail] [--ccxt-head]
perpbt data validate [--pairs ...]
```

`fetch` downloads what the manifest does not already have, verifies each
checksum, parses, merges, writes Parquet, deletes the zip, and updates the
manifest. Re-running is a no-op unless new daily files exist.

## 1.6 Tasks and tests

- **1.1 Bulk downloader and parser.**
  Tests: fixture CSVs in both header styles and both timestamp units parse
  to identical frames; a bad checksum raises and leaves no Parquet; a
  missing month (404) is recorded in the manifest and does not abort;
  re-running is idempotent (no re-download, no duplicate rows); zip deleted
  after conversion.
- **1.2 CandleStore with validation and holdout guard.**
  Tests: unsorted or duplicate input rejected at `write`; gap report on a
  synthetic series with a 45-minute hole lists one gap of 3 slots;
  `load(end > 2025-12-31)` raises `HoldoutAccessError` without
  `allow_holdout=True` and succeeds with it; a `load` whose start precedes
  `insample_start` succeeds (warmup); `slice` bounds are `[start, end)`.
- **1.3 1m→15m consistency check.**
  Tests: synthetic 1m aggregates exactly to 15m; a deliberately altered 15m
  high is reported as one mismatch. Slow: one real month per pair agrees.
- **1.4 FundingStore.**
  Tests: jittered timestamps (`…:00:00.001`, `…:59:59.998`) round to the
  minute; `interval_h` preserved, 4-hour files honoured; `events_between`
  inclusive on both ends; guard as in 1.2.
- **1.5 SessionCalendar.**
  Tests: NY opens 14:30 UTC on 2024-03-08 and 13:30 UTC on 2024-03-11;
  London opens 08:00 UTC on 2024-03-29 and 07:00 UTC on 2024-04-01; during
  2024-03-10..03-30 NY is on EDT while London is on GMT; weekday rule drops
  Saturday and Sunday for NY and London only; every window boundary is on
  the 15m grid; `is_last` is true exactly on the candle ending at the
  window end; UTC session id increments at 00:00; `eligible_days` respects
  the weekday rule; a spec whose close is not on the 15m grid raises.
- **1.6 ccxt head and tail fetch.**
  Tests (mocked ccxt): pages merge with the bulk frame without duplicates
  or gaps; bulk rows win on overlap; head backfill covers exactly
  `2019-11-01 .. 2019-12-31` for BTC and ETH and nothing for SOL; the
  manifest records ccxt ranges.

Exit criterion: `perpbt data fetch` and `validate` run end to end on the
real archive (slow test), manifests committed, gap and consistency counts
reviewed. Then merge into `dev` and delete the branch.
