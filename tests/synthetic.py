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
    if not np.all(np.isfinite(arr)):
        bad_row = int(np.flatnonzero(~np.isfinite(arr).all(axis=1))[0])
        raise ValueError(f"row {bad_row}: every value must be finite")
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


_PART_NAMES = ("values", "confirmed_at")


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
