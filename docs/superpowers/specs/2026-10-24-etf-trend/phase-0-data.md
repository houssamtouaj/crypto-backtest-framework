# Phase 0 — Data

Read with `00-overview.md`. Delivers daily prices, dividends and splits for
the four ETFs, T-bill rates, a cross-check against a second source, a
validated local store with manifests, the ETF data config, and the holdout
guard.

**Branch:** `etf/0-data`, from `strategy/etf-trend-spec`.

## 0.1 Data config (`configs/etf/data.yaml`)

```yaml
data_dir: data/etf
tickers: [SPY, QQQ, IWM, GLD]
warmup_start: "2004-11-18"     # GLD's first trading day; the common start
insample_start: "2005-08-01"   # after the 160-bar warmup (about 2005-07-13)
insample_end: "2017-12-31"
holdout_start: "2018-01-01"
holdout_end: null              # set by the fetch: the last full US trading day before the download day
download_date: null            # set by the fetch (UTC date)
rates: {source: fred, series: DTB3}
cross_check: {source: stooq, close_tol: 0.005}
```

`EtfDataConfig` (frozen dataclass) loads it with the duplicate-key loader,
validates exact keys and date order, and refuses a `holdout_end` before
`holdout_start`.

## 0.2 Sources (`etf/sources.py`)

The only module that touches the network. Each function returns a pandas
frame and the raw bytes it parsed (for the manifest hash).

- **Yahoo** via `yfinance` (pinned in `pyproject.toml`):
  `Ticker(t).history(start=warmup_start, end=download_date, interval="1d",
  auto_adjust=False, actions=True)`. Keep `Open, High, Low, Close, Volume,
  Dividends, Stock Splits`, and Yahoo's `Adj Close` (for the consistency
  check only). Yahoo's `Close` and `Open` are split-adjusted; `Dividends`
  are per share on the split-adjusted basis. Dates are exchange dates.
- **Stooq** daily CSV (`https://stooq.com/q/d/l/?s=<ticker>.us&i=d`):
  split-adjusted close, used only to cross-check (§0.4). If Stooq is
  unreachable or needs a key, the fetch records that, runs the internal
  `Adj Close` consistency check instead, and this phase's notes say so
  *(my call)*.
- **FRED** `DTB3` CSV
  (`https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3`): the 3-month
  T-bill secondary market rate, percent, discount basis. Missing values
  (`.`) are holidays.

Network calls retry three times with backoff, then raise; nothing falls
back silently to another source.

## 0.3 Store (`etf/store.py`)

```
data/etf/
  SPY/prices.parquet        date, open, high, low, close, volume, adj_close
  SPY/actions.parquet       date, dividend, split
  ...
  rates/dtb3.parquet        date, rate_pct
  stooq/SPY.parquet         date, close   (when available)
  manifest.json             per file: source, url/params, download time, rows, first/last date, sha256 of the raw bytes and of the parquet
```

Writes are atomic (temp file and rename). A re-fetch never overwrites a
store whose manifest has a different `download_date` unless `--refetch` is
given; the old store is then moved to `data/etf/_old/<download_date>/`.
After the prereg freeze the store is the frozen snapshot: the code lock
(Phase 5) records the manifest's hash, and every run checks it.

## 0.4 Validation (`etf/validate.py`)

A fetch passes only if every rule holds; each failure names the ticker and
date.

1. **Calendar.** All four tickers have the same set of dates from
   2004-11-18 on (they are all NYSE Arca listed). A date missing from one
   ticker only is an error. A weekday that no ticker has must be a NYSE
   holiday or market closure (a fixed list in the module, including
   2007-01-02, 2012-10-29/30, 2018-12-05 and 2025-01-09); anything else is an
   error.
2. **Prices.** All prices positive and finite; `low ≤ min(open, close)`,
   `high ≥ max(open, close)`; no day-over-day close move above 25% unless a
   split is recorded that day.
3. **Splits.** Every `split ≠ 0` is listed and must leave the split-adjusted
   close continuous (move under 25%). Splits inside the window are expected
   (IWM likely split 2-for-1 in June 2005, during the warmup); fixtures and
   validation must not assume there are none.
4. **Dividends.** SPY, QQQ, IWM pay quarterly: each calendar year from 2005
   through the last full year has 4 dividends ± 1 (special distributions
   happen). GLD pays none; a GLD dividend is an error.
5. **Adj Close consistency.** Total return from `close` and `dividend`
   (Phase 1 §1.2) and Yahoo's `adj_close` returns agree within 0.0005 on
   99.5% of days and within 0.005 on every day. This catches a missing or
   doubled dividend.
6. **Cross-check.** It is the only independent check of the dividends, so
   it is not switched off. On the first fetch, determine Stooq's basis
   (split-only or split-and-dividend adjusted) from a known ex-date and
   record it. Split-only: compare Stooq's close with Yahoo's `close`.
   Dividend-adjusted: compare **daily returns** — Stooq's against the
   repo's total return (Phase 1 §1.2) — since adjusted levels drift with
   later dividends. Either way: every common day under `close_tol` (0.5%,
   on levels or on returns), and the share of days over 0.1% reported.
7. **Rates.** `DTB3` covers 2004-11-01 to at least the holdout end; values
   in [0, 10]; gaps longer than 5 business days are errors.

`perpbt etf validate` runs the rules on the store and prints a table; the
fetch runs it automatically and refuses to write the manifest on failure.

## 0.5 Holdout guard

`EtfStore.load(ticker, start, end, *, allow_holdout=False)` refuses any
`end ≥ 2018-01-01` without `allow_holdout=True`. The date is a constant in
`etf/store.py`, not read from the config, so a wrong config cannot open the
holdout (the carry loader's pattern). A test greps `perpbt/etf/` for
`allow_holdout=True` and allows it only in `holdout.py`, `forward.py` and
`validate.py`.

## 0.6 CLI

- `perpbt etf fetch` — download all sources, validate, write the store and
  manifest, set `holdout_end` and `download_date` in `data.yaml`.
- `perpbt etf validate` — re-run §0.4 on the store.

## 0.7 Tests

- Sources: parsing from recorded fixtures (no network in tests); retry and
  raise on failure.
- Store: atomic write, manifest hashes, refusal to overwrite without
  `--refetch`.
- Validation: one synthetic failure per rule, plus a clean synthetic pass.
- Holdout guard: refusal, the grep test.
- The carry lock guard (overview §7): `carry_code_version()` equals the
  locked value.

## 0.8 Exit criterion

Store fetched once for real; `perpbt etf validate` passes; the validation
table, each ticker's first and last date, row counts, dividend counts per
year, cross-check results, and `holdout_end` recorded in this file's
implementation notes. Tests green, the carry guard included.
