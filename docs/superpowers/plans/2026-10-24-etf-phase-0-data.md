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

- [x] 1. `etf/config.py` + `configs/etf/data.yaml` + tests.
- [x] 2. `etf/sources.py`: retry, parsers (Yahoo CSV, FRED CSV, Nasdaq history and dividends JSON), fetchers +
  fixture tests.
- [x] 3. `etf/store.py`: `EtfData`, atomic writes, manifest, refetch/`_old`, guarded loads + tests.
- [x] 4. `etf/validate.py`: NYSE closures, rules 1–7, report table + one failing synthetic case per rule.
- [x] 5. `etf/fetch.py` + CLI `perpbt etf fetch|validate` + data.yaml date update + tests with fake sources.
- [x] 6. Guards: grep test, carry lock test.
- [x] 7. Real fetch, validate, record the notes in spec §0.9.
- [x] 8. Carry tests: run against the pre-lock copy of the committed prereg (separate commit, tests only).
- [x] 9. Full suite, fresh review, fixes.

## Progress log

- 2026-10-24, before the code: probes found Stooq behind a bot check, FRED needing a plain user agent, Nasdaq's
  API serving ten years of prices and QQQ-only dividends (Facts above).
- First real dry run (into the scratchpad) failed four rules on real data: QQQ paid twice in 2005; negative
  T-bill prints in 2015/2020; Yahoo lacking QQQ's 2020-09-21 dividend while Nasdaq lacks two 2012 ones; stale
  Yahoo opens on two SPY days, a stale Nasdaq GLD bar, two undecidable IWM opens. Resolved by calls 4–5 of spec
  §0.9 and a reviewed `configs/etf/corrections.yaml` (new module `etf/corrections.py`, not in the spec's module
  list). Stale opens measured: about 1% of Yahoo's days.
- The corrections hash is over the parsed content, not the bytes: git turns the YAML's LF into CRLF on checkout.
  `data/etf/manifest.json` is pinned to LF in `.gitattributes` for the same reason (Phase 2 pins its sha256).
- Real fetch 2026-10-24: every rule passes; `perpbt etf validate` passes; notes in spec §0.9. Commits b066172,
  aa2d18c.
- Baseline before this branch: 27 failed + 30 errors, all in `tests/carry/` — the Phase 7 code lock set the
  committed prereg's forward start and the lock's code fields, and the tests copied those files. Fixed in
  tests only (`tests/carry/prelock.py`), 48a3584; carry code untouched, carry code version still 7ce43816.
- `tests/test_holdout.py`'s repo-wide grep now admits `etf/validate.py` (c148ae6).
- Full suite: 1,555 passed + the grep test fixed after (1 failure before c148ae6).
- Fresh review (Opus): "merge after fixes"; constraints verified (no locked file touched, guard, nothing written
  on failure). Fixed: Important 1 (rule 4 exactly 4 a year for SPY/IWM, QQQ 4–5, 120-day gap incl. the partial
  year, duplicate check), 2 (QQQ 2010-06-25 duplicate removed by a correction), 3 (parquet sha256 checked on
  every load), 4 (`cross_check.dividend_tickers`; a Nasdaq error reply raises); Minor 5 (`min_span_days`, end
  on Yahoo's last day), 6 (missing opens fail), 7 (`was` on every fix, stale fixes refused; accepted
  differences still have no size bound: declined, each is one reviewed day), 8 (validate checks the config
  dates and a recorded basis), 9 (any existing store needs `--refetch`), 10 (manifest retired first), 11
  (token-based grep), 12 (CLI catches ValueError). Re-fetched with `--refetch` (only `adj_close` noise moved).
  The forward-data pointer for Phase 5 is in spec §0.9.
