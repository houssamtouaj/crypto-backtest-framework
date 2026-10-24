# ETF Phase 0 — Data Implementation Plan

> Working document; executed natively (inline) with one final fresh review, per the standing workflow.

**Goal:** Daily prices, dividends and splits for SPY, QQQ, IWM, GLD from Yahoo, T-bill rates from FRED, an
independent cross-check, a validated local store with a manifest, `configs/etf/data.yaml`, and the holdout guard
— no strategy logic.

**Architecture:** new subpackage `perpbt/etf/`: `config.py` (`EtfDataConfig`), `sources.py` (the only network
module; pure parsers over raw bytes), `store.py` (`EtfStore`, `EtfData`, the holdout guard), `validate.py`
(rules 1–7 of spec §0.4 on in-memory frames), `fetch.py` (download → validate → write → set the data.yaml
dates). CLI group `perpbt etf {fetch,validate}`.

**Spec:** `docs/superpowers/specs/2026-10-24-etf-trend/phase-0-data.md` with `00-overview.md`.

## Facts found before the plan (2026-10-24)

- Yahoo via yfinance 1.7.0: 5,517 rows per ETF, 2004-11-18..2026-10-23; IWM 2:1 split on 2005-06-09; a
  `Capital Gains` column, zero for all four; GLD no dividends.
- FRED `fredgraph.csv?id=DTB3`: works with a plain user agent (a browser user agent times out); missing values
  are empty fields; the last value is 2026-10-22, one trading day before the holdout end.
- Stooq now serves a JavaScript bot check instead of the CSV. Not bypassed.
- Nasdaq's public API (`api.nasdaq.com`): daily OHLC for the last ten years only (2,514 rows), unadjusted;
  dividend history for Nasdaq-listed symbols only (QQQ, from 2012), "not available" for SPY, IWM, GLD.
- Index total-return series (`^SP500TR`, `^RUTTR` on Yahoo) track the ETFs too loosely (daily 99th percentile
  0.5–0.8%) to check a dividend; not used.

## My calls (listed for the user)

1. **Cross-check source: Nasdaq instead of Stooq** (Stooq is unreachable). Prices: the last ten years, all four
   ETFs; basis detected (raw vs adjusted) and recorded in the manifest. Dividends: every ex-date and amount
   where Nasdaq serves a history (QQQ). SPY's and IWM's dividends have no independent check; rules 4 and 5
   stay their only checks, and the notes say so. `cross_check.source: nasdaq` in data.yaml.
2. **Rule 7 end:** the rates must reach the trading day before `holdout_end` (the last rate any cash day uses,
   Phase 1 §1.3), not `holdout_end` itself: FRED publishes a day late.
3. **Rule 1 extras:** each ticker's first date is `warmup_start`; a listed NYSE closure with a price row is an
   error too.
4. **Capital gains** are kept in `actions.parquet` (`capital_gain`) and must be zero (rule 4); the total return
   would miss them.
5. **A failed validation writes nothing** (stricter than "no manifest").
6. **The rates load is guarded too** (`end ≥ 2018-01-01` needs `allow_holdout`).
7. **`yfinance==1.7.0` is an optional extra** `etf` (as `ccxt` is), imported lazily so the suite runs without it.
8. **Holdout start is checked in the config** against the store constant.

## Global constraints

- No edit to any file in `CARRY_CODE_PATHS` or `perpbt/trend/`. A test pins `carry_code_version()` to the lock.
- `allow_holdout=True` appears in `perpbt/etf/` only in `holdout.py`, `forward.py`, `validate.py` (grep test).
- Day keys are UTC int64 ms of the exchange date (`day_ms`).
- Tests use recorded fixtures; no network.

## Tasks

- [ ] 1. `etf/config.py` + `configs/etf/data.yaml` + tests.
- [ ] 2. `etf/sources.py`: retry, parsers (Yahoo CSV, FRED CSV, Nasdaq history and dividends JSON), fetchers +
  fixture tests.
- [ ] 3. `etf/store.py`: `EtfData`, atomic writes, manifest, refetch/`_old`, guarded loads + tests.
- [ ] 4. `etf/validate.py`: NYSE closures, rules 1–7, report table + one failing synthetic case per rule.
- [ ] 5. `etf/fetch.py` + CLI `perpbt etf fetch|validate` + data.yaml date update + tests with fake sources.
- [ ] 6. Guards: grep test, carry lock test.
- [ ] 7. Real fetch, validate, record the notes in spec §0.9.
- [ ] 8. Carry tests: run against the pre-lock copy of the committed prereg (separate commit, tests only).
- [ ] 9. Full suite, fresh review, fixes.

## Progress log
