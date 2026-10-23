# Carry Phase 0 — Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spot 15m candles for the ten carry pairs from 2020, the spot store guard split at 2020-01-01, the carry data config, and the in-sample basis sanity check — no strategy logic.

**Architecture:** `CandleStore.load` keeps the trend spot holdout (loads starting before 2020-01-01 need `allow_holdout=True`) and otherwise applies the ordinary `insample_end` date guard to spot as to perps. `configs/carry/data.yaml` serves both stores. A new `perpbt/carry/basis.py` computes perp/spot close ratios over a fixed window (2020-07-01..2023-12-31) through the guarded loaders, exposed as `perpbt carry basis`.

**Tech Stack:** Python 3.11, numpy, pandas, pytest; existing `perpbt` data layer.

**Spec:** `docs/superpowers/specs/2026-10-23-funding-carry/phase-0-data.md` with `00-overview.md`.

## Global Constraints

- `SPOT_TREND_HOLDOUT_END_EXCLUSIVE` = 2020-01-01 00:00 UTC, a constant in `perpbt/data/store.py`.
- Carry periods: insample_start 2020-01-01, insample_end 2023-12-31, holdout 2024-01-01..2026-10-20, warmup_start 2020-01-01.
- Carry listing = the trend `um` members with their trend `listing` dates (BTC ETH BCH 2020-01-01, LINK 01-17, ETC 01-16, LTC 01-09, XRP 01-06, EOS 01-08, TRX 01-15, XLM 01-20).
- Basis window 2020-07-01..2023-12-31 only; flag |median| > 0.5% or max |·| > 20%; no return, P&L or funding statistic.
- Never pass `allow_holdout=True` anywhere new in `perpbt/` (user instruction).
- Do not edit `configs/data.yaml` or any `configs/trend/*` file. Do not run any first-strategy run command or any trend run command.
- Fetches: one pair per call, in the foreground.
- `tests/test_store.py::test_spot_load_refused_without_allow_holdout` stays unchanged and green; the full suite stays green.

## Review Focus

1. A spot load that starts before 2020-01-01 and ends after it (straddling) without `allow_holdout` → refused (the start decides).
2. A 2020+ spot load under the trend spot config (`insample_end` 2019-12-31) → still refused by the date guard.
3. Basis slots where only one leg has a candle (spot outage, perp gap) → excluded from the ratio distribution, counted in coverage; a pair with no common slot → NaN stats and flagged, no crash.
4. The basis window cannot be pushed into the carry holdout by giving another config (e.g. `configs/trend/data.yaml`) → the window is a module constant, not a parameter.
5. A pair with no stored spot data → exit 2 with a message naming the pair, nothing written.

---

### Task 1: Spot guard split

**Files:**
- Modify: `perpbt/data/store.py` (module docstring, constant, `CandleStore.load`)
- Modify: `perpbt/trend/daily.py:156-158` (docstring of `load_dayframe` only)
- Test: `tests/test_store.py`, `tests/trend/test_daily.py:210-217`

**Interfaces:**
- Produces: `store.SPOT_TREND_HOLDOUT_END_EXCLUSIVE: int` (= `date_ms("2020-01-01")`); `CandleStore.load` semantics: spot with `start_ms < SPOT_TREND_HOLDOUT_END_EXCLUSIVE` needs `allow_holdout`; every load (both markets) keeps the `insample_end` date guard.

- [ ] **Step 1: Failing tests in `tests/test_store.py`** (markets section; `data_cfg` has insample_end 2025-12-31):
  - `test_spot_load_from_2020_follows_date_guard`: write spot 2020-01-01 (3 candles) → `load(t, t+3*STEP)` returns 3 without `allow_holdout`; write a candle on 2026-01-01 and a load ending past `insample_end` raises `HoldoutAccessError` matching `"insample_end"`.
  - `test_spot_guard_boundary`: candles on 2019-12-31 23:45 and 2020-01-01 00:00; a load from 2019-12-31 23:45 raises matching `"spot"`; a load from 2020-01-01 returns 1 candle; with `allow_holdout=True` the straddling load returns 2.
  - `test_spot_2020_refused_under_trend_spot_config`: a config with insample_end 2019-12-31 refuses a 2020-01-01 spot load without `allow_holdout`.
- [ ] **Step 2:** `python -m pytest tests/test_store.py -q -k spot` → the three new tests FAIL (the 2020+ load raises "spot").
- [ ] **Step 3:** Implement in `store.py`: the constant beside `DAY_MS`; in `load`, the spot check becomes `self.market == "spot" and start_ms < SPOT_TREND_HOLDOUT_END_EXCLUSIVE and not allow_holdout`, message names the trend spot holdout and 2020-01-01. Update the module and `load` docstrings.
- [ ] **Step 4:** Update `tests/trend/test_daily.py::test_load_dayframe_spot_needs_allow_holdout`: listing and warmup 2019-12-30, candles from 2019-12-30, so the load starts in the trend spot holdout; keep its asserts; add `test_load_dayframe_spot_from_2020_needs_no_allow_holdout` (the original 2020-01-01 data loads without the flag). Update `load_dayframe`'s docstring ("refuses loads that start before 2020-01-01").
- [ ] **Step 5:** `python -m pytest tests/test_store.py tests/trend/test_daily.py -q` → PASS, `test_spot_load_refused_without_allow_holdout` unchanged.
- [ ] **Step 6:** Commit `Carry phase 0: split the spot guard at 2020-01-01`.

### Task 2: Carry data config

**Files:**
- Create: `configs/carry/data.yaml`
- Test: `tests/carry/__init__.py`, `tests/carry/test_configs.py`

- [ ] **Step 1: Failing tests:**
  - `test_carry_data_config_periods`: `load_yaml(configs/carry/data.yaml, DataConfig)` has `(data_dir, insample_start, insample_end, holdout_start, holdout_end, warmup_start) == ("data", "2020-01-01", "2023-12-31", "2024-01-01", "2026-10-20", "2020-01-01")`.
  - `test_carry_listing_is_trend_um`: `cfg.listing == load_yaml(configs/trend/data.yaml).listing` and `sorted(cfg.listing) == sorted(universe.json["um"]["members"])`.
  - `test_carry_stores_refuse_the_holdout`: with the committed config but `data_dir` pointed at `tmp_path`, a perp and a 2020+ spot candle on 2024-01-01 — loads ending past 2024-01-01 raise `HoldoutAccessError`, loads ending at 2024-01-01 do not.
- [ ] **Step 2:** run → FAIL (file missing).
- [ ] **Step 3:** Write the YAML with a header comment in the trend configs' style (both stores; the carry loader's own end check comes in Phase 1).
- [ ] **Step 4:** `python -m pytest tests/carry -q` → PASS.
- [ ] **Step 5:** Commit `Carry phase 0: configs/carry/data.yaml`.

### Task 3: Basis sanity check (code)

**Files:**
- Create: `perpbt/carry/__init__.py`, `perpbt/carry/basis.py`
- Modify: `perpbt/cli.py` (`carry basis` subcommand, docstring), `.gitignore` (`!/runs/carry/basis_check.json`)
- Test: `tests/carry/test_basis.py`, `tests/test_cli.py` (or `tests/carry/test_basis.py` via `main`)

**Interfaces:**
- Produces:
  - `BASIS_START = "2020-07-01"`, `BASIS_END = "2023-12-31"` (inclusive), `MEDIAN_LIMIT = 0.005`, `MAX_ABS_LIMIT = 0.20`.
  - `basis_stats(perp: Candles, spot: Candles, start_ms: int, end_ms: int) -> dict` with keys `slots` (grid slots in the window), `perp_rows`, `spot_rows`, `common`, `coverage` (= common / slots), `median`, `p01`, `p99`, `max_abs`, `max_abs_at` (ISO UTC of the slot), `flagged` (bool), `reasons` (list of str). Ratio `perp.c / spot.c − 1` on common open times only. NaN stats (JSON null) and `flagged` with reason `"no common slots"` when `common == 0`.
  - `basis_check(data_cfg: DataConfig, pairs: Sequence[str]) -> dict` — `{"window": [BASIS_START, BASIS_END], "limits": {...}, "pairs": {pair: basis_stats}}`; loads each leg with `CandleStore(..., market).load(pair, "15m", date_ms(BASIS_START), date_ms(BASIS_END) + DAY_MS)` (no `allow_holdout`).
  - CLI: `perpbt carry basis [--config configs/carry/data.yaml] [--pairs ...] [--out runs/carry/basis_check.json]`; prints one line per pair, exit 0; 2 on a config error or missing data (`FileNotFoundError`), 1 on `HoldoutAccessError`; writes the JSON (sorted keys, indent 2, `allow_nan=False` with NaN → null).

- [ ] **Step 1: Failing tests** in `tests/carry/test_basis.py` with synthetic candles:
  - `test_ratio_stats`: spot closes 100, perp closes 100.1 on 4 slots and 101 on one → median ≈ 0.001, max_abs ≈ 0.01, `max_abs_at` = that slot, coverage = 5/slots, not flagged.
  - `test_one_leg_slots_excluded`: a spot-only and a perp-only slot change `common` and `coverage` but not `median`/`max_abs`.
  - `test_flags`: median 0.6% → flagged "median"; one 25% spike → flagged "max_abs".
  - `test_no_common_slots`: disjoint ts → stats None in JSON, flagged.
  - `test_window_is_fixed`: `basis_check` with a config whose insample_end is 2026-10-20 and data through 2025 loads only ≤ 2023-12-31 (rows after are ignored: `perp_rows` counts window rows only).
  - `test_cli_basis_writes_json` and `test_cli_basis_missing_pair_exit_2` (no file written).
- [ ] **Step 2:** run → FAIL (module missing).
- [ ] **Step 3:** Implement `basis.py` (numpy `intersect1d` on ts, `np.quantile`) and the CLI handler/subparser (`DEFAULT_CARRY_DATA = "configs/carry/data.yaml"`); add the `.gitignore` exception.
- [ ] **Step 4:** `python -m pytest tests/carry tests/test_cli.py -q` → PASS.
- [ ] **Step 5:** Commit `Carry phase 0: basis sanity check (perpbt carry basis)`.

### Task 4: Fetch and validate spot (real data)

- [ ] **Step 1:** For each pair in `BTCUSDT ETHUSDT BCHUSDT LINKUSDT ETCUSDT LTCUSDT XRPUSDT EOSUSDT TRXUSDT XLMUSDT`, one foreground call: `python -m perpbt data fetch --config configs/carry/data.yaml --market spot --tfs 15m --from 2020-01-01 --to 2026-10-20 --pairs <PAIR>`. Exit 0 each.
- [ ] **Step 2:** `python -m perpbt data validate --config configs/carry/data.yaml --market spot` → exit 0. Record per pair: rows, first/last candle, gaps (count, longest, those ≥ 1 day), snapped, overlap mismatches.
- [ ] **Step 3:** Commit the spot manifests: `Carry phase 0: spot 15m 2020-01-01..2026-10-20 for the ten pairs, validated`.

### Task 5: Run the basis check, record, full suite

- [ ] **Step 1:** `python -m perpbt carry basis` → `runs/carry/basis_check.json`; inspect flagged pairs and their `max_abs_at` slots (look at both legs' candles around them, read-only, in-sample only).
- [ ] **Step 2:** `python -m pytest -q` (full) → all pass.
- [ ] **Step 3:** Spec `phase-0-data.md` gets §0.7 "Implementation notes" (builder's calls, data facts, basis results); progress log below; commit `Carry phase 0: basis check run, implementation notes`.
- [ ] **Step 4:** Fresh whole-branch review (most capable model); fix; then STOP before the `--no-ff` merge into `dev`.

## Progress log

Spec: docs/superpowers/specs/2026-10-23-funding-carry/phase-0-data.md
- Ruling: `tests/trend/test_daily.py::test_load_dayframe_spot_needs_allow_holdout` loads spot from 2020-01-01, which the spec's split makes legal; its dates move to 2019-12-30 (same intent: the trend spot holdout needs the flag) and a new test pins the 2020 case — cost if wrong: none (test data only).
- Ruling: the basis check lives in `perpbt/carry/basis.py` with a CLI `perpbt carry basis` and a committed `runs/carry/basis_check.json`, so the check is reproducible; its window is a module constant, so no config can move it into the holdout — cost if wrong: one small module Phase 1 does not need.
