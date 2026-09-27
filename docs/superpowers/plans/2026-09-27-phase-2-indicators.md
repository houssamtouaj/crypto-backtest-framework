# Phase 2 — Indicators Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the three causal indicators the strategy uses (Wilder ATR, swing highs with their confirmation index, completed-day SMA/ADX aligned to the 15m index) and the `MarketView` guard that makes look-ahead impossible in strategy code, each with perturbation (and truncation) proofs of causality.

**Architecture:** `indicators/atr.py` holds Wilder's recursion (`wilder_rma`) and `atr`; `indicators/daily.py` aggregates 15m candles into UTC daily bars, computes SMA and ADX on them (reusing `wilder_rma`) and aligns each day's value to the candles of the *following* present day; `indicators/swings.py` returns swing highs as three aligned arrays sorted by `confirmed_at`. `strategy/base.py` holds `MarketView`, a single object per simulation that keeps read-only views of the full arrays and a cursor `i`; every accessor checks its index against `i`. All indicator functions are vectorised numpy except the two Wilder recursions, which are a plain loop over the series.

**Tech Stack:** Python 3.11, numpy 2.2, pandas 2.2 (tests only, as the independent reference for daily bars and SMA), pytest 9.

**Spec:** `docs/superpowers/specs/2026-09-26-ob-backtest/phase-2-indicators.md`, read with `00-overview.md` (§4.1 MarketView, §6 conventions, §8.1 branches, D7, D14) and `phase-3-strategy.md` §3.1–§3.8 (the consumer of every interface here). Code this builds on: `perpbt/data/store.py` (`Candles`, `CandleStore`, `DAY_MS`, `date_ms`), `perpbt/data/sessions.py` (`SessionCalendar`), `tests/synthetic.py` (`candles_from_rows`, `random_walk`, `perturb_after`, `assert_causal`, `T0_MS`, `STEP_15M_MS`).

## Global Constraints

- Branch `phase/2-indicators` from `dev` after Phase 1 is merged; push on the first commit; small commits each with tests; merge into `dev` with `--no-ff`; delete the branch locally and on `origin` (overview §8.1). Spec amendments go in the same commit as the deviating code.
- Every indicator returns arrays aligned to the 15m index: `float64` with NaN before warmup (integer arrays use −1). Every indicator's docstring states its confirmation lag as the literal text `Lag N` (`Lag 0` for ATR and the daily indicators, `Lag k` for swings). A value with lag 0 is usable at the close of `i`.
- Nothing at index `i` may depend on candles after `i` (overview §6). Every indicator has an `assert_causal` test, with the truncation check on (Task 1).
- ATR: `TR_0 = h_0 − l_0`; `TR_i = max(h_i − l_i, |h_i − c_{i−1}|, |l_i − c_{i−1}|)`; `ATR_{n−1} = mean(TR_0..TR_{n−1})`; `ATR_i = (ATR_{i−1} × (n−1) + TR_i) / n`; NaN for `i < n−1`; "previous" is the previous row, whatever its timestamp.
- Swings: `s` is a swing high iff `high[s] > high[s ± j]` for all `j = 1..k`, strict; confirmed at `s + k`; `k ≤ s ≤ len − 1 − k`.
- Daily: a UTC day's bar is built from its 15m candles present in the data (open of the first, max high, min low, close of the last, sum of volume). The aligned value on every 15m candle of day `D` is the indicator on the daily bars of days `< D` only, constant through the day, changing at 00:00 UTC. A day with no candles at all has no bar and is skipped (the SMA is over the previous `n` *present* days).
- ADX: `+DM_d = up` if `up > down and up > 0` else 0, `−DM_d = down` if `down > up and down > 0` else 0, with `up = h_d − h_{d−1}`, `down = l_{d−1} − l_d`; `TR_d` as in ATR on daily bars; `wilder_rma(n)` of `TR`, `+DM`, `−DM` over days `1..`; `DI± = 100 × RMA(±DM) / RMA(TR)` (0 when `RMA(TR) = 0`); `DX = 100 × |DI+ − DI−| / (DI+ + DI−)` (0 when the sum is 0); `ADX = wilder_rma(n)(DX)` starting at the first valid DX. First valid daily bar: SMA at index `n − 1`, ADX at index `2n − 1`.
- `MarketView`: accessors succeed iff `0 <= j <= i` (ranges: `0 <= a <= b <= i`); anything else raises `LookaheadError`; a non-integer index raises `TypeError`. The view never exposes the series length. It keeps references, never copies the full arrays; returned arrays are read-only views.
- Tests: pytest, one test file per module (`tests/test_atr.py`, `tests/test_swings.py`, `tests/test_daily.py`, `tests/test_strategy_base.py`), synthetic data by default; real-data tests are `@pytest.mark.slow` and skip when `data/` is absent. Tolerances: `1e-12` absolute for the ATR hand example, `rtol=1e-12` for implementation-vs-reference comparisons.
- Working directory for every command is the repo root `D:\Users\khali\projects\trading_backtest`; commands are shown for Git Bash.

## Review Focus

1. **A negative index silently wraps in numpy** (`arr[-1]` is the *last* candle, the furthest future). `MarketView` must raise `LookaheadError` for `j < 0`, and for a range with `a < 0`. Pinned in Task 5 by `test_negative_index_raises` and the fuzz test.
2. **A daily value used on the day it forms** (an off-by-one at 00:00 UTC) is the classic look-ahead for completed-day indicators and survives a perturbation test at cuts that fall on day boundaries only by luck. The 00:00 candle of day `D` must carry the value computed through `D − 1`, and the 23:45 candle of `D − 1` the value through `D − 2`. Pinned in Task 4 by `test_value_changes_at_midnight_and_uses_only_previous_days` and `test_changing_day_d_never_changes_day_d_values`.
3. **Truncation, not only perturbation.** `assert_causal` keeps `ts` and the length fixed, so an indicator that reads `len(candles)` or `ts[-1]` (e.g. an end-of-series rule for the last day, or "within `k` of the end") passes it while being non-causal. Task 1 adds `truncate=True`, and every indicator test uses it. Pinned in Task 1 by `test_assert_causal_truncate_catches_length_dependence`.
4. **Arrays handed to the strategy must not be writable or wider than asked**: a writable view would let strategy code corrupt the shared data for every later candle. Range accessors and `swings_confirmed_by` return read-only views of exactly the requested rows. Pinned in Task 5 by `test_returned_arrays_are_read_only_and_exact`.
5. **Short and degenerate series**: fewer candles than `n` (ATR), fewer than `2k + 1` (swings), fewer than `n` or `2n` days (daily), a flat series, and a day whose 00:00 candle is missing must give all-NaN or empty results or a correct value, never an exception or an index error. Pinned in Tasks 2–4 by `test_series_shorter_than_n_is_all_nan`, `test_short_and_flat_series_have_no_swings`, `test_too_few_days_is_all_nan` and `test_missing_midnight_candle_still_completes_the_day`.

Also pinned though not in the spec's test list: a float index raises `TypeError` instead of truncating (Task 5); `advance_to` refuses to move backwards or past the end (Task 5); ATR ignores timestamp gaps (Task 2).

---

### Task 1: Branch, `assert_causal` truncation check, synthetic-helper minors

**Files:**
- Modify: `tests/synthetic.py`
- Modify: `tests/test_synthetic.py`

**Interfaces:**
- Consumes: `Candles.slice(start_ms, end_ms)` from `perpbt/data/store.py`.
- Produces: `assert_causal(fn, candles, *, cuts, seeds, truncate=False)`. With `truncate=True`, for every cut it also compares the visible rows of `fn(candles)` with those of `fn(candles.slice(ts[0], ts[cut] + 1))`. Tasks 2–4 call it with `truncate=True`.

This task also folds in two Phase 0 deferred minors that live in the same file: the pair-form failure message now says whether the values or `confirmed_at` differed, and `candles_from_rows` rejects NaN rows.

- [x] **Step 1: Create the phase branch from an up-to-date `dev`** (done by the controller together with committing this plan)

```bash
git switch dev
git pull
git switch -c phase/2-indicators dev
git status --short   # expected: empty (data/ is ignored except manifests)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_synthetic.py`:

```python
def test_assert_causal_truncate_catches_length_dependence():
    def depends_on_length(cd: Candles) -> np.ndarray:
        # identical under perturbation (length fixed), different under truncation
        return np.full(len(cd), float(len(cd)))

    cd = random_walk(200, seed=9, start_ms=T0_MS)
    assert_causal(depends_on_length, cd, cuts=[50], seeds=[1])  # perturbation alone is fooled
    with pytest.raises(AssertionError, match="truncation"):
        assert_causal(depends_on_length, cd, cuts=[50], seeds=[1], truncate=True)


def test_assert_causal_truncate_passes_causal_functions():
    cd = random_walk(300, seed=10, start_ms=T0_MS)
    assert_causal(cumsum, cd, cuts=[0, 1, 150, 299], seeds=[1, 2], truncate=True)

    k = 2

    def forward_max_confirmed(c: Candles):
        idx = np.arange(max(len(c) - k, 0))
        values = np.array([c.h[i : i + k + 1].max() for i in idx])
        return values, idx + k

    assert_causal(forward_max_confirmed, cd, cuts=[0, 2, 100, 299], seeds=[1], truncate=True)


def test_assert_causal_rejects_out_of_range_cut_with_truncate():
    cd = random_walk(20, seed=11, start_ms=T0_MS)
    with pytest.raises(IndexError):
        assert_causal(cumsum, cd, cuts=[20], seeds=[1], truncate=True)


def test_pair_form_message_names_the_differing_part():
    def shifted_confirmation(cd: Candles):
        n = len(cd)
        confirmed_at = np.arange(n)
        confirmed_at[0] = 0 if cd.c[-1] > cd.c[0] else 1
        return np.ones(n), confirmed_at

    cd = random_walk(300, seed=8, start_ms=T0_MS)
    with pytest.raises(AssertionError, match="confirmed_at"):
        assert_causal(shifted_confirmation, cd, cuts=[0], seeds=[1, 2, 3, 4, 5, 6, 7, 8])


def test_candles_from_rows_rejects_nan():
    with pytest.raises(ValueError, match="finite"):
        candles_from_rows([(1.0, 2.0, 0.5, 1.5), (1.5, float("nan"), 1.0, 1.2)], start_ms=T0_MS)
```

Check the imports at the top of `tests/test_synthetic.py` include `candles_from_rows`, `random_walk`, `assert_causal`, `T0_MS`, `Candles`, `np` and `pytest` (they do in the Phase 0 file; add any that are missing).

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_synthetic.py -q`
Expected: the five new tests FAIL (`TypeError: assert_causal() got an unexpected keyword argument 'truncate'`, no "confirmed_at" in the message, NaN accepted); all older tests pass.

- [ ] **Step 4: Implement**

In `tests/synthetic.py`, in `candles_from_rows`, right after `arr = np.asarray(rows, dtype=np.float64)`:

```python
    if not np.all(np.isfinite(arr)):
        bad_row = int(np.flatnonzero(~np.isfinite(arr).all(axis=1))[0])
        raise ValueError(f"row {bad_row}: every value must be finite")
```

Replace `_visible`, the comparison loop and `assert_causal` with:

```python
_PART_NAMES = ("values", "confirmed_at")


def _visible(values: np.ndarray, confirmed_at: np.ndarray | None, cut: int) -> list[np.ndarray]:
    if confirmed_at is None:
        return [values[: cut + 1]]
    mask = confirmed_at <= cut
    return [values[mask], confirmed_at[mask]]


def _compare(name: str, expected: list[np.ndarray], got: list[np.ndarray], how: str) -> None:
    pair_form = len(expected) == 2
    # confirmed_at first: a changed row set shows up as a shape difference in both parts,
    # and "which rows are confirmed" is the more useful description of it
    for part in ((1, 0) if pair_form else (0,)):
        a, b = expected[part], got[part]
        if not _equal(a, b):
            which = f" ({_PART_NAMES[part]})" if pair_form else ""
            raise AssertionError(f"{name} is not causal: output{which} {how}{_first_diff(a, b)}")


def assert_causal(
    fn: Callable[[Candles], Any],
    candles: Candles,
    *,
    cuts: Iterable[int],
    seeds: Iterable[int],
    truncate: bool = False,
) -> None:
    """Assert ``fn``'s output at or before each cut does not depend on later candles.

    ``fn`` returns either an array aligned to the candle index (rows ``[:cut+1]``
    are compared) or a ``(values, confirmed_at)`` pair (only rows with
    ``confirmed_at <= cut`` are compared, values and ``confirmed_at`` both).
    NaNs compare equal to NaNs.

    The perturbation holds ``ts`` and the series length fixed, so on its own
    it proves independence from future o/h/l/c/v only. With ``truncate=True``
    each cut is also checked against ``fn`` applied to the series cut after
    ``cut`` (``candles.slice(ts[0], ts[cut] + 1)``), which catches a
    dependence on the series length or on future timestamps. Both ``cuts``
    and ``seeds`` are materialised up front; an empty one raises ValueError
    so the check can never pass vacuously; a cut outside ``[0, len)`` raises
    IndexError.
    """
    cuts = list(cuts)
    seeds = list(seeds)
    if not cuts or not seeds:
        raise ValueError("assert_causal: cuts and seeds must both be non-empty")
    n = len(candles)
    name = getattr(fn, "__name__", repr(fn))
    base_values, base_conf = _split(fn(candles))
    for cut in cuts:
        if not 0 <= cut < n:
            raise IndexError(f"cut must be in [0, {n}), got {cut}")
        expected = _visible(base_values, base_conf, cut)
        if truncate:
            head = candles.slice(int(candles.ts[0]), int(candles.ts[cut]) + 1)
            got = _visible(*_split(fn(head)), cut)
            _compare(name, expected, got, f"at or before cut={cut} changed under truncation after the cut")
        for seed in seeds:
            got = _visible(*_split(fn(perturb_after(candles, cut, seed=seed))), cut)
            _compare(
                name, expected, got,
                f"at or before cut={cut} changed under perturbation with seed={seed}",
            )
```

The old messages said `output at or before cut=... changed under perturbation with seed=...`; the new ones keep that text (with the part name inserted after `output` in pair form), so `match="not causal"` in the older tests still matches.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_synthetic.py -q`
Expected: all pass.

Run: `python -m pytest -q -m "not slow"`
Expected: all pass (314 + 5).

- [ ] **Step 6: Commit and push**

```bash
git add tests/synthetic.py tests/test_synthetic.py
git commit -m "Phase 2: assert_causal truncation check; pair-form message names the differing part; candles_from_rows rejects NaN"
git push
```

---

### Task 2: Wilder ATR — `indicators/atr.py`

**Files:**
- Create: `perpbt/indicators/atr.py`
- Create: `tests/test_atr.py`

**Interfaces:**
- Consumes: `Candles` (`h`, `l`, `c` float64 arrays).
- Produces:
  - `wilder_rma(x: np.ndarray, n: int, start: int = 0) -> np.ndarray` — float64, same length as `x`; NaN before `start + n − 1`; `out[start+n−1] = mean(x[start:start+n])`; then `out[t] = (out[t−1] × (n−1) + x[t]) / n`. All NaN when `len(x) − start < n`. Task 4 uses it for ADX.
  - `true_range(h, l, c) -> np.ndarray` — `TR_0 = h_0 − l_0`, later rows the three-way max. Task 4 uses it on daily bars.
  - `check_period(n) -> int` — `operator.index(n)`, `>= 1`; `TypeError` / `ValueError` otherwise. Task 4 uses it.
  - `atr(candles: Candles, n: int = 14) -> np.ndarray` — lag 0.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_atr.py`:

```python
"""Wilder ATR (spec §2.1)."""
import numpy as np
import pytest

from perpbt.indicators.atr import atr, true_range, wilder_rma
from tests.synthetic import T0_MS, STEP_15M_MS, assert_causal, candles_from_rows, random_walk

# (o, h, l, c). Row 3's TR is set by |h - c_prev|, row 4's by |l - c_prev|.
HAND_ROWS = [
    (10.0, 11.0, 9.0, 10.5),    # TR 2.0 (h - l, first row)
    (10.5, 12.0, 10.0, 11.5),   # TR max(2.0, 1.5, 0.5) = 2.0
    (11.5, 11.8, 11.0, 11.2),   # TR max(0.8, 0.3, 0.5) = 0.8
    (12.5, 13.0, 12.4, 12.9),   # TR max(0.6, 1.8, 1.2) = 1.8
    (10.5, 10.6, 10.0, 10.2),   # TR max(0.6, 2.3, 2.9) = 2.9
    (10.2, 10.5, 9.5, 10.0),    # TR max(1.0, 0.3, 0.7) = 1.0
]


def test_true_range_hand_values():
    cd = candles_from_rows(HAND_ROWS, start_ms=T0_MS)
    np.testing.assert_allclose(true_range(cd.h, cd.l, cd.c), [2.0, 2.0, 0.8, 1.8, 2.9, 1.0], rtol=0, atol=1e-12)


def test_atr_hand_computed_n3():
    cd = candles_from_rows(HAND_ROWS, start_ms=T0_MS)
    out = atr(cd, n=3)
    # ATR_2 = mean(2, 2, 0.8) = 8/5; ATR_3 = (2*8/5 + 1.8)/3 = 5/3;
    # ATR_4 = (2*5/3 + 2.9)/3 = 187/90; ATR_5 = (2*187/90 + 1)/3 = 232/135
    expected = [np.nan, np.nan, 8 / 5, 5 / 3, 187 / 90, 232 / 135]
    np.testing.assert_allclose(out, expected, rtol=0, atol=1e-12)


def _reference_atr(rows, n):
    """Plain-Python Wilder ATR, written from the spec formula without numpy."""
    trs = []
    for i, (_, h, l, _c) in enumerate(rows):
        if i == 0:
            trs.append(h - l)
        else:
            pc = rows[i - 1][3]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    out = [float("nan")] * len(rows)
    if len(rows) >= n:
        prev = sum(trs[:n]) / n
        out[n - 1] = prev
        for i in range(n, len(rows)):
            prev = (prev * (n - 1) + trs[i]) / n
            out[i] = prev
    return out


def test_atr_matches_a_20_candle_example():
    rng = np.random.default_rng(7)
    rows, price = [], 100.0
    for _ in range(20):
        o = price
        c = o * (1 + rng.normal(0, 0.01))
        h = max(o, c) * (1 + abs(rng.normal(0, 0.005)))
        l = min(o, c) * (1 - abs(rng.normal(0, 0.005)))
        rows.append((o, h, l, c))
        price = c * (1 + rng.normal(0, 0.003))  # gap between candles so |h - c_prev| matters
    cd = candles_from_rows(rows, start_ms=T0_MS)
    for n in (1, 5, 14, 20):
        np.testing.assert_allclose(atr(cd, n=n), _reference_atr(rows, n), rtol=0, atol=1e-12)


@pytest.mark.parametrize("n", [1, 5, 14])
def test_first_n_minus_1_are_nan_and_rest_finite(n):
    cd = random_walk(200, seed=3, start_ms=T0_MS)
    out = atr(cd, n=n)
    assert np.isnan(out[: n - 1]).all()
    assert np.isfinite(out[n - 1 :]).all()


def test_series_shorter_than_n_is_all_nan():
    cd = random_walk(10, seed=3, start_ms=T0_MS)
    out = atr(cd, n=14)
    assert out.shape == (10,) and np.isnan(out).all()
    assert atr(random_walk(0, seed=3, start_ms=T0_MS), n=14).shape == (0,)


def test_timestamp_gaps_are_ignored():
    cd = random_walk(100, seed=4, start_ms=T0_MS)
    ts = cd.ts.copy()
    ts[50:] += 7 * STEP_15M_MS  # an outage between rows 49 and 50
    gapped = type(cd)(cd.pair, cd.tf, ts, cd.o, cd.h, cd.l, cd.c, cd.v)
    np.testing.assert_array_equal(atr(gapped, n=14), atr(cd, n=14))


@pytest.mark.parametrize("bad", [0, -1])
def test_n_must_be_positive(bad):
    with pytest.raises(ValueError):
        atr(random_walk(20, seed=1, start_ms=T0_MS), n=bad)


def test_n_must_be_an_integer():
    with pytest.raises(TypeError):
        atr(random_walk(20, seed=1, start_ms=T0_MS), n=14.0)


def test_wilder_rma_start_offset():
    x = np.array([99.0, 1.0, 2.0, 3.0, 4.0])
    out = wilder_rma(x, 2, start=1)
    np.testing.assert_allclose(out, [np.nan, np.nan, 1.5, (1.5 + 3.0) / 2, (2.25 + 4.0) / 2], atol=1e-12)
    assert np.isnan(wilder_rma(x, 5, start=1)).all()


def test_atr_is_causal():
    for seed in range(20):
        cd = random_walk(600, seed=100 + seed, start_ms=T0_MS)
        assert_causal(lambda c: atr(c, 14), cd, cuts=[5, 13, 400], seeds=[seed], truncate=True)


def test_docstring_states_lag():
    assert "Lag 0" in atr.__doc__
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_atr.py -q`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'perpbt.indicators.atr'`.

- [ ] **Step 3: Implement**

Create `perpbt/indicators/atr.py`:

```python
"""Wilder ATR on 15m candles (spec §2.1) and the Wilder recursion it shares with ADX."""
from __future__ import annotations

import operator

import numpy as np

from perpbt.data.store import Candles


def check_period(n: object) -> int:
    """``n`` as an int >= 1; TypeError for a non-integer, ValueError below 1."""
    try:
        n = operator.index(n)
    except TypeError:
        raise TypeError(f"period must be an integer, got {type(n).__name__}") from None
    if n < 1:
        raise ValueError(f"period must be >= 1, got {n}")
    return n


def wilder_rma(x: np.ndarray, n: int, start: int = 0) -> np.ndarray:
    """Wilder's running mean of ``x[start:]``: seeded by the mean of the first ``n``.

    ``out[start + n - 1] = mean(x[start : start + n])`` and afterwards
    ``out[t] = (out[t-1] * (n - 1) + x[t]) / n``. Earlier rows are NaN, and
    so is everything when fewer than ``n`` values follow ``start``. Lag 0.
    """
    n = check_period(n)
    x = np.asarray(x, dtype=np.float64)
    out = np.full(len(x), np.nan)
    first = start + n - 1
    if first >= len(x):
        return out
    acc = float(x[start : start + n].mean())
    out[first] = acc
    for t in range(first + 1, len(x)):
        acc = (acc * (n - 1) + float(x[t])) / n
        out[t] = acc
    return out


def true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:  # noqa: E741
    """``TR_0 = h_0 - l_0``; ``TR_i = max(h_i - l_i, |h_i - c_{i-1}|, |l_i - c_{i-1}|)``."""
    h = np.asarray(h, dtype=np.float64)
    l = np.asarray(l, dtype=np.float64)  # noqa: E741
    c = np.asarray(c, dtype=np.float64)
    tr = h - l
    if len(tr) > 1:
        pc = c[:-1]
        tr[1:] = np.maximum(tr[1:], np.maximum(np.abs(h[1:] - pc), np.abs(l[1:] - pc)))
    return tr


def atr(candles: Candles, n: int = 14) -> np.ndarray:
    """Wilder ATR(n) aligned to the candle index. Lag 0: the value at ``i`` is known at the close of ``i``.

    ``ATR_{n-1}`` is the mean of the first ``n`` true ranges, then
    ``ATR_i = (ATR_{i-1} * (n - 1) + TR_i) / n``. NaN for ``i < n - 1``.
    "Previous" is the previous row: timestamp gaps are ignored.
    """
    n = check_period(n)
    return wilder_rma(true_range(candles.h, candles.l, candles.c), n)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_atr.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add perpbt/indicators/atr.py tests/test_atr.py
git commit -m "Phase 2: Wilder ATR with the shared wilder_rma recursion (lag 0)"
```

---

### Task 3: Swing highs — `indicators/swings.py`

**Files:**
- Create: `perpbt/indicators/swings.py`
- Create: `tests/test_swings.py`

**Interfaces:**
- Consumes: `Candles.h`.
- Produces:
  - `Swings` — `@dataclass(frozen=True, eq=False)` with `idx: np.ndarray` (int64), `level: np.ndarray` (float64), `confirmed_at: np.ndarray` (int64), equal lengths, `confirmed_at` strictly increasing; `__len__`. `__post_init__` validates shapes and order. `Swings._unchecked(idx, level, confirmed_at)` wraps prefixes of a validated instance without re-checking; Task 5's `MarketView.swings_confirmed_by` uses it on every candle.
  - `swing_highs(candles: Candles, k: int) -> Swings` — lag `k`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_swings.py`:

```python
"""Swing highs with their confirmation index (spec §2.2)."""
import numpy as np
import pytest

from perpbt.indicators.swings import Swings, swing_highs
from tests.synthetic import T0_MS, assert_causal, candles_from_rows, random_walk


def from_highs(highs):
    """Candles whose highs are ``highs`` (open = close = high - 0.5, low = high - 1)."""
    return candles_from_rows([(h - 0.5, h, h - 1.0, h - 0.5) for h in highs], start_ms=T0_MS)


HIGHS = [1, 3, 2, 5, 4, 4, 6, 2, 3, 1]


@pytest.mark.parametrize(
    "k, idx",
    [
        (1, [1, 3, 6, 8]),  # the 4s at s=4 and s=5 tie, so neither is a swing
        (2, [3, 6]),
        (3, [6]),           # 5 at s=3 fails: high[6] = 6 lies within 3 to its right
    ],
)
def test_known_swings(k, idx):
    sw = swing_highs(from_highs(HIGHS), k)
    assert sw.idx.tolist() == idx
    assert sw.level.tolist() == [float(HIGHS[s]) for s in idx]
    assert sw.confirmed_at.tolist() == [s + k for s in idx]
    assert sw.idx.dtype == np.int64 and sw.confirmed_at.dtype == np.int64 and sw.level.dtype == np.float64


@pytest.mark.parametrize(
    "highs, k",
    [
        ([1, 5, 5, 1], 1),               # plateau of two
        ([1, 2, 5, 3, 5, 2, 1], 2),      # equal highs two apart
        ([1, 2, 3, 7, 4, 5, 7, 1, 1, 1], 3),  # two 7s three apart: each sees the other
    ],
)
def test_equal_highs_are_excluded(highs, k):
    assert len(swing_highs(from_highs(highs), k)) == 0


@pytest.mark.parametrize("k", [1, 2, 3])
def test_confirmed_at_is_idx_plus_k_and_sorted(k):
    sw = swing_highs(random_walk(2000, seed=12, start_ms=T0_MS), k)
    assert len(sw) > 50
    np.testing.assert_array_equal(sw.confirmed_at, sw.idx + k)
    assert np.all(np.diff(sw.idx) > 0)


@pytest.mark.parametrize("k", [1, 2, 3])
def test_candles_within_k_of_either_end_are_never_swings(k):
    rising = from_highs(list(range(1, 12)))           # the last candle is the highest
    assert len(swing_highs(rising, k)) == 0
    falling = from_highs(list(range(12, 1, -1)))       # the first candle is the highest
    assert len(swing_highs(falling, k)) == 0
    cd = random_walk(1500, seed=13, start_ms=T0_MS)
    sw = swing_highs(cd, k)
    assert sw.idx.min() >= k and sw.idx.max() <= len(cd) - 1 - k


def test_every_swing_satisfies_the_definition_and_none_is_missed():
    cd = random_walk(800, seed=14, start_ms=T0_MS)
    h = cd.h
    for k in (1, 2, 3):
        expected = [
            s for s in range(k, len(h) - k)
            if all(h[s] > h[s - j] and h[s] > h[s + j] for j in range(1, k + 1))
        ]
        assert swing_highs(cd, k).idx.tolist() == expected


def test_short_and_flat_series_have_no_swings():
    for n in (0, 1, 2, 4):
        sw = swing_highs(random_walk(n, seed=1, start_ms=T0_MS), 2)
        assert len(sw) == 0 and sw.idx.dtype == np.int64 and sw.level.dtype == np.float64
    assert len(swing_highs(from_highs([5.0] * 20), 1)) == 0


@pytest.mark.parametrize("bad", [0, -2])
def test_k_must_be_positive(bad):
    with pytest.raises(ValueError):
        swing_highs(random_walk(20, seed=1, start_ms=T0_MS), bad)


def test_unchecked_constructor_keeps_the_arrays():
    idx = np.array([2, 5], dtype=np.int64)
    level = np.array([1.0, 2.0])
    conf = idx + 2
    sw = Swings._unchecked(idx, level, conf)
    assert sw.idx is idx and sw.level is level and sw.confirmed_at is conf and len(sw) == 2


def test_swings_validates_order_and_shapes():
    with pytest.raises(ValueError):
        Swings(np.array([3, 1]), np.array([1.0, 2.0]), np.array([5, 3]))
    with pytest.raises(ValueError):
        Swings(np.array([1]), np.array([1.0, 2.0]), np.array([3]))


@pytest.mark.parametrize("k", [1, 2, 3])
def test_swings_are_causal(k):
    def fn(cd):
        sw = swing_highs(cd, k)
        return sw.level, sw.confirmed_at

    for seed in range(20):
        cd = random_walk(500, seed=200 + seed, start_ms=T0_MS)
        assert_causal(fn, cd, cuts=[k, 150, 498], seeds=[seed], truncate=True)


def test_docstring_states_lag():
    assert "Lag k" in swing_highs.__doc__
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_swings.py -q`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'perpbt.indicators.swings'`.

- [ ] **Step 3: Implement**

Create `perpbt/indicators/swings.py`:

```python
"""Swing highs with their confirmation index (spec §2.2).

Consumers never index ``Swings`` directly; strategy code goes through
``MarketView.swings_confirmed_by(i)``, which hides swings confirmed after ``i``.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass

import numpy as np

from perpbt.data.store import Candles


@dataclass(frozen=True, eq=False)  # eq=False: the generated __eq__ raises on ndarray fields
class Swings:
    """Swing highs sorted by ``confirmed_at`` (equivalently by ``idx``).

    ``idx`` is the candle index ``s`` (int64), ``level`` is ``high[s]``
    (float64), ``confirmed_at`` is ``s + k`` (int64, strictly increasing).
    """

    idx: np.ndarray
    level: np.ndarray
    confirmed_at: np.ndarray

    def __post_init__(self) -> None:
        idx = np.asarray(self.idx).astype(np.int64, copy=False)
        level = np.asarray(self.level, dtype=np.float64)
        conf = np.asarray(self.confirmed_at).astype(np.int64, copy=False)
        if idx.ndim != 1 or level.shape != idx.shape or conf.shape != idx.shape:
            raise ValueError(
                f"Swings: idx, level, confirmed_at must be 1-D and aligned, got "
                f"{idx.shape}, {level.shape}, {conf.shape}"
            )
        if len(conf) > 1 and not np.all(np.diff(conf) > 0):
            raise ValueError("Swings.confirmed_at must be strictly increasing")
        object.__setattr__(self, "idx", idx)
        object.__setattr__(self, "level", level)
        object.__setattr__(self, "confirmed_at", conf)

    def __len__(self) -> int:
        return len(self.idx)

    @classmethod
    def _unchecked(cls, idx: np.ndarray, level: np.ndarray, confirmed_at: np.ndarray) -> Swings:
        """Wrap arrays already known to be valid (prefixes of a validated ``Swings``) without the O(m) checks.

        ``MarketView.swings_confirmed_by`` is called on every candle; validating
        each prefix again would cost O(m) per call.
        """
        obj = object.__new__(cls)
        object.__setattr__(obj, "idx", idx)
        object.__setattr__(obj, "level", level)
        object.__setattr__(obj, "confirmed_at", confirmed_at)
        return obj


def swing_highs(candles: Candles, k: int) -> Swings:
    """Swing highs of order ``k``. Lag k: the swing at ``s`` is known at the close of ``s + k``.

    Candle ``s`` is a swing high iff ``high[s] > high[s - j]`` and
    ``high[s] > high[s + j]`` for every ``j = 1..k`` (strict: equal highs do
    not qualify). Candles within ``k`` of either end of the series cannot be
    swing highs.
    """
    try:
        k = operator.index(k)
    except TypeError:
        raise TypeError(f"k must be an integer, got {type(k).__name__}") from None
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    h = np.asarray(candles.h, dtype=np.float64)
    n = len(h)
    if n < 2 * k + 1:
        empty = np.zeros(0, dtype=np.int64)
        return Swings(empty, np.zeros(0), empty.copy())
    s = np.arange(k, n - k, dtype=np.int64)
    ok = np.ones(len(s), dtype=bool)
    for j in range(1, k + 1):
        ok &= h[s] > h[s - j]
        ok &= h[s] > h[s + j]
    idx = s[ok]
    return Swings(idx, h[idx], idx + k)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_swings.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add perpbt/indicators/swings.py tests/test_swings.py
git commit -m "Phase 2: swing highs with confirmed_at = s + k (lag k)"
```

---

### Task 4: Daily bars, SMA and ADX aligned to 15m — `indicators/daily.py`

**Files:**
- Create: `perpbt/indicators/daily.py`
- Create: `tests/test_daily.py`
- Modify: `docs/superpowers/specs/2026-09-26-ob-backtest/phase-2-indicators.md` (§2.3 wording, see Step 5)

**Interfaces:**
- Consumes: `Candles`; `DAY_MS` from `perpbt/data/store.py`; `wilder_rma`, `true_range` from Task 2.
- Produces:
  - `DailyBars` — `@dataclass(frozen=True, eq=False)`: `day_ms` (int64 UTC midnights, strictly increasing), `o`, `h`, `l`, `c`, `v` (float64); `__len__`.
  - `daily_bars(candles15: Candles) -> DailyBars` — one bar per UTC day that has at least one candle.
  - `daily_sma(bars: DailyBars, n: int = 50) -> np.ndarray` and `daily_adx(bars: DailyBars, n: int = 14) -> np.ndarray` — per daily bar, NaN before index `n − 1` / `2n − 1`.
  - `daily_sma_aligned(candles15: Candles, n: int = 50) -> np.ndarray` and `daily_adx_aligned(candles15: Candles, n: int = 14) -> np.ndarray` — lag 0 at the 15m level; the value on candle `i` of day `D` is the daily indicator at the last bar with `day_ms < D`. Phase 3's trend filter (§3.8) and Task 5's `MarketView.daily_sma/daily_adx` read these.

**Spec reading applied here.** §2.3 says a day is completed "once the candle opening at `D+1 00:00` exists". Read literally, a missing 00:00 candle would leave day `D` uncompleted forever, contradicting "outage gaps do not disqualify a day". Since every candle of day `D` sees only bars of days `< D`, and every such day has a later candle (the one being evaluated), the rule reduces to: *the value on day `D` uses the bars of all present days before `D`*, and the last day of the series is never used. Step 5 amends the spec wording. §2.3 also says SMA(50) and ADX(14) are valid on 2020-01-01 for ETH; ETHUSDT listed 2019-11-27, so only 35 daily bars precede 2020-01-01 and SMA(50) becomes valid on 2020-01-16 (ADX(14), needing 28 bars, on 2019-12-25). Step 5 corrects that sentence; Task 6 pins it on real data.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daily.py`:

```python
"""Completed-day SMA / ADX aligned to the 15m index (spec §2.3)."""
import numpy as np
import pandas as pd
import pytest

from perpbt.data.store import DAY_MS, Candles
from perpbt.indicators.daily import (
    daily_adx,
    daily_adx_aligned,
    daily_bars,
    daily_sma,
    daily_sma_aligned,
)
from tests.synthetic import T0_MS, STEP_15M_MS, assert_causal, random_walk

PER_DAY = 96


def walk_days(days, seed, step_sigma=0.004):
    return random_walk(days * PER_DAY, seed=seed, start_ms=T0_MS, step_sigma=step_sigma)


def drop(cd: Candles, keep: np.ndarray) -> Candles:
    return Candles(cd.pair, cd.tf, cd.ts[keep], cd.o[keep], cd.h[keep], cd.l[keep], cd.c[keep], cd.v[keep])


def pandas_daily(cd: Candles) -> pd.DataFrame:
    """Independent daily bars: pandas groupby on the UTC calendar day."""
    df = pd.DataFrame({
        "t": pd.to_datetime(cd.ts, unit="ms", utc=True),
        "o": cd.o, "h": cd.h, "l": cd.l, "c": cd.c, "v": cd.v,
    })
    g = df.groupby(df["t"].dt.floor("D"))
    return pd.DataFrame({
        "o": g["o"].first(), "h": g["h"].max(), "l": g["l"].min(), "c": g["c"].last(), "v": g["v"].sum(),
    })


def reference_adx(daily: pd.DataFrame, n: int) -> pd.Series:
    """Textbook Wilder ADX with plain floats, one day at a time."""
    h, l, c = daily["h"].tolist(), daily["l"].tolist(), daily["c"].tolist()
    m = len(c)
    nan = float("nan")
    tr, pdm, mdm = [nan] * m, [nan] * m, [nan] * m
    for d in range(1, m):
        tr[d] = max(h[d] - l[d], abs(h[d] - c[d - 1]), abs(l[d] - c[d - 1]))
        up, down = h[d] - h[d - 1], l[d - 1] - l[d]
        pdm[d] = up if (up > down and up > 0) else 0.0
        mdm[d] = down if (down > up and down > 0) else 0.0

    def rma(x, start):
        out = [nan] * m
        if m - start < n:
            return out
        acc = sum(x[start : start + n]) / n
        out[start + n - 1] = acc
        for t in range(start + n, m):
            acc = (acc * (n - 1) + x[t]) / n
            out[t] = acc
        return out

    atr_, p, q = rma(tr, 1), rma(pdm, 1), rma(mdm, 1)
    dx = [nan] * m
    for d in range(n, m):
        dip = 100 * p[d] / atr_[d] if atr_[d] > 0 else 0.0
        dim = 100 * q[d] / atr_[d] if atr_[d] > 0 else 0.0
        dx[d] = 100 * abs(dip - dim) / (dip + dim) if dip + dim > 0 else 0.0
    return pd.Series(rma(dx, n), index=daily.index)


def aligned_reference(cd: Candles, per_day: pd.Series) -> np.ndarray:
    """Value of each candle = per-day series at the previous present day."""
    days = pd.to_datetime(cd.ts, unit="ms", utc=True).floor("D")
    shifted = per_day.shift(1)  # the previous *present* day, since per_day has one row per present day
    return shifted.reindex(days).to_numpy(dtype=np.float64)


def test_daily_bars_match_pandas():
    cd = walk_days(10, seed=21)
    bars = daily_bars(cd)
    ref = pandas_daily(cd)
    assert len(bars) == 10
    np.testing.assert_array_equal(bars.day_ms, T0_MS + DAY_MS * np.arange(10))
    for col in "ohlcv":
        np.testing.assert_allclose(getattr(bars, col), ref[col].to_numpy(), rtol=1e-12)


@pytest.mark.parametrize("n", [5, 50])
def test_sma_matches_pandas_on_previous_completed_days(n):
    cd = walk_days(80, seed=22)
    ref = aligned_reference(cd, pandas_daily(cd)["c"].rolling(n).mean())
    np.testing.assert_allclose(daily_sma_aligned(cd, n), ref, rtol=1e-12, equal_nan=True)
    assert np.isfinite(daily_sma_aligned(cd, n)[n * PER_DAY :]).all()
    assert np.isnan(daily_sma_aligned(cd, n)[: n * PER_DAY]).all()


@pytest.mark.parametrize("n", [3, 14])
def test_adx_matches_reference_on_previous_completed_days(n):
    cd = walk_days(70, seed=23)
    ref = aligned_reference(cd, reference_adx(pandas_daily(cd), n))
    out = daily_adx_aligned(cd, n)
    np.testing.assert_allclose(out, ref, rtol=1e-12, equal_nan=True)
    # first valid daily bar is index 2n - 1, so the aligned value starts on day 2n
    assert np.isnan(out[: 2 * n * PER_DAY]).all()
    assert np.isfinite(out[2 * n * PER_DAY :]).all()
    assert ((out[2 * n * PER_DAY :] >= 0) & (out[2 * n * PER_DAY :] <= 100)).all()


def test_value_changes_at_midnight_and_uses_only_previous_days():
    cd = walk_days(12, seed=24)
    bars = daily_bars(cd)
    sma = daily_sma(bars, 3)
    out = daily_sma_aligned(cd, 3)
    for d in range(4, 12):
        day = out[d * PER_DAY : (d + 1) * PER_DAY]
        assert (day == sma[d - 1]).all()          # constant through day d, from bars < d
        assert out[d * PER_DAY - 1] == sma[d - 2]  # 23:45 of day d-1 still uses bars < d-1
        assert out[d * PER_DAY] != out[d * PER_DAY - 1]


def test_changing_day_d_never_changes_day_d_values():
    cd = walk_days(30, seed=25)
    base_sma, base_adx = daily_sma_aligned(cd, 5), daily_adx_aligned(cd, 5)
    for d in (12, 20, 29):
        sl = slice(d * PER_DAY, (d + 1) * PER_DAY)
        h, l, c = cd.h.copy(), cd.l.copy(), cd.c.copy()
        j = sl.stop - 1  # the day's last candle: the day's close moves, so the next day must change
        c[j] *= 1.05
        h[j] = max(h[j], c[j]) * 1.01
        l[j] = min(l[j], c[j]) * 0.99
        changed = Candles(cd.pair, cd.tf, cd.ts, cd.o, h, l, c, cd.v)
        np.testing.assert_array_equal(daily_sma_aligned(changed, 5)[: sl.stop], base_sma[: sl.stop])
        np.testing.assert_array_equal(daily_adx_aligned(changed, 5)[: sl.stop], base_adx[: sl.stop])
        if d + 1 < 30:  # the next day does see the change
            assert daily_sma_aligned(changed, 5)[sl.stop] != base_sma[sl.stop]


def test_outage_gap_day_counts_with_its_last_available_close():
    cd = walk_days(12, seed=26)
    d = 6
    keep = np.ones(len(cd), dtype=bool)
    keep[d * PER_DAY + 72 : (d + 1) * PER_DAY] = False  # day 6 ends at 17:45 (outage 18:00-24:00)
    gapped = drop(cd, keep)
    bars = daily_bars(gapped)
    assert len(bars) == 12
    assert bars.c[d] == cd.c[d * PER_DAY + 71]
    ref = aligned_reference(gapped, pandas_daily(gapped)["c"].rolling(3).mean())
    np.testing.assert_allclose(daily_sma_aligned(gapped, 3), ref, rtol=1e-12, equal_nan=True)


def test_missing_midnight_candle_still_completes_the_day():
    cd = walk_days(8, seed=27)
    keep = np.ones(len(cd), dtype=bool)
    keep[5 * PER_DAY] = False  # the 00:00 candle of day 5 is missing
    gapped = drop(cd, keep)
    out = daily_sma_aligned(gapped, 2)
    first_of_day5 = 5 * PER_DAY  # index in gapped of day 5's 00:15 candle
    expected = (cd.c[4 * PER_DAY - 1] + cd.c[5 * PER_DAY - 1]) / 2  # closes of days 3 and 4
    assert out[first_of_day5] == pytest.approx(expected, rel=1e-12)


def test_whole_missing_day_is_skipped():
    cd = walk_days(10, seed=28)
    keep = np.ones(len(cd), dtype=bool)
    keep[4 * PER_DAY : 5 * PER_DAY] = False  # no candles at all on day 4
    gapped = drop(cd, keep)
    bars = daily_bars(gapped)
    assert len(bars) == 9 and (T0_MS + 4 * DAY_MS) not in bars.day_ms.tolist()
    out = daily_sma_aligned(gapped, 2)
    idx_day5 = 4 * PER_DAY  # day 5's first candle in gapped
    assert out[idx_day5] == pytest.approx((cd.c[3 * PER_DAY - 1] + cd.c[4 * PER_DAY - 1]) / 2, rel=1e-12)


def test_too_few_days_is_all_nan():
    for days in (0, 1, 3):
        cd = walk_days(days, seed=29)
        assert np.isnan(daily_sma_aligned(cd, 5)).all() and len(daily_sma_aligned(cd, 5)) == len(cd)
        assert np.isnan(daily_adx_aligned(cd, 3)).all()
    cd = walk_days(6, seed=29)  # 6 bars: ADX(3) valid at bar 5, aligned only after it (never)
    assert np.isnan(daily_adx_aligned(cd, 3)).all()


def test_flat_series_gives_zero_adx_not_nan():
    n_days = 12
    flat = Candles("T", "15m", T0_MS + STEP_15M_MS * np.arange(n_days * PER_DAY, dtype=np.int64),
                   *(np.full(n_days * PER_DAY, 100.0) for _ in range(4)), np.ones(n_days * PER_DAY))
    out = daily_adx_aligned(flat, 3)
    assert (out[6 * PER_DAY :] == 0.0).all()


def test_rejects_non_15m_candles():
    cd = random_walk(10, seed=1, start_ms=T0_MS, step_ms=60_000, tf="1m")
    with pytest.raises(ValueError, match="15m"):
        daily_bars(cd)


@pytest.mark.parametrize("fn, n", [(daily_sma_aligned, 5), (daily_adx_aligned, 3)])
def test_daily_indicators_are_causal(fn, n):
    for seed in range(20):
        cd = walk_days(30, seed=300 + seed)
        # cuts: mid-day, the last candle of a day, the first candle of a day, the last candle
        assert_causal(lambda c: fn(c, n), cd, cuts=[10 * PER_DAY + 37, 15 * PER_DAY - 1, 20 * PER_DAY, len(cd) - 1],
                      seeds=[seed], truncate=True)


def test_docstrings_state_lag():
    assert "Lag 0" in daily_sma_aligned.__doc__
    assert "Lag 0" in daily_adx_aligned.__doc__
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_daily.py -q`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'perpbt.indicators.daily'`.

- [ ] **Step 3: Implement**

Create `perpbt/indicators/daily.py`:

```python
"""Completed-day SMA and ADX aligned to the 15m index (spec §2.3, D7).

A UTC day's bar is built from the 15m candles of that day present in the
data: open of the first, max high, min low, close of the last, sum of
volume. Outage gaps inside a day do not disqualify it; a day with no candle
at all has no bar. For every 15m candle of day ``D`` the aligned value is
the indicator computed on the bars of the days before ``D`` only, so it is
constant through the day, changes at 00:00 UTC, and the last day of the
series (never completed) is never used.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from perpbt.data.store import DAY_MS, Candles
from perpbt.indicators.atr import check_period, true_range, wilder_rma


@dataclass(frozen=True, eq=False)  # eq=False: the generated __eq__ raises on ndarray fields
class DailyBars:
    """UTC daily bars. ``day_ms`` is the day's 00:00 UTC in ms, strictly increasing."""

    day_ms: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741 - column name from the spec
    c: np.ndarray
    v: np.ndarray

    def __len__(self) -> int:
        return len(self.day_ms)


def _day_of(ts: np.ndarray) -> np.ndarray:
    return (np.asarray(ts, dtype=np.int64) // DAY_MS) * DAY_MS


def daily_bars(candles15: Candles) -> DailyBars:
    """One bar per UTC day that has at least one 15m candle."""
    if candles15.tf != "15m":
        raise ValueError(f"daily bars are built from 15m candles, got {candles15.tf!r}")
    days = _day_of(candles15.ts)
    if len(days) == 0:
        z = np.zeros(0)
        return DailyBars(np.zeros(0, dtype=np.int64), z, z, z, z, z)
    day_ms, starts = np.unique(days, return_index=True)
    ends = np.append(starts[1:], len(days)) - 1
    return DailyBars(
        day_ms.astype(np.int64),
        candles15.o[starts].astype(np.float64),
        np.maximum.reduceat(candles15.h, starts).astype(np.float64),
        np.minimum.reduceat(candles15.l, starts).astype(np.float64),
        candles15.c[ends].astype(np.float64),
        np.add.reduceat(candles15.v, starts).astype(np.float64),
    )


def daily_sma(bars: DailyBars, n: int = 50) -> np.ndarray:
    """SMA(n) of daily closes per bar; NaN before bar ``n - 1``."""
    n = check_period(n)
    out = np.full(len(bars), np.nan)
    if len(bars) >= n:
        out[n - 1 :] = sliding_window_view(bars.c, n).mean(axis=1)
    return out


def daily_adx(bars: DailyBars, n: int = 14) -> np.ndarray:
    """Wilder ADX(n) per bar; NaN before bar ``2n - 1``.

    +DM/−DM from consecutive bars, Wilder RMA(n) of TR, +DM, −DM over bars
    ``1..``; ``DI± = 100 × RMA(±DM) / RMA(TR)`` (0 when ``RMA(TR) = 0``);
    ``DX = 100 × |DI+ − DI−| / (DI+ + DI−)`` (0 when the sum is 0);
    ``ADX = RMA(n)(DX)`` from the first valid DX (bar ``n``).
    """
    n = check_period(n)
    m = len(bars)
    if m < 2 * n:
        return np.full(m, np.nan)
    h, l, c = bars.h, bars.l, bars.c  # noqa: E741
    tr = true_range(h, l, c)
    up = np.diff(h)
    down = -np.diff(l)
    pdm = np.zeros(m)
    mdm = np.zeros(m)
    pdm[1:] = np.where((up > down) & (up > 0), up, 0.0)
    mdm[1:] = np.where((down > up) & (down > 0), down, 0.0)
    rtr = wilder_rma(tr, n, start=1)
    rp = wilder_rma(pdm, n, start=1)
    rm = wilder_rma(mdm, n, start=1)
    valid = slice(n, m)  # first RMA value is at bar 1 + n - 1 = n
    safe_tr = np.where(rtr[valid] > 0, rtr[valid], 1.0)
    dip = np.where(rtr[valid] > 0, 100.0 * rp[valid] / safe_tr, 0.0)
    dim = np.where(rtr[valid] > 0, 100.0 * rm[valid] / safe_tr, 0.0)
    total = dip + dim
    dx = np.full(m, np.nan)
    dx[valid] = np.where(total > 0, 100.0 * np.abs(dip - dim) / np.where(total > 0, total, 1.0), 0.0)
    return wilder_rma(dx, n, start=n)


def _align(candles15: Candles, bars: DailyBars, per_bar: np.ndarray) -> np.ndarray:
    """Value on each candle = ``per_bar`` at the last bar strictly before the candle's day."""
    pos = np.searchsorted(bars.day_ms, _day_of(candles15.ts), side="left")  # the candle's own bar
    out = np.full(len(candles15), np.nan)
    has_prev = pos >= 1
    out[has_prev] = per_bar[pos[has_prev] - 1]
    return out


def daily_sma_aligned(candles15: Candles, n: int = 50) -> np.ndarray:
    """SMA(n) of completed UTC daily closes, aligned to the 15m index. Lag 0 at the 15m level.

    The value on every candle of day ``D`` is the mean close of the ``n``
    present days before ``D``; NaN until ``n`` such days exist.
    """
    bars = daily_bars(candles15)
    return _align(candles15, bars, daily_sma(bars, n))


def daily_adx_aligned(candles15: Candles, n: int = 14) -> np.ndarray:
    """Wilder ADX(n) of completed UTC days, aligned to the 15m index. Lag 0 at the 15m level.

    The value on every candle of day ``D`` is the ADX on the present days
    before ``D``; NaN until ``2n`` such days exist (warmup ``2n - 1``).
    """
    bars = daily_bars(candles15)
    return _align(candles15, bars, daily_adx(bars, n))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_daily.py -q`
Expected: all pass.

- [ ] **Step 5: Amend spec §2.3 in the same commit**

In `docs/superpowers/specs/2026-09-26-ob-backtest/phase-2-indicators.md` §2.3, replace the paragraph starting "A UTC day `D` is *completed*" with:

```markdown
A UTC day `D` is *completed* once any candle of a later day exists; the
last day of the series is never completed. (Earlier wording said "once the
candle opening at `D+1 00:00` exists"; that would leave a day uncompleted
forever when that one candle is missing.) The daily close of `D` is the
close of the last 15m candle of `D` present in the data (outage gaps do
not disqualify a day); a day with no candle at all has no bar and is
skipped. For any 15m candle `i` on day `D`, the aligned value is the
indicator computed on the bars of the days `< D` only. So the value is
constant across all candles of a day and changes at 00:00 UTC. Days before
the first `n` completed days give NaN.
```

and replace the sentence "with the 2019-11-01 backfill both SMA(50) and ADX(14) are valid on 2020-01-01 for BTC and ETH. For SOL they are valid from listing plus warmup." with:

```markdown
with the 2019-11-01 backfill both SMA(50) and ADX(14) are valid on
2020-01-01 for BTC. ETHUSDT listed on 2019-11-27, so only 35 daily bars
precede 2020-01-01: ADX(14) is valid from 2019-12-25 but SMA(50) only from
2020-01-16, and the trend filter (Phase 3 §3.8) rejects ETH blocks before
then. For SOL both are valid from listing plus warmup.
```

- [ ] **Step 6: Commit**

```bash
git add perpbt/indicators/daily.py tests/test_daily.py docs/superpowers/specs/2026-09-26-ob-backtest/phase-2-indicators.md
git commit -m "Phase 2: daily bars, completed-day SMA and Wilder ADX aligned to 15m (lag 0); spec: completion rule and ETH warmup"
```

---

### Task 5: `MarketView`, `AccountView`, `SessionInfo`, `LookaheadError` — `strategy/base.py`

**Files:**
- Create: `perpbt/strategy/base.py`
- Create: `tests/test_strategy_base.py`
- Modify: `docs/superpowers/specs/2026-09-26-ob-backtest/phase-2-indicators.md` (§2.4, see Step 5)

**Interfaces:**
- Consumes: `Candles`; `Swings` (Task 3); `SessionCalendar` from `perpbt/data/sessions.py` (attributes `ts`, `session_id`, `open_ms`, `end_ms`, `in_window`, `is_last`, all aligned to the 15m index).
- Produces (Phase 3 imports every name here; Phase 4 constructs and advances the view):
  - `class LookaheadError(RuntimeError)`.
  - `SessionInfo(id: int, open_ms: int, end_ms: int, in_window: bool, is_last: bool)`, frozen; outside any window `id = open_ms = end_ms = -1`, `in_window = is_last = False`.
  - `MarketView(candles, *, atr, daily_sma, daily_adx, swings, calendar, start_i=0)`; property `i`; `advance_to(i)`; `ts(j) -> int`; `open/high/low/close/atr/daily_sma/daily_adx(j) -> float`; `opens/highs/lows/closes(a, b) -> np.ndarray` (read-only, rows `a..b` inclusive); `swings_confirmed_by(j) -> Swings` (read-only views); property `session -> SessionInfo` (at `i`); `is_bearish(j) -> bool`.
  - `OrderView(order_id: int, side: str, price: float, stop: float, target: float, expires_ms: int, placed_idx: int)`, `PositionView(position_id: int, side: str, entry: float, stop: float, target: float, qty: float, fill_idx: int)`, `AccountView(equity_mtm: float, open_positions: tuple[PositionView, ...], pending_orders: tuple[OrderView, ...])`, all frozen dataclasses. Phase 4 builds them; the fields are the minimum that `CancelOrder(order_id)` and `ClosePosition(position_id)` in Phase 3 §3.1 need to be addressable, plus the prices a strategy might inspect.

Design notes the implementer needs:
- The constructor keeps read-only *views* of the full arrays (`a.view()` with `flags.writeable = False`), never copies. Every slice handed out is a view of those, so it is read-only too.
- The view never exposes the series length: no `__len__`, no public `n`. It stores the length privately only to bound `advance_to`.
- The guard targets accidental look-ahead in strategy code. Underscored attributes and an array's `.base` are private by convention; strategy code must not touch them (Phase 3's reviewer checks for it).
- Indices go through `operator.index`, so numpy integers work, floats raise `TypeError`, and `bool` is accepted as 0/1 (harmless).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_strategy_base.py`:

```python
"""MarketView look-ahead guard, SessionInfo, AccountView (spec §2.4)."""
import dataclasses

import numpy as np
import pytest

from perpbt.config import SessionSpec
from perpbt.data.sessions import SessionCalendar
from perpbt.indicators.atr import atr
from perpbt.indicators.daily import daily_adx_aligned, daily_sma_aligned
from perpbt.indicators.swings import swing_highs
from perpbt.strategy.base import (
    AccountView,
    LookaheadError,
    MarketView,
    OrderView,
    PositionView,
    SessionInfo,
)
from tests.synthetic import T0_MS, random_walk

N = 3 * 96  # three UTC days
UTC = SessionSpec(name="utc", tz="UTC", open="00:00", close="24:00", days=(0, 1, 2, 3, 4, 5, 6))


@pytest.fixture(scope="module")
def cd():
    return random_walk(N, seed=31, start_ms=T0_MS)


def make_view(cd, start_i=0):
    return MarketView(
        cd,
        atr=atr(cd, 14),
        daily_sma=daily_sma_aligned(cd, 2),
        daily_adx=daily_adx_aligned(cd, 1),
        swings=swing_highs(cd, 2),
        calendar=SessionCalendar(UTC, cd.ts),
        start_i=start_i,
    )


SCALARS = ("ts", "open", "high", "low", "close", "atr", "daily_sma", "daily_adx", "is_bearish")
RANGES = ("opens", "highs", "lows", "closes")


def test_close_beyond_i_raises(cd):
    v = make_view(cd, start_i=100)
    assert v.close(100) == cd.c[100]
    with pytest.raises(LookaheadError):
        v.close(v.i + 1)


def test_range_beyond_i_raises(cd):
    v = make_view(cd, start_i=100)
    i = v.i
    np.testing.assert_array_equal(v.lows(i - 3, i), cd.l[i - 3 : i + 1])
    with pytest.raises(LookaheadError):
        v.lows(i - 3, i + 1)


def test_negative_index_raises(cd):
    v = make_view(cd, start_i=50)
    for name in SCALARS:
        with pytest.raises(LookaheadError):
            getattr(v, name)(-1)
    for name in RANGES:
        with pytest.raises(LookaheadError):
            getattr(v, name)(-2, 3)
    with pytest.raises(LookaheadError):
        v.swings_confirmed_by(-1)


def test_reversed_range_raises(cd):
    v = make_view(cd, start_i=50)
    with pytest.raises(LookaheadError):
        v.closes(10, 9)


def test_float_index_raises_type_error(cd):
    v = make_view(cd, start_i=50)
    with pytest.raises(TypeError):
        v.close(10.0)
    with pytest.raises(TypeError):
        v.lows(1.5, 3)


def test_numpy_integer_indices_work(cd):
    v = make_view(cd, start_i=50)
    assert v.close(np.int64(7)) == cd.c[7]
    assert v.lows(np.int32(1), np.int64(3)).tolist() == cd.l[1:4].tolist()


def test_values_match_the_underlying_arrays(cd):
    v = make_view(cd, start_i=N - 1)
    a, s, x = atr(cd, 14), daily_sma_aligned(cd, 2), daily_adx_aligned(cd, 1)
    for j in (0, 13, 100, 200, N - 1):
        assert v.ts(j) == int(cd.ts[j]) and isinstance(v.ts(j), int)
        assert (v.open(j), v.high(j), v.low(j), v.close(j)) == (cd.o[j], cd.h[j], cd.l[j], cd.c[j])
        assert np.array_equal([v.atr(j), v.daily_sma(j), v.daily_adx(j)], [a[j], s[j], x[j]], equal_nan=True)
        assert v.is_bearish(j) == bool(cd.c[j] < cd.o[j])


def test_swings_confirmed_by_never_returns_future_confirmations(cd):
    full = swing_highs(cd, 2)
    v = make_view(cd)
    for i in range(N):
        v.advance_to(i)
        got = v.swings_confirmed_by(i)
        assert (got.confirmed_at <= i).all()
        mask = full.confirmed_at <= i
        np.testing.assert_array_equal(got.idx, full.idx[mask])
        np.testing.assert_array_equal(got.level, full.level[mask])
        if i >= 5:
            assert len(v.swings_confirmed_by(i - 5)) == int((full.confirmed_at <= i - 5).sum())
    with pytest.raises(LookaheadError):
        v.swings_confirmed_by(N)


def test_advancing_exposes_exactly_one_more_candle(cd):
    v = make_view(cd, start_i=40)
    assert len(v.closes(0, 40)) == 41
    with pytest.raises(LookaheadError):
        v.close(41)
    v.advance_to(41)
    assert v.i == 41 and v.close(41) == cd.c[41]
    assert len(v.closes(0, 41)) == 42
    with pytest.raises(LookaheadError):
        v.close(42)


def test_advance_to_refuses_backwards_and_past_the_end(cd):
    v = make_view(cd, start_i=40)
    with pytest.raises(ValueError):
        v.advance_to(39)
    with pytest.raises(ValueError):
        v.advance_to(N)
    v.advance_to(40)  # staying put is allowed
    with pytest.raises(ValueError):
        make_view(cd, start_i=N)


def test_view_does_not_reveal_the_series_length(cd):
    v = make_view(cd, start_i=10)
    with pytest.raises(TypeError):
        len(v)
    public = {name for name in dir(v) if not name.startswith("_")}
    assert public == set(SCALARS) | set(RANGES) | {"i", "advance_to", "swings_confirmed_by", "session"}


def test_i_is_read_only(cd):
    v = make_view(cd, start_i=10)
    with pytest.raises(AttributeError):
        v.i = 20


def test_returned_arrays_are_read_only_and_exact(cd):
    v = make_view(cd, start_i=100)
    for name in RANGES:
        arr = getattr(v, name)(90, 100)
        assert arr.shape == (11,) and not arr.flags.writeable
        with pytest.raises(ValueError):
            arr[0] = 0.0
    sw = v.swings_confirmed_by(100)
    for arr in (sw.idx, sw.level, sw.confirmed_at):
        assert not arr.flags.writeable
    assert cd.c.flags.writeable  # the caller's arrays are untouched


def test_fuzz_access_succeeds_iff_in_range(cd):
    rng = np.random.default_rng(99)
    v = make_view(cd)
    checked = 0
    for i in np.sort(rng.integers(0, N, 60)):
        v.advance_to(int(i))
        for j in rng.integers(-5, N + 5, 40):
            j = int(j)
            ok = 0 <= j <= i
            for name in SCALARS:
                if ok:
                    getattr(v, name)(j)
                else:
                    with pytest.raises(LookaheadError):
                        getattr(v, name)(j)
            a = int(rng.integers(-5, N + 5))
            ok_range = 0 <= a <= j <= i
            for name in RANGES:
                if ok_range:
                    assert len(getattr(v, name)(a, j)) == j - a + 1
                else:
                    with pytest.raises(LookaheadError):
                        getattr(v, name)(a, j)
            checked += 1
    assert checked == 60 * 40


def test_session_info_follows_the_calendar(cd):
    cal = SessionCalendar(UTC, cd.ts)
    v = make_view(cd)
    for i in (0, 95, 96, 150, N - 1):
        v.advance_to(i)
        s = v.session
        assert s == SessionInfo(
            id=int(cal.session_id[i]), open_ms=int(cal.open_ms[i]), end_ms=int(cal.end_ms[i]),
            in_window=bool(cal.in_window[i]), is_last=bool(cal.is_last[i]),
        )


def test_session_outside_window_is_minus_one():
    ny = SessionSpec(name="ny", tz="America/New_York", open="09:30", close="16:00", days=(0, 1, 2, 3, 4))
    cd = random_walk(96, seed=5, start_ms=T0_MS)  # 2020-01-01, a Wednesday; 00:00 UTC is 19:00 NY
    v = MarketView(cd, atr=atr(cd, 14), daily_sma=daily_sma_aligned(cd, 2), daily_adx=daily_adx_aligned(cd, 1),
                   swings=swing_highs(cd, 2), calendar=SessionCalendar(ny, cd.ts))
    assert v.session == SessionInfo(id=-1, open_ms=-1, end_ms=-1, in_window=False, is_last=False)


def test_constructor_rejects_misaligned_inputs(cd):
    good = dict(atr=atr(cd, 14), daily_sma=daily_sma_aligned(cd, 2), daily_adx=daily_adx_aligned(cd, 1),
                swings=swing_highs(cd, 2), calendar=SessionCalendar(UTC, cd.ts))
    with pytest.raises(ValueError):
        MarketView(cd, **{**good, "atr": good["atr"][:-1]})
    short = random_walk(N - 96, seed=31, start_ms=T0_MS)
    with pytest.raises(ValueError):
        MarketView(cd, **{**good, "calendar": SessionCalendar(UTC, short.ts)})


def test_account_view_is_frozen():
    o = OrderView(order_id=1, side="long", price=10.0, stop=9.0, target=12.0, expires_ms=T0_MS, placed_idx=3)
    p = PositionView(position_id=2, side="long", entry=10.0, stop=9.0, target=12.0, qty=1.5, fill_idx=4)
    acct = AccountView(equity_mtm=10_000.0, open_positions=(p,), pending_orders=(o,))
    with pytest.raises(dataclasses.FrozenInstanceError):
        acct.equity_mtm = 0.0
    assert acct.open_positions[0].position_id == 2 and acct.pending_orders[0].order_id == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_strategy_base.py -q`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'perpbt.strategy.base'`.

- [ ] **Step 3: Implement**

Create `perpbt/strategy/base.py`:

```python
"""Strategy-facing views: the look-ahead guard (spec §2.4, overview §4.1).

``MarketView`` is built once per simulation over the full arrays and moved
forward with ``advance_to``; every accessor refuses an index after the
current candle ``i`` (or before 0) with ``LookaheadError``, so warmup NaNs
are the only signal of "not yet available". The guard targets accidental
look-ahead: underscored attributes and an array's ``.base`` are private and
strategy code must not touch them. The Phase 3 strategy types (intents, the
``Strategy`` protocol) join this module in Phase 3.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass

import numpy as np

from perpbt.data.sessions import SessionCalendar
from perpbt.data.store import Candles
from perpbt.indicators.swings import Swings


class LookaheadError(RuntimeError):
    """A strategy asked for a candle after the current one (or a negative index)."""


@dataclass(frozen=True)
class SessionInfo:
    """The session of the current candle; ``-1``/``False`` everywhere outside a window."""

    id: int
    open_ms: int
    end_ms: int
    in_window: bool
    is_last: bool


@dataclass(frozen=True)
class OrderView:
    """A pending entry order as the strategy sees it (built by the simulator, Phase 4)."""

    order_id: int
    side: str
    price: float
    stop: float
    target: float
    expires_ms: int
    placed_idx: int


@dataclass(frozen=True)
class PositionView:
    """An open position as the strategy sees it (built by the simulator, Phase 4)."""

    position_id: int
    side: str
    entry: float
    stop: float
    target: float
    qty: float
    fill_idx: int


@dataclass(frozen=True)
class AccountView:
    """Read-only account state of this variant at the close of the current candle."""

    equity_mtm: float
    open_positions: tuple[PositionView, ...]
    pending_orders: tuple[OrderView, ...]


def _read_only(arr: np.ndarray, dtype: type) -> np.ndarray:
    view = np.asarray(arr, dtype=dtype).view()
    view.flags.writeable = False
    return view


def _index(j: object) -> int:
    try:
        return operator.index(j)
    except TypeError:
        raise TypeError(f"candle index must be an integer, got {type(j).__name__}") from None


class MarketView:
    """Window ending at candle ``i``. Any access beyond ``i`` raises LookaheadError.

    The view never reveals how many candles follow ``i``.
    """

    __slots__ = (
        "_i", "_n", "_ts", "_o", "_h", "_l", "_c", "_atr", "_sma", "_adx",
        "_sw_idx", "_sw_level", "_sw_conf", "_sid", "_sopen", "_send", "_sin", "_slast",
        "_session_cache",
    )

    def __init__(
        self,
        candles: Candles,
        *,
        atr: np.ndarray,
        daily_sma: np.ndarray,
        daily_adx: np.ndarray,
        swings: Swings,
        calendar: SessionCalendar,
        start_i: int = 0,
    ) -> None:
        n = len(candles)
        for name, arr in (("atr", atr), ("daily_sma", daily_sma), ("daily_adx", daily_adx)):
            if np.shape(arr) != (n,):
                raise ValueError(f"MarketView: {name} has shape {np.shape(arr)}, expected ({n},)")
        if not np.array_equal(calendar.ts, candles.ts):
            raise ValueError("MarketView: the session calendar is not built on these candles' timestamps")
        self._n = n
        self._ts = _read_only(candles.ts, np.int64)
        self._o = _read_only(candles.o, np.float64)
        self._h = _read_only(candles.h, np.float64)
        self._l = _read_only(candles.l, np.float64)
        self._c = _read_only(candles.c, np.float64)
        self._atr = _read_only(atr, np.float64)
        self._sma = _read_only(daily_sma, np.float64)
        self._adx = _read_only(daily_adx, np.float64)
        self._sw_idx = _read_only(swings.idx, np.int64)
        self._sw_level = _read_only(swings.level, np.float64)
        self._sw_conf = _read_only(swings.confirmed_at, np.int64)
        self._sid = calendar.session_id
        self._sopen = calendar.open_ms
        self._send = calendar.end_ms
        self._sin = calendar.in_window
        self._slast = calendar.is_last
        self._session_cache: SessionInfo | None = None
        start_i = _index(start_i)
        if not 0 <= start_i < n:
            raise ValueError(f"MarketView: start_i must be in [0, {n}), got {start_i}")
        self._i = start_i

    # --- cursor ------------------------------------------------------------------------

    @property
    def i(self) -> int:
        """The current candle: decisions happen at its close."""
        return self._i

    def advance_to(self, i: int) -> None:
        """Move the cursor forward to ``i`` (simulator only). Backwards or past the end raises ValueError."""
        i = _index(i)
        if i < self._i or i >= self._n:
            raise ValueError(f"MarketView.advance_to: cannot move from {self._i} to {i}")
        if i != self._i:
            self._i = i
            self._session_cache = None

    # --- guards ------------------------------------------------------------------------

    def _j(self, j: object) -> int:
        j = _index(j)
        if j < 0 or j > self._i:
            raise LookaheadError(f"candle {j} is not visible at i={self._i}")
        return j

    def _ab(self, a: object, b: object) -> slice:
        a, b = _index(a), _index(b)
        if not 0 <= a <= b <= self._i:
            raise LookaheadError(f"range [{a}, {b}] is not visible at i={self._i} (need 0 <= a <= b <= i)")
        return slice(a, b + 1)

    # --- scalars -----------------------------------------------------------------------

    def ts(self, j: int) -> int:
        """Open time of candle ``j`` in UTC ms."""
        return int(self._ts[self._j(j)])

    def open(self, j: int) -> float:
        return float(self._o[self._j(j)])

    def high(self, j: int) -> float:
        return float(self._h[self._j(j)])

    def low(self, j: int) -> float:
        return float(self._l[self._j(j)])

    def close(self, j: int) -> float:
        return float(self._c[self._j(j)])

    def atr(self, j: int) -> float:
        return float(self._atr[self._j(j)])

    def daily_sma(self, j: int) -> float:
        return float(self._sma[self._j(j)])

    def daily_adx(self, j: int) -> float:
        return float(self._adx[self._j(j)])

    def is_bearish(self, j: int) -> bool:
        """``close[j] < open[j]``."""
        j = self._j(j)
        return bool(self._c[j] < self._o[j])

    # --- ranges (read-only views, inclusive of b) -------------------------------------

    def opens(self, a: int, b: int) -> np.ndarray:
        return self._o[self._ab(a, b)]

    def highs(self, a: int, b: int) -> np.ndarray:
        return self._h[self._ab(a, b)]

    def lows(self, a: int, b: int) -> np.ndarray:
        return self._l[self._ab(a, b)]

    def closes(self, a: int, b: int) -> np.ndarray:
        return self._c[self._ab(a, b)]

    # --- swings and session ------------------------------------------------------------

    def swings_confirmed_by(self, j: int) -> Swings:
        """Swing highs with ``confirmed_at <= j`` (``j <= i``), as read-only views."""
        j = self._j(j)
        m = int(np.searchsorted(self._sw_conf, j, side="right"))
        return Swings._unchecked(self._sw_idx[:m], self._sw_level[:m], self._sw_conf[:m])

    @property
    def session(self) -> SessionInfo:
        """The session of candle ``i``."""
        if self._session_cache is None:
            i = self._i
            self._session_cache = SessionInfo(
                id=int(self._sid[i]),
                open_ms=int(self._sopen[i]),
                end_ms=int(self._send[i]),
                in_window=bool(self._sin[i]),
                is_last=bool(self._slast[i]),
            )
        return self._session_cache
```

`Swings._unchecked` wraps the prefix views as they are: no copy, still read-only (the test checks `flags.writeable`), and no O(m) re-validation per candle. The constructor already validated the full `Swings` it received.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_strategy_base.py -q`
Expected: all pass.

- [ ] **Step 5: Amend spec §2.4 in the same commit**

In `docs/superpowers/specs/2026-09-26-ob-backtest/phase-2-indicators.md` §2.4, change the accessor line to

```python
    def ts(self, j) -> int
    def open(self, j) / high(self, j) / low(self, j) / close(self, j) -> float
```

add below `session: SessionInfo`:

```python
    def advance_to(self, i) -> None                 # simulator only; forward, < series length
```

replace the `AccountView` block with:

```python
@dataclass(frozen=True)
class OrderView:    order_id: int; side: str; price: float; stop: float; target: float; expires_ms: int; placed_idx: int
@dataclass(frozen=True)
class PositionView: position_id: int; side: str; entry: float; stop: float; target: float; qty: float; fill_idx: int
@dataclass(frozen=True)
class AccountView:                     # read-only, this variant only
    equity_mtm: float
    open_positions: tuple[PositionView, ...]
    pending_orders: tuple[OrderView, ...]
```

and append to the paragraph after the code block: "Range accessors and `swings_confirmed_by` return read-only views of exactly the requested rows. A non-integer index raises `TypeError`. `SessionInfo` outside any window has `id = open_ms = end_ms = -1` and both flags false."

- [ ] **Step 6: Commit**

```bash
git add perpbt/strategy/base.py tests/test_strategy_base.py docs/superpowers/specs/2026-09-26-ob-backtest/phase-2-indicators.md
git commit -m "Phase 2: MarketView look-ahead guard, SessionInfo, AccountView; spec: ts is int, advance_to, view types"
```

---

### Task 6: Slow real-data checks, exit criterion, whole-branch review, merge

**Files:**
- Modify: `tests/test_daily.py` (append slow tests)
- Modify: this plan (append "Post-review amendments" if the review asks for changes)

**Interfaces:**
- Consumes: `CandleStore.load(pair, "15m", start_ms, end_ms)`, `date_ms`, `DataConfig`, `load_yaml`, `to_dict`; everything above.
- Produces: `dev` contains Phase 2, merged with `--no-ff`; `phase/2-indicators` deleted locally and on `origin`.

- [ ] **Step 1: Write the slow tests**

In `tests/test_daily.py`, add to the imports at the top (merge `CandleStore, date_ms` into the existing
`from perpbt.data.store import DAY_MS, Candles` line):

```python
from pathlib import Path

from perpbt.config import DataConfig, load_yaml, to_dict
from perpbt.data.store import DAY_MS, CandleStore, Candles, date_ms
```

then append:

```python
# --- real data (slow) -----------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def real_cfg():
    cfg = load_yaml(ROOT / "configs" / "data.yaml", DataConfig)
    cfg = DataConfig(**{**to_dict(cfg), "data_dir": str(ROOT / cfg.data_dir)})
    if not (Path(cfg.data_dir) / "candles").is_dir():
        pytest.skip("no downloaded data under data/; run `perpbt data fetch` first")
    return cfg


def _first_valid_day(cd: Candles, values: np.ndarray) -> int:
    return int(cd.ts[np.flatnonzero(np.isfinite(values))[0]])


@pytest.mark.slow
def test_btc_sma50_and_adx14_valid_on_2020_01_01(real_cfg):
    cd = CandleStore(real_cfg).load("BTCUSDT", "15m", date_ms("2019-11-01"), date_ms("2020-01-02"))
    i = cd.index_at(date_ms("2020-01-01"), exact=True)
    assert np.isfinite(daily_sma_aligned(cd, 50)[i])
    assert np.isfinite(daily_adx_aligned(cd, 14)[i])


@pytest.mark.slow
def test_eth_warmup_starts_at_listing(real_cfg):
    cd = CandleStore(real_cfg).load("ETHUSDT", "15m", date_ms("2019-11-01"), date_ms("2020-02-01"))
    assert _first_valid_day(cd, daily_adx_aligned(cd, 14)) == date_ms("2019-12-25")
    assert _first_valid_day(cd, daily_sma_aligned(cd, 50)) == date_ms("2020-01-16")


@pytest.mark.slow
def test_sol_valid_from_listing_plus_warmup(real_cfg):
    cd = CandleStore(real_cfg).load("SOLUSDT", "15m", date_ms("2020-09-01"), date_ms("2020-12-31"))
    first_day = (int(cd.ts[0]) // DAY_MS) * DAY_MS
    assert _first_valid_day(cd, daily_sma_aligned(cd, 50)) == first_day + 50 * DAY_MS
    assert _first_valid_day(cd, daily_adx_aligned(cd, 14)) == first_day + 28 * DAY_MS
```

The ETH dates assume the stored ETHUSDT 15m series starts on 2019-11-27 with no whole day missing before 2020-01-16. Check that before trusting a failure:
`python -c "from perpbt.config import *; from perpbt.data.store import *; import numpy as np; cfg=load_yaml('configs/data.yaml', DataConfig); c=CandleStore(cfg).load('ETHUSDT','15m',date_ms('2019-11-01'),date_ms('2020-02-01')); d=np.unique(c.ts//DAY_MS); print(len(d), d[0]*DAY_MS, np.diff(d).max())"`
Expected: the first day is `1574812800000` (2019-11-27) and the largest day step is 1. If the archive differs, fix the expected dates in the test and in the spec §2.3 sentence from Task 4 together, in this commit, and say so in the commit message.

- [ ] **Step 2: Run the whole suite**

```bash
python -m pytest -q -m "not slow"
python -m pytest -q -m slow
git status --short          # expected: only tests/test_daily.py modified
```

Expected: every test passes, fast and slow.

- [ ] **Step 3: Exit-criterion check: every indicator's docstring states its lag**

```bash
python -c "
from perpbt.indicators.atr import atr
from perpbt.indicators.swings import swing_highs
from perpbt.indicators.daily import daily_sma_aligned, daily_adx_aligned
for f, lag in ((atr, 'Lag 0'), (swing_highs, 'Lag k'), (daily_sma_aligned, 'Lag 0'), (daily_adx_aligned, 'Lag 0')):
    assert lag in f.__doc__, f.__name__
print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit and push**

```bash
git add tests/test_daily.py
git commit -m "Phase 2: slow real-data checks of the daily warmup (BTC valid 2020-01-01, ETH and SOL from listing plus warmup)"
git push
```

- [ ] **Step 5: Whole-branch review**

Dispatch a fresh reviewer over `git diff dev...phase/2-indicators` with the spec (`phase-2-indicators.md`, `00-overview.md`, `phase-3-strategy.md` §3.1–§3.8) and this plan; ask for correctness, spec conformance, causality (can any value at `i` see a candle after `i`, including through the series length?), and the Review Focus items. Fix everything blocking, with a test per fix, in small commits. Append a "Post-review amendments" section to this plan listing what changed. Rulings that defer work go into the merge commit message and the project memory.

- [ ] **Step 6: Merge into `dev` with `--no-ff`, push, delete the branch**

```bash
git push
git switch dev
git pull
git merge --no-ff phase/2-indicators -m "Merge phase/2-indicators into dev: Wilder ATR, swing highs, completed-day SMA/ADX, MarketView look-ahead guard"
python -m pytest -q -m "not slow"   # expected: all pass on dev
git push origin dev
git branch -d phase/2-indicators
git push origin --delete phase/2-indicators
git branch -a               # expected: no phase/2-indicators locally or on origin
```

---

## Self-review notes

- **Spec coverage.** §2.1 ATR formula, NaN warmup, gaps ignored (T2: hand example, 20-candle reference, gap test, `assert_causal` 20 seeds × 3 cuts). §2.2 `Swings` fields and ordering, strict definition, end exclusion, injective `confirmed_at` (T3: `k = 1, 2, 3` known series, equal highs, `confirmed_at == idx + k`, end exclusion, brute-force definition check, pair-form `assert_causal`). §2.3 `daily_bars`, `daily_sma_aligned`, `daily_adx_aligned`, completion rule, outage days, constant through the day, NaN before `n` days, ADX construction and `2n − 1` warmup (T4: pandas SMA, loop-reference ADX, midnight rule, day-`D` invariance, outage day, missing midnight candle, missing whole day, `assert_causal` with truncation), slow BTC 2020-01-01 (T6). §2.4 `LookaheadError`, `SessionInfo`, every `MarketView` accessor, `swings_confirmed_by`, `session`, `is_bearish`, `AccountView`, construct-once-and-advance, never copies, negative `j` (T5: every test in the spec's list plus read-only, length hiding, float index, misaligned inputs). Exit criterion (T6 Steps 2–3). Branch rules (T1 Step 1, T6 Step 6).
- **Deviations, each amended in the spec in the task that makes it:** the completion rule reads "any later candle" instead of "the candle at `D+1 00:00`" and ETH's SMA(50) warmup ends 2020-01-16, not 2020-01-01 (T4); `ts` returns `int`, `advance_to` is the advancing method, `OrderView`/`PositionView` fields are fixed, returned arrays are read-only views (T5).
- **Type consistency.** `wilder_rma(x, n, start)` and `true_range(h, l, c)` are defined in T2 and used with those signatures in T4. `Swings(idx, level, confirmed_at)` from T3 is what T5 slices. `daily_sma_aligned(c, n)` / `daily_adx_aligned(c, n)` and `atr(c, n)` are what T5's fixture passes to `MarketView`. `SessionCalendar` attribute names (`session_id`, `open_ms`, `end_ms`, `in_window`, `is_last`, `ts`) match `perpbt/data/sessions.py`.
- **Review Focus.** Items 1–5 name their tests (T5 ×2, T4 ×2, T1, T2–T4).
- **Phase 0 pointers.** `assert_causal` truncation check (T1, used in T2–T4); the `step_sigma` kwarg was not needed (the daily tests perturb with the default and still move daily values) and stays deferred. Deferred Phase 0 minors folded in: pair-form message, `candles_from_rows` NaN (T1). Phase 1 deferred minors touch no file in this phase and stay in the memory file.
