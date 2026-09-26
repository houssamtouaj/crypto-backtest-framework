# Phase 0 — Scaffold

Read with `00-overview.md`. Delivers the package skeleton, the configuration
types every later phase imports, the code-version hash, and the test
harness. Nothing here touches market data.

**Branch:** `phase/0-scaffold`, created from `dev`; merged into `dev` with
`--no-ff` when the exit criterion below is met (overview §8.1).

## 0.1 Environment (verified 2026-09-26)

- Python 3.11.2 with pandas 2.2.3, numpy 2.2.4, pyarrow 20.0, ccxt 4.5.64,
  matplotlib 3.10.1, scipy 1.15.2, pytest 9.1.1, PyYAML 6.0.2, tzdata.
  `zoneinfo` is in the standard library. No plotly, none needed.
- Git repository exists with `main` and `dev`; remote `origin` is
  `git@github.com:houssamtouaj/crypto-backtest-framework.git`.
- `.gitignore` already excludes `.idea/`, `__pycache__/`, `*.pyc`,
  `.pytest_cache/`, `data/`, `runs/`.

## 0.2 Deliverables

```
pyproject.toml          package perpbt; deps numpy, pandas, pyarrow, scipy, matplotlib, pyyaml;
                        optional extra "ccxt"; pytest config with the "slow" marker registered
perpbt/__init__.py      __version__
perpbt/config.py        frozen dataclasses + YAML load/dump + canonical JSON + hashes
perpbt/version.py       code_version(), git_commit()
perpbt/cli.py           argparse skeleton with the subcommands listed in the overview; each
                        prints "not implemented" until its phase lands
perpbt/{data,indicators,strategy,execution,stats,experiments,report}/__init__.py
tests/conftest.py       shared fixtures (tmp data dir, fixed rng)
tests/synthetic.py      candle builders (see 0.5)
tests/test_config.py, tests/test_version.py, tests/test_synthetic.py
```

`data/` and `runs/` are created on demand by later phases, never committed
(except `data/**/manifest.json`, see Phase 1).

## 0.3 Configuration types (`perpbt/config.py`)

All dataclasses are `frozen=True`. Nested types are dataclasses too, so YAML
round-trip is mechanical.

```python
@dataclass(frozen=True)
class SessionSpec:
    name: str                    # "utc" | "ny" | "london"
    tz: str                      # IANA zone, e.g. "America/New_York"
    open: str                    # "HH:MM" local
    close: str                   # "HH:MM" local; "24:00" allowed
    days: tuple[int, ...]        # ISO weekday numbers Mon=0..Sun=6, in session-local time

@dataclass(frozen=True)
class StopBuffer:
    kind: str                    # "atr" | "pct"
    value: float                 # multiple of ATR14, or fraction of entry price

@dataclass(frozen=True)
class HoldRule:
    kind: str                    # "none" | "session_end" | "max_hold"
    hours: float | None = None   # required iff kind == "max_hold"

@dataclass(frozen=True)
class StrategyParams:
    swing_k: int = 2
    confirm_n: int = 3
    zone: str = "full"           # "full" | "body"
    entry_level: str = "top"     # "top" | "mid"
    stop_buffer: StopBuffer = StopBuffer("atr", 0.1)
    r_target: float = 2.0
    hold_rule: HoldRule = HoldRule("none")
    trend_filter: bool = False
    pierce: float = 0.0          # fraction of price, e.g. 0.0005 = 0.05%
    structure_break: str = "fresh"     # D2; "literal" is accepted but not in the grid
    skip_mitigated: str = "continue"   # D4; "stop" is accepted but not in the grid

@dataclass(frozen=True)
class ExecConfig:
    fee_maker: float = 0.0002
    fee_taker: float = 0.0005
    slippage: float = 0.0002     # fraction of price on stop and time exits
    mmr: float = 0.004           # maintenance margin rate, per pair
    risk_per_trade: float = 0.01
    max_leverage: float = 25.0
    start_equity: float = 10_000.0
    use_1m: bool = True

@dataclass(frozen=True)
class StatsConfig:
    bootstrap_n: int = 10_000
    block_len_days: int = 10
    baseline_runs: int = 5_000
    alpha: float = 0.05
    master_seed: int = 20260926
    reprice_slippage: tuple[float, ...] = (0.0, 0.0002, 0.0005, 0.001)
    reprice_maker: tuple[float, ...] = (0.0, 0.0002)

@dataclass(frozen=True)
class DataConfig:
    data_dir: str
    insample_start: str          # ISO date
    insample_end: str            # ISO date, inclusive
    holdout_start: str
    holdout_end: str | None      # None until data_download_date is set
    warmup_start: str            # "2019-11-01": first candle needed for indicators
    listing: dict[str, str]      # pair -> first perpetual trading date

@dataclass(frozen=True)
class VariantConfig:
    pair: str
    session: SessionSpec
    params: StrategyParams
    exec: ExecConfig
    stats: StatsConfig
    period_start: str            # ISO date; first decision candle
    period_end: str              # ISO date, inclusive
    is_holdout: bool

    def canonical_json(self) -> str
    def config_hash(self) -> str          # sha256 of canonical_json
    def variant_id(self, code_version: str) -> str   # sha256(canonical_json + code_version)
```

Canonical JSON: `json.dumps(asdict(obj), sort_keys=True, separators=(",", ":"))`
with floats rendered by `repr` (Python's shortest round-trip form). YAML
load builds the dataclasses through a small registry keyed on field names;
unknown keys raise. `load_yaml(path, cls)` and `dump_yaml(obj, path)`.

Per-pair overrides (`slippage`, `mmr`) live in the pre-registration file
(Phase 6) and are resolved into `ExecConfig` when a `VariantConfig` is built,
so a `VariantConfig` is always fully explicit.

## 0.4 Code version (`perpbt/version.py`)

```python
def code_version(root: Path = <perpbt package dir>) -> str
    # sha256 over, for every *.py under root in sorted relative-path order:
    #   relative path as UTF-8, a NUL byte, the file bytes, a NUL byte
def git_commit() -> str | None      # `git rev-parse HEAD`, None if unavailable
```

Rationale (D15): docs and test edits must not change `variant_id`, so the
run cache and resume stay valid across commits that do not touch `perpbt/`.

## 0.5 Synthetic data builders (`tests/synthetic.py`)

Used by every later phase's tests. Written now so Phases 1 and 2 can start.

```python
def candles_from_rows(rows, *, start_ms, step_ms=900_000, pair="TEST", tf="15m") -> Candles
    # rows: iterable of (o, h, l, c) or (o, h, l, c, v); ts = start_ms + k*step_ms

def random_walk(n, *, seed, start_price=100.0, step_sigma=0.002, start_ms, step_ms=900_000) -> Candles
    # log-price random walk; each candle's o = previous c; h/l = max/min of o, c plus
    # a seeded positive wick; returns a valid Candles (h >= max(o,c), l <= min(o,c))

def perturb_after(candles, cut, *, seed) -> Candles
    # candles[:cut+1] identical; candles[cut+1:] replaced by a fresh random walk that
    # starts from close[cut], so the perturbation is not detectable from the past

def assert_causal(fn, candles, *, cuts, seeds)
    # for each cut and seed: fn(candles)[:cut+1] == fn(perturb_after(candles, cut))[:cut+1]
    # fn returns an array aligned to the candle index, or a (values, confirmed_at) pair,
    # in which case only entries with confirmed_at <= cut are compared
```

`Candles` itself is defined in Phase 1 (`perpbt/data/store.py`); Phase 0
defines it there as a plain dataclass with `ts, o, h, l, c, v` arrays and
`index_at`/`slice`, and Phase 1 adds the store around it.

## 0.6 Tasks and tests

- **0.1 Package skeleton, `pyproject.toml`, pytest config.**
  Test: `pytest` collects and runs an empty suite; `python -m perpbt --help`
  lists the subcommands.
- **0.2 Config dataclasses, YAML round-trip, canonical hash.**
  Tests: every dataclass round-trips through YAML unchanged; `config_hash`
  is identical for two YAML files with different key order; changing any
  single field changes the hash; `HoldRule("max_hold")` without `hours`
  raises; unknown YAML keys raise; floats `0.1` and `0.10` hash the same.
- **0.3 Code version.**
  Tests: on a temporary tree, hash is stable across runs; editing one byte
  of one `.py` changes it; adding a `.md` or `.pyc` does not; renaming a
  file changes it.
- **0.4 Synthetic builders.**
  Tests: `random_walk` produces valid OHLC and is reproducible per seed;
  `perturb_after` leaves `[:cut+1]` byte-identical and keeps price
  continuity at `cut+1`; `assert_causal` passes on a trivially causal
  function (cumulative sum) and fails on a non-causal one (reversed
  cumulative sum).

Exit criterion: all Phase 0 tests green; `perpbt` importable; the skeleton
committed on `phase/0-scaffold`. Then merge into `dev` and delete the branch.
