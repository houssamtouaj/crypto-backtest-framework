# Phase 0 — Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the `perpbt` package skeleton, the frozen configuration dataclasses with YAML round-trip and canonical hashing, the source-tree code-version hash, the CLI stub, the `Candles` container, and the synthetic-candle test harness that every later phase builds on.

**Architecture:** A plain Python package `perpbt` with empty subpackages for later phases. `config.py` holds frozen dataclasses that coerce and validate their own fields on construction, so a config built from Python, from YAML, or from a mapping is always identical and always hashes the same. `version.py` hashes the source tree bytes. `tests/synthetic.py` builds deterministic random-walk candles and provides the causality assertion used by every later perturbation test.

**Tech Stack:** Python 3.11, numpy 2.2, PyYAML 6, pytest 9, setuptools (pyproject). No market data, no network.

**Spec:** `docs/superpowers/specs/2026-09-26-ob-backtest/phase-0-scaffold.md`, read together with `docs/superpowers/specs/2026-09-26-ob-backtest/00-overview.md` (§5 architecture, §6 conventions, §8.1 branches).

## Global Constraints

- Package name is `perpbt`; module layout is exactly overview §5 (`data`, `indicators`, `strategy`, `execution`, `stats`, `experiments`, `report` subpackages, plus `config.py`, `version.py`, `cli.py`).
- Python `>=3.11`; verified versions: pandas 2.2.3, numpy 2.2.4, pyarrow 20.0, scipy 1.15.2, matplotlib 3.10.1, PyYAML 6.0.2, pytest 9.1.1. `ccxt` is an optional extra, never a hard dependency.
- Every config dataclass is `frozen=True`. Nested types are dataclasses.
- Canonical JSON is `json.dumps(asdict(obj), sort_keys=True, separators=(",", ":"))` with floats rendered by Python `repr`. `config_hash = sha256(canonical_json)`. `variant_id = sha256(canonical_json + code_version)`.
- `code_version(root)` = SHA-256 over every `*.py` under `root`, in sorted relative-path order, feeding `relative_path_utf8, NUL, file_bytes, NUL` per file. Only `.py` files count (D15).
- All timestamps are UTC `int64` milliseconds with an `_ms` suffix; candle timestamps are open times (overview §6).
- `Candles` holds numpy arrays (`ts` int64; `o,h,l,c,v` float64), one pair, one timeframe, sorted, unique.
- Tests: pytest, one test file per module, synthetic data only in this phase; the `slow` marker is registered.
- Git: branch `phase/0-scaffold` from `dev`; push on first commit; small commits each with tests; merge into `dev` with `--no-ff`; delete the branch (overview §8.1).
- `data/` and `runs/` are never committed.
- The CLI subcommands are the overview list: `data fetch`, `data validate`, `run`, `grid`, `baselines`, `report`, `holdout`. Each prints "not implemented" and exits non-zero until its phase lands.
- Working directory for every command below is the repo root `D:\Users\khali\projects\trading_backtest`. Commands are shown for Git Bash.

## Review Focus

1. **Windows path separators in `code_version`.** The relative path fed into the hash must use `/`, never `\`, or the same tree hashes differently on Windows and Linux and every `variant_id` changes across machines. Pinned in Task 6 by `test_matches_hand_computed_format`, which hashes a tree with a subpackage by hand using POSIX paths.
2. **Integer YAML literal for a float field.** `r_target: 2` and `r_target: 2.0` are the same config and must produce the same `config_hash`; otherwise formatting drift silently invalidates the run cache. Pinned in Task 5 by `test_float_text_forms_hash_same`, and by the constructor coercion test in Task 4.
3. **PyYAML 1.1 scalar resolution.** An unquoted `close: 24:00` loads as the integer `1440`, and an unquoted `insample_start: 2020-01-01` loads as a `datetime.date`. The Phase 6 pre-registration file writes dates unquoted, so `date` values must be accepted for string date fields, and integer time values must raise with a message that says to quote the value. Pinned in Task 4 (`test_str_field_accepts_date_object`, `test_str_field_rejects_int_with_quoting_hint`) and Task 5 (`test_load_yaml_unquoted_date_is_accepted`, `test_load_yaml_unquoted_2400_gives_quoting_hint`).
4. **`HoldRule.hours` is required *iff* `kind == "max_hold"`.** Both directions: `max_hold` without hours raises, and hours with any other kind raises, so a grid entry like `{kind: none, hours: 24}` cannot pass unnoticed. Pinned in Task 4.
5. **`git_commit()` when git is missing or the tree is not a repository** must return `None`, not raise, so the runner still records a code version on a machine without git. Pinned in Task 6 by two monkeypatched tests.

---

### Task 1: Branch, package skeleton, `pyproject.toml`, pytest config

**Files:**
- Create: `pyproject.toml`
- Create: `perpbt/__init__.py`
- Create: `perpbt/data/__init__.py`, `perpbt/indicators/__init__.py`, `perpbt/strategy/__init__.py`, `perpbt/execution/__init__.py`, `perpbt/stats/__init__.py`, `perpbt/experiments/__init__.py`, `perpbt/report/__init__.py`
- Create: `tests/__init__.py`
- Create: `.gitattributes`
- Modify: `.gitignore`
- Test: `tests/test_package.py`

**Interfaces:**
- Consumes: nothing.
- Produces: importable package `perpbt` with `perpbt.__version__: str`; pytest configured with `testpaths = ["tests"]`, `--strict-markers`, and the `slow` marker; `tests` is a package so later test files import helpers as `from tests.synthetic import ...`.

- [ ] **Step 1: Create the phase branch from an up-to-date `dev`**

```bash
git switch dev
git pull
git switch -c phase/0-scaffold dev
git status --short   # expected: empty
```

- [ ] **Step 2: Write the failing smoke test**

Create `tests/__init__.py` as an empty file, then `tests/test_package.py`:

```python
"""Smoke tests: the package and every subpackage import."""
import importlib

import pytest

import perpbt

SUBPACKAGES = [
    "perpbt.data",
    "perpbt.indicators",
    "perpbt.strategy",
    "perpbt.execution",
    "perpbt.stats",
    "perpbt.experiments",
    "perpbt.report",
]


def test_version_is_a_non_empty_string():
    assert isinstance(perpbt.__version__, str)
    assert perpbt.__version__


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name):
    assert importlib.import_module(name).__name__ == name
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python -m pytest tests/test_package.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'perpbt'`.

- [ ] **Step 4: Create the package skeleton**

`perpbt/__init__.py`:

```python
"""perpbt: backtest framework for Binance USDT-M perpetuals.

Phase 0 delivers the package skeleton, configuration types, the code-version
hash, and the test harness. See docs/superpowers/specs/2026-09-26-ob-backtest/.
"""

__version__ = "0.0.1"
```

Each of the seven subpackage `__init__.py` files contains one docstring line naming the phase that fills it:

```python
"""perpbt.data: candles and funding stores, validation, session calendar (Phase 1)."""
```

```python
"""perpbt.indicators: ATR, swing highs, daily SMA/ADX, MarketView guard (Phase 2)."""
```

```python
"""perpbt.strategy: Strategy protocol, MarketView, the order-block rule (Phase 3)."""
```

```python
"""perpbt.execution: orders, fills, costs, sizing, ledger, simulator (Phase 4)."""
```

```python
"""perpbt.stats: bootstraps, evaluator, baselines, re-pricing, DSR, Holm (Phase 5)."""
```

```python
"""perpbt.experiments: pre-registration, registry, grid, runner, holdout guard (Phase 6)."""
```

```python
"""perpbt.report: figures, per-variant report, summary (Phase 7)."""
```

- [ ] **Step 5: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "perpbt"
description = "Backtest framework for Binance USDT-M perpetuals (ICT order-block strategy)"
requires-python = ">=3.11"
dynamic = ["version"]
dependencies = [
    "numpy>=2.0",
    "pandas>=2.2",
    "pyarrow>=15",
    "scipy>=1.13",
    "matplotlib>=3.8",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
ccxt = ["ccxt>=4.0"]
dev = ["pytest>=8"]

[project.scripts]
perpbt = "perpbt.cli:main"

[tool.setuptools.packages.find]
include = ["perpbt*"]

[tool.setuptools.dynamic]
version = {attr = "perpbt.__version__"}

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
markers = [
    "slow: uses real downloaded market data; deselect with -m 'not slow'",
]
```

- [ ] **Step 6: Git housekeeping files**

Append to `.gitignore` (keep the existing lines):

```
*.egg-info/
build/
dist/
```

Create `.gitattributes` so `code_version()` hashes the same bytes on every OS (this machine has `core.autocrlf=true`, which would otherwise check out `.py` files with CRLF and change the hash relative to a Linux checkout):

```
* text=auto
*.py text eol=lf
```

- [ ] **Step 7: Run the smoke test and the whole suite**

Run: `python -m pytest -q`
Expected: `8 passed` (1 version test + 7 subpackage imports). No warnings about unknown markers.

- [ ] **Step 8: Editable install (optional convenience)**

Run: `python -m pip install -e .`
Expected: `Successfully installed perpbt-0.0.1`. If pip cannot reach the package index, skip this step; the tests and `python -m perpbt` work from the repo root without it because pytest inserts the root on `sys.path` (rootdir with `tests/__init__.py`) and `-m` uses the current directory.

- [ ] **Step 9: Commit and push the branch**

```bash
git add pyproject.toml perpbt tests .gitignore .gitattributes
git commit -m "Phase 0: package skeleton, pyproject, pytest config"
git push -u origin phase/0-scaffold
```

---

### Task 2: CLI skeleton

**Files:**
- Create: `perpbt/cli.py`
- Create: `perpbt/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `perpbt.cli.build_parser() -> argparse.ArgumentParser` and `perpbt.cli.main(argv: Sequence[str] | None = None) -> int`. Every subparser sets `handler` via `set_defaults(handler=fn)` where `fn(args: argparse.Namespace) -> int`; later phases replace a stub by calling `set_defaults(handler=real_fn)` on that subparser and adding arguments to it.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
"""CLI skeleton: every subcommand exists and reports 'not implemented'."""
import subprocess
import sys
from pathlib import Path

import pytest

from perpbt.cli import main

ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL = ["data", "run", "grid", "baselines", "report", "holdout"]
ALL_ARGV = [
    ["data", "fetch"],
    ["data", "validate"],
    ["run"],
    ["grid"],
    ["baselines"],
    ["report"],
    ["holdout"],
]


def test_help_lists_every_subcommand(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name in TOP_LEVEL:
        assert name in out


def test_data_help_lists_fetch_and_validate(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["data", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "fetch" in out
    assert "validate" in out


def test_python_dash_m_perpbt_help_runs():
    result = subprocess.run(
        [sys.executable, "-m", "perpbt", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for name in TOP_LEVEL:
        assert name in result.stdout


@pytest.mark.parametrize("argv", ALL_ARGV, ids=lambda a: " ".join(a))
def test_stub_reports_not_implemented_and_fails(argv, capsys):
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert "not implemented" in err
    assert " ".join(argv) in err


def test_no_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_cli.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'perpbt.cli'`.

- [ ] **Step 3: Implement `perpbt/cli.py`**

```python
"""Command-line entry point.

Phase 0 registers every subcommand from the overview as a stub that prints
"not implemented" and exits with status 2. Each later phase replaces the
handler of its own subparser and adds that subparser's arguments.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

Handler = Callable[[argparse.Namespace], int]

# (name, help text, phase that implements it)
_TOP_LEVEL: tuple[tuple[str, str, int], ...] = (
    ("run", "run one variant, or the nine primary cells, end to end", 6),
    ("grid", "enumerate (--dry-run) or run the pre-registered grid", 6),
    ("baselines", "run random-timing baselines A and B", 6),
    ("report", "write per-variant reports and the summary", 7),
    ("holdout", "one-shot holdout run with the guard", 6),
)
_DATA: tuple[tuple[str, str, int], ...] = (
    ("fetch", "download the bulk archive and the ccxt head/tail", 1),
    ("validate", "validate stored candles and funding", 1),
)


def _not_implemented(name: str, phase: int) -> Handler:
    def handler(args: argparse.Namespace) -> int:
        print(f"perpbt {name}: not implemented (Phase {phase})", file=sys.stderr)
        return 2

    return handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="perpbt",
        description="Backtest framework for Binance USDT-M perpetuals.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    data = sub.add_parser("data", help="fetch and validate market data")
    data_sub = data.add_subparsers(dest="data_command", metavar="SUBCOMMAND", required=True)
    for name, help_text, phase in _DATA:
        p = data_sub.add_parser(name, help=help_text)
        p.set_defaults(handler=_not_implemented(f"data {name}", phase))

    for name, help_text, phase in _TOP_LEVEL:
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(handler=_not_implemented(name, phase))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Handler = args.handler
    return handler(args)
```

`perpbt/__main__.py`:

```python
"""Allow ``python -m perpbt``."""
import sys

from perpbt.cli import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_cli.py -q`
Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add perpbt/cli.py perpbt/__main__.py tests/test_cli.py
git commit -m "Phase 0: CLI skeleton with not-implemented stubs"
```

---

### Task 3: `Candles` container

**Files:**
- Create: `perpbt/data/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `perpbt.data.store.Candles` frozen dataclass with fields `pair: str, tf: str, ts, o, h, l, c, v: np.ndarray`, and methods `__len__() -> int`, `index_at(ts_ms: int, *, exact: bool = False) -> int`, `slice(start_ms: int, end_ms: int) -> Candles`, `step_ms() -> int`. Construction coerces `ts` to int64 and the price arrays to float64, and raises `ValueError` on unequal lengths or non-increasing `ts`, `TypeError` on a non-integer `ts`. Phase 1 adds `Funding`, `CandleStore`, `FundingStore` to this module without changing `Candles`.

- [ ] **Step 1: Write the failing tests**

`tests/test_store.py`:

```python
"""Candles container (Phase 0 part of perpbt.data.store)."""
import numpy as np
import pytest

from perpbt.data.store import Candles


def make(n=5, start_ms=1_000, step_ms=100, tf="15m"):
    ts = start_ms + step_ms * np.arange(n)
    o = np.full(n, 10.0)
    c = o + 1.0
    h = c + 1.0
    l = o - 1.0
    v = np.ones(n)
    return Candles("TEST", tf, ts, o, h, l, c, v)


def test_len_and_dtypes():
    cd = make(5)
    assert len(cd) == 5
    assert cd.ts.dtype == np.int64
    for name in ("o", "h", "l", "c", "v"):
        assert getattr(cd, name).dtype == np.float64


def test_lists_are_coerced_to_arrays():
    cd = Candles("TEST", "15m", [0, 900_000], [1, 2], [2, 3], [0, 1], [1.5, 2.5], [1, 1])
    assert cd.ts.dtype == np.int64
    assert cd.c.dtype == np.float64
    assert cd.c.tolist() == [1.5, 2.5]


def test_empty_is_allowed():
    z = np.zeros(0)
    cd = Candles("TEST", "15m", np.zeros(0, dtype=np.int64), z, z, z, z, z)
    assert len(cd) == 0


def test_arrays_must_have_equal_length():
    with pytest.raises(ValueError, match="length"):
        Candles("TEST", "15m", [0, 1], [1, 2], [2, 3], [0, 1], [1.5], [1, 1])


@pytest.mark.parametrize("ts", [[0, 0], [10, 5], [0, 5, 5, 9]])
def test_ts_must_be_strictly_increasing(ts):
    n = len(ts)
    a = np.ones(n)
    with pytest.raises(ValueError, match="strictly increasing"):
        Candles("TEST", "15m", ts, a, a, a, a, a)


def test_ts_must_be_integer():
    a = np.ones(2)
    with pytest.raises(TypeError, match="integer"):
        Candles("TEST", "15m", [0.0, 1.5], a, a, a, a, a)


def test_index_at_is_left_searchsorted():
    cd = make(5)  # ts = 1000, 1100, 1200, 1300, 1400
    assert cd.index_at(1000) == 0
    assert cd.index_at(1050) == 1
    assert cd.index_at(1100) == 1
    assert cd.index_at(999) == 0
    assert cd.index_at(1400) == 4
    assert cd.index_at(1401) == 5
    assert isinstance(cd.index_at(1100), int)


def test_index_at_exact():
    cd = make(5)
    assert cd.index_at(1100, exact=True) == 1
    with pytest.raises(KeyError):
        cd.index_at(1050, exact=True)
    with pytest.raises(KeyError):
        cd.index_at(99_999, exact=True)


def test_slice_is_half_open():
    cd = make(5)
    s = cd.slice(1100, 1300)
    assert s.ts.tolist() == [1100, 1200]
    assert s.pair == "TEST" and s.tf == "15m"
    assert cd.slice(1150, 1250).ts.tolist() == [1200]
    assert len(cd.slice(1300, 1300)) == 0
    assert cd.slice(0, 10_000).ts.tolist() == cd.ts.tolist()


def test_slice_keeps_columns_aligned():
    cd = make(5)
    s = cd.slice(1200, 1400)
    assert s.o.tolist() == cd.o[2:4].tolist()
    assert s.v.tolist() == cd.v[2:4].tolist()


def test_step_ms_from_timeframe():
    assert make(tf="1m").step_ms() == 60_000
    assert make(tf="15m").step_ms() == 900_000
    with pytest.raises(ValueError, match="timeframe"):
        make(tf="1h").step_ms()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_store.py -q`
Expected: collection error, `ImportError: cannot import name 'Candles'` (or `ModuleNotFoundError` for `perpbt.data.store`).

- [ ] **Step 3: Implement `perpbt/data/store.py`**

```python
"""Candle container.

Phase 0 defines ``Candles``. Phase 1 adds ``Funding``, ``CandleStore`` and
``FundingStore`` around it. Arrays at the core (overview §6): numpy only.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_STEP_MS: dict[str, int] = {"1m": 60_000, "15m": 900_000}


@dataclass(frozen=True, eq=False)
class Candles:
    """One pair, one timeframe, UTC.

    ``ts`` is the candle open time in int64 milliseconds and is strictly
    increasing (sorted, unique). Price and volume arrays are float64 and
    aligned to ``ts``. Instances are immutable; ``slice`` returns views.
    """

    pair: str
    tf: str
    ts: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741 - column name from the spec
    c: np.ndarray
    v: np.ndarray

    def __post_init__(self) -> None:
        ts = np.asarray(self.ts)
        if ts.ndim != 1:
            raise ValueError(f"Candles.ts: expected a 1-D array, got shape {ts.shape}")
        if not np.issubdtype(ts.dtype, np.integer):
            raise TypeError(f"Candles.ts: expected an integer dtype (UTC ms), got {ts.dtype}")
        ts = ts.astype(np.int64, copy=False)
        object.__setattr__(self, "ts", ts)
        n = len(ts)
        for name in ("o", "h", "l", "c", "v"):
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            if arr.ndim != 1 or len(arr) != n:
                raise ValueError(
                    f"Candles.{name}: expected a 1-D array of length {n}, got shape {arr.shape}"
                )
            object.__setattr__(self, name, arr)
        if n > 1 and not np.all(np.diff(ts) > 0):
            raise ValueError("Candles.ts must be strictly increasing (sorted, unique)")

    def __len__(self) -> int:
        return len(self.ts)

    def index_at(self, ts_ms: int, *, exact: bool = False) -> int:
        """Index of the first candle with open time >= ``ts_ms`` (searchsorted, left).

        With ``exact=True`` the candle must exist, else ``KeyError``.
        """
        i = int(np.searchsorted(self.ts, ts_ms, side="left"))
        if exact and (i == len(self.ts) or self.ts[i] != ts_ms):
            raise KeyError(f"no {self.pair} {self.tf} candle opens at {ts_ms} ms")
        return i

    def slice(self, start_ms: int, end_ms: int) -> Candles:
        """Candles with ``start_ms <= ts < end_ms``, as views on the same arrays."""
        a = self.index_at(start_ms)
        b = self.index_at(end_ms)
        return Candles(
            self.pair, self.tf,
            self.ts[a:b], self.o[a:b], self.h[a:b], self.l[a:b], self.c[a:b], self.v[a:b],
        )

    def step_ms(self) -> int:
        """Nominal candle spacing in ms for this timeframe (60_000 or 900_000)."""
        try:
            return _STEP_MS[self.tf]
        except KeyError:
            raise ValueError(
                f"unknown timeframe {self.tf!r}; expected one of {sorted(_STEP_MS)}"
            ) from None
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_store.py -q`
Expected: `13 passed`.

- [ ] **Step 5: Commit**

```bash
git add perpbt/data/store.py tests/test_store.py
git commit -m "Phase 0: Candles container with index_at, slice, step_ms"
```

---

### Task 4: Configuration dataclasses with coercion and validation

**Files:**
- Create: `perpbt/config.py` (dataclasses, `_coerce`, `from_dict`, `to_dict`; YAML and hashing come in Task 5)
- Test: `tests/test_config.py` (construction and validation tests; Task 5 appends the YAML and hash tests to the same file)

**Interfaces:**
- Consumes: nothing.
- Produces: `ConfigError(ValueError)`; the frozen dataclasses `SessionSpec, StopBuffer, HoldRule, StrategyParams, ExecConfig, StatsConfig, DataConfig, VariantConfig` with exactly the spec §0.3 fields and defaults; `from_dict(cls, data: Mapping) -> cls` (unknown keys, missing required keys, wrong types all raise `ConfigError`); `to_dict(obj) -> dict` (nested dicts, tuples as lists). Construction coerces `int -> float` for float fields, `list -> tuple` for tuple fields, `Mapping -> dataclass` for nested fields, and `datetime.date -> ISO str` for str fields; it rejects `bool` where a number is expected, non-integers where `int` is expected, and non-bools where `bool` is expected.

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:

```python
"""Configuration types: construction, coercion, validation, from_dict/to_dict."""
import dataclasses
import json
from datetime import date

import pytest

from perpbt.config import (
    ConfigError,
    DataConfig,
    ExecConfig,
    HoldRule,
    SessionSpec,
    StatsConfig,
    StopBuffer,
    StrategyParams,
    VariantConfig,
    from_dict,
    to_dict,
)

NY = SessionSpec("ny", "America/New_York", "09:30", "16:00", (0, 1, 2, 3, 4))
UTC = SessionSpec("utc", "UTC", "00:00", "24:00", (0, 1, 2, 3, 4, 5, 6))


def data_config(**overrides) -> DataConfig:
    kw = dict(
        data_dir="data",
        insample_start="2020-01-01",
        insample_end="2025-12-31",
        holdout_start="2026-01-01",
        holdout_end=None,
        warmup_start="2019-11-01",
        listing={"BTCUSDT": "2019-09-08", "ETHUSDT": "2019-11-27", "SOLUSDT": "2020-09-14"},
    )
    kw.update(overrides)
    return DataConfig(**kw)


def primary_variant(**overrides) -> VariantConfig:
    kw = dict(
        pair="BTCUSDT",
        session=NY,
        params=StrategyParams(),
        exec=ExecConfig(),
        stats=StatsConfig(),
        period_start="2020-01-01",
        period_end="2025-12-31",
        is_holdout=False,
    )
    kw.update(overrides)
    return VariantConfig(**kw)


# --- defaults and immutability ------------------------------------------------


def test_strategy_defaults_match_spec():
    p = StrategyParams()
    assert (p.swing_k, p.confirm_n, p.zone, p.entry_level) == (2, 3, "full", "top")
    assert p.stop_buffer == StopBuffer("atr", 0.1)
    assert p.r_target == 2.0
    assert p.hold_rule == HoldRule("none")
    assert (p.trend_filter, p.pierce) == (False, 0.0)
    assert (p.structure_break, p.skip_mitigated) == ("fresh", "continue")


def test_exec_defaults_match_spec():
    e = ExecConfig()
    assert (e.fee_maker, e.fee_taker, e.slippage, e.mmr) == (0.0002, 0.0005, 0.0002, 0.004)
    assert (e.risk_per_trade, e.max_leverage, e.start_equity, e.use_1m) == (0.01, 25.0, 10_000.0, True)


def test_stats_defaults_match_spec():
    s = StatsConfig()
    assert (s.bootstrap_n, s.block_len_days, s.baseline_runs) == (10_000, 10, 5_000)
    assert (s.alpha, s.master_seed) == (0.05, 20260926)
    assert s.reprice_slippage == (0.0, 0.0002, 0.0005, 0.001)
    assert s.reprice_maker == (0.0, 0.0002)


def test_dataclasses_are_frozen():
    p = StrategyParams()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.swing_k = 3  # type: ignore[misc]


# --- coercion ---------------------------------------------------------------


def test_int_for_float_field_is_coerced():
    p = StrategyParams(r_target=2)
    assert isinstance(p.r_target, float) and p.r_target == 2.0
    e = ExecConfig(start_equity=10_000)
    assert isinstance(e.start_equity, float)


def test_list_for_tuple_field_is_coerced():
    s = SessionSpec("ny", "America/New_York", "09:30", "16:00", [0, 1])
    assert s.days == (0, 1) and isinstance(s.days, tuple)
    st = StatsConfig(reprice_maker=[0, 0.0002])
    assert st.reprice_maker == (0.0, 0.0002)
    assert all(isinstance(x, float) for x in st.reprice_maker)


def test_nested_mapping_builds_nested_dataclass():
    p = StrategyParams(stop_buffer={"kind": "pct", "value": 0.001})
    assert p.stop_buffer == StopBuffer("pct", 0.001)
    v = primary_variant(session={"name": "utc", "tz": "UTC", "open": "00:00", "close": "24:00", "days": [0, 1, 2, 3, 4, 5, 6]})
    assert v.session == UTC


def test_str_field_accepts_date_object():
    d = data_config(insample_start=date(2020, 1, 1), listing={"BTCUSDT": date(2019, 9, 8)})
    assert d.insample_start == "2020-01-01"
    assert d.listing == {"BTCUSDT": "2019-09-08"}


def test_str_field_rejects_int_with_quoting_hint():
    with pytest.raises(ConfigError, match="quote"):
        SessionSpec("utc", "UTC", "00:00", 1440, (0,))


@pytest.mark.parametrize("bad", ["2", 2.5, True, None])
def test_int_field_rejects_non_integers(bad):
    with pytest.raises(ConfigError, match="swing_k"):
        StrategyParams(swing_k=bad)


def test_bool_field_rejects_int():
    with pytest.raises(ConfigError, match="trend_filter"):
        StrategyParams(trend_filter=1)


@pytest.mark.parametrize("bad", [True, "0.1", None])
def test_float_field_rejects_bool_str_none(bad):
    with pytest.raises(ConfigError, match="fee_maker"):
        ExecConfig(fee_maker=bad)


def test_optional_field_accepts_none_and_value():
    assert data_config(holdout_end=None).holdout_end is None
    assert data_config(holdout_end="2026-09-27").holdout_end == "2026-09-27"
    assert HoldRule("max_hold", hours=24).hours == 24.0


# --- validation -------------------------------------------------------------


def test_hold_rule_max_hold_requires_hours():
    with pytest.raises(ConfigError, match="hours"):
        HoldRule("max_hold")


def test_hold_rule_hours_only_with_max_hold():
    with pytest.raises(ConfigError, match="hours"):
        HoldRule("none", hours=24)
    with pytest.raises(ConfigError, match="hours"):
        HoldRule("session_end", hours=24)


def test_hold_rule_hours_must_be_positive():
    with pytest.raises(ConfigError, match="hours"):
        HoldRule("max_hold", hours=0)


@pytest.mark.parametrize(
    "build",
    [
        lambda: StopBuffer("atrr", 0.1),
        lambda: HoldRule("forever"),
        lambda: StrategyParams(zone="wick"),
        lambda: StrategyParams(entry_level="bottom"),
        lambda: StrategyParams(structure_break="loose"),
        lambda: StrategyParams(skip_mitigated="retry"),
    ],
)
def test_literal_fields_are_validated(build):
    with pytest.raises(ConfigError, match="expected one of"):
        build()


def test_literal_alternatives_accepted_but_not_in_grid():
    assert StrategyParams(structure_break="literal").structure_break == "literal"
    assert StrategyParams(skip_mitigated="stop").skip_mitigated == "stop"


@pytest.mark.parametrize("days", [(7,), (-1,), (0, 0), ()])
def test_session_days_validated(days):
    with pytest.raises(ConfigError, match="days"):
        SessionSpec("x", "UTC", "00:00", "24:00", days)


@pytest.mark.parametrize("open_, close", [("9:30", "16:00"), ("24:00", "24:00"), ("09:30", "16:60"), ("09:30", "25:00"), ("09:30", "24:30")])
def test_session_time_format_validated(open_, close):
    with pytest.raises(ConfigError, match="(open|close)"):
        SessionSpec("x", "UTC", open_, close, (0,))


def test_session_close_2400_allowed():
    assert UTC.close == "24:00"


def test_session_tz_validated():
    with pytest.raises(ConfigError, match="tz"):
        SessionSpec("x", "Mars/Olympus_Mons", "00:00", "24:00", (0,))


@pytest.mark.parametrize("field, value", [("insample_start", "2020/01/01"), ("warmup_start", "20191101"), ("holdout_end", "yesterday")])
def test_data_config_dates_validated(field, value):
    with pytest.raises(ConfigError, match=field):
        data_config(**{field: value})


def test_data_config_listing_dates_validated():
    with pytest.raises(ConfigError, match="listing"):
        data_config(listing={"BTCUSDT": "not a date"})


def test_data_config_insample_order_validated():
    with pytest.raises(ConfigError, match="insample"):
        data_config(insample_start="2026-01-01", insample_end="2025-12-31")


def test_variant_period_validated():
    with pytest.raises(ConfigError, match="period"):
        primary_variant(period_start="2025-12-31", period_end="2020-01-01")
    with pytest.raises(ConfigError, match="period_end"):
        primary_variant(period_end="2025-13-01")


@pytest.mark.parametrize(
    "build",
    [
        lambda: StrategyParams(swing_k=0),
        lambda: StrategyParams(confirm_n=0),
        lambda: StrategyParams(r_target=0),
        lambda: StrategyParams(pierce=-0.1),
        lambda: ExecConfig(risk_per_trade=0),
        lambda: ExecConfig(risk_per_trade=1.5),
        lambda: ExecConfig(max_leverage=0),
        lambda: ExecConfig(start_equity=0),
        lambda: ExecConfig(fee_taker=-1),
        lambda: StatsConfig(bootstrap_n=0),
        lambda: StatsConfig(alpha=0),
        lambda: StatsConfig(alpha=1),
        lambda: StatsConfig(reprice_slippage=()),
    ],
)
def test_numeric_ranges_validated(build):
    with pytest.raises(ConfigError):
        build()


# --- from_dict / to_dict ----------------------------------------------------


def test_from_dict_builds_nested_config():
    v = from_dict(VariantConfig, to_dict(primary_variant()))
    assert v == primary_variant()


def test_from_dict_unknown_key_raises():
    with pytest.raises(ConfigError, match="unknown.*vlaue"):
        from_dict(StopBuffer, {"kind": "atr", "value": 0.1, "vlaue": 1})


def test_from_dict_nested_unknown_key_raises():
    with pytest.raises(ConfigError, match="unknown.*extra"):
        from_dict(StrategyParams, {"stop_buffer": {"kind": "atr", "value": 0.1, "extra": 1}})


def test_from_dict_missing_required_key_raises():
    with pytest.raises(ConfigError, match="missing.*value"):
        from_dict(StopBuffer, {"kind": "atr"})


def test_from_dict_rejects_non_mapping():
    with pytest.raises(ConfigError, match="mapping"):
        from_dict(StopBuffer, ["atr", 0.1])


def test_to_dict_is_plain_json_serialisable():
    d = to_dict(primary_variant())
    assert isinstance(d["session"]["days"], list)
    assert isinstance(d["stats"]["reprice_maker"], list)
    assert d["params"]["hold_rule"] == {"kind": "none", "hours": None}
    assert d["exec"]["start_equity"] == 10_000.0
    json.dumps(d)  # must not raise
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_config.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'perpbt.config'`.

- [ ] **Step 3: Implement `perpbt/config.py`**

```python
"""Configuration types: frozen dataclasses, YAML round-trip, canonical JSON, hashes.

Every type here is immutable and fully explicit. Field values are coerced to
the declared annotation on construction (so ``r_target=2`` and
``r_target=2.0`` are the same config and hash the same) and then validated.
YAML loading goes through :func:`from_dict`, which rejects unknown keys.
Hashes are over the canonical JSON form (sorted keys, compact separators,
floats rendered by ``repr``).
"""
from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import re
import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

__all__ = [
    "ConfigError",
    "SessionSpec",
    "StopBuffer",
    "HoldRule",
    "StrategyParams",
    "ExecConfig",
    "StatsConfig",
    "DataConfig",
    "VariantConfig",
    "from_dict",
    "to_dict",
    "canonical_json",
    "config_hash",
    "load_yaml",
    "dump_yaml",
]

T = TypeVar("T")


class ConfigError(ValueError):
    """Unknown or missing keys, wrong types, or invalid values in a config."""


# --- type-directed coercion -----------------------------------------------------


@functools.lru_cache(maxsize=None)
def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def _coerce(tp: Any, value: Any, where: str) -> Any:
    """Return ``value`` converted to annotation ``tp``, or raise ConfigError."""
    if isinstance(tp, type) and is_dataclass(tp):
        if isinstance(value, tp):
            return value
        if isinstance(value, Mapping):
            return from_dict(tp, value)
        raise ConfigError(f"{where}: expected a {tp.__name__} mapping, got {type(value).__name__}")

    origin = typing.get_origin(tp)
    if origin is None:
        if tp is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(f"{where}: expected a number, got {value!r}")
            return float(value)
        if tp is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(f"{where}: expected an integer, got {value!r}")
            return value
        if tp is bool:
            if not isinstance(value, bool):
                raise ConfigError(f"{where}: expected true/false, got {value!r}")
            return value
        if tp is str:
            if isinstance(value, str):
                return value
            if isinstance(value, date) and not isinstance(value, datetime):
                return value.isoformat()  # YAML parses an unquoted 2020-01-01 as a date
            if isinstance(value, int) and not isinstance(value, bool):
                raise ConfigError(
                    f"{where}: expected a string, got {value!r} "
                    "(YAML reads unquoted values like 24:00 as numbers; quote them: '24:00')"
                )
            raise ConfigError(f"{where}: expected a string, got {value!r}")
        raise ConfigError(f"{where}: unsupported field type {tp!r}")

    args = typing.get_args(tp)
    if origin in (types.UnionType, typing.Union):
        if value is None:
            if type(None) in args:
                return None
            raise ConfigError(f"{where}: null is not allowed")
        inner = [a for a in args if a is not type(None)]
        if len(inner) != 1:
            raise ConfigError(f"{where}: unsupported field type {tp!r}")
        return _coerce(inner[0], value, where)
    if origin is tuple:
        if isinstance(value, str) or not isinstance(value, (list, tuple)):
            raise ConfigError(f"{where}: expected a list, got {value!r}")
        if len(args) != 2 or args[1] is not Ellipsis:
            raise ConfigError(f"{where}: unsupported field type {tp!r}")
        return tuple(_coerce(args[0], v, f"{where}[{k}]") for k, v in enumerate(value))
    if origin is dict:
        if not isinstance(value, Mapping):
            raise ConfigError(f"{where}: expected a mapping, got {value!r}")
        key_tp, val_tp = args
        return {
            _coerce(key_tp, k, f"{where} key"): _coerce(val_tp, v, f"{where}[{k!r}]")
            for k, v in value.items()
        }
    raise ConfigError(f"{where}: unsupported field type {tp!r}")


def from_dict(cls: type[T], data: Mapping[str, Any]) -> T:
    """Build dataclass ``cls`` from a mapping. Unknown or missing keys raise."""
    if not (isinstance(cls, type) and is_dataclass(cls)):
        raise TypeError(f"{cls!r} is not a dataclass type")
    if not isinstance(data, Mapping):
        raise ConfigError(f"{cls.__name__}: expected a mapping, got {type(data).__name__}")
    names = [f.name for f in fields(cls)]
    unknown = [k for k in data if k not in names]
    if unknown:
        raise ConfigError(f"{cls.__name__}: unknown keys {unknown}")
    missing = [
        f.name
        for f in fields(cls)
        if f.name not in data
        and f.default is dataclasses.MISSING
        and f.default_factory is dataclasses.MISSING
    ]
    if missing:
        raise ConfigError(f"{cls.__name__}: missing required keys {missing}")
    return cls(**dict(data))  # __post_init__ coerces and validates


def _plain(x: Any) -> Any:
    if is_dataclass(x) and not isinstance(x, type):
        return _plain(dataclasses.asdict(x))
    if isinstance(x, Mapping):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return x


def to_dict(obj: Any) -> Any:
    """Nested plain dicts and lists (tuples become lists), ready for YAML or JSON."""
    return _plain(obj)


# --- validation helpers --------------------------------------------------------

_HHMM = re.compile(r"^(\d{2}):(\d{2})$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _check_hhmm(value: str, where: str, *, allow_24: bool) -> None:
    m = _HHMM.match(value)
    if not m:
        raise ConfigError(f"{where}: expected 'HH:MM', got {value!r}")
    hh, mm = int(m[1]), int(m[2])
    if mm > 59 or hh > 24 or (hh == 24 and (mm != 0 or not allow_24)):
        raise ConfigError(f"{where}: invalid time {value!r}")


def _check_iso_date(value: str, where: str) -> None:
    if not _ISO_DATE.match(value):
        raise ConfigError(f"{where}: expected an ISO date 'YYYY-MM-DD', got {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ConfigError(f"{where}: invalid date {value!r}") from None


def _check_literal(value: str, allowed: tuple[str, ...], where: str) -> None:
    if value not in allowed:
        raise ConfigError(f"{where}: expected one of {list(allowed)}, got {value!r}")


def _check_positive(value: float, where: str) -> None:
    if not value > 0:
        raise ConfigError(f"{where}: must be > 0, got {value!r}")


def _check_non_negative(value: float, where: str) -> None:
    if value < 0:
        raise ConfigError(f"{where}: must be >= 0, got {value!r}")


# --- dataclasses ---------------------------------------------------------------


@dataclass(frozen=True)
class _Config:
    """Base: coerce every field to its annotation, then run ``_validate``."""

    def __post_init__(self) -> None:
        hints = _hints(type(self))
        for f in fields(self):
            where = f"{type(self).__name__}.{f.name}"
            object.__setattr__(self, f.name, _coerce(hints[f.name], getattr(self, f.name), where))
        self._validate()

    def _validate(self) -> None:
        pass


@dataclass(frozen=True)
class SessionSpec(_Config):
    name: str  # "utc" | "ny" | "london"
    tz: str  # IANA zone, e.g. "America/New_York"
    open: str  # "HH:MM" local
    close: str  # "HH:MM" local; "24:00" allowed
    days: tuple[int, ...]  # ISO weekday numbers Mon=0..Sun=6, in session-local time

    def _validate(self) -> None:
        try:
            ZoneInfo(self.tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(f"SessionSpec.tz: unknown IANA zone {self.tz!r}") from None
        _check_hhmm(self.open, "SessionSpec.open", allow_24=False)
        _check_hhmm(self.close, "SessionSpec.close", allow_24=True)
        if not self.days:
            raise ConfigError("SessionSpec.days: must not be empty")
        bad = [d for d in self.days if not 0 <= d <= 6]
        if bad:
            raise ConfigError(f"SessionSpec.days: weekdays must be 0 (Mon) .. 6 (Sun), got {bad}")
        if len(set(self.days)) != len(self.days):
            raise ConfigError(f"SessionSpec.days: duplicate weekday in {list(self.days)}")


@dataclass(frozen=True)
class StopBuffer(_Config):
    kind: str  # "atr" | "pct"
    value: float  # multiple of ATR14, or fraction of entry price

    def _validate(self) -> None:
        _check_literal(self.kind, ("atr", "pct"), "StopBuffer.kind")
        _check_non_negative(self.value, "StopBuffer.value")


@dataclass(frozen=True)
class HoldRule(_Config):
    kind: str  # "none" | "session_end" | "max_hold"
    hours: float | None = None  # required iff kind == "max_hold"

    def _validate(self) -> None:
        _check_literal(self.kind, ("none", "session_end", "max_hold"), "HoldRule.kind")
        if self.kind == "max_hold":
            if self.hours is None:
                raise ConfigError("HoldRule.hours: required when kind is 'max_hold'")
            _check_positive(self.hours, "HoldRule.hours")
        elif self.hours is not None:
            raise ConfigError(f"HoldRule.hours: only allowed when kind is 'max_hold' (kind is {self.kind!r})")


@dataclass(frozen=True)
class StrategyParams(_Config):
    swing_k: int = 2
    confirm_n: int = 3
    zone: str = "full"  # "full" | "body"
    entry_level: str = "top"  # "top" | "mid"
    stop_buffer: StopBuffer = StopBuffer("atr", 0.1)
    r_target: float = 2.0
    hold_rule: HoldRule = HoldRule("none")
    trend_filter: bool = False
    pierce: float = 0.0  # fraction of price, e.g. 0.0005 = 0.05%
    structure_break: str = "fresh"  # D2; "literal" is accepted but not in the grid
    skip_mitigated: str = "continue"  # D4; "stop" is accepted but not in the grid

    def _validate(self) -> None:
        _check_literal(self.zone, ("full", "body"), "StrategyParams.zone")
        _check_literal(self.entry_level, ("top", "mid"), "StrategyParams.entry_level")
        _check_literal(self.structure_break, ("fresh", "literal"), "StrategyParams.structure_break")
        _check_literal(self.skip_mitigated, ("continue", "stop"), "StrategyParams.skip_mitigated")
        _check_positive(self.swing_k, "StrategyParams.swing_k")
        _check_positive(self.confirm_n, "StrategyParams.confirm_n")
        _check_positive(self.r_target, "StrategyParams.r_target")
        _check_non_negative(self.pierce, "StrategyParams.pierce")


@dataclass(frozen=True)
class ExecConfig(_Config):
    fee_maker: float = 0.0002
    fee_taker: float = 0.0005
    slippage: float = 0.0002  # fraction of price on stop and time exits
    mmr: float = 0.004  # maintenance margin rate, per pair
    risk_per_trade: float = 0.01
    max_leverage: float = 25.0
    start_equity: float = 10_000.0
    use_1m: bool = True

    def _validate(self) -> None:
        for name in ("fee_maker", "fee_taker", "slippage", "mmr"):
            _check_non_negative(getattr(self, name), f"ExecConfig.{name}")
        if not 0 < self.risk_per_trade <= 1:
            raise ConfigError(f"ExecConfig.risk_per_trade: must be in (0, 1], got {self.risk_per_trade!r}")
        _check_positive(self.max_leverage, "ExecConfig.max_leverage")
        _check_positive(self.start_equity, "ExecConfig.start_equity")


@dataclass(frozen=True)
class StatsConfig(_Config):
    bootstrap_n: int = 10_000
    block_len_days: int = 10
    baseline_runs: int = 5_000
    alpha: float = 0.05
    master_seed: int = 20260926
    reprice_slippage: tuple[float, ...] = (0.0, 0.0002, 0.0005, 0.001)
    reprice_maker: tuple[float, ...] = (0.0, 0.0002)

    def _validate(self) -> None:
        _check_positive(self.bootstrap_n, "StatsConfig.bootstrap_n")
        _check_positive(self.block_len_days, "StatsConfig.block_len_days")
        _check_positive(self.baseline_runs, "StatsConfig.baseline_runs")
        if not 0 < self.alpha < 1:
            raise ConfigError(f"StatsConfig.alpha: must be in (0, 1), got {self.alpha!r}")
        for name in ("reprice_slippage", "reprice_maker"):
            values = getattr(self, name)
            if not values:
                raise ConfigError(f"StatsConfig.{name}: must not be empty")
            for k, x in enumerate(values):
                _check_non_negative(x, f"StatsConfig.{name}[{k}]")


@dataclass(frozen=True)
class DataConfig(_Config):
    data_dir: str
    insample_start: str  # ISO date
    insample_end: str  # ISO date, inclusive
    holdout_start: str
    holdout_end: str | None  # None until data_download_date is set
    warmup_start: str  # "2019-11-01": first candle needed for indicators
    listing: dict[str, str]  # pair -> first perpetual trading date

    def _validate(self) -> None:
        for name in ("insample_start", "insample_end", "holdout_start", "warmup_start"):
            _check_iso_date(getattr(self, name), f"DataConfig.{name}")
        if self.holdout_end is not None:
            _check_iso_date(self.holdout_end, "DataConfig.holdout_end")
        for pair, first in self.listing.items():
            _check_iso_date(first, f"DataConfig.listing[{pair!r}]")
        if self.insample_start > self.insample_end:
            raise ConfigError(
                f"DataConfig: insample_start {self.insample_start} is after insample_end {self.insample_end}"
            )


@dataclass(frozen=True)
class VariantConfig(_Config):
    pair: str
    session: SessionSpec
    params: StrategyParams
    exec: ExecConfig
    stats: StatsConfig
    period_start: str  # ISO date; first decision candle
    period_end: str  # ISO date, inclusive
    is_holdout: bool

    def _validate(self) -> None:
        _check_iso_date(self.period_start, "VariantConfig.period_start")
        _check_iso_date(self.period_end, "VariantConfig.period_end")
        if self.period_start > self.period_end:
            raise ConfigError(
                f"VariantConfig: period_start {self.period_start} is after period_end {self.period_end}"
            )

    def canonical_json(self) -> str:
        return canonical_json(self)

    def config_hash(self) -> str:
        return config_hash(self)

    def variant_id(self, code_version: str) -> str:
        """sha256(canonical_json + code_version): the identity of one experiment (D15)."""
        return _sha256(self.canonical_json() + code_version)


# --- canonical JSON, hashes, YAML ------------------------------------------------


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(obj: Any) -> str:
    """Compact JSON with sorted keys; floats in ``repr`` form. Accepts dataclasses or plain mappings."""
    return json.dumps(to_dict(obj), sort_keys=True, separators=(",", ":"), allow_nan=False)


def config_hash(obj: Any) -> str:
    """SHA-256 of :func:`canonical_json`."""
    return _sha256(canonical_json(obj))


def load_yaml(path: str | Path, cls: type[T]) -> T:
    """Read a YAML file into dataclass ``cls``. Unknown keys raise ConfigError."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {}
    return from_dict(cls, data)


def dump_yaml(obj: Any, path: str | Path) -> None:
    """Write a dataclass (or plain mapping) as YAML, fields in declaration order."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        yaml.safe_dump(to_dict(obj), f, sort_keys=False, default_flow_style=False, allow_unicode=True)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_config.py -q`
Expected: all pass (about 60 tests including parametrized cases).

- [ ] **Step 5: Commit**

```bash
git add perpbt/config.py tests/test_config.py
git commit -m "Phase 0: config dataclasses with coercion and validation"
```

---

### Task 5: YAML round-trip, canonical JSON, hashes

**Files:**
- Modify: `perpbt/config.py` (already contains `canonical_json`, `config_hash`, `load_yaml`, `dump_yaml` from Task 4; this task pins their behaviour with tests and fixes anything the tests reveal)
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Consumes: everything from Task 4.
- Produces: `canonical_json(obj) -> str`, `config_hash(obj) -> str`, `load_yaml(path, cls) -> cls`, `dump_yaml(obj, path) -> None`, and `VariantConfig.canonical_json() / config_hash() / variant_id(code_version)`. `canonical_json` accepts a plain mapping too (Phase 6 hashes the loaded pre-registration content with it).

- [ ] **Step 1: Append the failing tests to `tests/test_config.py`**

Add these imports at the top of the file, next to the existing ones:

```python
import copy
import hashlib
import re
from datetime import timedelta

from perpbt.config import canonical_json, config_hash, dump_yaml, load_yaml
```

Append at the end of the file:

```python
# --- YAML round-trip --------------------------------------------------------

SAMPLES = [
    UTC,
    NY,
    StopBuffer("pct", 0.0025),
    HoldRule("max_hold", 24.0),
    HoldRule("none"),
    StrategyParams(swing_k=3, hold_rule=HoldRule("session_end"), pierce=0.0005, structure_break="literal"),
    ExecConfig(slippage=0.0005),
    StatsConfig(bootstrap_n=100, reprice_maker=(0.0,)),
    data_config(),
    data_config(holdout_end="2026-09-27"),
    primary_variant(),
    primary_variant(session=UTC, is_holdout=True),
]


@pytest.mark.parametrize("obj", SAMPLES, ids=lambda o: type(o).__name__)
def test_yaml_round_trip_is_identity(obj, tmp_path):
    path = tmp_path / "cfg.yaml"
    dump_yaml(obj, path)
    loaded = load_yaml(path, type(obj))
    assert loaded == obj
    assert config_hash(loaded) == config_hash(obj)


def test_load_yaml_unquoted_date_is_accepted(tmp_path):
    path = tmp_path / "d.yaml"
    path.write_text(
        "data_dir: data\n"
        "insample_start: 2020-01-01\n"
        "insample_end: 2025-12-31\n"
        "holdout_start: 2026-01-01\n"
        "holdout_end: null\n"
        "warmup_start: 2019-11-01\n"
        "listing: {BTCUSDT: 2019-09-08, ETHUSDT: 2019-11-27, SOLUSDT: 2020-09-14}\n",
        encoding="utf-8",
    )
    assert load_yaml(path, DataConfig) == data_config()


def test_load_yaml_unquoted_2400_gives_quoting_hint(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("name: utc\ntz: UTC\nopen: '00:00'\nclose: 24:00\ndays: [0, 1]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="quote"):
        load_yaml(path, SessionSpec)


def test_load_yaml_unknown_key_raises(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text("swing_k: 2\nconfirm_m: 3\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown.*confirm_m"):
        load_yaml(path, StrategyParams)


def test_load_yaml_empty_file_gives_defaults(tmp_path):
    path = tmp_path / "e.yaml"
    path.write_text("", encoding="utf-8")
    assert load_yaml(path, StrategyParams) == StrategyParams()
    with pytest.raises(ConfigError, match="missing"):
        load_yaml(path, StopBuffer)


# --- canonical JSON and hashes ---------------------------------------------


def test_config_hash_ignores_yaml_key_order(tmp_path):
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("swing_k: 3\nr_target: 1.5\nstop_buffer:\n  kind: pct\n  value: 0.001\n", encoding="utf-8")
    b.write_text("stop_buffer:\n  value: 0.001\n  kind: pct\nr_target: 1.5\nswing_k: 3\n", encoding="utf-8")
    pa, pb = load_yaml(a, StrategyParams), load_yaml(b, StrategyParams)
    assert pa == pb
    assert config_hash(pa) == config_hash(pb)


@pytest.mark.parametrize("text", ["value: 0.1\n", "value: 0.10\n", "value: 1.0e-1\n", "value: 0.1000000\n"])
def test_float_text_forms_hash_same(text, tmp_path):
    path = tmp_path / "sb.yaml"
    path.write_text("kind: atr\n" + text, encoding="utf-8")
    assert config_hash(load_yaml(path, StopBuffer)) == config_hash(StopBuffer("atr", 0.1))


@pytest.mark.parametrize("text", ["r_target: 2\n", "r_target: 2.0\n", "r_target: 2.00\n"])
def test_int_and_float_literals_hash_same(text, tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text(text, encoding="utf-8")
    assert config_hash(load_yaml(path, StrategyParams)) == config_hash(StrategyParams())


def test_canonical_json_is_compact_sorted_repr_floats():
    assert canonical_json(StopBuffer("atr", 0.10)) == '{"kind":"atr","value":0.1}'
    assert canonical_json(HoldRule("none")) == '{"hours":null,"kind":"none"}'
    cj = primary_variant().canonical_json()
    assert cj.startswith('{"exec":{"fee_maker":0.0002,')
    assert " " not in cj and "\n" not in cj
    keys = re.findall(r'"([a-z_0-9]+)":', cj.split('"session"')[0])
    assert keys[:1] == ["exec"]


def test_canonical_json_accepts_plain_mapping():
    assert canonical_json({"b": (1, 2), "a": {"y": 0.10, "x": None}}) == '{"a":{"x":null,"y":0.1},"b":[1,2]}'


def test_config_hash_is_sha256_of_canonical_json():
    v = primary_variant()
    expected = hashlib.sha256(v.canonical_json().encode("utf-8")).hexdigest()
    assert v.config_hash() == expected == config_hash(v)
    assert len(expected) == 64


def test_variant_id_combines_config_and_code_version():
    v = primary_variant()
    cj = v.canonical_json()
    assert v.variant_id("abc") == hashlib.sha256((cj + "abc").encode("utf-8")).hexdigest()
    assert v.variant_id("abc") != v.variant_id("abd")
    assert v.variant_id("abc") != v.config_hash()


# --- every field participates in the hash ----------------------------------

_ALT_STR = {
    "kind": {"atr": "pct", "pct": "atr", "none": "session_end", "session_end": "none", "max_hold": "none"},
    "zone": {"full": "body", "body": "full"},
    "entry_level": {"top": "mid", "mid": "top"},
    "structure_break": {"fresh": "literal", "literal": "fresh"},
    "skip_mitigated": {"continue": "stop", "stop": "continue"},
}
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HHMM = re.compile(r"^(\d{2}):(\d{2})$")


def _alternative(key, value):
    """A different valid value for a leaf, so the config still constructs."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 0.25
    if isinstance(value, list):
        return value[:-1]
    if isinstance(value, str):
        if key in _ALT_STR:
            return _ALT_STR[key][value]
        if key == "tz":
            return "Europe/London" if value != "Europe/London" else "UTC"
        if _ISO.match(value):
            return (date.fromisoformat(value) + timedelta(days=1)).isoformat()
        m = _HHMM.match(value)
        if m:
            return "23:00" if value == "24:00" else f"{m[1]}:{(int(m[2]) + 1) % 60:02d}"
        return value + "x"
    raise TypeError(f"no alternative for {key}={value!r}")


def _leaf_paths(d, prefix=()):
    for k, v in d.items():
        if isinstance(v, dict):
            yield from _leaf_paths(v, prefix + (k,))
        else:
            yield prefix + (k,), v


def _with_leaf(d, path, value):
    d = copy.deepcopy(d)
    node = d
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    return d


def test_changing_any_single_field_changes_the_hash():
    base = primary_variant()
    base_dict = to_dict(base)
    checked = 0
    for path, value in _leaf_paths(base_dict):
        if value is None:
            continue  # hold_rule.hours is coupled to kind; covered by the test below
        changed = from_dict(VariantConfig, _with_leaf(base_dict, path, _alternative(path[-1], value)))
        assert changed.config_hash() != base.config_hash(), path
        checked += 1
    assert checked >= 36  # every leaf of VariantConfig except hold_rule.hours


def test_hold_rule_hours_changes_the_hash():
    a = primary_variant(params=StrategyParams(hold_rule=HoldRule("max_hold", 24)))
    b = primary_variant(params=StrategyParams(hold_rule=HoldRule("max_hold", 72)))
    assert a.config_hash() != b.config_hash()
```

- [ ] **Step 2: Run the new tests**

Run: `python -m pytest tests/test_config.py -q`
Expected: all pass. If `test_canonical_json_is_compact_sorted_repr_floats` fails on the `startswith` assertion, check the sorted key order of `VariantConfig` (`exec` sorts first) and that `ExecConfig.fee_maker` is the first sorted key of the nested dict; do not change the canonical form to fit the test unless the spec's definition was violated.

- [ ] **Step 3: Commit**

```bash
git add tests/test_config.py perpbt/config.py
git commit -m "Phase 0: YAML round-trip, canonical JSON, config_hash, variant_id"
```

---

### Task 6: Code version

**Files:**
- Create: `perpbt/version.py`
- Test: `tests/test_version.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `perpbt.version.PACKAGE_DIR: Path`, `code_version(root: Path | str = PACKAGE_DIR) -> str` (64 hex chars), `git_commit(cwd: Path | str | None = None) -> str | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_version.py`:

```python
"""code_version(): SHA-256 of the perpbt source tree; git_commit(): best effort."""
import hashlib
import subprocess
from pathlib import Path

import pytest

from perpbt.version import PACKAGE_DIR, code_version, git_commit

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def make_tree(root: Path) -> None:
    (root / "a.py").write_bytes(b"x = 1\n")
    (root / "sub").mkdir()
    (root / "sub" / "__init__.py").write_bytes(b"")
    (root / "sub" / "b.py").write_bytes(b"y = 2\r\n")


def test_empty_tree_hashes_to_sha256_of_nothing(tmp_path):
    assert code_version(tmp_path) == EMPTY_SHA256


def test_stable_across_calls(tmp_path):
    make_tree(tmp_path)
    assert code_version(tmp_path) == code_version(tmp_path)
    assert code_version(str(tmp_path)) == code_version(tmp_path)


def test_matches_hand_computed_format(tmp_path):
    make_tree(tmp_path)
    h = hashlib.sha256()
    for rel, data in [("a.py", b"x = 1\n"), ("sub/__init__.py", b""), ("sub/b.py", b"y = 2\r\n")]:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(data)
        h.update(b"\0")
    assert code_version(tmp_path) == h.hexdigest()


def test_editing_one_byte_changes_hash(tmp_path):
    make_tree(tmp_path)
    before = code_version(tmp_path)
    (tmp_path / "sub" / "b.py").write_bytes(b"y = 3\r\n")
    assert code_version(tmp_path) != before


def test_non_py_files_do_not_count(tmp_path):
    make_tree(tmp_path)
    before = code_version(tmp_path)
    (tmp_path / "README.md").write_text("docs\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("notes\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.cpython-311.pyc").write_bytes(b"\x00\x01")
    (tmp_path / "sub" / "b.pyc").write_bytes(b"\x00\x01")
    assert code_version(tmp_path) == before


def test_renaming_a_file_changes_hash(tmp_path):
    make_tree(tmp_path)
    before = code_version(tmp_path)
    (tmp_path / "a.py").rename(tmp_path / "a2.py")
    assert code_version(tmp_path) != before


def test_moving_a_file_between_directories_changes_hash(tmp_path):
    make_tree(tmp_path)
    before = code_version(tmp_path)
    (tmp_path / "sub" / "b.py").rename(tmp_path / "b.py")
    assert code_version(tmp_path) != before


def test_real_package_hash_is_hex64():
    cv = code_version()
    assert len(cv) == 64 and int(cv, 16) >= 0
    assert PACKAGE_DIR.name == "perpbt"
    assert (PACKAGE_DIR / "version.py").is_file()


def test_git_commit_on_this_repo():
    commit = git_commit()
    assert commit is not None
    assert len(commit) == 40 and int(commit, 16) >= 0


def test_git_commit_returns_none_when_git_missing(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", boom)
    assert git_commit() is None


def test_git_commit_returns_none_outside_a_repo(monkeypatch):
    def failed(*args, **kwargs):
        return subprocess.CompletedProcess(args, 128, stdout="", stderr="fatal: not a git repository")

    monkeypatch.setattr(subprocess, "run", failed)
    assert git_commit() is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_version.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'perpbt.version'`.

- [ ] **Step 3: Implement `perpbt/version.py`**

```python
"""Code identity (D15).

``code_version`` is the SHA-256 of the ``perpbt`` source tree, so edits to
docs, tests, or configs do not change ``variant_id`` and the run cache stays
valid. ``git_commit`` is recorded alongside, best effort.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def code_version(root: Path | str = PACKAGE_DIR) -> str:
    """SHA-256 over every ``*.py`` under ``root``.

    Files are visited in sorted order of their POSIX relative path (``/``
    separators on every OS). For each file the hash absorbs the relative path
    as UTF-8, a NUL byte, the file bytes, and a NUL byte. Nothing else counts:
    ``.pyc``, docs, tests, and configs leave the hash unchanged.
    """
    root = Path(root)
    entries = sorted(
        (p.relative_to(root).as_posix(), p) for p in root.rglob("*.py") if p.is_file()
    )
    h = hashlib.sha256()
    for rel, path in entries:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def git_commit(cwd: Path | str | None = None) -> str | None:
    """``git rev-parse HEAD`` for the repository containing ``cwd`` (default: this package), or None."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd or PACKAGE_DIR,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_version.py -q`
Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add perpbt/version.py tests/test_version.py
git commit -m "Phase 0: code_version() over the source tree and git_commit()"
```

---

### Task 7: Synthetic candle builders and shared fixtures

**Files:**
- Create: `tests/synthetic.py`
- Create: `tests/conftest.py`
- Test: `tests/test_synthetic.py`

**Interfaces:**
- Consumes: `perpbt.data.store.Candles` (Task 3).
- Produces (importable as `from tests.synthetic import ...`):
  - `T0_MS = 1_577_836_800_000` (2020-01-01T00:00Z) and `STEP_15M_MS = 900_000`.
  - `candles_from_rows(rows, *, start_ms, step_ms=900_000, pair="TEST", tf="15m") -> Candles`; rows of `(o, h, l, c)` or `(o, h, l, c, v)`, `v` defaults to `1.0`; invalid OHLC raises `ValueError`.
  - `random_walk(n, *, seed, start_price=100.0, step_sigma=0.002, start_ms, step_ms=900_000, pair="TEST", tf="15m") -> Candles`.
  - `perturb_after(candles, cut, *, seed, step_sigma=0.002) -> Candles`; same `ts`, `[:cut+1]` identical, `o[cut+1] == c[cut]`; `cut` outside `[0, len)` raises `IndexError`.
  - `assert_causal(fn, candles, *, cuts, seeds) -> None`; raises `AssertionError` whose message contains "not causal".
  - Fixtures `tmp_data_dir` (a fresh `data/` directory under `tmp_path`) and `rng` (`np.random.default_rng(20260926)`).

- [ ] **Step 1: Write `tests/conftest.py`**

```python
"""Shared fixtures for the perpbt test suite."""
import numpy as np
import pytest


@pytest.fixture
def tmp_data_dir(tmp_path):
    """An empty data directory, deleted after the test."""
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def rng():
    """A fixed-seed numpy Generator; tests that need randomness take this."""
    return np.random.default_rng(20260926)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_synthetic.py`:

```python
"""Synthetic builders: random_walk, candles_from_rows, perturb_after, assert_causal."""
import numpy as np
import pytest

from perpbt.data.store import Candles
from tests.synthetic import (
    STEP_15M_MS,
    T0_MS,
    assert_causal,
    candles_from_rows,
    perturb_after,
    random_walk,
)


def arrays_equal(a: Candles, b: Candles, sl: slice) -> bool:
    return all(
        np.array_equal(getattr(a, name)[sl], getattr(b, name)[sl])
        for name in ("ts", "o", "h", "l", "c", "v")
    )


# --- fixtures from conftest -------------------------------------------------


def test_conftest_fixtures(tmp_data_dir, rng):
    assert tmp_data_dir.is_dir() and tmp_data_dir.name == "data"
    assert isinstance(rng, np.random.Generator)
    assert rng.integers(0, 1_000_000) == np.random.default_rng(20260926).integers(0, 1_000_000)


# --- candles_from_rows ------------------------------------------------------


def test_candles_from_rows_four_columns():
    cd = candles_from_rows([(1, 2, 0.5, 1.5), (1.5, 1.6, 1.4, 1.45)], start_ms=T0_MS)
    assert len(cd) == 2
    assert cd.ts.tolist() == [T0_MS, T0_MS + STEP_15M_MS]
    assert cd.o.tolist() == [1.0, 1.5]
    assert cd.h.tolist() == [2.0, 1.6]
    assert cd.l.tolist() == [0.5, 1.4]
    assert cd.c.tolist() == [1.5, 1.45]
    assert cd.v.tolist() == [1.0, 1.0]
    assert (cd.pair, cd.tf) == ("TEST", "15m")


def test_candles_from_rows_five_columns_and_step():
    cd = candles_from_rows([(1, 1, 1, 1, 7), (1, 1, 1, 1, 8)], start_ms=10, step_ms=60_000, pair="P", tf="1m")
    assert cd.v.tolist() == [7.0, 8.0]
    assert cd.ts.tolist() == [10, 60_010]
    assert (cd.pair, cd.tf) == ("P", "1m")
    assert cd.step_ms() == 60_000


def test_candles_from_rows_empty():
    assert len(candles_from_rows([], start_ms=T0_MS)) == 0


@pytest.mark.parametrize("row", [(1, 0.9, 0.5, 1.0), (1, 2, 1.2, 1.5), (1, 2, 0.5)])
def test_candles_from_rows_rejects_invalid_rows(row):
    with pytest.raises(ValueError):
        candles_from_rows([(1, 2, 0.5, 1.5), row], start_ms=T0_MS)


# --- random_walk ------------------------------------------------------------


def test_random_walk_is_valid_ohlc():
    cd = random_walk(500, seed=1, start_ms=T0_MS)
    assert len(cd) == 500
    assert cd.ts.tolist() == (T0_MS + STEP_15M_MS * np.arange(500)).tolist()
    assert np.all(cd.h >= np.maximum(cd.o, cd.c))
    assert np.all(cd.l <= np.minimum(cd.o, cd.c))
    assert np.all(cd.l > 0)
    assert np.all(cd.v > 0)
    assert cd.o[0] == 100.0
    assert np.array_equal(cd.o[1:], cd.c[:-1])


def test_random_walk_is_reproducible_per_seed():
    a = random_walk(200, seed=7, start_ms=T0_MS)
    b = random_walk(200, seed=7, start_ms=T0_MS)
    c = random_walk(200, seed=8, start_ms=T0_MS)
    assert arrays_equal(a, b, slice(None))
    assert not np.array_equal(a.c, c.c)


def test_random_walk_parameters():
    cd = random_walk(50, seed=3, start_price=2_000.0, step_sigma=0.0, start_ms=5, step_ms=60_000, pair="X", tf="1m")
    assert cd.o[0] == 2_000.0
    assert np.allclose(cd.c, 2_000.0)  # zero volatility: flat closes
    assert cd.ts[1] - cd.ts[0] == 60_000
    assert (cd.pair, cd.tf) == ("X", "1m")


def test_random_walk_zero_length():
    assert len(random_walk(0, seed=1, start_ms=T0_MS)) == 0


# --- perturb_after ----------------------------------------------------------


def test_perturb_after_keeps_prefix_and_timestamps():
    base = random_walk(300, seed=11, start_ms=T0_MS)
    cut = 120
    pert = perturb_after(base, cut, seed=99)
    assert len(pert) == len(base)
    assert np.array_equal(pert.ts, base.ts)
    assert arrays_equal(base, pert, slice(0, cut + 1))
    assert (pert.pair, pert.tf) == (base.pair, base.tf)


def test_perturb_after_changes_suffix_and_keeps_continuity():
    base = random_walk(300, seed=11, start_ms=T0_MS)
    cut = 120
    pert = perturb_after(base, cut, seed=99)
    assert pert.o[cut + 1] == base.c[cut]
    assert not np.array_equal(pert.c[cut + 1 :], base.c[cut + 1 :])
    assert np.all(pert.h >= np.maximum(pert.o, pert.c))
    assert np.all(pert.l <= np.minimum(pert.o, pert.c))


def test_perturb_after_is_reproducible_per_seed():
    base = random_walk(100, seed=1, start_ms=T0_MS)
    a = perturb_after(base, 10, seed=5)
    b = perturb_after(base, 10, seed=5)
    c = perturb_after(base, 10, seed=6)
    assert arrays_equal(a, b, slice(None))
    assert not np.array_equal(a.c[11:], c.c[11:])


def test_perturb_after_last_index_is_identity():
    base = random_walk(50, seed=1, start_ms=T0_MS)
    pert = perturb_after(base, 49, seed=5)
    assert arrays_equal(base, pert, slice(None))


@pytest.mark.parametrize("cut", [-1, 50, 51])
def test_perturb_after_rejects_out_of_range_cut(cut):
    base = random_walk(50, seed=1, start_ms=T0_MS)
    with pytest.raises(IndexError):
        perturb_after(base, cut, seed=5)


# --- assert_causal ----------------------------------------------------------


def cumsum(cd: Candles) -> np.ndarray:
    return np.cumsum(cd.c)


def reversed_cumsum(cd: Candles) -> np.ndarray:
    return np.cumsum(cd.c[::-1])[::-1]


def test_assert_causal_passes_on_causal_function():
    cd = random_walk(400, seed=2, start_ms=T0_MS)
    assert_causal(cumsum, cd, cuts=[0, 10, 200, 398], seeds=[1, 2, 3])


def test_assert_causal_fails_on_non_causal_function():
    cd = random_walk(400, seed=2, start_ms=T0_MS)
    with pytest.raises(AssertionError, match="not causal"):
        assert_causal(reversed_cumsum, cd, cuts=[200], seeds=[1])


def test_assert_causal_handles_nan_warmup():
    def lagged_mean(cd: Candles) -> np.ndarray:
        out = np.full(len(cd), np.nan)
        for i in range(4, len(cd)):
            out[i] = cd.c[i - 4 : i + 1].mean()
        return out

    cd = random_walk(200, seed=4, start_ms=T0_MS)
    assert_causal(lagged_mean, cd, cuts=[0, 3, 4, 100], seeds=[1, 2])


def test_assert_causal_pair_form_respects_confirmed_at():
    k = 2

    def forward_max_confirmed(cd: Candles):
        # value at i looks k candles ahead, but is only confirmed at i + k
        n = len(cd)
        idx = np.arange(n - k)
        values = np.array([cd.h[i : i + k + 1].max() for i in idx])
        return values, idx + k

    def forward_max_unconfirmed(cd: Candles):
        values, _ = forward_max_confirmed(cd)
        return values, np.arange(len(values))

    cd = random_walk(300, seed=5, start_ms=T0_MS)
    assert_causal(forward_max_confirmed, cd, cuts=[0, 1, 50, 150], seeds=[1, 2])
    # Several seeds: a single perturbation can leave a 3-candle max unchanged by chance.
    with pytest.raises(AssertionError, match="not causal"):
        assert_causal(forward_max_unconfirmed, cd, cuts=[150], seeds=[1, 2, 3, 4, 5])


def test_assert_causal_pair_form_compares_values_of_confirmed_rows():
    def leaks_last_close(cd: Candles):
        idx = np.arange(len(cd))
        return cd.c + cd.c[-1], idx  # confirmed at idx, but every value uses the last close

    cd = random_walk(300, seed=6, start_ms=T0_MS)
    with pytest.raises(AssertionError, match="not causal"):
        assert_causal(leaks_last_close, cd, cuts=[100], seeds=[1])


def test_assert_causal_pair_form_detects_changed_row_set():
    def rows_depend_on_future(cd: Candles):
        # the number of "confirmed" rows depends on the whole series: non-causal
        count = int(np.sum(cd.c > cd.c.mean()))
        return cd.c[:count], np.arange(count)

    cd = random_walk(300, seed=6, start_ms=T0_MS)
    # Several seeds: one perturbation could leave the count unchanged by chance.
    with pytest.raises(AssertionError, match="not causal"):
        assert_causal(rows_depend_on_future, cd, cuts=[100], seeds=[1, 2, 3, 4, 5, 6])


def test_assert_causal_rejects_bad_return_shape():
    cd = random_walk(20, seed=6, start_ms=T0_MS)
    with pytest.raises(TypeError):
        assert_causal(lambda c: (c.c, c.c, c.c), cd, cuts=[5], seeds=[1])
```

- [ ] **Step 3: Run them to verify they fail**

Run: `python -m pytest tests/test_synthetic.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'tests.synthetic'`.

- [ ] **Step 4: Implement `tests/synthetic.py`**

```python
"""Synthetic candle builders for tests. Nothing here touches market data.

Every later phase's causality (perturbation) tests use ``perturb_after`` and
``assert_causal``: changing candles after ``cut`` must leave outputs at or
before ``cut`` unchanged (overview §6).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any

import numpy as np

from perpbt.data.store import Candles

T0_MS = 1_577_836_800_000  # 2020-01-01T00:00:00Z
STEP_15M_MS = 900_000


def candles_from_rows(
    rows: Iterable[Sequence[float]],
    *,
    start_ms: int,
    step_ms: int = STEP_15M_MS,
    pair: str = "TEST",
    tf: str = "15m",
) -> Candles:
    """Candles from ``(o, h, l, c)`` or ``(o, h, l, c, v)`` rows; ``ts = start_ms + k * step_ms``."""
    rows = [tuple(r) for r in rows]
    n = len(rows)
    if n == 0:
        z = np.zeros(0)
        return Candles(pair, tf, np.zeros(0, dtype=np.int64), z, z, z, z, z)
    width = len(rows[0])
    if width not in (4, 5) or any(len(r) != width for r in rows):
        raise ValueError("rows must all be (o, h, l, c) or (o, h, l, c, v)")
    arr = np.asarray(rows, dtype=np.float64)
    o, h, l, c = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
    v = arr[:, 4] if width == 5 else np.ones(n)
    bad = np.flatnonzero((h < np.maximum(o, c)) | (l > np.minimum(o, c)))
    if bad.size:
        raise ValueError(
            f"row {int(bad[0])}: high must be >= max(open, close) and low <= min(open, close)"
        )
    ts = start_ms + step_ms * np.arange(n, dtype=np.int64)
    return Candles(pair, tf, ts, o, h, l, c, v)


def _walk_arrays(
    n: int, rng: np.random.Generator, start_price: float, step_sigma: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Log-price random walk: o = previous c, seeded positive wicks, lognormal volume."""
    steps = rng.normal(0.0, step_sigma, n)
    c = start_price * np.exp(np.cumsum(steps))
    o = np.concatenate(([start_price], c))[:n]
    h = np.maximum(o, c) * (1.0 + np.abs(rng.normal(0.0, step_sigma, n)))
    l = np.minimum(o, c) * (1.0 - np.abs(rng.normal(0.0, step_sigma, n)))
    v = rng.lognormal(0.0, 0.5, n)
    return o, h, l, c, v


def random_walk(
    n: int,
    *,
    seed: int,
    start_price: float = 100.0,
    step_sigma: float = 0.002,
    start_ms: int,
    step_ms: int = STEP_15M_MS,
    pair: str = "TEST",
    tf: str = "15m",
) -> Candles:
    """``n`` valid candles from a seeded log-price random walk starting at ``start_price``."""
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    rng = np.random.default_rng(seed)
    o, h, l, c, v = _walk_arrays(n, rng, float(start_price), float(step_sigma))
    ts = start_ms + step_ms * np.arange(n, dtype=np.int64)
    return Candles(pair, tf, ts, o, h, l, c, v)


def perturb_after(candles: Candles, cut: int, *, seed: int, step_sigma: float = 0.002) -> Candles:
    """Same ``ts``; ``[:cut+1]`` identical; ``[cut+1:]`` is a fresh walk from ``close[cut]``.

    The replacement starts where the original left off (``o[cut+1] == c[cut]``)
    and follows the same generating process, so the perturbation is not
    detectable from candles at or before ``cut``.
    """
    n = len(candles)
    if not 0 <= cut < n:
        raise IndexError(f"cut must be in [0, {n}), got {cut}")
    k = cut + 1
    rng = np.random.default_rng(seed)
    o, h, l, c, v = _walk_arrays(n - k, rng, float(candles.c[cut]), float(step_sigma))
    return Candles(
        candles.pair,
        candles.tf,
        candles.ts,
        np.concatenate((candles.o[:k], o)),
        np.concatenate((candles.h[:k], h)),
        np.concatenate((candles.l[:k], l)),
        np.concatenate((candles.c[:k], c)),
        np.concatenate((candles.v[:k], v)),
    )


def _split(out: Any) -> tuple[np.ndarray, np.ndarray | None]:
    if isinstance(out, tuple):
        if len(out) != 2:
            raise TypeError("fn must return an array or a (values, confirmed_at) pair")
        values, confirmed_at = out
        values = np.asarray(values)
        confirmed_at = np.asarray(confirmed_at)
        if confirmed_at.ndim != 1 or len(confirmed_at) != len(values):
            raise TypeError("confirmed_at must be a 1-D array aligned to values")
        return values, confirmed_at
    return np.asarray(out), None


def _visible(values: np.ndarray, confirmed_at: np.ndarray | None, cut: int) -> list[np.ndarray]:
    if confirmed_at is None:
        return [values[: cut + 1]]
    mask = confirmed_at <= cut
    return [values[mask], confirmed_at[mask]]


def _equal(a: np.ndarray, b: np.ndarray) -> bool:
    if a.shape != b.shape:
        return False
    if a.dtype.kind in "fc" and b.dtype.kind in "fc":
        return bool(np.array_equal(a, b, equal_nan=True))
    return bool(np.array_equal(a, b))


def _first_diff(a: np.ndarray, b: np.ndarray) -> str:
    if a.shape != b.shape:
        return f"; visible shapes differ: {a.shape} vs {b.shape}"
    if a.dtype.kind in "fc":
        diff = ~((a == b) | (np.isnan(a) & np.isnan(b)))
    else:
        diff = a != b
    where = np.flatnonzero(diff.reshape(len(a), -1).any(axis=1))
    if where.size == 0:
        return ""
    i = int(where[0])
    return f"; first difference at row {i}: {a[i]!r} vs {b[i]!r}"


def assert_causal(
    fn: Callable[[Candles], Any],
    candles: Candles,
    *,
    cuts: Iterable[int],
    seeds: Iterable[int],
) -> None:
    """Assert ``fn``'s output at or before each cut does not depend on later candles.

    ``fn`` returns either an array aligned to the candle index (rows ``[:cut+1]``
    are compared) or a ``(values, confirmed_at)`` pair (only rows with
    ``confirmed_at <= cut`` are compared, values and ``confirmed_at`` both).
    NaNs compare equal to NaNs.
    """
    name = getattr(fn, "__name__", repr(fn))
    base_values, base_conf = _split(fn(candles))
    for cut in cuts:
        expected = _visible(base_values, base_conf, cut)
        for seed in seeds:
            got_values, got_conf = _split(fn(perturb_after(candles, cut, seed=seed)))
            got = _visible(got_values, got_conf, cut)
            for a, b in zip(expected, got, strict=True):
                if not _equal(a, b):
                    raise AssertionError(
                        f"{name} is not causal: output at or before cut={cut} changed "
                        f"under perturbation with seed={seed}{_first_diff(a, b)}"
                    )
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_synthetic.py -q`
Expected: all pass (about 25 tests including parametrized cases).

- [ ] **Step 6: Commit**

```bash
git add tests/synthetic.py tests/conftest.py tests/test_synthetic.py
git commit -m "Phase 0: synthetic candle builders, perturb_after, assert_causal"
```

---

### Task 8: Exit criterion, merge into `dev`, delete the branch

**Files:**
- No new files. Verify, merge, clean up.

**Interfaces:**
- Consumes: everything above.
- Produces: `dev` contains Phase 0, merged with `--no-ff`; `phase/0-scaffold` deleted locally and on `origin`.

- [ ] **Step 1: Run the whole suite and the CLI**

```bash
python -m pytest -q
python -m perpbt --help
python -c "import perpbt, perpbt.config, perpbt.version, perpbt.cli, perpbt.data.store; print(perpbt.__version__, perpbt.version.code_version()[:12], perpbt.version.git_commit()[:12])"
```

Expected: every test passes with no warnings about unknown markers; `--help` lists `data, run, grid, baselines, report, holdout`; the last command prints the version, a 12-hex code-version prefix, and a 12-hex commit prefix.

- [ ] **Step 2: Confirm the working tree is clean and everything is pushed**

```bash
git status --short          # expected: empty (no stray data/, runs/, egg-info)
git log --oneline dev..phase/0-scaffold
git push
```

Expected: the log shows the seven Phase 0 commits; push reports up to date.

- [ ] **Step 3: Merge into `dev` with `--no-ff`, push, delete the branch**

```bash
git switch dev
git pull
git merge --no-ff phase/0-scaffold -m "Merge phase/0-scaffold into dev: package skeleton, config types, code version, test harness"
python -m pytest -q          # expected: all pass on dev
git push origin dev
git branch -d phase/0-scaffold
git push origin --delete phase/0-scaffold
git branch -a                # expected: no phase/0-scaffold locally or on origin
```

---

## Self-review notes

- **Spec coverage.** §0.2 deliverables: `pyproject.toml` (T1), `__init__.py` with `__version__` (T1), `config.py` (T4, T5), `version.py` (T6), `cli.py` (T2), seven subpackage inits (T1), `tests/conftest.py` and `tests/synthetic.py` (T7), `tests/test_config.py` (T4, T5), `tests/test_version.py` (T6), `tests/test_synthetic.py` (T7). §0.3 types and methods (T4, T5). §0.4 hash format and `git_commit` (T6). §0.5 builders and `Candles` with `index_at`/`slice` (T3, T7). §0.6 tests: every listed test has a named test function above. Exit criterion and branch rules (T1 step 1, T8). Additions beyond the spec, each stated where it happens: `perpbt/__main__.py` (needed for `python -m perpbt`), `tests/__init__.py` (so `tests.synthetic` imports from any test), `tests/test_package.py`, `tests/test_cli.py`, `tests/test_store.py` (one test file per module, overview §6), `.gitattributes` (byte-stable `.py` files under `core.autocrlf=true`), value validation in the dataclasses beyond the two cases the spec names (cheap, and Review Focus items 3 and 4 need it).
- **Type consistency.** `Candles(pair, tf, ts, o, h, l, c, v)` positional order is used identically in T3 and T7. `from_dict`/`to_dict`/`canonical_json`/`config_hash` names are the same in T4 and T5. `assert_causal(fn, candles, *, cuts, seeds)` matches the spec and the Phase 2 usage. `code_version(root)` and `git_commit(cwd=None)` match T6's tests.
- **Review Focus.** Items 1–5 each name the test that pins them.
