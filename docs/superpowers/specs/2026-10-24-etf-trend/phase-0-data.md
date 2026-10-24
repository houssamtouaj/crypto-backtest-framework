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

*Amended in §0.9:* `cross_check.source` is `nasdaq`, and a `corrections` key names the
reviewed corrections file.

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
- **Stooq** *(replaced by Nasdaq, §0.9: Stooq now serves a bot check)* daily CSV (`https://stooq.com/q/d/l/?s=<ticker>.us&i=d`):
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
   in [0, 10]; gaps longer than 5 business days are errors. *(Amended in
   §0.9: to the trading day before the holdout end, values in [−1, 10].)*

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

## 0.9 Implementation notes (2026-10-24, branch `etf/0-data`)

**Fetched for real** on 2026-10-24 (UTC), then re-fetched with `--refetch` the same day after the final review
changed the corrections (the first store is in `data/etf/_old/2026-10-24/`; between the two downloads only
Yahoo's `adj_close` moved, by about 1e-6 relative; open, high, low, close, volume and dividends were identical).
`download_date` 2026-10-24, `holdout_end` 2026-10-23 (both in `configs/etf/data.yaml`). `data/etf/manifest.json`
sha256 `308f5012c5a274b62ee2d5f29e4b299cd225be3fa0cf2b02ff60a73f88c60658` (pinned to LF in `.gitattributes`, so the
hash survives a checkout). yfinance 1.7.0.

**Validation table** (`perpbt etf validate`, every rule passes):

| Rule | Result |
|---|---|
| 1 calendar | all four tickers on the same 5,517 days, 2004-11-18..2026-10-23; every missing weekday is a listed NYSE closure and the list equals the weekdays Yahoo lacks |
| 2 prices | pass |
| 3 splits | one: IWM 2:1 on 2005-06-09 (in the warmup); the split-adjusted close is continuous |
| 4 dividends | SPY and IWM: exactly 4 in every year 2005–2025 (now required). QQQ: 4 or 5 a year 2006–2025 (5 in 2011, 2014, 2023: year-end specials, the 2014 and 2023 ones confirmed by Nasdaq). No ex-date gap over 120 days (max 111, IWM 2023); no equal dividend repeated within 10 days. GLD: none. No capital gains |
| 5 adj close | share of days within 0.0005: SPY, IWM, GLD 100%; QQQ 99.96% (two days: 2020-09-21, the added dividend, and 2010-06-25, the removed duplicate, where Yahoo's Adj Close follows Yahoo's own dividend list; max 0.0020) |
| 6 cross-check | Nasdaq, raw basis for all four, 2016-10-24..2026-10-23 (2,514 days, coverage 100%). Closes: max difference 0.20% (SPY), 0.08% (IWM), 0.00% (QQQ), 0.87% (GLD, the accepted stale Nasdaq bar); share over 0.1%: 0.04% of days for SPY and GLD, 0 for the others. Opens: max 0.47% (SPY, after the corrections), 1.02% (IWM, accepted), 0.19% (QQQ), 0.63% (GLD, accepted). Dividends: QQQ 58 of 58 Nasdaq dividends (2012-06-15..2026-09-21) agree after the correction (QQQ must have this source: `cross_check.dividend_tickers`); SPY, IWM: no independent source |
| 7 rates | DTB3 2004-11-01..2026-10-22, 5,498 published days, no gap over 5 business days |

| Store file | Rows | First | Last |
|---|---|---|---|
| `<T>/prices.parquet` (each of SPY, QQQ, IWM, GLD) | 5,517 | 2004-11-18 | 2026-10-23 |
| `SPY/actions.parquet` | 88 | 2004-12-17 | 2026-09-18 |
| `QQQ/actions.parquet` | 90 | 2004-12-17 | 2026-09-21 |
| `IWM/actions.parquet` | 89 | 2004-12-23 | 2026-09-15 |
| `GLD/actions.parquet` | 0 | — | — |
| `rates/dtb3.parquet` | 5,498 | 2004-11-01 | 2026-10-22 |
| `nasdaq/<T>.parquet` (each) | 2,514 | 2016-10-24 | 2026-10-23 |
| `nasdaq/QQQ_dividends.parquet` | 58 | 2012-06-15 | 2026-09-21 |

**Builder's calls** (on the standing instruction to take the recommended option; the user can overturn any):

1. **Nasdaq replaces Stooq as the cross-check.** Stooq now answers the CSV URL with a JavaScript bot check; it is
   not bypassed. Nasdaq's public API (`api.nasdaq.com`) serves unadjusted daily OHLC for the last ten years only,
   and dividend histories for Nasdaq-listed symbols only (QQQ). So closes and opens are cross-checked from
   2016-10-24, and **SPY's and IWM's dividends have no independent check**: rules 4 (exactly 4 a year, every
   year) and 5 are their only checks. Index total-return series (`^SP500TR`, `^RUTTR`) were tried and rejected:
   daily tracking noise (99th percentile 0.5–0.8%) is larger than a dividend.
2. **Opens are cross-checked too** (same 0.5% tolerance), because fills are at the open (ED5).
3. **Reviewed corrections** (`configs/etf/corrections.yaml`, `perpbt/etf/corrections.py`). The real data showed
   errors in both sources; each was resolved by hand, with its reason in the file:
   - SPY 2016-11-07 (open, low) and 2017-05-17 (open, high): Yahoo copied the previous close into the open and
     one extreme (a stale print); Nasdaq's values are used.
   - QQQ 2020-09-21: Yahoo lacks the 0.38824 dividend; added from Nasdaq.
   - QQQ 2010-06-25: a duplicate of the 2010-06-18 dividend (0.089 both, a week apart); removed. Found by the
     final review; no source covers 2010, so this is a judgement (a duplicate would add about 0.2% to QQQ's
     in-sample total return).
   - Accepted, Yahoo kept: QQQ dividends 2012-09-21 and 2012-12-21 (missing from Nasdaq's history), IWM opens
     2017-12-05 and 2017-12-07 (1.0% and 0.65% apart, neither bar stale, no third source), GLD 2026-04-20 open
     and close (Nasdaq's bar is the previous close on all four prices, no volume).
   Every fix records the Yahoo value it replaces (`was`); the fetch refuses a fix whose `was` no longer matches.
   The fetch applies the fixes before validation; rule 6 lists the accepted differences and fails on one that no
   longer occurs. The manifest records the corrections and the hash of their parsed content (comments and line
   endings do not count); `perpbt etf validate` refuses a corrections file changed since the fetch.
4. **Rule 4:** QQQ is checked from 2006 (it paid once in 2004 and twice in 2005, then quarterly). Tighter than
   the spec's 4 ± 1 (final review): SPY and IWM exactly 4 a year, QQQ 4–5; at most 120 days between ex-dates and
   from the last one to the last price day (so the partial last year counts); an equal dividend within 10 days
   fails as a likely duplicate. Rule 5 cannot catch these: Yahoo's Adj Close is built from its own dividend list.
5. **Rule 7:** the rates must reach the trading day before `holdout_end` (the last rate any cash day uses, Phase 1
   §1.3), since FRED publishes a day late; values in [−1, 10], since DTB3 printed −0.01 to −0.05 on eight days
   in 2015 and March 2020. Phase 1 uses those negative rates as they are.
6. **Rule 1 extras:** each ticker's first date must be `warmup_start`; prices on a listed closure are an error; the
   closure list runs to 2027 and the rule fails past it.
7. **Capital gains** are kept in `actions.parquet` (`capital_gain`) and must be zero (none in the data).
8. **A failed validation writes nothing** (not the store, not the data.yaml dates). **Any existing store needs
   `--refetch`**, the same download day included (the spec allowed a same-day overwrite; the snapshot may be
   frozen). Every load checks each parquet file's sha256 against the manifest (the parquet files are not
   committed). `perpbt etf validate` also refuses a config whose dates differ from the manifest's.
8a. **Rule 6 extras:** the cross-check prices must end on Yahoo's last day and span `min_span_days` (3,450);
   missing cross-check opens fail like missing closes; a Nasdaq error reply raises instead of reading as "no
   dividend history".
9. **The rates load is guarded** like the prices (`end ≥ 2018-01-01` needs `allow_holdout`).
10. **`yfinance==1.7.0` is an optional extra** (`pip install .[etf]`), imported only inside the fetch.
11. **FRED needs a plain user agent** (a browser one times out); Nasdaq needs a browser one.

**Known data limits (for Phases 1–3):**

- About 1% of Yahoo's opens equal the previous close (SPY 49, QQQ 62, IWM 57, GLD 39 days over 2004–2026). Where
  Nasdaq can check them (2016-10 on), about half of SPY's are wrong (stale) by 0.1–1.4%, while QQQ's agree.
  Only the two over 0.5% were corrected; before 2016-10 nothing can be checked. Fills at the open on those days
  carry the error; the `fill_close` exploratory row shows the sensitivity.
- The holdout's GLD 2026-04-20 bar comes from Yahoo alone.
- **For Phase 5:** forward runs need data past this snapshot, but `--refetch` retires the frozen store (and its
  manifest hash, which the prereg pins). Phase 5 needs its own forward-data path that leaves `data/etf/` as frozen.
