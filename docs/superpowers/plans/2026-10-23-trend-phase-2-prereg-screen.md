# Trend Phase 2 — Pre-registration and Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `configs/trend/prereg.yaml` with its loader, hash and JSON lock; the gross signal screen (`trend/screen.py`) with its one-shot CLI; then, with the user's go-ahead, the freeze, the tag `trend-prereg-v1`, and one run of the screen that decides go or stop.

**Architecture:** `trend/prereg.py` parses the YAML (exact key sets, duplicate keys rejected) into a frozen `TrendPrereg` whose blocks go through `SignalConfig`, `SizingConfig`, `RebalanceConfig`, `CostConfig` and small `_Config` classes. The hash is the canonical JSON of the normalised content without `periods.forward.start`. `trend/screen.py` aligns the pairs' Phase 1 signals on decision days, computes the gross portfolio return series (no fill delay, band, costs or funding), and compares its Sharpe with 1,000 circular-shift paths (one shared offset per path, Phase 4 §4.1). The CLI refuses to screen without a matching lock and refuses to overwrite `runs/trend/screen.json`.

**Tech Stack:** Python 3.11, numpy, PyYAML, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-23-trend-following/phase-2-prereg-screen.md`, read with `00-overview.md` (TD6–TD8, TD12, §7), Phase 4 §4.1 (the shift rule), Phase 1 §1.8, Phase 5 §5.4 (the import scope). Code this builds on: `perpbt/trend/{config,daily,signals}.py`, `perpbt/experiments/prereg.py` (duplicate-key loader, `_exact_keys` pattern), `perpbt/config.py`, `perpbt/stats/bootstrap.sharpe`, `perpbt/stats/seeds.rng_for`, `perpbt/version.git_commit`, `perpbt/cli.py`.

## Global Constraints

- Branch `trend/2-prereg-screen` from `dev` (dc667bc).
- **Stops (user):** (1) a rule or statistics question the spec leaves open; (2) before the freeze and the tag — show the final `prereg.yaml`; (3) after the screen has run once — report `p_screen` and the decision before anything else; never re-run it with changes; (4) before the `--no-ff` merge into `dev`.
- The prereg content is the spec §2.1 YAML verbatim in values. The hash covers the normalised content except `periods.forward.start`. After the freeze only `periods.forward.start` may change.
- `universe` in the prereg must equal `configs/trend/universe.json`'s `rule` and `members` per market. `universe.json` is committed and is not regenerated.
- Trend modules import only perpbt modules in the Phase 5 §5.4 scope (`tests/trend/test_imports.py`). `perpbt/experiments/prereg.py` is outside it, so the duplicate-key YAML loader moves to `perpbt/config.py` (in scope) and `experiments/prereg.py` imports it from there (backward compatible; the first strategy's suite stays green).
- Screen model (spec §2.3): pair return on day `d+1` = `w*_d × (c_{d+1}/c_d − 1)`; no fill delay, no band, no fees, no slippage, **no funding**; portfolio = mean over pairs active on `d`; statistic = `stats.bootstrap.sharpe` (√365) of the portfolio series; 1,000 paths; `p_screen = (1 + #{S_m ≥ S_obs}) / 1001`; stop iff `p_screen ≥ 0.10`.
- Shift (Phase 4 §4.1): per path one `k` uniform on `[shift_margin_days, n − shift_margin_days]` (60), `n` = decision days in the period; pair `i` takes `e'_{i,t} = e_{i,(t+k) mod n_i}` over its `n_i` active decision days; `w'` = `signals.target_weight(e', σ̂, sizing)` with the actual `σ̂`.
- Seeds from `stats.seeds.rng_for(master_seed, prereg_hash, "screen")`.
- Perps in-sample only (`configs/trend/data.yaml`, `end = insample_end`); no spot load; no `allow_holdout=True` under `perpbt/`. Do not run any first-strategy command. Long jobs in the foreground, one at a time.

## Review Focus

1. **The screen runs only on the frozen file.** A missing lock, or a prereg whose hash differs from the lock, must refuse the screen. Pinned in Task 4 by `test_screen_refuses_without_lock` and `test_screen_refuses_hash_mismatch`.
2. **The screen is one shot.** A second `perpbt trend screen` must refuse when `runs/trend/screen.json` exists. Pinned in Task 4 by `test_screen_refuses_to_overwrite`.
3. **A delisted pair leaves the portfolio.** After EOSUSDT's last day the mean is over the remaining pairs, and its shifted exposures stay inside its own active days. Pinned in Task 3 by `test_delisted_pair_leaves_active_set` and `test_shift_stays_inside_active_days`.
4. **The prereg and `universe.json` agree.** A members list that differs from the committed universe must not load as valid. Pinned in Task 2 by `test_universe_must_match_universe_json`.
5. **Funding is not in the screen.** The same prices with heavy funding give the same screen series. Pinned in Task 3 by `test_screen_ignores_funding`.

---

### Task 1: Shared YAML loader; `RebalanceConfig`, `CostConfig`; canonical member order

**Files:**
- Modify: `perpbt/config.py`, `perpbt/experiments/prereg.py`, `perpbt/trend/config.py`
- Test: `tests/test_config.py`, `tests/trend/test_config.py`

**Interfaces:**
- Produces: `perpbt.config.read_yaml_unique(path) -> Any` (duplicate keys raise `ConfigError` with the line); `experiments/prereg.py` keeps its `_read_yaml` name as an alias. `trend.config.RebalanceConfig(_Config)`: `frequency: str` (`"daily"` | `"weekly"`), `drift_band: float` (≥ 0), `fill_offset_min: int` (in `trend.daily.FILL_OFFSETS_MIN`). `trend.config.CostConfig(_Config)`: `fee_taker: float` (≥ 0), `slippage: dict[str, float]` and `mmr: dict[str, float]` (each must have `"default"`; slippage ≥ 0, mmr > 0), `spot_funding_per_8h: float` (≥ 0); methods `slippage_for(pair) -> float`, `mmr_for(pair) -> float`. `SignalConfig` now requires `members` in `MEMBERS` order and `lookbacks` strictly increasing.

- [ ] **Step 1: Failing tests:** `tests/test_config.py::test_read_yaml_unique_rejects_duplicates` (tmp YAML with a repeated nested key → `ConfigError` naming the key and line); `tests/trend/test_config.py`: `test_members_must_be_canonical_order` (`["donchian", "ma_filter", "return_sign"]` → `ConfigError`), `test_lookbacks_must_increase` (`[40, 20]` → `ConfigError`), `test_rebalance_config` (round trip of `{daily, 0.20, 15}`; `frequency: "monthly"`, `drift_band: -0.1`, `fill_offset_min: 30` → `ConfigError`), `test_cost_config` (round trip of the spec block; `slippage_for("BTCUSDT") == 0.0002`, `slippage_for("XRPUSDT") == 0.0005`, `mmr_for("ETHUSDT") == 0.005`; no `default` key → `ConfigError`).
- [ ] **Step 2:** Run `python -m pytest tests/test_config.py tests/trend/test_config.py -q` → the new tests fail.
- [ ] **Step 3:** Implement; `experiments/prereg.py` imports the loader from `perpbt.config`.
- [ ] **Step 4:** `python -m pytest tests/test_config.py tests/test_prereg.py tests/trend -q -m "not slow"` → pass. Commit `Trend phase 2: shared duplicate-key YAML loader; rebalance and cost configs; canonical member order`.

### Task 2: `trend/prereg.py` — parse, hash, universe check, JSON lock

**Files:**
- Create: `perpbt/trend/prereg.py`, `tests/trend/test_prereg.py`

**Interfaces:**
- Consumes: Task 1 loader and configs; `SignalConfig`, `SizingConfig`; `config.canonical_json`, `config_hash`; `version.git_commit`.
- Produces:
  - `TOP_KEYS = ("registered_on", "universe", "periods", "signal", "sizing", "rebalance", "costs", "exploratory", "reprice", "stats", "screen", "verdict_rule")`; `LOCK_KEYS = ("frozen_on", "prereg_hash", "git_commit", "code_version", "code_locked_on", "code_git_commit")`; `VERDICT_KEYS = ("t1", "t2", "edge", "t3", "reported_for", "forward_12m")`.
  - `_Config` classes: `Periods` (`warmup_start, warmup_end, insample_start, insample_end, holdout_market, holdout_end, forward_start: str | None, forward_reviews: tuple[int, ...]`; ISO dates; `warmup_end < insample_start`; `holdout_market == "spot"`), `TrendStats` (`alpha` in (0, 1), `baseline_runs ≥ 1`, `shift_margin_days ≥ 0`, `bootstrap_n ≥ 1`, `block_len_days ≥ 1`, `master_seed: int`), `ScreenConfig` (`baseline_runs ≥ 1`, `stop_if_p_at_least` in (0, 1]), `RepriceConfig` (`slippage`, `fee_taker`: non-empty tuples of floats ≥ 0), `Exploratory` (`name: str`, `signal: SignalConfig`, `rebalance: RebalanceConfig`; built by merging the entry's overrides into the primary; names unique; only `signal` and `rebalance` overrides allowed).
  - `@dataclass(frozen=True, eq=False) class TrendPrereg` with one field per top key (`universe: dict[str, dict]` with `rule: str`, `members: tuple[str, ...]` per market; `verdict_rule: dict`); `content() -> dict` (normalised, plain); `prereg_hash() -> str` (= `config_hash` of `content()` with `periods.forward_start` removed).
  - `parse_prereg(raw) -> TrendPrereg`; `load_prereg(path) -> TrendPrereg`; `check_universe(prereg, universe: dict) -> None` (raises `ConfigError` naming the market when `rule` or `members` differ); `lock_path(prereg_path) -> Path` (`.lock` suffix); `freeze(prereg_path, *, universe_path, today=None) -> dict` (checks the universe, writes JSON with `indent=2` in `LOCK_KEYS` order, `open(..., "x")`, code fields null); `read_lock(path) -> dict` (exact keys); `check_frozen(prereg_path) -> TrendPrereg` (raises `ConfigError` when the lock is missing or its hash differs).
- [ ] **Step 1: Failing tests** in `tests/trend/test_prereg.py`, on a fixture dict equal to the spec §2.1 YAML (written to `tmp_path`):
  - `test_round_trip`: `load_prereg` → `signal == SignalConfig(...)` of the spec, `sizing.target_vol == 0.30`, `rebalance.fill_offset_min == 15`, five exploratory entries with `long_short.signal.direction == "long_short"` and `fill_0800.rebalance.fill_offset_min == 480`, `weekly.rebalance.frequency == "weekly"`.
  - `test_hash_stable_under_formatting`: key order shuffled, comments added, `target_vol: 0.3` vs `0.30`, `max_weight: 2` vs `2.0` → same hash.
  - `test_hash_changes_on_content`: `drift_band: 0.25`, a lookback added, an exploratory entry removed → different hash.
  - `test_forward_start_excluded_from_hash`: `periods.forward.start: 2026-11-01` vs `null` → same hash; `content()` still carries it.
  - `test_unknown_and_missing_keys`: an extra top key, a missing `screen`, an extra key in `periods.insample`, `exploratory` entry with a `costs` override, duplicate exploratory names, a duplicate YAML key → `ConfigError`.
  - `test_universe_must_match_universe_json`: `check_universe` passes for the spec members and `configs/trend/universe.json`; a reordered or shortened `um.members` or an edited `rule` raises.
  - `test_freeze_writes_json_lock_once`: `freeze(...)` → file parses as JSON, keys `LOCK_KEYS` in order, `prereg_hash` matches, code fields None; a second freeze raises `FileExistsError`.
  - `test_check_frozen`: no lock → `ConfigError`; after freeze → returns the prereg; after editing `drift_band` → `ConfigError` naming both hashes; after editing only `forward.start` → passes.
- [ ] **Step 2:** Run `python -m pytest tests/trend/test_prereg.py -q` → fail (module missing).
- [ ] **Step 3:** Implement. Dates in the YAML may be unquoted (YAML dates); normalise to ISO strings.
- [ ] **Step 4:** `python -m pytest tests/trend -q -m "not slow"` → pass. Commit `Trend phase 2: trend pre-registration (parse, hash, universe check, JSON lock)`.

### Task 3: `trend/screen.py` — alignment, shift, gross returns, run

**Files:**
- Create: `perpbt/trend/screen.py`, `tests/trend/test_screen.py`

**Interfaces:**
- Consumes: `DayFrame`, `Signals`, `compute_signals`, `target_weight`; `SizingConfig`; `stats.bootstrap.sharpe`; `stats.seeds.rng_for`.
- Produces:
  - `@dataclass(frozen=True, eq=False) class ScreenInputs`: `pairs: tuple[str, ...]`, `day_ms: int64[n]` (decision days: the day before `start` through the day before `end`), `ret_next: float64[P, n]` (`c_{d+1}/c_d − 1`, NaN where unavailable), `exposure`, `sigma: float64[P, n]`, `active: bool[P, n]` (`valid[d]` and day `d+1` in the frame).
  - `screen_inputs(frames: dict[str, DayFrame], signals: dict[str, Signals], *, start: str, end: str) -> ScreenInputs` (pairs in dict order; days aligned by `day_ms`).
  - `portfolio_returns(weight: np.ndarray, inputs: ScreenInputs) -> np.ndarray`: `weight` shape `(P, n)` or `(M, P, n)`; per day the mean of `weight × ret_next` over active pairs, 0 on a day with no active pair.
  - `shift_offsets(n: int, M: int, margin: int, rng) -> int64[M]` (uniform on `[margin, n − margin]`; `ValueError` when `n − margin < margin`).
  - `shifted_exposure(inputs: ScreenInputs, k: np.ndarray) -> float64[M, P, n]` (NaN where not active).
  - `run_screen(inputs, sizing: SizingConfig, *, paths: int, margin: int, rng, stop_if_p_at_least: float, chunk: int = 100) -> dict` → `{"n_days", "n_pairs", "observed_sharpe", "paths": {"n", "mean", "std", "p05", "p50", "p95"}, "p_screen", "stop_if_p_at_least", "decision": "go" | "stop", "bh_sharpe", "mean_active_pairs"}` (`bh_sharpe`: `e ≡ 1` on active days, same model; descriptive).
- [ ] **Step 1: Failing tests** in `tests/trend/test_screen.py` (DayFrames from one candle per day as in `tests/trend/test_signals.py::frame_from_closes`; tiny `SizingConfig` and `SignalConfig` with lookbacks `[4, 8]`, `vol_window 5` where warmup must stay short):
  - `test_screen_inputs_alignment`: two pairs with different first days → `day_ms` runs from `start − 1` to `end − 1`; `ret_next[i, t]` equals the pair's `c[d+1]/c[d] − 1`; `active` False before a pair is valid.
  - `test_portfolio_returns_hand`: 2 pairs, 3 days, chosen weights and returns → hand values; a day with one active pair uses only that pair; a day with none is 0.
  - `test_delisted_pair_leaves_active_set`: pair B ends 5 days before `end` → `active[B]` False from its last day on; portfolio = pair A's return there.
  - `test_screen_ignores_funding`: frames with and without 1e-3 funding per 8 h → identical `ret_next` and portfolio series.
  - `test_shift_offsets`: `n = 300, margin = 60` → all in `[60, 240]`, both ends reachable over 20,000 draws; `n = 100, margin = 60` → `ValueError`.
  - `test_shift_preserves_time_in_market_and_changes`: for each path and pair, the multiset of active exposures is unchanged and the circular number of exposure changes is unchanged.
  - `test_shift_stays_inside_active_days`: shifted exposures are NaN exactly where `active` is False; a pair with `n_i < n` takes `k mod n_i`.
  - `test_one_shared_offset`: two pairs with identical exposure series and identical active days get identical shifted series on every path.
  - `test_trending_beats_shifted`: 4 pairs of 900 days with trend regimes (drift ±0.3%/day switching every 120 days, noise 1%) → `p_screen < 0.05`, `decision == "go"`.
  - `test_random_walk_p_roughly_uniform`: 30 seeds of 4 driftless random-walk pairs × 700 days, 99 paths each → mean `p_screen` in `[0.3, 0.7]` and at most 9 of 30 below 0.1.
  - `test_run_screen_deterministic`: same inputs and seed → identical dict; `p_screen == (1 + #{S_m ≥ S_obs}) / (paths + 1)` checked from the returned path count.
- [ ] **Step 2:** Run `python -m pytest tests/trend/test_screen.py -q` → fail.
- [ ] **Step 3:** Implement; paths in chunks (`chunk` paths at a time) to bound memory; Sharpe per path with `stats.bootstrap.sharpe`.
- [ ] **Step 4:** `python -m pytest tests/trend -q -m "not slow"` → pass. Commit `Trend phase 2: the gross signal screen (alignment, shared circular shift, random-days paths)`.

### Task 4: CLI `perpbt trend prereg validate|freeze` and `perpbt trend screen`

**Files:**
- Modify: `perpbt/cli.py`, `.gitignore`
- Create: `perpbt/trend/screen.py` addition `screen_from_store(prereg, data_cfg) -> dict` and `write_screen(result: dict, runs_dir: Path) -> Path`
- Test: `tests/trend/test_screen_cli.py`

**Interfaces:**
- Consumes: Task 2 `load_prereg`, `check_universe`, `freeze`, `check_frozen`, `prereg_hash`; Task 3 `screen_inputs`, `run_screen`; Phase 1 `load_dayframe`, `compute_signals`.
- Produces: `DEFAULT_TREND_PREREG = "configs/trend/prereg.yaml"`, `DEFAULT_TREND_DATA = "configs/trend/data.yaml"`. `perpbt trend prereg validate` (prints the hash; checks the universe) and `perpbt trend prereg freeze` (exit 1 if the lock exists). `perpbt trend screen [--prereg] [--data-config] [--runs-dir runs]`: `check_frozen` (exit 1 on failure); exit 1 if `<runs>/trend/screen.json` exists; `data_cfg.insample_end == prereg.periods.insample_end` (exit 2 otherwise); loads every `universe.um.members` pair with `load_dayframe(end=insample_end)`, `compute_signals` with the primary `signal`/`sizing`, runs the screen with `screen.baseline_runs`, `stats.shift_margin_days`, `rng_for(master_seed, prereg_hash, "screen")`; writes `screen.json` (`result` plus `prereg_hash`, `git_commit`, `computed_on`, `period`, `pairs`); on `stop` also writes `<runs>/trend/summary.md` (decision, `p_screen`, observed Sharpe, path summary, B&H Sharpe, "Phases 3–7 are not built"); prints `p_screen` and the decision. `.gitignore` admits `runs/trend/screen.json` and `runs/trend/summary.md`.
- [ ] **Step 1: Failing tests** in `tests/trend/test_screen_cli.py` (a synthetic perp store of 3 pairs via `CandleStore.write`/`FundingStore.write` in `tmp_path`, a prereg fixture whose `universe.um.members` are those pairs, a matching `universe.json`, short lookbacks so the synthetic period is short): `test_prereg_validate_prints_hash`, `test_prereg_freeze_once`, `test_screen_refuses_without_lock`, `test_screen_refuses_hash_mismatch`, `test_screen_writes_json_then_refuses_to_overwrite` (first run exit 0, `screen.json` has the keys above and `prereg_hash`; second run exit 1 and the file is unchanged), `test_screen_stop_writes_summary` (a fixture with `stop_if_p_at_least: 1.0`, so any p stops → `summary.md` exists and says stop), `test_screen_refuses_insample_mismatch`.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `python -m pytest tests/trend tests/test_cli.py -q -m "not slow"` → pass. Commit `Trend phase 2: perpbt trend prereg and trend screen`.

### Task 5: The prereg file, spec notes, suite, review

**Files:**
- Create: `configs/trend/prereg.yaml`; `tests/trend/test_prereg_file.py`
- Modify: the Phase 2 spec (§2.6 Implementation notes); this plan's progress log

- [ ] **Step 1:** Write `configs/trend/prereg.yaml` from spec §2.1 with `universe` copied from `universe.json` (`rule`, `members`), `registered_on: 2026-10-23`, `periods.forward.start: null`, comments naming the spec sections. Test `test_committed_prereg_loads_and_matches_universe` (loads, `check_universe` passes, `signal`/`sizing` equal the Phase 1 primary, the five exploratory names). Run → pass. `perpbt trend prereg validate` prints the hash.
- [ ] **Step 2:** §2.6 notes in the spec: the loader move, canonical member order, decision/return days, active set, 0 on no-active days, the one-shot and lock checks, the seed purpose. Commit `Trend phase 2: configs/trend/prereg.yaml; spec implementation notes`.
- [ ] **Step 3:** `python -m pytest -q` (full, slow included, foreground) → green; tail in the log.
- [ ] **Step 4:** Fresh whole-branch review (most capable model); fix Critical/Important with RED→GREEN; commit.
- [ ] **Step 5: STOP** — show the user the final `prereg.yaml` and its hash; wait for the go-ahead to freeze.

### Task 6 (after the user's go-ahead): freeze, tag, screen once

- [ ] **Step 1:** `perpbt trend prereg freeze`; commit `prereg.yaml`, `universe.json` and `prereg.lock` together (`Trend phase 2: pre-registration frozen`); `git tag trend-prereg-v1`; push the branch and the tag.
- [ ] **Step 2:** `perpbt trend screen` once, in the foreground. Commit `runs/trend/screen.json` (and `summary.md` on stop).
- [ ] **Step 3: STOP** — report `p_screen`, the observed Sharpe against the path distribution, the B&H Sharpe (descriptive) and the decision. Then ask about the `--no-ff` merge into `dev`.

## Progress log

Spec: docs/superpowers/specs/2026-10-23-trend-following/phase-2-prereg-screen.md
Pre-flight:
- T1 -> T2: read_yaml_unique, RebalanceConfig, CostConfig, canonical SignalConfig — consistent.
- T2 -> T4: load_prereg, check_universe, freeze, check_frozen, prereg_hash — consistent.
- T3 -> T4: screen_inputs, run_screen — consistent.
- Ruling: decision days are the day before insample.start through the day before insample.end, so the screen's returns fall on 2020-07-01..2026-10-20 ("trading starts 2020-07-01", Phase 0 §0.3; Phase 3 fills on d+1) — cost if wrong: the series shifts by one day.
- Ruling: members must be listed in the canonical order (return_sign, ma_filter, donchian) and lookbacks strictly increasing, enforced in SignalConfig, so equal signals hash equal; the alternative (sorting silently) hides an edited file — cost if wrong: none (exposure is order-free).
- Ruling: the duplicate-key YAML loader moves to perpbt/config.py (in the Phase 5 §5.4 scope); experiments/prereg.py imports it — spec §2.1 says reuse it, and the import-scope test forbids importing experiments/prereg — cost if wrong: the first strategy's code version moves (closed strategy, overview §11 item 9).
Task 1: complete (commits 30c6b54..370585a, tests: python -m pytest tests/test_config.py tests/test_prereg.py tests/trend -q -m 'not slow' → 236 passed)
- Task 2: Ruling: configs/trend/prereg.yaml is written now (Task 5 step 1 planned it) and is the tests' fixture instead of a copy of the spec YAML inside the tests — one source; the round-trip test pins its values — cost if wrong: none.
- Task 2: Ruling: the small helpers (_mapping, _exact_keys, _iso) are re-implemented in trend/prereg.py rather than imported from experiments/prereg.py (outside the Phase 5 scope) — about 20 lines duplicated — cost if wrong: two copies to keep in step.
- Task 2: Ruling: exploratory rows are stored as full merged SignalConfig + RebalanceConfig (hash covers the merged values) and may override only signal and rebalance — the spec's five rows need nothing else — cost if wrong: a later row type needs a schema change before the freeze.
Task 2: complete (commits 370585a..a6daac6, tests: python -m pytest tests/trend -q -m 'not slow' → 118 passed)
- Task 3: Ruling: test_trending_beats_shifted uses aperiodic regimes (lengths 60-199 days) instead of the plan's fixed 120-day switch — a periodic series lets a shift by a multiple of the period copy the real timing (p was 0.195 on the periodic one; 0.005-0.015 over 5 aperiodic seeds) — cost if wrong: none (test data only).
- Task 3: Ruling: added test_run_screen_chunking_does_not_change_result — pins that the memory chunking is output-neutral — cost: none.
Task 3: complete (commits a6daac6..5c80788, tests: python -m pytest tests/trend -q -m 'not slow' → 130 passed)
- Task 4: Ruling: `trend prereg validate|freeze` take --universe (default configs/trend/universe.json) and exit 2 on a config or universe error, 1 when the lock exists; `trend screen` exits 1 on a refusal (no lock, hash mismatch, screen.json exists) and 2 on a config error — the CLI's existing convention — cost: none.
- Task 4: Ruling: screen_from_store/write_screen live in trend/screen.py (the plan named them there); write_screen opens screen.json with "x", so the one-shot holds even if the CLI's existence check is bypassed — cost: none.
- Task 4: Ruling: added test_prereg_validate_universe_mismatch_is_exit_2 and test_screen_go_writes_no_summary beyond the brief — cost: none.
Task 4: complete (commits 5c80788..0501197, tests: python -m pytest tests/test_cli.py tests/trend -q -m 'not slow' → 159 passed)
- Task 5: Ruling: no separate test_prereg_file.py — test_round_trip and test_universe_must_match_universe_json already load the committed prereg and check it against universe.json — cost: none.
Task 5: suite: python -m pytest -q (full, slow included) → 1103 passed in 152.46s; perpbt trend prereg validate → prereg_hash 9f2f2856cec49fdd38e29da000dd9de315f42f521f3b505763242dddf132c606 (not frozen)
