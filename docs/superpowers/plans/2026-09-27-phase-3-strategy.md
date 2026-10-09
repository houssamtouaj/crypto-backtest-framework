# Phase 3 — Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the strategy plug-in interface (intents, `SimEvent`, the `Strategy` protocol) and the ICT order-block rule as `OrderBlockStrategy`: a causal function from `MarketView` to `PlaceBracketLimit` intents, with every skipped block counted under exactly one reason.

**Architecture:** `strategy/base.py` gains the intent dataclasses, `SimEvent` and the `Strategy` protocol next to the Phase 2 `MarketView`. `strategy/order_block.py` holds three layers: `LiveLevels` (spec §3.3; a list of live swing-high levels updated once per candle), `block_prices` (§3.6; a pure function of one candle, ATR and the params) and `OrderBlockStrategy` (§3.4–§3.11; per-candle driver that runs the checks in spec order and counts the first failure). The strategy reads only through `MarketView`, keeps its state incrementally, and on its first call rebuilds the live levels from candle 0, so its output does not depend on where the simulator's loop starts. A small test harness (`tests/strategy_harness.py`) drives the strategy the way the Phase 4 simulator will.

**Tech Stack:** Python 3.11, numpy 2.2, pytest 9, zoneinfo (tests only, for independent DST window ends).

**Spec:** `docs/superpowers/specs/2026-09-26-ob-backtest/phase-3-strategy.md`, read with `00-overview.md` (§4.1 MarketView, §6 conventions and counting, §8.1 branches, D1–D4, D7, D12) and `phase-4-execution.md` §4.1, §4.3, §4.6 (the consumer of every interface here). Code this builds on: `perpbt/strategy/base.py` (`MarketView`, `SessionInfo`, `AccountView`, `LookaheadError`), `perpbt/indicators/{atr,swings,daily}.py`, `perpbt/data/sessions.py` (`SessionCalendar`), `perpbt/config.py` (`StrategyParams`, `StopBuffer`, `HoldRule`, `SessionSpec`), `tests/synthetic.py` (`candles_from_rows`, `random_walk`, `perturb_after`, `T0_MS`, `STEP_15M_MS`).

**Verified before writing.** Every code block below was run in a throwaway worktree of `phase/3-strategy`: the fast suite gave 475 passed (the new tests take about 5 s), and the slow real-data test passed on BTCUSDT, ETHUSDT and SOLUSDT (about 3.5 s each). On real BTC 2019-11..2025 the primary rule gives about 2,200 intents in the UTC session and 900 in NY, and a first call at 2024-01-01 yields exactly the same intents as a run from candle 0.

## Global Constraints

- Branch `phase/3-strategy` from `dev` after Phases 1 and 2 are merged (done: `dev` at `ef65eae`); push on the first commit; small commits each with tests; merge into `dev` with `--no-ff`; delete the branch locally and on `origin` (overview §8.1). Spec amendments go in the same commit as the code that deviates.
- Decision time for candle `i` is its close; "as of `i`" means candles `≤ i` only. The strategy reads market data only through `MarketView` accessors, never its underscored attributes, an array's `.base`, `Swings._unchecked` or `advance_to` (the simulator's).
- Swing high: `high[s] > high[s ± j]` for `j = 1..k`, strict, confirmed at `s + k`, level `high[s]` (Phase 2).
- Live levels (§3.3), at the close of `t` in this order: 1 add swings with `confirmed_at == t`; 2 reference = live level with the greatest `confirmed_at`; 3 impulse iff a reference exists and `close[t] > reference.level`; 4 retire every live level with `level < close[t]`. `structure_break = "literal"` skips step 4.
- Candidate (§3.4): most recent `c` with `close[c] < open[c]` and `max(t − N, 0) ≤ c ≤ t − 1`. One impulse → at most one candidate.
- Eligibility (§3.5): `τ_c ≥ session.open_ms` and `session.in_window` at `t`. At most one `PlaceBracketLimit` per session, whatever happens to it.
- Prices (§3.6): zone `full` = `[low[c], high[c]]`, `body` = `[close[c], open[c]]`; entry `top` = zone high, `mid` = `(zone_low + zone_high) / 2`; `stop = low[c] − buffer` with buffer `value × ATR14[t]` (`atr`) or `value × entry` (`pct`); `stop_dist = entry − stop`, must be `> 0`; `target = entry + r_target × stop_dist`; `pierce_abs = pierce × entry`.
- Displacement and mitigation (§3.7): `d` = first candle in `c+1..t` with `close[d] > high[c]`; if `d < t` and `min(low[d+1..t]) ≤ entry − pierce_abs` → `mitigated`; `close[t] ≤ entry` → `entry_above_price`.
- Trend (§3.8): with `trend_filter`, reject unless `close[t] > daily_sma(t)`; NaN rejects.
- Intent (§3.9): `PlaceBracketLimit(side="long", price=entry, stop=stop, target=target, expires_ms=session.end_ms, hold_rule=params.hold_rule, tag=...)`, tag keys in this order: `candidate_idx, impulse_idx, displacement_idx, swing_idx, swing_level, zone_low, zone_high, entry_kind, atr, stop_dist, session_id`.
- Tests: pytest, one test file per module (`tests/test_checks.py`, `tests/test_strategy_base.py`, `tests/test_order_block.py`), synthetic data by default; real-data tests are `@pytest.mark.slow` in `tests/test_real_data.py` and skip when `data/` is absent. Scenario tests assert exact intents (prices compared with `==`, since the test recomputes them with the same float operations) or exact skip counts.
- Files are LF. On Windows, edit with the Edit tool; a Python script that rewrites a file must open it with `newline=""` or it turns LF into CRLF.
- Working directory for every command is the repo root `D:\Users\khali\projects\trading_backtest`; commands are shown for Git Bash. Fast suite: `python -m pytest -q -m "not slow"`.

## Review Focus

1. **A late first call must see the same live levels as a run from candle 0.** The holdout starts at 2026-01-01 and a variant's loop starts at `period_start`; a strategy that only knows the levels confirmed since its first call would miss an older, still-live high and silently trade a different rule. On its first call the strategy replays the live-level updates for candles `0..i0−1`. Pinned in Task 5 by `test_first_call_mid_series_rebuilds_the_live_levels` and in Task 6 by `test_order_block_rule_on_real_candles` (first call at 2024-01-01).
2. **A skipped or repeated `on_candle` call silently loses a retirement** (a level closed above on the skipped candle stays live). `on_candle` raises `ValueError` unless it is called on `last + 1`. Pinned in Task 4 by `test_on_candle_refuses_a_skipped_or_repeated_candle`.
3. **The candidate search near the start of the series** reaches `t − N < 0`; `MarketView` raises `LookaheadError` for negative indices, so the search must clamp at 0 and still find candle 0. Pinned in Task 4 by `test_candidate_search_clamps_at_the_first_candle`.
4. **NaN ATR in the first 13 candles** must give `degenerate`, never an intent with a NaN stop; an ATR buffer of value 0 must not need ATR at all; a `PlaceBracketLimit` with a NaN or wrong-side price must not be constructible. Pinned in Task 4 by `test_s13_nan_atr_is_degenerate_and_other_buffers_do_not_need_atr`, in Task 3 by `test_block_prices_nan_atr`, and in Task 2 by `test_bracket_limit_rejects_bad_prices`.
5. **Tags are written to JSON by Phase 4** (orders table, `tag` column): numpy scalars or NaN would break `json.dumps(..., allow_nan=False)` or leak `np.float64` reprs. Tag values must be plain `int`/`float`/`str`/`None`, keys in spec order. Pinned in Task 5 by `test_random_walk_intents_are_well_formed`.

Also pinned though not in the spec's test list: the §3.11 count identities on random walks for four parameter sets × three sessions (Task 5); one intent per session id and `expires_ms ≥` the decision candle's close (Task 5); the body zone still needs a close above the candle's true high (Task 4); the next session is free again after an intent (Task 4); `hold_rule` is carried through (Task 4); `skip_mitigated = "stop"` ends the session (Task 4).

---

### Task 1: Phase 2 carry-overs — shared integer check, `Swings` dtype check, `MarketView` fast paths

**Files:**
- Create: `perpbt/checks.py`
- Create: `tests/test_checks.py`
- Modify: `perpbt/indicators/atr.py` (imports, `check_period`, `wilder_rma`)
- Modify: `perpbt/indicators/swings.py` (imports, `Swings.__post_init__`, `swing_highs`)
- Modify: `perpbt/strategy/base.py` (imports, `_read_only` docstring, remove `_index`, `swings_confirmed_by`)
- Modify: `tests/test_atr.py`, `tests/test_swings.py` (append tests)
- Modify: `docs/superpowers/specs/2026-09-26-ob-backtest/00-overview.md` §5 (one line)

**Interfaces:**
- Produces: `perpbt.checks.as_int(value: object, what: str) -> int`: returns a Python `int` for a Python or numpy integer; raises `TypeError("<what> must be an integer, got <type>")` for a bool, `np.bool_`, float or anything without `__index__`. `check_period`, `wilder_rma(start=...)`, `swing_highs(k=...)` and every `MarketView` index use it.

These are the Phase 2 deferred minors that live in files Phase 3 touches (memory: one shared integer-argument helper for the three `operator.index` blocks; `wilder_rma` validating `start`; `Swings` rejecting float indices; the `_read_only` copy note; the f-string without interpolation at `base.py:81`, which disappears with `_index`). The two `MarketView` fast paths (`type(j) is int` first; `ndarray.searchsorted` instead of the `np.searchsorted` wrapper) halve the per-candle cost of the strategy loop; behaviour is unchanged and the existing Phase 2 tests cover it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_checks.py`:

```python
"""Shared argument checks (perpbt/checks.py)."""
import numpy as np
import pytest

from perpbt.checks import as_int


def test_as_int_accepts_python_and_numpy_integers():
    assert as_int(3, "x") == 3
    for v in (np.int64(3), np.int32(3), np.uint8(3)):
        out = as_int(v, "x")
        assert out == 3 and type(out) is int


@pytest.mark.parametrize("bad", [True, False, np.bool_(True), 3.0, np.float64(2.0), "3", None])
def test_as_int_rejects_bools_and_non_integers(bad):
    with pytest.raises(TypeError, match="x must be an integer"):
        as_int(bad, "x")
```

Append to `tests/test_atr.py`:

```python


def test_n_must_not_be_a_bool():
    with pytest.raises(TypeError):
        atr(random_walk(20, seed=1, start_ms=T0_MS), n=True)


def test_wilder_rma_validates_start():
    x = np.arange(5.0)
    with pytest.raises(ValueError, match="start"):
        wilder_rma(x, 2, start=-1)
    with pytest.raises(TypeError, match="start"):
        wilder_rma(x, 2, start=1.0)
```

Append to `tests/test_swings.py`:

```python


def test_swings_rejects_float_indices():
    with pytest.raises(TypeError, match="idx"):
        Swings(np.array([1.5]), np.array([2.0]), np.array([3]))
    with pytest.raises(TypeError, match="confirmed_at"):
        Swings(np.array([1]), np.array([2.0]), np.array([3.0]))
    assert len(Swings(np.array([]), np.array([]), np.array([]))) == 0  # empty float arrays are fine


def test_k_must_be_an_integer():
    cd = random_walk(20, seed=1, start_ms=T0_MS)
    for bad in (2.0, True):
        with pytest.raises(TypeError):
            swing_highs(cd, bad)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_checks.py tests/test_atr.py tests/test_swings.py`
Expected: `tests/test_checks.py` fails to import (`ModuleNotFoundError: No module named 'perpbt.checks'`); `test_n_must_not_be_a_bool`, `test_wilder_rma_validates_start`, `test_swings_rejects_float_indices` and `test_k_must_be_an_integer` FAIL (bools are accepted as 1, a negative `start` is not rejected, float indices are truncated); all older tests pass.

- [ ] **Step 3: Implement**

Create `perpbt/checks.py`:

```python
"""Argument checks shared across modules."""
from __future__ import annotations

import operator

import numpy as np


def as_int(value: object, what: str) -> int:
    """``value`` as a Python ``int``; TypeError for a bool or any non-integer (a float is never truncated).

    Accepts Python and numpy integers (anything with ``__index__``).
    """
    if type(value) is int:  # fast path: the simulator calls this on every candle
        return value
    if isinstance(value, (bool, np.bool_)):
        raise TypeError(f"{what} must be an integer, got bool")
    try:
        return operator.index(value)
    except TypeError:
        raise TypeError(f"{what} must be an integer, got {type(value).__name__}") from None
```

In `perpbt/indicators/atr.py`, replace

```python
import operator

import numpy as np

from perpbt.data.store import Candles
```

with

```python
import numpy as np

from perpbt.checks import as_int
from perpbt.data.store import Candles
```

replace the body of `check_period` down to (not including) `if n < 1:`

```python
    """``n`` as an int >= 1; TypeError for a non-integer, ValueError below 1."""
    try:
        n = operator.index(n)
    except TypeError:
        raise TypeError(f"period must be an integer, got {type(n).__name__}") from None
```

with

```python
    """``n`` as an int >= 1; TypeError for a bool or non-integer, ValueError below 1."""
    n = as_int(n, "period")
```

and in `wilder_rma` replace

```python
    n = check_period(n)
    x = np.asarray(x, dtype=np.float64)
```

with

```python
    n = check_period(n)
    start = as_int(start, "start")
    if start < 0:
        raise ValueError(f"start must be >= 0, got {start}")
    x = np.asarray(x, dtype=np.float64)
```

In `perpbt/indicators/swings.py`, replace

```python
import operator
from dataclasses import dataclass

import numpy as np

from perpbt.data.store import Candles
```

with

```python
from dataclasses import dataclass

import numpy as np

from perpbt.checks import as_int
from perpbt.data.store import Candles
```

at the top of `Swings.__post_init__` replace

```python
    def __post_init__(self) -> None:
        idx = np.asarray(self.idx).astype(np.int64, copy=False)
```

with

```python
    def __post_init__(self) -> None:
        for name in ("idx", "confirmed_at"):
            raw = np.asarray(getattr(self, name))
            if raw.size and not np.issubdtype(raw.dtype, np.integer):
                raise TypeError(f"Swings.{name} must hold integers, got dtype {raw.dtype}")
        idx = np.asarray(self.idx).astype(np.int64, copy=False)
```

and in `swing_highs` replace

```python
    try:
        k = operator.index(k)
    except TypeError:
        raise TypeError(f"k must be an integer, got {type(k).__name__}") from None
    if k < 1:
```

with

```python
    k = as_int(k, "k")
    if k < 1:
```

In `perpbt/strategy/base.py`, replace

```python
import operator
from dataclasses import dataclass

import numpy as np

```

with

```python
from dataclasses import dataclass

import numpy as np

from perpbt.checks import as_int
```

(the next line stays `from perpbt.data.sessions import SessionCalendar`). Replace

```python
def _read_only(arr: np.ndarray, dtype: type) -> np.ndarray:
    view = np.asarray(arr, dtype=dtype).view()
```

with

```python
def _read_only(arr: np.ndarray, dtype: type) -> np.ndarray:
    """A read-only view of ``arr`` as ``dtype``.

    ``np.asarray`` copies only when ``arr`` has another dtype; otherwise the
    view shares memory with the caller's array, whose own flag is untouched.
    """
    view = np.asarray(arr, dtype=dtype).view()
```

Delete the whole `_index` function (and the two blank lines after it):

```python
def _index(j: object) -> int:
    if isinstance(j, (bool, np.bool_)):
        raise TypeError(f"candle index must be an integer, got bool")
    try:
        return operator.index(j)
    except TypeError:
        raise TypeError(f"candle index must be an integer, got {type(j).__name__}") from None
```

and replace its five uses:

| old | new |
|---|---|
| `start_i = _index(start_i)` | `start_i = as_int(start_i, "start_i")` |
| `i = _index(i)` (in `advance_to`) | `i = as_int(i, "candle index")` |
| `j = _index(j)` (in `_j`) | `j = as_int(j, "candle index")` |
| `a, b = _index(a), _index(b)` (in `_ab`) | `a, b = as_int(a, "candle index"), as_int(b, "candle index")` |

In `swings_confirmed_by` replace

```python
        m = int(np.searchsorted(self._sw_conf, j, side="right"))
```

with

```python
        m = int(self._sw_conf.searchsorted(j, side="right"))
```

In `docs/superpowers/specs/2026-09-26-ob-backtest/00-overview.md` §5, after the line `  version.py           code_version() = SHA-256 of the perpbt source tree; git commit best effort` add:

```
  checks.py            as_int: the shared integer-argument check
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -q -m "not slow"` and `grep -rnwE "operator|_index" perpbt/strategy/base.py perpbt/indicators/`
Expected: 403 passed (391 before plus 12 new); the grep prints nothing (`-w` keeps `return_index` in `daily.py` out).

- [ ] **Step 5: Commit and push the branch**

```bash
git add perpbt/checks.py tests/test_checks.py perpbt/indicators/atr.py perpbt/indicators/swings.py \
        perpbt/strategy/base.py tests/test_atr.py tests/test_swings.py \
        docs/superpowers/specs/2026-09-26-ob-backtest/00-overview.md
git commit -m "Phase 3: shared as_int check (bools and floats rejected), Swings rejects float indices, wilder_rma validates start, MarketView fast paths"
git push -u origin phase/3-strategy
```

---

### Task 2: Intents, `SimEvent`, the `Strategy` protocol — `strategy/base.py`

**Files:**
- Modify: `perpbt/strategy/base.py` (module docstring, imports, append the new types)
- Modify: `tests/test_strategy_base.py` (imports, append tests)
- Modify: `docs/superpowers/specs/2026-09-26-ob-backtest/phase-3-strategy.md` §3.1
- Modify: `docs/superpowers/specs/2026-09-26-ob-backtest/00-overview.md` §5 (the `orders.py` line)

**Interfaces:**
- Consumes: `HoldRule`, `StrategyParams` from `perpbt.config`; `MarketView`, `AccountView` (same module).
- Produces (all in `perpbt.strategy.base`):
  - `PlaceBracketLimit(side: str, price: float, stop: float, target: float, expires_ms: int, hold_rule: HoldRule, tag: dict)`, frozen, validated: `ValueError` for a side outside `("long", "short")`, a non-finite price, or prices out of order (`stop < price < target` for long, `target < price < stop` for short); `TypeError` for a non-int or bool `expires_ms`, a non-`HoldRule` `hold_rule`, a non-dict `tag`.
  - `CancelOrder(order_id: int)`, `ClosePosition(position_id: int, reason: str)`, frozen.
  - `Intent = PlaceBracketLimit | CancelOrder | ClosePosition` (usable in `isinstance`).
  - `SimEvent(kind: str, idx: int, order_id: int | None = None, position_id: int | None = None, reason: str | None = None)`, frozen; `kind` in `EVENT_KINDS = ("filled", "cancelled", "closed", "skipped_leverage")`, else `ValueError`.
  - `Strategy`, a `runtime_checkable` `Protocol` with attributes `name: str`, `params: StrategyParams`, `warmup_bars: int` and methods `on_candle(view: MarketView, account: AccountView) -> list[Intent]`, `on_event(event: SimEvent) -> None`, `skip_counts() -> dict[str, int]`.

`SimEvent` lives in `strategy/base.py`, not `execution/orders.py` as overview §5 had it, so that strategy code never imports the simulator; Phase 4 imports it from here.

- [ ] **Step 1: Write the failing tests**

In `tests/test_strategy_base.py` replace

```python
from perpbt.config import SessionSpec
```

with

```python
from perpbt.config import HoldRule, SessionSpec, StrategyParams
```

and replace the `perpbt.strategy.base` import block

```python
from perpbt.strategy.base import (
    AccountView,
    LookaheadError,
    MarketView,
    OrderView,
    PositionView,
    SessionInfo,
)
```

with

```python
from perpbt.strategy.base import (
    AccountView,
    CancelOrder,
    ClosePosition,
    Intent,
    LookaheadError,
    MarketView,
    OrderView,
    PlaceBracketLimit,
    PositionView,
    SessionInfo,
    SimEvent,
    Strategy,
)
```

Append:

```python


# --- intents, events, protocol (spec §3.1) -------------------------------------------------

def bracket(**kw):
    args = dict(side="long", price=100.0, stop=99.0, target=102.0, expires_ms=T0_MS,
                hold_rule=HoldRule("none"), tag={"candidate_idx": 3})
    args.update(kw)
    return PlaceBracketLimit(**args)


def test_bracket_limit_holds_its_fields_and_compares_by_value():
    a = bracket()
    assert (a.side, a.price, a.stop, a.target, a.expires_ms) == ("long", 100.0, 99.0, 102.0, T0_MS)
    assert a == bracket() and a != bracket(tag={"candidate_idx": 4})
    assert isinstance(a, Intent) and isinstance(CancelOrder(1), Intent) and isinstance(ClosePosition(2, "x"), Intent)
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.price = 1.0
    assert bracket(side="short", stop=101.0, target=98.0).side == "short"


@pytest.mark.parametrize("kw", [
    dict(side="buy"),
    dict(price=float("nan")),
    dict(stop=float("-inf")),
    dict(target=float("nan")),
    dict(stop=100.0),                      # stop not below price
    dict(target=100.0),                    # target not above price
    dict(side="short"),                    # long-ordered prices on a short
])
def test_bracket_limit_rejects_bad_prices(kw):
    with pytest.raises(ValueError):
        bracket(**kw)


@pytest.mark.parametrize("kw", [
    dict(expires_ms=float(T0_MS)),
    dict(expires_ms=True),
    dict(hold_rule="none"),
    dict(tag=[("candidate_idx", 3)]),
])
def test_bracket_limit_rejects_bad_types(kw):
    with pytest.raises(TypeError):
        bracket(**kw)


def test_sim_event_kinds():
    for kind in ("filled", "cancelled", "closed", "skipped_leverage"):
        ev = SimEvent(kind, 5)
        assert (ev.kind, ev.idx, ev.order_id, ev.position_id, ev.reason) == (kind, 5, None, None, None)
    with pytest.raises(ValueError):
        SimEvent("fill", 5)


def test_strategy_protocol_is_structural():
    class Dummy:
        name = "dummy"
        params = StrategyParams()
        warmup_bars = 0

        def on_candle(self, view, account):
            return []

        def on_event(self, event):
            return None

        def skip_counts(self):
            return {}

    assert isinstance(Dummy(), Strategy)
    assert not isinstance(object(), Strategy)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_strategy_base.py`
Expected: collection error, `ImportError: cannot import name 'CancelOrder' from 'perpbt.strategy.base'`.

- [ ] **Step 3: Implement**

In `perpbt/strategy/base.py` replace the first line of the module docstring

```python
"""Strategy-facing views: the look-ahead guard (spec §2.4, overview §4.1).
```

with

```python
"""The strategy plug-in interface (spec §3.1) and the look-ahead guard (spec §2.4, overview §4.1).
```

and the end of the docstring

```python
strategy code must not touch them. The Phase 3 strategy types (intents, the
``Strategy`` protocol) join this module in Phase 3.
"""
```

with

```python
strategy code must not touch them.

A strategy turns ``(MarketView, AccountView)`` into ``Intent`` objects and
hears back through ``SimEvent``. ``SimEvent`` lives here rather than in
``execution/`` so that strategy code never imports the simulator.
"""
```

Replace the imports

```python
from dataclasses import dataclass

import numpy as np

from perpbt.checks import as_int
```

with

```python
import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from perpbt.checks import as_int
from perpbt.config import HoldRule, StrategyParams
```

Append at the end of the file (after `MarketView.session`):

```python


# --- intents, events, the strategy protocol (spec §3.1) ---------------------------------

SIDES = ("long", "short")
EVENT_KINDS = ("filled", "cancelled", "closed", "skipped_leverage")


@dataclass(frozen=True)
class PlaceBracketLimit:
    """Place an entry limit with an attached stop and target (spec §3.1).

    ``expires_ms``: cancel if unfilled by this instant (the window end).
    ``hold_rule``: the simulator derives the exit deadline at the fill.
    ``tag``: audit metadata of plain ``int``/``float``/``str``/``None``
    values, copied to orders and trades. Validated on construction so the
    simulator never sees NaN prices or a stop on the wrong side. Not
    hashable (``tag`` is a dict).
    """

    side: str
    price: float
    stop: float
    target: float
    expires_ms: int
    hold_rule: HoldRule
    tag: dict

    def __post_init__(self) -> None:
        if self.side not in SIDES:
            raise ValueError(f"PlaceBracketLimit.side must be one of {SIDES}, got {self.side!r}")
        for name in ("price", "stop", "target"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"PlaceBracketLimit.{name} must be finite, got {getattr(self, name)!r}")
        if self.side == "long":
            ordered = self.stop < self.price < self.target
        else:
            ordered = self.target < self.price < self.stop
        if not ordered:
            raise ValueError(
                f"PlaceBracketLimit: stop {self.stop}, price {self.price}, target {self.target} "
                f"are out of order for a {self.side}"
            )
        if isinstance(self.expires_ms, bool) or not isinstance(self.expires_ms, int):
            raise TypeError(
                f"PlaceBracketLimit.expires_ms must be an int (UTC ms), got {type(self.expires_ms).__name__}"
            )
        if not isinstance(self.hold_rule, HoldRule):
            raise TypeError(f"PlaceBracketLimit.hold_rule must be a HoldRule, got {type(self.hold_rule).__name__}")
        if not isinstance(self.tag, dict):
            raise TypeError(f"PlaceBracketLimit.tag must be a dict, got {type(self.tag).__name__}")


@dataclass(frozen=True)
class CancelOrder:
    """Cancel a pending entry order."""

    order_id: int


@dataclass(frozen=True)
class ClosePosition:
    """Close an open position at the current close (a time exit)."""

    position_id: int
    reason: str


Intent = PlaceBracketLimit | CancelOrder | ClosePosition


@dataclass(frozen=True)
class SimEvent:
    """What the simulator tells the strategy after acting (spec §3.1; built by Phase 4).

    ``kind``: ``filled`` (an entry order filled; ``order_id``, ``position_id``),
    ``cancelled`` (a pending order cancelled; ``order_id``, ``reason`` =
    ``expired``/``strategy``/``leverage_cap``/``data_end``), ``closed`` (a
    position exited; ``position_id``, ``reason`` = the exit reason),
    ``skipped_leverage`` (a ``PlaceBracketLimit`` refused by the leverage
    cap at placement; no ids). ``idx`` is the candle it happened on. Phase 4
    may add fields, with defaults.
    """

    kind: str
    idx: int
    order_id: int | None = None
    position_id: int | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"SimEvent.kind must be one of {EVENT_KINDS}, got {self.kind!r}")


@runtime_checkable
class Strategy(Protocol):
    """The plug-in interface every strategy implements (spec §3.1).

    Call contract: the simulator calls ``on_candle`` once per candle, in
    order, without gaps, from the first decision candle on; intents are
    placed at that candle's close. ``on_event`` delivers what happened to
    earlier intents. ``warmup_bars`` is the number of candles before the
    first decision candle the strategy's indicators need.
    """

    name: str
    params: StrategyParams
    warmup_bars: int

    def on_candle(self, view: MarketView, account: AccountView) -> list[Intent]: ...

    def on_event(self, event: SimEvent) -> None: ...

    def skip_counts(self) -> dict[str, int]: ...
```

Spec amendment, `phase-3-strategy.md` §3.1: between the closing code fence of the interface block and the line ``` `tag` for this strategy: `candidate_idx, impulse_idx, displacement_idx, ``` insert:

```markdown
`SimEvent` is defined in `strategy/base.py` too, so strategy code never
imports `execution/`: `kind` is `filled`, `cancelled`, `closed` or
`skipped_leverage`; `idx` is the candle it happened on; `order_id`,
`position_id` and `reason` are optional. Phase 4 may add fields with
defaults. `PlaceBracketLimit` validates itself on construction: side
`long` or `short`, finite prices, `stop < price < target` for a long
(reversed for a short), an integer `expires_ms`, a `HoldRule`, a dict tag.

```

Overview §5: replace

```
    orders.py          Order, Position, SimEvent dataclasses, lifecycle enum
```

with

```
    orders.py          Order, Position dataclasses, lifecycle enum (SimEvent is in strategy/base.py)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_strategy_base.py` then `python -m pytest -q -m "not slow"`
Expected: 33 passed in `test_strategy_base.py`; the fast suite 417 passed.

- [ ] **Step 5: Commit**

```bash
git add perpbt/strategy/base.py tests/test_strategy_base.py \
        docs/superpowers/specs/2026-09-26-ob-backtest/phase-3-strategy.md \
        docs/superpowers/specs/2026-09-26-ob-backtest/00-overview.md
git commit -m "Phase 3: PlaceBracketLimit (validated), CancelOrder, ClosePosition, SimEvent, Strategy protocol; spec: SimEvent lives in strategy/base.py"
```

---

### Task 3: Live levels and block prices — `strategy/order_block.py` (part 1)

**Files:**
- Create: `perpbt/strategy/order_block.py`
- Create: `tests/test_order_block.py`

**Interfaces:**
- Consumes: `StrategyParams`, `StopBuffer` from `perpbt.config`; `swing_highs` (tests only).
- Produces (in `perpbt.strategy.order_block`):
  - `Level(swing_idx: int, level: float, confirmed_at: int)`, a `NamedTuple`.
  - `LiveLevels(fresh: bool = True)` with `step(new: Sequence[Level], close: float) -> Level | None` (runs §3.3 steps 1–4 for one candle and returns the reference taken after adding and before retiring; `None` when there is none) and the property `live -> tuple[Level, ...]` (in confirmation order). `fresh=False` is `structure_break = "literal"`.
  - `BlockPrices(zone_low, zone_high, entry, stop, stop_dist, target, pierce_abs)`, frozen, all `float`.
  - `block_prices(o: float, h: float, l: float, c: float, atr_t: float, params: StrategyParams) -> BlockPrices` (§3.6; does not reject `stop_dist ≤ 0`, the caller does).
  Task 4 relies on exactly these names; the entry arithmetic `(zone_low + zone_high) / 2` and `pierce_abs = params.pierce * entry` must be computed exactly as written, because scenario tests recompute them with the same float operations.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_order_block.py` (Task 4 replaces this header and appends the scenarios):

```python
"""The ICT order-block rule (spec phase-3 §3.2–§3.12)."""
import numpy as np

from perpbt.config import StopBuffer, StrategyParams
from perpbt.indicators.swings import swing_highs
from perpbt.strategy.order_block import Level, LiveLevels, block_prices
from tests.synthetic import T0_MS, random_walk

PRIMARY = StrategyParams()  # k = 2, N = 3, full zone, top entry, 0.1 x ATR buffer, 2 R


# --- 3.1 live levels and impulse -----------------------------------------------------------


def test_level_retires_on_the_first_close_above_it():
    lv = LiveLevels()
    a = Level(1, 103.0, 3)
    assert lv.step([a], 100.0) == a
    assert lv.step([], 103.0) == a and lv.live == (a,)  # a close equal to the level does not retire it
    assert lv.step([], 103.5) == a and lv.live == ()  # the impulse candle retires it
    assert lv.step([], 110.0) is None


def test_most_recent_live_level_is_the_reference_even_below_an_older_higher_one():
    lv = LiveLevels()
    old, new = Level(1, 110.0, 3), Level(5, 105.0, 7)
    lv.step([old], 100.0)
    assert lv.step([new], 100.0) == new
    assert lv.step([], 104.0) == new


def test_close_above_older_level_but_below_reference_retires_it_and_is_not_an_impulse():
    lv = LiveLevels()
    old, ref = Level(1, 105.0, 3), Level(5, 110.0, 7)
    lv.step([old], 100.0)
    lv.step([ref], 100.0)
    got = lv.step([], 106.0)
    assert got == ref and not 106.0 > got.level  # not an impulse
    assert lv.live == (ref,)


def test_after_the_reference_breaks_the_surviving_older_level_is_the_reference_next_candle():
    lv = LiveLevels()
    old, ref = Level(1, 110.0, 3), Level(5, 105.0, 7)
    lv.step([old], 100.0)
    lv.step([ref], 100.0)
    assert lv.step([], 106.0) == ref  # impulse on ref; ref retires
    assert lv.step([], 106.0) == old


def test_literal_mode_ignores_retirement():
    lv = LiveLevels(fresh=False)
    a = Level(1, 103.0, 3)
    lv.step([a], 100.0)
    assert lv.step([], 120.0) == a
    assert lv.step([], 120.0) == a and lv.live == (a,)
    b = Level(6, 125.0, 8)
    assert lv.step([b], 121.0) == b


def test_a_level_confirmed_on_t_cannot_be_broken_on_t():
    for seed in range(10):
        cd = random_walk(2000, seed=seed, start_ms=T0_MS)
        for k in (1, 2, 3):
            sw = swing_highs(cd, k)
            assert (cd.c[sw.confirmed_at] < sw.level).all()


def test_a_level_confirmed_on_t_is_the_reference_on_t():
    lv = LiveLevels()
    lv.step([Level(1, 110.0, 3)], 100.0)
    new = Level(6, 108.0, 8)
    assert lv.step([new], 100.0) == new


# --- 3.6 block prices -------------------------------------------------------------------------


def test_block_prices_full_top_atr_buffer():
    bp = block_prices(100.0, 100.8, 99.0, 99.2, 2.0, PRIMARY)
    assert (bp.zone_low, bp.zone_high, bp.entry) == (99.0, 100.8, 100.8)
    assert bp.stop == 99.0 - 0.1 * 2.0 and bp.stop_dist == 100.8 - bp.stop
    assert bp.target == 100.8 + 2.0 * bp.stop_dist and bp.pierce_abs == 0.0


def test_block_prices_body_mid_pct_buffer_and_pierce():
    p = StrategyParams(zone="body", entry_level="mid", stop_buffer=StopBuffer("pct", 0.0025), r_target=1.5,
                       pierce=0.0005)
    bp = block_prices(100.0, 100.8, 99.0, 99.2, float("nan"), p)
    entry = (99.2 + 100.0) / 2
    assert (bp.zone_low, bp.zone_high, bp.entry) == (99.2, 100.0, entry)
    assert bp.stop == 99.0 - 0.0025 * entry  # anchored at the true low, not the body
    assert bp.target == entry + 1.5 * bp.stop_dist and bp.pierce_abs == 0.0005 * entry


def test_block_prices_zero_range_candle_with_zero_buffer_is_degenerate():
    bp = block_prices(100.0, 100.0, 100.0, 100.0, 1.0, StrategyParams(stop_buffer=StopBuffer("atr", 0.0)))
    assert bp.stop_dist == 0 and not bp.stop_dist > 0


def test_block_prices_nan_atr():
    assert np.isnan(block_prices(100.0, 100.8, 99.0, 99.2, float("nan"), PRIMARY).stop_dist)
    zero = StrategyParams(stop_buffer=StopBuffer("atr", 0.0))
    assert block_prices(100.0, 100.8, 99.0, 99.2, float("nan"), zero).stop_dist == 100.8 - 99.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_order_block.py`
Expected: collection error, `ModuleNotFoundError: No module named 'perpbt.strategy.order_block'`.

- [ ] **Step 3: Implement**

Create `perpbt/strategy/order_block.py`:

```python
"""The ICT order-block strategy, mechanized (spec phase-3 §3.2–§3.11).

At the close of every candle ``t`` the strategy updates its live levels
(§3.3); if ``t`` is an impulse it looks for the order-block candidate
(§3.4) and runs the checks of §3.5–§3.8 in spec order. The first check a
block fails is its skip reason (§3.11); a block that passes them all
becomes one ``PlaceBracketLimit`` unless the session already has one.
Every read goes through ``MarketView``, so nothing after ``t`` is visible.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

from perpbt.config import StrategyParams


class Level(NamedTuple):
    """A confirmed swing high: candle ``swing_idx``, price ``level``, confirmed at ``confirmed_at``."""

    swing_idx: int
    level: float
    confirmed_at: int


class LiveLevels:
    """The live levels of spec §3.3 (D2): confirmed swing highs not yet closed above.

    ``step`` runs steps 1–4 for one candle and returns the reference (the
    live level with the greatest ``confirmed_at``, taken after adding and
    before retiring). With ``fresh=False`` (``structure_break = "literal"``)
    nothing retires, so the reference is the most recently confirmed swing
    high regardless of breaks.
    """

    def __init__(self, fresh: bool = True) -> None:
        self.fresh = fresh
        self._live: list[Level] = []  # in confirmation order
        self._min = math.inf  # lowest live level: nothing retires unless a close exceeds it

    @property
    def live(self) -> tuple[Level, ...]:
        return tuple(self._live)

    def step(self, new: Sequence[Level], close: float) -> Level | None:
        """Add ``new`` (confirmed on this candle), take the reference, retire every level below ``close``."""
        for lv in new:
            self._live.append(lv)
            self._min = min(self._min, lv.level)
        ref = self._live[-1] if self._live else None
        if self.fresh and close > self._min:
            self._live = [lv for lv in self._live if not lv.level < close]
            self._min = min((lv.level for lv in self._live), default=math.inf)
        return ref


@dataclass(frozen=True)
class BlockPrices:
    """Zone, entry, stop and target of one candidate (spec §3.6)."""

    zone_low: float
    zone_high: float
    entry: float
    stop: float
    stop_dist: float
    target: float
    pierce_abs: float


def block_prices(o: float, h: float, l: float, c: float, atr_t: float, params: StrategyParams) -> BlockPrices:  # noqa: E741
    """Spec §3.6 for a candidate with prices ``o, h, l, c`` and ``ATR14[t] = atr_t``.

    ``stop_dist`` is not checked here: the caller rejects ``not stop_dist > 0``
    (which includes NaN) as ``degenerate``. An ATR buffer of value 0 is 0
    even when ``atr_t`` is NaN.
    """
    zone_low, zone_high = (l, h) if params.zone == "full" else (c, o)
    entry = zone_high if params.entry_level == "top" else (zone_low + zone_high) / 2
    sb = params.stop_buffer
    if sb.kind == "atr":
        buffer = 0.0 if sb.value == 0 else sb.value * atr_t
    else:
        buffer = sb.value * entry
    stop = l - buffer  # the candle's true low in both zone modes
    stop_dist = entry - stop
    return BlockPrices(
        zone_low=zone_low, zone_high=zone_high, entry=entry, stop=stop, stop_dist=stop_dist,
        target=entry + params.r_target * stop_dist, pierce_abs=params.pierce * entry,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_order_block.py`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add perpbt/strategy/order_block.py tests/test_order_block.py
git commit -m "Phase 3: live levels (fresh and literal) and block prices"
```

---

### Task 4: `OrderBlockStrategy`, the harness, scenarios S1–S13 — `strategy/order_block.py` (part 2)

**Files:**
- Modify: `perpbt/strategy/order_block.py` (imports, constants, `_finite_or_none`, `OrderBlockStrategy`)
- Create: `tests/strategy_harness.py`
- Modify: `tests/test_order_block.py` (replace the header, append the scenarios)
- Modify: `docs/superpowers/specs/2026-09-26-ob-backtest/phase-3-strategy.md` §3.1, §3.6, §3.7, §3.11, §3.12
- Modify: `docs/superpowers/specs/2026-09-26-ob-backtest/phase-4-execution.md` §4.1

**Interfaces:**
- Consumes: Task 2's `PlaceBracketLimit`, `SimEvent`, `Intent`, `Strategy`; Task 3's `Level`, `LiveLevels`, `block_prices`; Phase 2's `MarketView` (`i`, `ts`, `open`, `high`, `low`, `close`, `atr`, `daily_sma`, `is_bearish`, `closes`, `lows`, `swings_confirmed_by`, `session`) and `SessionInfo` (`id`, `open_ms`, `end_ms`, `in_window`).
- Produces:
  - In `perpbt.strategy.order_block`: `ATR_PERIOD = 14`, `TREND_SMA_DAYS = 50`, `CANDLES_PER_DAY = 96`; `SKIP_REASONS` (8 names in check order, `no_candidate` first); `COUNT_KEYS = ("impulses", "blocks_seen", *SKIP_REASONS, "intents")`; `TAG_KEYS` (the 11 tag keys in spec order); `OrderBlockStrategy(params: StrategyParams)` with `name = "order_block"`, `params`, `warmup_bars` (14, or `50 * 96` with the trend filter), `on_candle`, `on_event` (no-op), `skip_counts() -> dict[str, int]` (a copy, keys in `COUNT_KEYS` order).
  - In `tests/strategy_harness.py`: `UTC`, `NY`, `LONDON` (`SessionSpec`s from D1/D3), `FLAT_ACCOUNT`, `Run(intents: list[tuple[int, Intent]], counts: dict, counts_at: dict[int, dict])`, `assert_counts_consistent(counts)`, `build_view(candles, spec, *, swing_k, daily_sma=None, start_i=0) -> MarketView`, `run_strategy(candles, params, spec=UTC, *, daily_sma=None, start_i=0, stop_at=None, events=None, counts_at=()) -> Run`. Task 5 and Task 6 use these.

Design decisions this task writes into the spec (each is a clarification the spec left open; see the amendments in Step 3):

- **Check order and counting (§3.11).** A block gets exactly one outcome, the first that applies in rule order; `session_used` is last (per §3.9 "if the block survives 3.4–3.8 and the session has no intent yet"), so it counts blocks the per-session cap cost, not every later impulse. A new `intents` count closes the identity `blocks_seen = Σ outcomes`.
- **S13 as written cannot happen in the strategy**: a bearish candle has `open > close`, so its range is positive and `stop_dist > 0` for any finite buffer. The zero-range case is tested on `block_prices` (Task 3); the strategy-level `degenerate` is NaN ATR14 in warmup.
- **`skip_mitigated = "stop"`** (accepted by the config, undefined in the spec): a `mitigated` block ends the session.
- **Call contract**: consecutive calls only; the first call rebuilds the live levels from candle 0; `warmup_bars` is informational.
- **Tag `atr` is `None` when NaN**, so the tag stays valid JSON.

- [ ] **Step 1: Write the failing tests**

Create `tests/strategy_harness.py`:

```python
"""Drive a strategy over candles the way the Phase 4 simulator will, minus execution.

Builds the ``MarketView`` from the candles (ATR14, swing highs of the
params' ``k``, completed-day SMA(50) unless an explicit array is given,
ADX(14), the session calendar), advances it one candle at a time, calls
``on_candle`` with a flat account and records every intent with its
decision index. Events given for candle ``i`` are delivered right after
``on_candle(i)``, where the simulator's placement step would emit them.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from perpbt.config import SessionSpec, StrategyParams
from perpbt.data.sessions import SessionCalendar
from perpbt.data.store import Candles
from perpbt.indicators.atr import atr
from perpbt.indicators.daily import daily_adx_aligned, daily_sma_aligned
from perpbt.indicators.swings import swing_highs
from perpbt.strategy.base import AccountView, Intent, MarketView, SimEvent
from perpbt.strategy.order_block import ATR_PERIOD, COUNT_KEYS, SKIP_REASONS, TREND_SMA_DAYS, OrderBlockStrategy

UTC = SessionSpec(name="utc", tz="UTC", open="00:00", close="24:00", days=(0, 1, 2, 3, 4, 5, 6))
NY = SessionSpec(name="ny", tz="America/New_York", open="09:30", close="16:00", days=(0, 1, 2, 3, 4))
LONDON = SessionSpec(name="london", tz="Europe/London", open="08:00", close="16:30", days=(0, 1, 2, 3, 4))

FLAT_ACCOUNT = AccountView(equity_mtm=10_000.0, open_positions=(), pending_orders=())


@dataclass
class Run:
    intents: list[tuple[int, Intent]]  # (decision index, intent)
    counts: dict[str, int]  # skip_counts() after the last candle run
    counts_at: dict[int, dict[str, int]] = field(default_factory=dict)  # skip_counts() after candle i


def assert_counts_consistent(c: dict[str, int]) -> None:
    """Spec §3.11: every impulse and every block has exactly one outcome."""
    assert list(c) == list(COUNT_KEYS), list(c)
    assert c["impulses"] == c["no_candidate"] + c["blocks_seen"], c
    assert c["blocks_seen"] == sum(c[r] for r in SKIP_REASONS if r != "no_candidate") + c["intents"], c


def build_view(
    candles: Candles, spec: SessionSpec, *, swing_k: int,
    daily_sma: np.ndarray | None = None, start_i: int = 0,
) -> MarketView:
    sma = daily_sma_aligned(candles, TREND_SMA_DAYS) if daily_sma is None else np.asarray(daily_sma, dtype=np.float64)
    return MarketView(
        candles,
        atr=atr(candles, ATR_PERIOD),
        daily_sma=sma,
        daily_adx=daily_adx_aligned(candles, 14),
        swings=swing_highs(candles, swing_k),
        calendar=SessionCalendar(spec, candles.ts),
        start_i=start_i,
    )


def run_strategy(
    candles: Candles, params: StrategyParams, spec: SessionSpec = UTC, *,
    daily_sma: np.ndarray | None = None, start_i: int = 0, stop_at: int | None = None,
    events: Mapping[int, Sequence[SimEvent]] | None = None, counts_at: Sequence[int] = (),
) -> Run:
    """Run ``OrderBlockStrategy(params)`` on candles ``start_i .. stop_at`` (default: to the end)."""
    strategy = OrderBlockStrategy(params)
    view = build_view(candles, spec, swing_k=params.swing_k, daily_sma=daily_sma, start_i=start_i)
    last = len(candles) - 1 if stop_at is None else stop_at
    events = events or {}
    snap = set(counts_at)
    run = Run([], {})
    for i in range(start_i, last + 1):
        view.advance_to(i)
        for intent in strategy.on_candle(view, FLAT_ACCOUNT):
            run.intents.append((i, intent))
        for ev in events.get(i, ()):
            strategy.on_event(ev)
        if i in snap:
            run.counts_at[i] = strategy.skip_counts()
    run.counts = strategy.skip_counts()
    return run
```

In `tests/test_order_block.py` replace everything from the first line down to and including the line `PRIMARY = StrategyParams()  # k = 2, N = 3, full zone, top entry, 0.1 x ATR buffer, 2 R` with the header below. Task 5 uses the imports that this task's tests do not (`json`, `SessionCalendar`, `perturb_after`, `assert_counts_consistent`, `TAG_KEYS` in part).

```python
"""The ICT order-block rule (spec phase-3 §3.2–§3.12)."""
import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from perpbt.config import HoldRule, StopBuffer, StrategyParams
from perpbt.data.sessions import SessionCalendar
from perpbt.indicators.atr import atr
from perpbt.indicators.swings import swing_highs
from perpbt.strategy.base import PlaceBracketLimit, SimEvent, Strategy
from perpbt.strategy.order_block import (
    COUNT_KEYS,
    TAG_KEYS,
    Level,
    LiveLevels,
    OrderBlockStrategy,
    block_prices,
)
from tests.strategy_harness import (
    FLAT_ACCOUNT,
    LONDON,
    NY,
    UTC,
    assert_counts_consistent,
    build_view,
    run_strategy,
)
from tests.synthetic import STEP_15M_MS, T0_MS, candles_from_rows, perturb_after, random_walk

DAY_MS = 86_400_000
PRIMARY = StrategyParams()  # k = 2, N = 3, full zone, top entry, 0.1 x ATR buffer, 2 R

# Hand-placed scenario candles (o, h, l, c). With k = 2 and N = 3:
FILL = (100.0, 100.5, 99.5, 100.0)  # equal highs, never a swing; open == close, never bearish
BASE = [FILL] * 20 + [
    (100.0, 101.0, 99.5, 100.5),   # 20
    (100.5, 103.0, 100.0, 102.0),  # 21 swing high, level 103
    (101.0, 102.5, 100.5, 101.0),  # 22 doji
    (100.0, 101.5, 99.8, 100.0),   # 23 doji; confirms swing 21 (s + k)
]
C24 = (100.0, 100.8, 99.0, 99.2)   # bearish candidate c = 24: zone [99.0, 100.8]
T25 = (99.2, 104.0, 99.1, 103.5)   # impulse t = 25: closes above 103 and above high[c]
S1 = BASE + [C24, T25]
TAIL = [  # appended after a candle closing at 103.5: a second, independent valid block
    (103.5, 105.0, 103.0, 104.5),  # +0
    (104.5, 106.0, 104.0, 105.5),  # +1 swing high, level 106
    (105.5, 105.8, 104.5, 105.0),  # +2
    (105.0, 105.2, 104.2, 104.4),  # +3 confirms +1
    (104.4, 104.6, 103.8, 104.0),  # +4 bearish candidate, high 104.6
    (104.0, 107.0, 103.9, 106.5),  # +5 impulse
]


def counts(**kw):
    out = dict.fromkeys(COUNT_KEYS, 0)
    out.update(kw)
    return out


def cd_of(rows, start_ms=T0_MS):
    return candles_from_rows(rows, start_ms=start_ms)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)
```

Append to `tests/test_order_block.py`:

```python


# --- 3.2 block selection: scenarios -------------------------------------------------------


def test_s1_basic_block():
    cd = cd_of(S1)
    run = run_strategy(cd, PRIMARY)
    a = float(atr(cd, 14)[25])
    stop = 99.0 - 0.1 * a
    stop_dist = 100.8 - stop
    tag = {
        "candidate_idx": 24, "impulse_idx": 25, "displacement_idx": 25, "swing_idx": 21, "swing_level": 103.0,
        "zone_low": 99.0, "zone_high": 100.8, "entry_kind": "top", "atr": a, "stop_dist": stop_dist,
        "session_id": date(2020, 1, 1).toordinal(),
    }
    assert run.intents == [(25, PlaceBracketLimit(
        side="long", price=100.8, stop=stop, target=100.8 + 2.0 * stop_dist,
        expires_ms=T0_MS + DAY_MS, hold_rule=HoldRule("none"), tag=tag,
    ))]
    assert list(run.intents[0][1].tag) == list(TAG_KEYS)
    assert run.counts == counts(impulses=1, blocks_seen=1, intents=1)


def test_s1_carries_the_hold_rule():
    rule = HoldRule("max_hold", 24)
    (_, x), = run_strategy(cd_of(S1), StrategyParams(hold_rule=rule)).intents
    assert x.hold_rule == rule


S2 = BASE + [
    C24,
    (99.2, 100.0, 99.0, 99.8),     # 25 bullish
    (99.8, 100.5, 99.5, 100.2),    # 26 bullish
    (100.2, 101.0, 100.0, 100.6),  # 27 bullish
    (100.6, 104.0, 100.5, 103.5),  # 28 impulse = c + N + 1
]


def test_s2_impulse_at_c_plus_n_plus_1_has_no_candidate():
    run = run_strategy(cd_of(S2), PRIMARY)
    assert run.intents == [] and run.counts == counts(impulses=1, no_candidate=1)
    wider = run_strategy(cd_of(S2), StrategyParams(confirm_n=4))  # N = 4 reaches c
    (i, x), = wider.intents
    assert i == 28 and x.tag["candidate_idx"] == 24 and x.tag["displacement_idx"] == 28


def test_s3_the_later_of_two_bearish_candles_is_the_block():
    rows = BASE[:23] + [(101.0, 101.5, 99.8, 100.0), C24, T25]  # 23 and 24 both bearish
    (_, x), = run_strategy(cd_of(rows), PRIMARY).intents
    assert x.tag["candidate_idx"] == 24 and x.price == 100.8


def test_s4_level_already_closed_above_gives_no_impulse():
    rows = BASE + [
        (100.0, 103.6, 99.9, 103.2),  # 24 closes above 103: impulse, no candidate; 103 retires
        (103.2, 103.4, 102.0, 102.2),  # 25 bearish
        (102.2, 104.5, 102.1, 104.0),  # 26 closes above 103 again: no live level, no impulse
    ]
    run = run_strategy(cd_of(rows), PRIMARY)
    assert run.intents == [] and run.counts == counts(impulses=1, no_candidate=1)


def test_s5_candidate_before_the_session_open_is_ineligible_and_the_next_block_is_taken():
    cd = cd_of(S1 + TAIL, start_ms=T0_MS - 25 * STEP_15M_MS)  # c = 24 at 23:45, t = 25 at 00:00 UTC
    run = run_strategy(cd, PRIMARY)
    assert run.counts == counts(impulses=2, blocks_seen=2, ineligible=1, intents=1)
    (i, x), = run.intents
    assert i == 31 and x.tag["candidate_idx"] == 30 and x.price == 104.6
    assert x.expires_ms == T0_MS + DAY_MS


def test_s6a_the_immediate_pattern_is_not_mitigated():
    (_, x), = run_strategy(cd_of(S1), PRIMARY).intents
    assert x.tag["displacement_idx"] == x.tag["impulse_idx"] == 25


S6B = BASE + [
    C24,                            # 24 c, entry 100.8
    (99.2, 101.5, 99.1, 101.2),     # 25 d: closes above 100.8, below 103
    (101.0, 101.8, 100.7, 101.4),   # 26 trades back to 100.7 <= entry
    (101.4, 104.0, 101.3, 103.5),   # 27 impulse
] + TAIL                            # 28..33: the next block


def test_s6b_traded_back_after_displacement_is_mitigated_and_the_next_block_is_taken():
    run = run_strategy(cd_of(S6B), PRIMARY)
    assert run.counts == counts(impulses=2, blocks_seen=2, mitigated=1, intents=1)
    (i, x), = run.intents
    assert i == 33 and x.tag["candidate_idx"] == 32 and x.tag["displacement_idx"] == 33


def test_s6b_skip_mitigated_stop_ends_the_session():
    run = run_strategy(cd_of(S6B), StrategyParams(skip_mitigated="stop"))
    assert run.intents == [] and run.counts == counts(impulses=2, blocks_seen=2, mitigated=1, session_used=1)


def _dip_rows(low26):
    return BASE + [C24, (99.2, 101.5, 99.1, 101.2), (101.0, 101.8, low26, 101.4), (101.4, 104.0, 101.3, 103.5)]


def test_s6c_with_pierce_a_low_at_entry_is_not_mitigation_but_entry_minus_pierce_is():
    pierce = StrategyParams(pierce=0.0005)
    run = run_strategy(cd_of(_dip_rows(100.8)), pierce)
    assert [i for i, _ in run.intents] == [27] and run.counts["mitigated"] == 0
    run = run_strategy(cd_of(_dip_rows(100.8 - 0.0005 * 100.8)), pierce)
    assert run.intents == [] and run.counts["mitigated"] == 1
    run = run_strategy(cd_of(_dip_rows(100.8)), PRIMARY)  # pierce 0: a low at entry mitigates
    assert run.intents == [] and run.counts["mitigated"] == 1


@pytest.mark.parametrize("events", [
    {},
    {25: [SimEvent("skipped_leverage", 25)]},
    {26: [SimEvent("filled", 26, order_id=1, position_id=1)], 27: [SimEvent("closed", 27, position_id=1, reason="stop")]},
    {26: [SimEvent("cancelled", 26, order_id=1, reason="expired")]},
], ids=["no_events", "leverage_skip", "stopped_out", "expired"])
def test_s7_one_intent_per_session_whatever_happens_to_it(events):
    run = run_strategy(cd_of(S1 + TAIL), PRIMARY, events=events)
    assert [i for i, _ in run.intents] == [25]
    assert run.counts == counts(impulses=2, blocks_seen=2, session_used=1, intents=1)


def test_s7_the_next_session_is_free_again():
    cd = cd_of(S1 + TAIL, start_ms=T0_MS + DAY_MS - 26 * STEP_15M_MS)  # t = 25 at 23:30, the TAIL on the next day
    run = run_strategy(cd, PRIMARY)
    assert [i for i, _ in run.intents] == [25, 31]
    assert [x.expires_ms for _, x in run.intents] == [T0_MS + DAY_MS, T0_MS + 2 * DAY_MS]


@pytest.mark.parametrize("zone,entry_level,entry,zone_low,zone_high", [
    ("body", "top", 100.0, 99.2, 100.0),
    ("full", "mid", (99.0 + 100.8) / 2, 99.0, 100.8),
    ("body", "mid", (99.2 + 100.0) / 2, 99.2, 100.0),
])
def test_s8_zone_and_entry_modes(zone, entry_level, entry, zone_low, zone_high):
    cd = cd_of(S1)
    stop = 99.0 - 0.1 * float(atr(cd, 14)[25])  # from low[c] in both zone modes
    (_, x), = run_strategy(cd, StrategyParams(zone=zone, entry_level=entry_level)).intents
    assert x.price == entry and x.stop == stop and x.target == entry + 2.0 * (entry - stop)
    assert (x.tag["zone_low"], x.tag["zone_high"], x.tag["entry_kind"]) == (zone_low, zone_high, entry_level)


def test_s9_trend_filter():
    cd = cd_of(S1)
    n = len(cd)
    on = StrategyParams(trend_filter=True)
    assert len(run_strategy(cd, on, daily_sma=np.full(n, 103.0)).intents) == 1  # close 103.5 above the SMA
    below = run_strategy(cd, on, daily_sma=np.full(n, 104.0))
    assert below.intents == [] and below.counts == counts(impulses=1, blocks_seen=1, trend=1)
    warmup = run_strategy(cd, on, daily_sma=np.full(n, np.nan))
    assert warmup.intents == [] and warmup.counts["trend"] == 1
    assert len(run_strategy(cd, PRIMARY, daily_sma=np.full(n, 104.0)).intents) == 1  # filter off


@pytest.mark.parametrize("spec,t_utc,window_end", [
    # 2020-03-08 US and 2020-03-29 EU clocks go forward; windows move one hour earlier in UTC
    (UTC, datetime(2020, 3, 29, 12, 0, tzinfo=timezone.utc), datetime(2020, 3, 30, 0, 0, tzinfo=timezone.utc)),
    (NY, datetime(2020, 3, 6, 15, 0, tzinfo=timezone.utc),
     datetime(2020, 3, 6, 16, 0, tzinfo=ZoneInfo("America/New_York"))),
    (NY, datetime(2020, 3, 9, 14, 0, tzinfo=timezone.utc),
     datetime(2020, 3, 9, 16, 0, tzinfo=ZoneInfo("America/New_York"))),
    (LONDON, datetime(2020, 3, 27, 9, 0, tzinfo=timezone.utc),
     datetime(2020, 3, 27, 16, 30, tzinfo=ZoneInfo("Europe/London"))),
    (LONDON, datetime(2020, 3, 30, 8, 0, tzinfo=timezone.utc),
     datetime(2020, 3, 30, 16, 30, tzinfo=ZoneInfo("Europe/London"))),
], ids=["utc", "ny_before", "ny_after", "london_before", "london_after"])
def test_s10_expiry_is_the_window_end_across_dst(spec, t_utc, window_end):
    cd = cd_of(S1, start_ms=ms(t_utc) - 25 * STEP_15M_MS)
    (_, x), = run_strategy(cd, PRIMARY, spec).intents
    assert x.expires_ms == ms(window_end)


def test_s11_candidate_wick_above_every_close_has_no_displacement():
    rows = BASE + [(100.0, 104.5, 99.0, 99.2), T25]  # high[c] 104.5 above close[t] 103.5
    run = run_strategy(cd_of(rows), PRIMARY)
    assert run.intents == [] and run.counts == counts(impulses=1, blocks_seen=1, no_displacement=1)


def test_s11_body_zone_still_needs_a_close_above_the_true_high():
    rows = BASE + [(100.0, 103.6, 99.0, 99.2), (99.2, 103.8, 99.1, 103.5)]  # above open[c], below high[c]
    run = run_strategy(cd_of(rows), StrategyParams(zone="body"))
    assert run.intents == [] and run.counts["no_displacement"] == 1


S12 = [FILL] * 20 + [  # k = 1, N = 5, mid entry
    (100.0, 102.0, 98.0, 99.0),    # 20 c: zone [98, 102], mid entry 100
    (99.0, 102.6, 98.9, 102.5),    # 21 d: closes above 102 (no live level yet, so no impulse)
    (99.1, 99.35, 99.1, 99.3),     # 22 confirms swing 21 (level 102.6)
    (99.3, 99.45, 99.2, 99.4),     # 23 swing high, level 99.45
    (99.35, 99.4, 99.2, 99.4),     # 24 confirms 23
    (99.4, 99.9, 99.3, 99.6),      # 25 t: closes above 99.45, at or below entry 100
]


def test_s12_close_between_entry_minus_pierce_and_entry_is_entry_above_price():
    params = StrategyParams(swing_k=1, confirm_n=5, entry_level="mid", pierce=0.01)  # pierce_abs = 1.0
    run = run_strategy(cd_of(S12), params)
    assert run.intents == [] and run.counts == counts(impulses=1, blocks_seen=1, entry_above_price=1)
    no_pierce = run_strategy(cd_of(S12), StrategyParams(swing_k=1, confirm_n=5, entry_level="mid"))
    assert no_pierce.counts["mitigated"] == 1  # with pierce 0 the lows already reach the entry


def test_s13_nan_atr_is_degenerate_and_other_buffers_do_not_need_atr():
    cd = cd_of([FILL, FILL] + BASE[20:] + [C24, T25])  # t = 7: ATR14 is NaN
    assert np.isnan(atr(cd, 14)[7])
    run = run_strategy(cd, PRIMARY)
    assert run.intents == [] and run.counts == counts(impulses=1, blocks_seen=1, degenerate=1)
    for sb in (StopBuffer("pct", 0.001), StopBuffer("atr", 0.0)):
        (_, x), = run_strategy(cd, StrategyParams(stop_buffer=sb)).intents
        assert x.tag["atr"] is None


def test_candidate_search_clamps_at_the_first_candle():
    rows = [
        (100.5, 100.8, 99.5, 100.0),   # 0 bearish candidate
        (100.0, 101.0, 99.8, 100.6),   # 1 swing high (k = 1), level 101
        (100.6, 100.9, 100.2, 100.7),  # 2 confirms 1
        (100.7, 101.6, 100.5, 101.4),  # 3 impulse; N = 5 would reach index -2
    ]
    run = run_strategy(cd_of(rows), StrategyParams(swing_k=1, confirm_n=5, stop_buffer=StopBuffer("pct", 0.001)))
    (i, x), = run.intents
    assert i == 3 and x.tag["candidate_idx"] == 0 and x.tag["displacement_idx"] == 3


# --- protocol and call contract --------------------------------------------------------------


def test_order_block_strategy_is_a_strategy():
    s = OrderBlockStrategy(PRIMARY)
    assert isinstance(s, Strategy) and s.name == "order_block" and s.params is PRIMARY
    assert s.warmup_bars == 14 and OrderBlockStrategy(StrategyParams(trend_filter=True)).warmup_bars == 50 * 96
    assert s.skip_counts() == counts()
    s.skip_counts()["impulses"] = 99  # a copy
    assert s.skip_counts() == counts()


def test_on_candle_refuses_a_skipped_or_repeated_candle():
    cd = random_walk(100, seed=1, start_ms=T0_MS)
    view = build_view(cd, UTC, swing_k=2)
    s = OrderBlockStrategy(PRIMARY)
    s.on_candle(view, FLAT_ACCOUNT)
    with pytest.raises(ValueError, match="expected candle 1"):
        s.on_candle(view, FLAT_ACCOUNT)  # candle 0 again
    view.advance_to(2)
    with pytest.raises(ValueError, match="expected candle 1"):
        s.on_candle(view, FLAT_ACCOUNT)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_order_block.py`
Expected: collection error, `ImportError: cannot import name 'COUNT_KEYS' from 'perpbt.strategy.order_block'` (raised through the test module or `tests/strategy_harness.py`).

- [ ] **Step 3: Implement**

In `perpbt/strategy/order_block.py` replace

```python
from perpbt.config import StrategyParams
```

with

```python
from perpbt.config import StrategyParams
from perpbt.strategy.base import AccountView, Intent, MarketView, PlaceBracketLimit, SimEvent

ATR_PERIOD = 14  # the simulator builds the view's ATR with this period
TREND_SMA_DAYS = 50  # and the daily SMA with this one (D7)
CANDLES_PER_DAY = 96

SKIP_REASONS = (
    "no_candidate", "ineligible", "degenerate", "no_displacement",
    "mitigated", "entry_above_price", "trend", "session_used",
)
COUNT_KEYS = ("impulses", "blocks_seen", *SKIP_REASONS, "intents")
TAG_KEYS = (
    "candidate_idx", "impulse_idx", "displacement_idx", "swing_idx", "swing_level",
    "zone_low", "zone_high", "entry_kind", "atr", "stop_dist", "session_id",
)
```

Append at the end of the file:

```python


def _finite_or_none(x: float) -> float | None:
    return x if math.isfinite(x) else None


class OrderBlockStrategy:
    """The first-bullish-order-block rule as a ``Strategy`` (spec §3.1–§3.11).

    ``on_candle`` must be called once per candle, in order, without gaps
    (a gap would skip a retirement, so it raises ValueError). On the first
    call, at candle ``i0``, the live levels are rebuilt from candles
    ``0..i0-1`` without counting or emitting anything, so the output from
    ``i0`` on does not depend on where the loop starts. The used-session
    state is not rebuilt: start at a session boundary.
    """

    name = "order_block"

    def __init__(self, params: StrategyParams) -> None:
        self.params = params
        self.warmup_bars = TREND_SMA_DAYS * CANDLES_PER_DAY if params.trend_filter else ATR_PERIOD
        self._levels = LiveLevels(fresh=params.structure_break == "fresh")
        self._seen = 0  # swings already handed to the live levels
        self._next_i: int | None = None
        self._used_session: int | None = None
        self._counts = dict.fromkeys(COUNT_KEYS, 0)

    # --- Strategy protocol -------------------------------------------------------------

    def on_candle(self, view: MarketView, account: AccountView) -> list[Intent]:
        t = view.i
        if self._next_i is None:
            self._catch_up(view, t)
        elif t != self._next_i:
            raise ValueError(f"OrderBlockStrategy.on_candle: expected candle {self._next_i}, got {t}")
        self._next_i = t + 1
        close_t = view.close(t)
        ref = self._levels.step(self._new_levels(view, t), close_t)
        if ref is None or not close_t > ref.level:
            return []
        self._counts["impulses"] += 1
        intent = self._evaluate(view, t, ref, close_t)
        return [] if intent is None else [intent]

    def on_event(self, event: SimEvent) -> None:
        """Nothing to do: the session is consumed when the intent is emitted (§3.5)."""
        return None

    def skip_counts(self) -> dict[str, int]:
        """``impulses``, ``blocks_seen``, every skip reason and ``intents``, in ``COUNT_KEYS`` order (§3.11)."""
        return dict(self._counts)

    # --- live levels ---------------------------------------------------------------------

    def _catch_up(self, view: MarketView, i0: int) -> None:
        if i0 == 0:
            return
        sw = view.swings_confirmed_by(i0 - 1)
        closes = view.closes(0, i0 - 1)
        p = 0
        for j in range(i0):
            new = []
            while p < len(sw) and sw.confirmed_at[p] == j:
                new.append(Level(int(sw.idx[p]), float(sw.level[p]), j))
                p += 1
            self._levels.step(new, float(closes[j]))
        self._seen = len(sw)

    def _new_levels(self, view: MarketView, t: int) -> list[Level]:
        sw = view.swings_confirmed_by(t)
        new = [
            Level(int(sw.idx[j]), float(sw.level[j]), int(sw.confirmed_at[j]))
            for j in range(self._seen, len(sw))
        ]
        self._seen = len(sw)
        return new

    # --- the block ---------------------------------------------------------------------

    def _skip(self, reason: str) -> None:
        self._counts[reason] += 1

    def _candidate(self, view: MarketView, t: int) -> int | None:
        """§3.4: the most recent bearish candle in ``max(t - N, 0) .. t - 1``."""
        for j in range(t - 1, max(t - self.params.confirm_n, 0) - 1, -1):
            if view.is_bearish(j):
                return j
        return None

    def _displacement(self, view: MarketView, c: int, t: int) -> int | None:
        """§3.7: the first candle in ``c + 1 .. t`` closing above ``high[c]``."""
        high_c = view.high(c)
        for j, close in enumerate(view.closes(c + 1, t), start=c + 1):
            if close > high_c:
                return j
        return None

    def _evaluate(self, view: MarketView, t: int, ref: Level, close_t: float) -> PlaceBracketLimit | None:
        p = self.params
        c = self._candidate(view, t)
        if c is None:
            return self._skip("no_candidate")
        self._counts["blocks_seen"] += 1
        s = view.session
        if not (s.in_window and view.ts(c) >= s.open_ms):  # §3.5
            return self._skip("ineligible")
        bp = block_prices(view.open(c), view.high(c), view.low(c), view.close(c), view.atr(t), p)
        if not bp.stop_dist > 0:  # §3.6; NaN (ATR warmup) fails too
            return self._skip("degenerate")
        d = self._displacement(view, c, t)
        if d is None:
            return self._skip("no_displacement")
        if d < t and float(view.lows(d + 1, t).min()) <= bp.entry - bp.pierce_abs:
            if p.skip_mitigated == "stop":
                self._used_session = s.id
            return self._skip("mitigated")
        if not close_t > bp.entry:
            return self._skip("entry_above_price")
        if p.trend_filter and not close_t > view.daily_sma(t):  # §3.8; NaN SMA rejects
            return self._skip("trend")
        if s.id == self._used_session:  # §3.9: at most one intent per session
            return self._skip("session_used")
        self._used_session = s.id
        self._counts["intents"] += 1
        tag = {
            "candidate_idx": c,
            "impulse_idx": t,
            "displacement_idx": d,
            "swing_idx": ref.swing_idx,
            "swing_level": ref.level,
            "zone_low": bp.zone_low,
            "zone_high": bp.zone_high,
            "entry_kind": p.entry_level,
            "atr": _finite_or_none(view.atr(t)),
            "stop_dist": bp.stop_dist,
            "session_id": s.id,
        }
        return PlaceBracketLimit(
            side="long", price=bp.entry, stop=bp.stop, target=bp.target,
            expires_ms=s.end_ms, hold_rule=p.hold_rule, tag=tag,
        )
```

Spec amendments in `phase-3-strategy.md`, same commit:

(a) §3.1: after the paragraph that ends ``` zone_low, zone_high, entry_kind, atr, stop_dist,
session_id`. ``` add:

```markdown

Tag values are plain `int`, `float` or `str`; `atr` is `None` while ATR14
is NaN (reachable only with a `pct` or zero buffer, §3.6).

**Call contract.** The simulator calls `on_candle` once per candle, in
order, with no gaps; a skipped or repeated candle raises `ValueError`. On
its first call, at candle `i0`, the strategy rebuilds its live levels
(§3.3) from candles `0..i0−1` without counting or emitting anything, so
its output from `i0` on does not depend on where the loop starts (the
holdout's first call at 2026-01-01 sees the same levels as a run from
2019). The used-session state is not rebuilt, so the first call must be at
a session boundary; the simulator's is at `period_start`, 00:00 UTC, which
no window straddles. `warmup_bars` is informational: the candles before
the first decision the indicators need (14 for ATR14; 50 × 96 with the
trend filter on). A shortfall is handled by the rule itself: NaN ATR is
`degenerate` (§3.6), NaN SMA is `trend` (§3.8).
```

(b) §3.6: replace the whole bullet list

```markdown
- Zone: `full` → `[low[c], high[c]]`; `body` → `[close[c], open[c]]`.
- Entry: `top` → zone top; `mid` → zone midpoint.
- Stop: `low[c] − buffer`, anchored at the candle's true low in both zone
  modes. Buffer is `value × ATR14[t]` (Wilder, 15m, as of `t`) for `atr`,
  or `value × entry` for `pct`.
- `stop_dist = entry − stop` (must be `> 0`; otherwise `degenerate`, counted).
- Target: `entry + r_target × stop_dist`.
- Pierce: `pierce_abs = pierce × entry`.
```

with

```markdown
- Zone: `full` → `[low[c], high[c]]`; `body` → `[close[c], open[c]]`.
- Entry: `top` → zone top; `mid` → zone midpoint `(zone_low + zone_high) / 2`.
- Stop: `low[c] − buffer`, anchored at the candle's true low in both zone
  modes. Buffer is `value × ATR14[t]` (Wilder, 15m, as of `t`) for `atr`,
  or `value × entry` for `pct`. An `atr` buffer with `value = 0` is 0 even
  while ATR14 is NaN.
- `stop_dist = entry − stop` (must be `> 0`; otherwise `degenerate`,
  counted; NaN fails the test). A bearish candidate has positive range, so
  with a finite buffer `stop_dist > 0` always: in practice `degenerate`
  means ATR14 is NaN (the first 13 candles) under a nonzero `atr` buffer.
- Target: `entry + r_target × stop_dist`.
- Pierce: `pierce_abs = pierce × entry`; the mitigation threshold of §3.7
  is `entry − pierce_abs`, computed exactly so.
```

(c) §3.7: after the **Mitigation.** bullet (it ends ``` "would have filled" are the same test. ```) insert the bullet

```markdown
- **Stop mode.** With `skip_mitigated = "stop"` (accepted, not in the
  grid) a `mitigated` block also ends the session: later blocks in it are
  `session_used`. The default `continue` is D4.
```

(d) §3.11: replace the paragraph

```markdown
`skip_counts()` returns counts of `no_candidate, ineligible,
no_displacement, mitigated, entry_above_price, degenerate, trend,
session_used` (an impulse seen after the session's intent) plus
`impulses` and `blocks_seen`. The simulator adds `leverage`. All appear in
the results row and the report.
```

with

```markdown
`skip_counts()` returns, in this order, `impulses`, `blocks_seen`,
`no_candidate`, `ineligible`, `degenerate`, `no_displacement`, `mitigated`,
`entry_above_price`, `trend`, `session_used` and `intents`. Every impulse
counts in `impulses`. An impulse without a candidate is `no_candidate`;
one with a candidate is a block and counts in `blocks_seen`. Each block
gets exactly one outcome, the first that applies in rule order:
`ineligible` (3.5), `degenerate` (3.6), `no_displacement`, `mitigated`,
`entry_above_price` (3.7), `trend` (3.8), `session_used` (a block that
passed 3.4–3.8 in a session that already has its intent), else `intents`.
So

    impulses    = no_candidate + blocks_seen
    blocks_seen = ineligible + degenerate + no_displacement + mitigated
                + entry_above_price + trend + session_used + intents

The simulator adds `leverage`. All appear in the results row and the
report.
```

(e) §3.12: replace

```markdown
  - S13 `degenerate`: a zero-range candle with zero buffer.
```

with

```markdown
  - S13 `degenerate`: `block_prices` on a zero-range candle with zero
    buffer gives `stop_dist = 0`. In the strategy a bearish candidate
    always has positive range, so the reachable case is ATR14 NaN
    (warmup) under an `atr` buffer; a `pct` or zero buffer on the same
    candles gives an intent with `atr = None`.
```

`phase-4-execution.md` §4.1: between the numbered list (it ends with ``` with `placed_idx = i`. Orders placed here are first evaluated at `i+1`. ```) and the line `The simulator has no randomness. Running twice yields byte-identical` insert:

```markdown
The first `on_candle` call is at `period_start`: the strategy rebuilds its
state from the earlier candles through the view (Phase 3 §3.1), so the
loop does not run over the warmup and skip counts cover the period only.

```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_order_block.py` then `python -m pytest -q -m "not slow"`
Expected: 42 passed in `test_order_block.py` (11 from Task 3 plus 31 here, counting parametrized cases); the fast suite 459 passed. If a scenario fails, print `run.counts` and `run.intents` before touching the code: every row above was checked against this implementation, so a failure means the code or a row was copied wrongly.

- [ ] **Step 5: Commit**

```bash
git add perpbt/strategy/order_block.py tests/strategy_harness.py tests/test_order_block.py \
        docs/superpowers/specs/2026-09-26-ob-backtest/phase-3-strategy.md \
        docs/superpowers/specs/2026-09-26-ob-backtest/phase-4-execution.md
git commit -m "Phase 3: OrderBlockStrategy with scenarios S1-S13; spec: check order and count identities, call contract, S13 reachable case, skip_mitigated stop"
```

---

### Task 5: Strategy-level look-ahead, start independence, properties on random walks

**Files:**
- Modify: `tests/test_order_block.py` (append)
- Modify: `docs/superpowers/specs/2026-09-26-ob-backtest/phase-3-strategy.md` §3.12 item 3.3 (one clause)

**Interfaces:**
- Consumes: `run_strategy`, `assert_counts_consistent`, `UTC`, `NY`, `LONDON` (Task 4 harness); `perturb_after`, `random_walk` (`tests/synthetic.py`); `Candles.slice`; `TAG_KEYS`.
- Produces: tests only.

These tests exercise code that Task 4 already wrote, so they are expected to pass on the first run. A failure is a Task 4 bug: debug it (superpowers:systematic-debugging), do not loosen the test. `stop_at=cut` on the perturbed runs is sound: intents at `≤ cut` are recorded before the loop reaches later candles, and the perturbed data still reaches the strategy through indicators computed on the whole perturbed series.

- [ ] **Step 1: Write the tests**

Append to `tests/test_order_block.py`:

```python


@pytest.mark.parametrize("params", [PRIMARY, StrategyParams(structure_break="literal")], ids=["fresh", "literal"])
def test_first_call_mid_series_rebuilds_the_live_levels(params):
    checked = 0
    for seed in range(5):
        cd = random_walk(3000, seed=seed, start_ms=T0_MS)
        full = run_strategy(cd, params)
        for i0 in (96 * 5, 96 * 17):  # UTC session boundaries
            late = run_strategy(cd, params, start_i=i0)
            expected = [(i, x) for i, x in full.intents if i >= i0]
            assert late.intents == expected
            checked += len(expected)
    assert checked > 50


# --- 3.3 strategy-level look-ahead and properties on random walks -------------------------


CUTS = (1500, 3000, 4500)


@pytest.mark.parametrize("spec", [UTC, NY], ids=["utc", "ny"])
def test_intents_up_to_cut_ignore_later_candles(spec):
    total = 0
    for seed in range(20):
        cd = random_walk(5000, seed=seed, start_ms=T0_MS)
        full = run_strategy(cd, PRIMARY, spec, counts_at=CUTS)
        total += len(full.intents)
        for cut in CUTS:
            expected = [(i, x) for i, x in full.intents if i <= cut]
            head = run_strategy(cd.slice(int(cd.ts[0]), int(cd.ts[cut]) + 1), PRIMARY, spec)
            assert head.intents == expected and head.counts == full.counts_at[cut], (seed, cut, "truncated")
            moved = run_strategy(perturb_after(cd, cut, seed=1000 + seed), PRIMARY, spec, stop_at=cut)
            assert moved.intents == expected and moved.counts == full.counts_at[cut], (seed, cut, "perturbed")
    assert total > 100  # not vacuous: UTC about 900, NY about 300


VARIANTS = [
    PRIMARY,
    StrategyParams(swing_k=1, confirm_n=5, zone="body", entry_level="mid", pierce=0.0005,
                   stop_buffer=StopBuffer("pct", 0.0025), trend_filter=True),
    StrategyParams(swing_k=3, confirm_n=2, stop_buffer=StopBuffer("atr", 0.0), r_target=1.0),
    StrategyParams(structure_break="literal", skip_mitigated="stop"),
]


@pytest.mark.parametrize("params", VARIANTS, ids=["primary", "features", "k3n2", "literal_stop"])
@pytest.mark.parametrize("spec", [UTC, NY, LONDON], ids=["utc", "ny", "london"])
def test_random_walk_intents_are_well_formed(params, spec):
    for seed in range(3):
        cd = random_walk(3000, seed=50 + seed, start_ms=T0_MS)
        cal = SessionCalendar(spec, cd.ts)
        sma = np.convolve(cd.c, np.ones(96) / 96)[: len(cd)]  # any causal series: exercises both trend branches
        run = run_strategy(cd, params, spec, daily_sma=sma)
        assert_counts_consistent(run.counts)
        sessions = [x.tag["session_id"] for _, x in run.intents]
        assert len(sessions) == len(set(sessions)) == run.counts["intents"]
        for i, x in run.intents:
            assert cal.in_window[i] and x.expires_ms == cal.end_ms[i] >= cd.ts[i] + STEP_15M_MS
            assert x.tag["impulse_idx"] == i and x.tag["session_id"] == cal.session_id[i]
            assert i - params.confirm_n <= x.tag["candidate_idx"] < x.tag["displacement_idx"] <= i
            assert cd.ts[x.tag["candidate_idx"]] >= cal.open_ms[i]
            assert x.stop < x.price < cd.c[i] and x.price < x.target
            assert list(x.tag) == list(TAG_KEYS)
            assert all(type(v) in (int, float, str) or v is None for v in x.tag.values())
            json.dumps(x.tag, allow_nan=False)
```

In `phase-3-strategy.md` §3.12, item **3.3 Strategy-level look-ahead test.**, replace

```markdown
  with decision index `≤ cut` is identical between the full series, the
```

with

```markdown
  with decision index `≤ cut` (and the skip counts after `cut`) is identical between the full series, the
```

- [ ] **Step 2: Run the tests**

Run: `python -m pytest -q tests/test_order_block.py --durations=5`
Expected: 58 passed; the two look-ahead cases take about 2 s each, the rest well under a second. Fast suite: 475 passed.

- [ ] **Step 3: Check that the look-ahead test can fail**

Through the public accessors a future read raises `LookaheadError` (the Phase 2 proof). The remaining route is private access, which only review and the Task 6 grep can stop; confirm the look-ahead test would catch it. Insert a peek at the series' last close, run, restore:

```bash
python - <<'EOF'
import pathlib
p = pathlib.Path("perpbt/strategy/order_block.py")
s = p.read_text(encoding="utf-8")
old = "        p = self.params\n        c = self._candidate(view, t)\n"
assert s.count(old) == 1
p.write_text(s.replace(old, "        p = self.params\n        if float(view._c[-1]) < close_t:  # MUTATION: peeks at the last candle of the series\n            return self._skip(\"trend\")\n        c = self._candidate(view, t)\n"), encoding="utf-8", newline="")
EOF
python -m pytest -q tests/test_order_block.py -k "ignore_later" 2>&1 | tail -1
git checkout perpbt/strategy/order_block.py
grep -c MUTATION perpbt/strategy/order_block.py
```

Expected: `2 failed` (both `test_intents_up_to_cut_ignore_later_candles` cases); after `git checkout` (the file is committed since Task 4) the grep prints `0`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_order_block.py docs/superpowers/specs/2026-09-26-ob-backtest/phase-3-strategy.md
git commit -m "Phase 3: strategy-level look-ahead (20 seeds x 3 cuts, UTC and NY), start independence, count identities and tag types on random walks"
```

---

### Task 6: Real-data check, exit criterion, whole-branch review

**Files:**
- Modify: `tests/test_real_data.py` (imports, append one test)
- Modify: `docs/superpowers/plans/2026-09-27-phase-3-strategy.md` (tick boxes, post-review amendments)

**Interfaces:**
- Consumes: `real_cfg` fixture (`tests/conftest.py`), `CandleStore`, `date_ms`, `SessionCalendar`, the Task 4 harness.

- [ ] **Step 1: Write the slow test**

In `tests/test_real_data.py` replace

```python
from perpbt.data.store import CandleStore, FundingStore, date_ms
from perpbt.data.validate import consistency_1m_15m
```

with

```python
from perpbt.config import StrategyParams
from perpbt.data.sessions import SessionCalendar
from perpbt.data.store import CandleStore, FundingStore, date_ms
from perpbt.data.validate import consistency_1m_15m
from tests.strategy_harness import LONDON, NY, UTC, assert_counts_consistent, run_strategy
```

and append:

```python


@pytest.mark.parametrize("pair", PAIRS)
def test_order_block_rule_on_real_candles(real_cfg, pair):
    cd = CandleStore(real_cfg).load(pair, "15m", date_ms(real_cfg.warmup_start), date_ms("2026-01-01"))
    runs = {}
    for spec in (UTC, NY, LONDON):
        run = runs[spec.name] = run_strategy(cd, StrategyParams(), spec)
        assert_counts_consistent(run.counts)
        assert run.counts["intents"] > 300, (spec.name, run.counts)
        cal = SessionCalendar(spec, cd.ts)
        for i, x in run.intents:
            assert x.stop < x.price < cd.c[i] and x.expires_ms == cal.end_ms[i], (spec.name, i)
    i0 = cd.index_at(date_ms("2024-01-01"))  # a later first call rebuilds the same live levels
    late = run_strategy(cd, StrategyParams(), UTC, start_i=i0)
    assert late.intents == [(i, x) for i, x in runs["utc"].intents if i >= i0]
```

- [ ] **Step 2: Run the whole suite, slow tests included**

Run: `python -m pytest -q` (in the background if the machine is busy; about 25 s)
Expected: all pass, including the three `test_order_block_rule_on_real_candles` cases (about 3.5 s each).

- [ ] **Step 3: Exit-criterion checks**

1. Rule text against code, line by line (spec §3.12 exit criterion). Walk `phase-3-strategy.md` §3.3–§3.11 next to `perpbt/strategy/order_block.py`: §3.3 steps 1–4 ↔ `LiveLevels.step` (add, reference, `on_candle`'s impulse test, retire); §3.4 ↔ `_candidate`; §3.5 ↔ the `ineligible` line and `_used_session`; §3.6 ↔ `block_prices` and the `degenerate` line; §3.7 ↔ `_displacement`, the `mitigated` and `entry_above_price` lines; §3.8 ↔ the `trend` line; §3.9 ↔ the `PlaceBracketLimit(...)` call; §3.11 ↔ `COUNT_KEYS` and the order of the `_skip` calls. Note any mismatch in the post-review amendments and fix it in the same commit as the spec wording.
2. Private access (memory pointer from Phase 2): run

```bash
grep -nE "view\._|\.base\b|advance_to|_unchecked" perpbt/strategy/order_block.py | grep -v ":from "
grep -n "^from\|^import" perpbt/strategy/order_block.py
```

Expected: the first pipeline prints nothing (the `grep -v` drops the `perpbt.strategy.base` import line); the second shows only `__future__`, `math`, `collections.abc`, `dataclasses`, `typing`, `perpbt.config` and `perpbt.strategy.base`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_real_data.py docs/superpowers/plans/2026-09-27-phase-3-strategy.md
git commit -m "Phase 3: slow real-data check of the rule (three pairs, three sessions, late first call)"
git push
```

- [ ] **Step 5: Whole-branch review**

Dispatch a fresh reviewer on the most capable model over `git diff dev...phase/3-strategy`, with this plan, the spec and the Review Focus list. Apply fixes on the branch, each with a test, spec wording in the same commit; record them under "Post-review amendments" below.

- [ ] **Step 6: Record state; the merge waits for the user**

Update the project memory (`phase-2-indicators-state` pointers now done; a new `phase-3-strategy-state` note with the branch head, test counts, and anything deferred to Phase 4). The `--no-ff` merge into `dev` and the branch deletion happen only when the user says so:

```bash
git switch dev && git pull --ff-only
git merge --no-ff phase/3-strategy -m "Merge phase/3-strategy into dev: strategy interface, order-block rule, skip accounting"
python -m pytest -q
git push origin dev
git branch -d phase/3-strategy && git push origin --delete phase/3-strategy
```

---

## Self-review notes

- Spec coverage: §3.1 → Task 2 (types, protocol) and Task 4 (tag, call contract); §3.2 → Phase 2, used in Task 4; §3.3 → Task 3 (`LiveLevels`) and Task 4 (`on_candle`); §3.4–§3.9 → Task 4 (`_evaluate`); §3.10 → carried only (`hold_rule` passes through; Phase 4 enforces); §3.11 → Task 4 (`COUNT_KEYS`) and Task 5 (identities); §3.12 3.1 → Task 3, 3.2 S1–S13 → Task 4, 3.3 → Task 5; exit criterion → Task 6.
- The spec's 3.1 test list names six live-level cases; all six are in Task 3, the "confirmed on `t` cannot be broken on `t`" one both as a property of `swing_highs` on random walks and as "it is the reference on `t`".
- Spec deviations, each amended in the commit of the deviating code: `SimEvent` location (Task 2); check order, `intents` count, `session_used` meaning, S13's reachable case, `skip_mitigated = "stop"`, call contract and `warmup_bars`, `atr` tag `None`, the ATR-zero buffer (Task 4); counts in the look-ahead comparison (Task 5).
- Deferred to Phase 4 (not touched here): `DailyBars` validation (Phase 2 minor, `daily.py` not modified in this phase).

## Post-review amendments

(filled in during execution)
