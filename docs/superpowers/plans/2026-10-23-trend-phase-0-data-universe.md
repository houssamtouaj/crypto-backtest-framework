# Trend Phase 0 — Data and Universe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spot-archive support in the shared data layer (backward compatible), the point-in-time universe for perps and spot written to `configs/trend/universe.json`, the two trend data configs, and the fetched, validated 15m data (plus perp funding) for every member.

**Architecture:** `bulk.py` gains a `market` argument (`"um"` default, `"spot"`) for kline URLs and a per-market archive start. `CandleStore` takes `market`; spot lives under `data/spot/candles/` and refuses every `load` without `allow_holdout=True`. `fetch.py`/`validate.py` thread `market` through; spot skips funding and ccxt. The new `perpbt/trend/universe.py` lists the archive's S3 bucket, reads monthly **1d** klines in memory (never stored), and ranks candidates by window quote volume. `perpbt trend universe --market um|spot` writes one market's block of `universe.json`.

**Tech Stack:** Python 3.11, numpy, pandas, `urllib`, `xml.etree.ElementTree` for the S3 listing, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-23-trend-following/phase-0-data-universe.md`, read with `00-overview.md` (TD4, TD11, §6, §7, §9, §11 item 9). Code this builds on: `perpbt/data/{bulk,store,fetch,validate}.py`, `perpbt/cli.py`, `tests/fake_archive.py`, `tests/fake_exchange.py`.

## Global Constraints

- Branch `trend/0-data-universe`. It starts from `strategy/trend-following-spec` (= `dev` + the two spec commits), so the approved spec reaches `dev` with this phase's `--no-ff` merge. Ask the user before that merge.
- Shared modules change only backward compatibly: every default (`market="um"`) reproduces today's URLs, paths and behaviour; the existing suite stays green and is the regression guard (TD11).
- Spot kline URL: `https://data.binance.vision/data/spot/{monthly|daily}/klines/<SYM>/<tf>/<SYM>-<tf>-<period>.zip`. Perp: unchanged `.../data/futures/um/...`. Spot has no funding files.
- Archive start per market: `um` 2020-01-01 (unchanged), `spot` 2017-08-01.
- Store roots: perps `data/candles/<PAIR>/<tf>` (unchanged), spot `data/spot/candles/<PAIR>/<tf>`. Manifests as today.
- Spot guard: `CandleStore(cfg, market="spot").load(...)` raises `HoldoutAccessError` unless `allow_holdout=True`, whatever the dates. No code under `perpbt/` passes `allow_holdout=True` in this phase (the `test_holdout.py` grep test stays as is).
- Universe ranking reads 1d klines in memory only; it returns volume and coverage, never prices; nothing is written to the store.
- Perp rule: candidates = USDT-M perpetuals (`*USDT`, no `_`) with a 1d kline for every day 2020-01-01..2020-06-30; rank by summed `quote_volume` over those days; ties by symbol name; top 10; trading from 2020-07-01. Delisted symbols count.
- Spot rule: `*USDT` spot pairs with a 1d kline for every day 2017-12-01..2017-12-31; same ranking; up to 10.
- Exclusions (both markets): stablecoin bases `USDC, TUSD, PAX, USDP, BUSD, USDS, USDSB, DAI, FDUSD, UST, USTC, EUR, GBP, AUD`; leveraged tokens (base ends in `UP`, `DOWN`, `BULL`, `BEAR` with at least 3 characters left, so `JUPUSDT` is not one).
- `--ccxt-tail` and `--ccxt-gaps` skip a perp whose stored 15m series ends more than 7 days before today (delisted; ccxt may not know it), with a log line.
- Do not run any first-strategy command (`perpbt run`, `grid`, `baselines`, `holdout`, `stats`) on this tree (overview §11 item 9).
- Real fetches: one pair per process, sequentially, in the background (memory, Phase 1 note). Commands for Git Bash from the repo root.

## Review Focus

1. **The spot store is holdout data end to end.** A spot `load` with dates well inside `insample_*` must still raise without `allow_holdout=True`. Pinned in Task 2 by `test_spot_load_refused_without_allow_holdout`.
2. **Perp paths must not move.** `CandleStore(cfg)` must keep `data/candles/<PAIR>/<tf>` or the existing BTC/ETH files and committed manifests are orphaned and re-downloaded. Pinned in Task 2 by `test_default_market_paths_unchanged`.
3. **The S3 listing is paginated (1,000 entries).** Ignoring `IsTruncated` silently drops every symbol after `W…`, including delisted ones. Pinned in Task 4 by `test_listing_follows_markers` (fake page size 2).
4. **Dated quarterlies and non-USDT perps** (`BTCUSDT_200925`, `BTCBUSD`) must not enter the perp ranking. Pinned in Task 4 by `test_symbol_filter`.
5. **A pair listed mid-window** has a monthly file for every window month but misses days; it must be dropped, not ranked on partial volume. Pinned in Task 4 by `test_incomplete_coverage_dropped`.

Also pinned: `trend universe --market spot` keeps the `um` block of an existing `universe.json` (Task 5); `JUPUSDT` is not a leveraged token (Task 4).

---

### Task 1: Branch; spot URLs and per-market archive start in `bulk.py`

**Files:**
- Modify: `perpbt/data/bulk.py`, `tests/fake_archive.py`
- Test: `tests/test_bulk.py`

**Interfaces:**
- Produces: `MARKETS = ("um", "spot")`; `BASE_URLS: dict[str, str]`; `ARCHIVE_STARTS = {"um": date(2020, 1, 1), "spot": date(2017, 8, 1)}`; `BASE_URL` and `ARCHIVE_START` kept as the `um` values; `market_base(market: str) -> str` (raises `ValueError` on an unknown market); `kline_url(pair, tf, period, market="um") -> str`; `funding_url` unchanged (perps only). `FakeArchive.add_klines(..., market="um")`.

- [ ] **Step 1:** `git switch -c trend/0-data-universe strategy/trend-following-spec`; commit this plan; `git push -u origin trend/0-data-universe`.
- [ ] **Step 2: Failing tests** in `tests/test_bulk.py`:
  - `test_spot_kline_url`: `kline_url("BTCUSDT", "15m", "2017-08", market="spot") == "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/15m/BTCUSDT-15m-2017-08.zip"`; daily period gives `/data/spot/daily/klines/...`.
  - `test_default_market_url_unchanged`: `kline_url("BTCUSDT", "15m", "2020-01") == kline_url(..., market="um")` and starts with `BASE_URL`.
  - `test_archive_start_per_market`: `ARCHIVE_STARTS["spot"] == date(2017, 8, 1)`, `ARCHIVE_START == ARCHIVE_STARTS["um"] == date(2020, 1, 1)`.
  - `test_unknown_market_raises`: `kline_url(..., market="coinm")` raises `ValueError`.
- [ ] **Step 3:** Run `pytest tests/test_bulk.py -q` → the four fail.
- [ ] **Step 4:** Implement in `bulk.py`; add the `market` keyword to `FakeArchive.add_klines`.
- [ ] **Step 5:** `pytest tests/test_bulk.py tests/test_fetch.py -q` → all pass. Commit `Trend phase 0: spot kline URLs and per-market archive start`.

### Task 2: Per-market `CandleStore` and the spot guard

**Files:**
- Modify: `perpbt/data/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `CandleStore(data_cfg, market: str = "um")` with attribute `market`; `root` = `<data_dir>/candles` for `um`, `<data_dir>/spot/candles` for `spot`; `load` raises `HoldoutAccessError` for `spot` unless `allow_holdout=True`. `FundingStore` unchanged (perps only).

- [ ] **Step 1: Failing tests** in `tests/test_store.py`:
  - `test_default_market_paths_unchanged`: `CandleStore(data_cfg).dir("BTCUSDT", "15m") == Path(data_cfg.data_dir) / "candles" / "BTCUSDT" / "15m"` and `.market == "um"`.
  - `test_spot_store_root`: `CandleStore(data_cfg, market="spot").dir("BTCUSDT", "15m") == Path(data_cfg.data_dir) / "spot" / "candles" / "BTCUSDT" / "15m"`; a write there leaves `<data_dir>/candles` absent.
  - `test_spot_load_refused_without_allow_holdout`: write 2018 rows to the spot store; `load("BTCUSDT", "15m", date_ms("2018-01-01"), date_ms("2018-01-02"))` raises `HoldoutAccessError`; with `allow_holdout=True` returns the rows.
  - `test_unknown_market_raises`: `CandleStore(data_cfg, market="coinm")` raises `ValueError`.
- [ ] **Step 2:** Run them → fail.
- [ ] **Step 3:** Implement; the spot refusal message names the market ("the spot store is holdout data").
- [ ] **Step 4:** `pytest tests/test_store.py -q` → pass. Commit `Trend phase 0: per-market candle store root and spot holdout guard`.

### Task 3: `market` through fetch, validate and the `data` CLI; delisted perps skip ccxt

**Files:**
- Modify: `perpbt/data/fetch.py`, `perpbt/data/validate.py`, `perpbt/cli.py`
- Test: `tests/test_fetch.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 1 `kline_url(..., market=)`, `ARCHIVE_STARTS`; Task 2 `CandleStore(cfg, market=)`.
- Produces: `sync_candles` uses `store.market` for URLs and archive start; `run_fetch(..., market: str = "um")`; `run_validate(data_cfg, pairs, *, tfs=TIMEFRAMES, market="um")`; `DELISTED_AFTER_DAYS = 7`; `is_stale(manifest: dict, today: date) -> bool` (True when `last_open_ms` is set and before `date_ms(today) - 7 days`). CLI: `perpbt data fetch|validate --market {um,spot}` (default `um`).

- [ ] **Step 1: Failing tests** in `tests/test_fetch.py`:
  - `test_spot_fetch_writes_spot_store_and_no_funding`: a `FakeArchive` with spot 2017-12 and 2018-01 15m monthlies for `BTCUSDT` (listing `2017-08-17`); `run_fetch(cfg, pairs=["BTCUSDT"], tfs=("15m",), market="spot", from_date=date(2017, 12, 1), to_date=date(2018, 1, 31), today=TODAY, http_get=archive)` → rows under `data/spot/candles/BTCUSDT/15m`, nothing under `data/candles` or `data/funding`, no `fundingRate` URL in `archive.calls`, summary has no `"funding"` key.
  - `test_spot_months_before_archive_start_not_requested`: `from_date=date(2017, 1, 1)` requests nothing before `2017-08`.
  - `test_spot_refuses_ccxt_flags`: `market="spot", ccxt_tail=True` raises `ValueError`.
  - `test_stale_pair_skips_ccxt_tail_and_gaps`: stored 15m ending 30 days before `TODAY`, with one gap; `run_fetch(..., ccxt_tail=True, ccxt_gaps=True, exchange_factory=lambda: ex)` makes no exchange call for it and reports `entry["ccxt_skipped"] == "delisted"`; a fresh pair still gets its tail.
  - `test_validate_spot`: after the spot fetch, `run_validate(cfg, ["BTCUSDT"], market="spot")` reports `15m` rows and no `funding` key, and writes the spot manifest.
  - In `tests/test_cli.py`, `test_data_fetch_market_flag_parses`: `build_parser().parse_args(["data", "fetch", "--market", "spot"]).market == "spot"`; default `"um"`.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement. `sync_funding` is not called for spot. `fetch_head` stays perp-only (spot raises with ccxt flags anyway).
- [ ] **Step 4:** `pytest tests/test_fetch.py tests/test_validate.py tests/test_cli.py -q` → pass. Commit `Trend phase 0: market argument for fetch and validate; ccxt skips delisted perps`.

### Task 4: `perpbt/trend/universe.py` — archive listing and ranking

**Files:**
- Create: `perpbt/trend/__init__.py`, `perpbt/trend/universe.py`, `tests/trend/__init__.py`, `tests/trend/test_universe.py`
- Modify: `tests/fake_archive.py` (S3 listing responses)

**Interfaces:**
- Consumes: `bulk.kline_url(..., market=)`, `bulk.download_verified`, `bulk.parse_klines_zip`, `bulk.months_between`, `bulk.HttpGet`, `bulk.urllib_get`.
- Produces:
  - `LISTING_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"`.
  - `list_archive(prefix: str, *, delimiter: str | None = "/", http_get: HttpGet = urllib_get) -> tuple[list[str], list[str]]` → `(common_prefixes, keys)` over all pages (`marker` = `NextMarker`, else the last key/prefix, while `IsTruncated` is `true`).
  - `archive_symbols(market: str, *, http_get) -> list[str]` (sorted, from `data/<root>/monthly/klines/`).
  - `exclusion(pair: str, market: str) -> str | None` → `"not USDT"`, `"dated future"`, `"stablecoin"`, `"leveraged token"` or None.
  - `@dataclass(frozen=True) class UniverseRule: market, window_start, window_end, top_n, text` and `RULES = {"um": ..., "spot": ...}` (um 2020-01-01..2020-06-30, top 10; spot 2017-12-01..2017-12-31, top 10).
  - `rank_universe(market: str, *, today: date, http_get: HttpGet = urllib_get) -> dict` → `{"rule": str, "window": [start, end], "ranked": [{"pair", "quote_volume", "first_day", "last_day"}], "dropped": [{"pair", "reason"}], "members": [...], "computed_on": today.isoformat()}`. `ranked` sorted by `(-quote_volume, pair)`; `members = ranked[:top_n]` pairs. `first_day` = first row of the symbol's earliest monthly 1d file; `last_day` = last row of its latest monthly 1d file. `dropped` lists symbols that had at least one window month but failed coverage (`"missing days: N"` / `"missing month YYYY-MM"`) or an exclusion; symbols with no window month are not listed.
- FakeArchive: `FakeArchive(page_size=1000)`; a GET on `LISTING_URL?...` answers S3 `ListBucketResult` XML built from its stored URLs (keys = URL minus `https://data.binance.vision/`), honouring `prefix`, `delimiter`, `marker`, `page_size`; `NextMarker` only when a delimiter is given (as S3 does).

- [ ] **Step 1: Failing tests** in `tests/trend/test_universe.py` (synthetic 1d rows: one row per day with chosen `quote_volume`):
  - `test_listing_follows_markers`: 5 symbols, `page_size=2` → `archive_symbols("um")` returns all 5; the archive saw 3 listing GETs.
  - `test_symbol_filter`: `exclusion("BTCUSDT_200925", "um") == "dated future"`, `exclusion("BTCBUSD", "um") == "not USDT"`, `exclusion("USDCUSDT", "spot") == "stablecoin"`, `exclusion("BTCUPUSDT", "spot") == "leveraged token"`, `exclusion("ETHBEARUSDT", "spot") == "leveraged token"`, `exclusion("JUPUSDT", "spot") is None`, `exclusion("BTCUSDT", "um") is None`.
  - `test_ranking_and_ties`: three full-coverage um symbols with volumes 5, 7, 7 → ranked `["AAAUSDT", "BBBUSDT", "CCCUSDT"]` order by volume desc then name (the two 7s alphabetical); `quote_volume` is the window sum only (a July 2020 row is ignored).
  - `test_incomplete_coverage_dropped`: a symbol whose 2020-01 file starts on 2020-01-15 → absent from `ranked`, in `dropped` with reason `"missing days: 14"`; a symbol without a 2020-03 file → `"missing month 2020-03"`.
  - `test_delisted_symbol_kept`: a symbol whose last monthly file is 2021-06 is ranked with `last_day == "2021-06-30"`.
  - `test_exclusions_applied_in_ranking`: a full-coverage `USDCUSDT` on spot is in `dropped` with `"stablecoin"`, not in `ranked`.
  - `test_top_n`: 12 full-coverage symbols → 10 members, 12 ranked.
  - `test_no_prices_returned`: every `ranked` entry has exactly the keys `pair, quote_volume, first_day, last_day`.
- [ ] **Step 2:** Run → fail (module missing).
- [ ] **Step 3:** Implement. Per symbol: one listing of `data/<root>/monthly/klines/<SYM>/1d/` (keys ending `.zip`), then download only what the rule needs (window months; earliest and latest month for passing candidates).
- [ ] **Step 4:** `pytest tests/trend -q` → pass. Commit `Trend phase 0: point-in-time universe ranking from the archive's 1d klines`.

### Task 5: `perpbt trend universe` CLI

**Files:**
- Modify: `perpbt/cli.py`, `perpbt/trend/universe.py`
- Test: `tests/trend/test_universe.py`

**Interfaces:**
- Consumes: Task 4 `rank_universe`.
- Produces: `write_universe(path: Path, market: str, block: dict) -> None` (merges into an existing file, keeps other markets, `json.dump(indent=2, sort_keys=True)` + newline, LF). CLI `perpbt trend universe --market {um,spot} [--out configs/trend/universe.json]`, prints the members and counts; exit 1 on `DownloadError`/`ChecksumError`.

- [ ] **Step 1: Failing tests:** `test_write_universe_keeps_other_market` (write `um` then `spot` → both keys present, `um` unchanged); `test_cli_trend_universe` (monkeypatch `perpbt.trend.universe.urllib_get` with a `FakeArchive`; `main(["trend", "universe", "--market", "um", "--out", str(tmp)])` returns 0 and the file's `um.members` matches).
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement (the CLI passes no `http_get`, so `rank_universe` must look up `urllib_get` at call time: default `http_get=None` → `urllib_get`).
- [ ] **Step 4:** `pytest tests/trend tests/test_cli.py -q` → pass. Commit `Trend phase 0: perpbt trend universe`.

### Task 6: Resolve the universe; the trend data configs

**Files:**
- Create: `configs/trend/universe.json`, `configs/trend/data.yaml`, `configs/trend/data-spot.yaml`, `tests/trend/test_configs.py`

- [ ] **Step 1:** Run `perpbt trend universe --market um` then `--market spot` (network; log times). Read both blocks: check BTC/ETH ranked, SOL absent, the `dropped` list plausible, spot members ≤ 10 (BCCUSDT expected).
- [ ] **Step 2:** Write `configs/trend/data.yaml`: `data_dir: data`, `insample_start: "2020-01-01"`, `insample_end: "2026-10-20"`, `holdout_start: "2026-10-21"`, `holdout_end: null`, `warmup_start: "2020-01-01"`, `listing`: every `um` member → its `first_day`. Write `configs/trend/data-spot.yaml`: `insample_start`/`warmup_start` `"2017-08-01"`, `insample_end: "2019-12-31"`, `holdout_start: "2020-01-01"`, `holdout_end: null`, `listing`: every spot member → its `first_day`. Header comment on each naming the spec section.
- [ ] **Step 3: Tests** in `tests/trend/test_configs.py`: both load as `DataConfig`; `sorted(listing) == sorted(universe[market]["members"])` and each listing date equals that member's `first_day`; `um` members exclude `SOLUSDT`; `configs/data.yaml` is byte-identical to `git show dev:configs/data.yaml` (skip if git is unavailable).
- [ ] **Step 4:** `pytest tests/trend -q` → pass. Commit `Trend phase 0: universe resolved (um, spot) and the trend data configs`.

### Task 7: Fetch, validate, review gaps

- [ ] **Step 1:** Perps, background, one pair per process: `for p in <um members>; do perpbt data fetch --config configs/trend/data.yaml --tfs 15m --pairs $p --ccxt-tail --ccxt-gaps; done`.
- [ ] **Step 2:** Spot, same way: `perpbt data fetch --config configs/trend/data-spot.yaml --market spot --tfs 15m --to 2019-12-31 --pairs $p` (no data after the holdout end is needed).
- [ ] **Step 3:** `perpbt data validate --config configs/trend/data.yaml` and `... data-spot.yaml --market spot`. Record per pair: rows, first/last candle, gaps and missing slots, funding intervals, bad intervals, overlap mismatches, any delisting.
- [ ] **Step 4:** Review the gap counts; anything beyond small exchange outages is written up in the progress log below with a decision (accept / refetch).
- [ ] **Step 5:** Commit the new and changed manifests (`git add data/**/manifest.json`) with the summary in the message.

### Task 8: Spec amendments, full suite, review

- [ ] **Step 1:** Record in `phase-0-data-universe.md` (an "Implementation notes" section) every builder's call taken here: `--tfs 15m` (the spec wrote `--tf`), spot fetch `--to 2019-12-31`, spot coverage read from 1d klines (§0.1 and §0.4 agree on what a covered day is), the `dropped` list and `window` in `universe.json`, `first_day`/`last_day` definitions, the 7-day delisting rule for ccxt, the exclusion list.
- [ ] **Step 2:** `pytest -q` (full suite, including `slow`) → all green; paste the tail of the output into the progress log.
- [ ] **Step 3:** Fresh whole-branch review (one reviewer, most capable model) against the spec and this plan; fix findings; commit.
- [ ] **Step 4:** Ask the user to merge `trend/0-data-universe` into `dev` (`--no-ff`).

## Progress log

Spec: docs/superpowers/specs/2026-10-23-trend-following/phase-0-data-universe.md
Pre-flight:
- T1 -> T3: kline_url(market=), ARCHIVE_STARTS consumed by sync_candles via store.market — consistent.
- T2 -> T3: CandleStore(cfg, market=) — consistent.
- T1 -> T4: FakeArchive.add_klines(market=), kline_url(market=) — consistent.
- T4 -> T5: rank_universe default http_get — T4 says urllib_get, T5 says None. Ruling: default None resolved to module urllib_get at call time — lets the CLI test monkeypatch — cost if wrong: none.
Task 1: complete (commits 5e16b74..3800634, tests: python -m pytest tests/test_bulk.py tests/test_fetch.py -q → 68 passed in 10.08s)
Task 2: complete (commits 3800634..04a7caa, tests: python -m pytest tests/test_store.py -q → 46 passed in 2.62s)
- Task 3: Ruling: existing CLI tests asserted the exact run_fetch kwargs and a 2-arg run_validate fake; updated them to include market='um' — the CLI now always passes market — cost if wrong: none (test doubles only).
- Task 3: Ruling: added test_is_stale (boundary: exactly 7 days is not stale) beyond the brief — pins the delisting threshold — cost if wrong: none.
Task 3: complete (commits 04a7caa..ec1c633, tests: python -m pytest tests/test_fetch.py tests/test_validate.py tests/test_cli.py -q -k 'not Phase' → 65 passed in 76.61s (0:01:16))
- Task 4: Ruling: S3 listing parsed with stdlib xml.etree, not defusedxml (security hook suggestion) — the only XML source is Binance's archive bucket over HTTPS; stdlib expat (2.5+ in Python 3.11) does not fetch external entities and limits entity amplification; no new dependency — cost if wrong: a hostile bucket response could inflate memory in a one-off CLI run.
- Task 4: Ruling: added test_first_day_before_window_spot and test_unknown_market_raises beyond the brief — pin first_day from a pre-window month and the market check — cost: none.
Task 4: complete (commits ec1c633..f256d00, tests: python -m pytest tests/trend tests/test_bulk.py tests/test_fetch.py -q → 84 passed in 6.39s)
- Task 5: Ruling: the CLI test monkeypatches perpbt.data.bulk.urllib_get (rank_universe resolves bulk.urllib_get at call time), not perpbt.trend.universe.urllib_get as the brief said — same effect, one lookup site — cost: none. Added test_cli_trend_universe_download_error_is_exit_1 and 'trend' in test_cli TOP_LEVEL.
Task 5: complete (commits f256d00..2c12cfe, tests: python -m pytest tests/trend tests/test_cli.py -q -k 'trend or universe or help' → 17 passed, 16 deselected in 9.28s)
- Task 6: USER DECISION (2026-10-23): the spec's perp rule (1d kline every day of 2020-H1) gave 3 members (BTC ETH BCH); user chose "warm by 07-01": unbroken daily data from first day through 2020-06-30, first day <= 2020-01-23 (Phase 1 §1.5 warm: d-160 >= first day). Spot: user chose to admit LTC — unbroken daily data from first day through 2017-12-31 (cutoff = window end). Spec §0.3/§0.4, overview TD4 amended in the same commit.
- Task 6: first real ranking runs: um 24 min, spot 22 min (sequential S3 listings) vs "a few minutes" budget.
- Task 6: rerun with 8 threads: um 6m51s, spot 3m25s. um members BTC ETH BCH LINK ETC LTC XRP EOS TRX XLM (EOSUSDT delisted, last day 2025-05-21); spot BTC ETH BCC NEO BNB LTC.
- Task 6: Ruling: tests/trend/test_configs.py written before the configs existed and not run RED separately (they are consistency checks over committed files) — cost if wrong: a check that cannot fail; each asserts concrete values.
Task 6: complete (commits 2c12cfe..5da2d15, tests: python -m pytest tests/trend -q → 19 passed in 1.23s)
- Task 7: fetch KILLED by Claude Code (system low on memory, session idle) during XRPUSDT. Done: BTC ETH BCH LINK ETC LTC (rc=0, ~4 min each for new pairs). Not done: XRP (partial, resumable), EOS TRX XLM, all 6 spot. Not restarted: waiting for the user. Phase-0 spec implementation notes (§0.9) written, uncommitted.
- Task 7: perps fetched in the foreground after the user's "go": XRP 191s, EOS 454s (delisted: ccxt skipped; 525 post-delisting daily 404s recorded), TRX 237s, XLM 239s. The 2022-02-26..28 and 2022-04-01..02 archive holes filled by ccxt for every new pair (480 rows each); EOS has none.
- Task 7: finding: EOSUSDT funding files continue after its last candle (2025-05-21 09:00) at a flat 1e-4 per 8h through 2026-09 — archive artifacts of the dead contract. Pointer for Phases 1/3: a pair's funding must be cut at its last candle (Phase 3 §3.6 ends the DayFrame there).
- Task 7: Ruling: the spot archive has 81 candles per pair on a shifted grid after the 2018-02-09 exchange restart (09:58:14.789 .. 2018-02-10 05:58:14.789, BTC and BCC seen; 1m45s before a slot), which made the spot fetch fail the grid check. Added bulk.snap_to_grid at ingest: rows within a quarter step of the grid move to the nearest slot, collisions raise, larger offsets still fail; count recorded per file ("snapped") and logged — keeps the real restart prices instead of a 20-hour hole — cost if wrong: candle times off by <= 3m45s on 81 holdout candles. Tests: test_snap_to_grid_* (RED: import error), test_off_grid_archive_rows_are_snapped_and_recorded (written before the code, not run RED separately). Partial spot store (uncommitted, off-grid rows inside) deleted and refetched.
- Task 7: spot fetched (BTC 53s, ETH 55s, BCC 262s incl. 409 post-delisting daily 404s, NEO 39s, BNB 45s, LTC 47s); every pair had 81 candles snapped in 2018-02.
- Task 7: validate: perps 0 gaps (all 10), funding all 8h, no bad intervals; BTC/ETH 1m->15m mismatching candles 18 each (as before). Spot gaps are exchange-wide outages (2018-02-08 00:30..02-09 10:00 halt = 134 slots, the longest; 2018-06-26, 2018-07-04, 2019-05-15, ...), all < 5 days: accepted. BCCUSDT: 2018-11-15 16:45..11-20 02:45 suspension (BCH ABC/SV fork) then one last candle, delisted — pointer for Phase 3 §3.6.
Task 7: complete (commits 5da2d15..35989f7, tests: python -m pytest tests/trend tests/test_bulk.py tests/test_fetch.py -q → 98 passed in 5.18s)
- Task 8: full suite 962 passed in 215.75s (slow included).
Final review: fresh reviewer (opus) — "With fixes"; 0 Critical, 1 Important, 6 Minor.
- Final: fixed is_stale measured against today instead of the fetch end (past --to wrongly skipped gap fill as "delisted") — test_staleness_is_measured_against_the_requested_end RED→GREEN; skip now logged at warning, suite 963/963
- Final: fixed (re-graded Minor→Important: the rule text is hashed into the pre-registration) universe rule text said "from their first archived day" while the code checks from max(first day, window start) — test_listing_is_the_universe now pins block["rule"] == RULES[market].text RED→GREEN; universe.json rule strings and spec §0.4 updated, membership unchanged, suite 963/963
- Final: Ruling: snap_to_grid also applies to perp ingest (reviewer #3) — the perp archive has no off-grid rows (all ten validated), and accepting a within-quarter-step row with a warning and a per-file manifest record beats the pre-existing behaviour (off-grid rows written, then the refresh raises, poisoning the store) — cost if wrong: a perp file with slightly off-grid rows is accepted with only a log line and manifest entry, not a summary line.
- Final: Ruling: off-grid rows beyond a quarter step are still written before the grid check raises (reviewer declined) — pre-existing (Phase 1 deferred minor "store.write accepts off-grid timestamps") — cost if wrong: a manual store cleanup, as happened once here.
- Final: Ruling: um has exactly 10 candidates, so the volume ranking selects nothing and the 2020-01-23 cutoff alone sets membership (reviewer note) — that is the rule the user chose; reported to the user — cost: none.
- Final: minor (deferred): `last_day` cannot tell a live pair from a delisted one and drifts on rerun (with computed_on); universe.json should be treated as frozen once resolved (Phase 2 hashes it)
- Final: minor (deferred): `data fetch --config data-spot.yaml` without `--market spot` fetches perp data for the spot names into the perp store (and the reverse); a `market` field in the trend data configs would catch it
- Final: minor (deferred): write_universe writes in place (no tmp+replace); ET.ParseError from a non-XML 200 is not caught by `trend universe` (traceback instead of exit 1)
- Final: minor (deferred): snapped counts appear only in the manifest and log, not in the run_fetch summary or validate output
- Final: minor (deferred): DELISTED_AFTER_DAYS misfires if the daily archive lags > 7 days (now visible at warning level)
- Pointer for Phase 1 (reviewer): the shared BTC/ETH 15m store starts 2019-11-01 (first strategy's ccxt head) while the trend listing says 2020-01-01; trend code must take a pair's first day from listing/warmup_start, not the first stored candle.
