"""Bootstrap confidence intervals (spec §5.1).

- trade bootstrap of the mean net R (resample trades), the primary CI;
- day-block bootstrap of the mean net R (resample entry days, keep each
  day's trades together), the secondary CI for dependent trades;
- moving-block bootstrap of the daily Sharpe ratio, and the paired Sharpe
  difference of two aligned daily series (the same blocks for both).

Every function takes an explicit generator and returns the replicates;
``percentile_ci`` turns them into a percentile interval. Index draws use
``floor(u × n)`` on uniform doubles (one 64-bit draw each), so the result
does not depend on how the replicates are chunked.
"""
from __future__ import annotations

import math
from collections.abc import Iterator

import numpy as np

ANNUALIZE = math.sqrt(365.0)
_CELLS = 2_000_000  # index cells per chunk (about 16 MB of int64)


def _draw(rng: np.random.Generator, rows: int, cols: int, n: int) -> np.ndarray:
    """``rows × cols`` uniform integers in ``[0, n)``."""
    k = np.floor(rng.random((rows, cols)) * n).astype(np.int64)
    return np.minimum(k, n - 1)


def _chunks(total: int, per_row: int, chunk: int | None) -> Iterator[int]:
    size = chunk or max(1, _CELLS // max(per_row, 1))
    done = 0
    while done < total:
        rows = min(size, total - done)
        yield rows
        done += rows


def percentile_ci(reps: np.ndarray, level: float = 0.95) -> tuple[float | None, float | None]:
    """Percentile interval of the finite replicates; ``(None, None)`` when there are none."""
    r = np.asarray(reps, dtype=np.float64)
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return None, None
    tail = 100.0 * (1.0 - level) / 2.0
    lo, hi = np.percentile(r, [tail, 100.0 - tail])
    return float(lo), float(hi)


def trade_bootstrap(x: np.ndarray, B: int, rng: np.random.Generator, *, chunk: int | None = None) -> np.ndarray:
    """``B`` means of ``x`` resampled with replacement (empty when ``x`` is empty)."""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n == 0:
        return np.zeros(0)
    out = []
    for rows in _chunks(B, n, chunk):
        out.append(x[_draw(rng, rows, n, n)].mean(axis=1))
    return np.concatenate(out)


def day_block_bootstrap(
    x: np.ndarray, day: np.ndarray, B: int, rng: np.random.Generator, *, chunk: int | None = None,
) -> np.ndarray:
    """``B`` means of ``x`` after resampling whole days (``day`` labels each value's entry day).

    A replicate draws as many days as there are, with replacement, and
    takes the mean over all trades of the drawn days (``Σ sums / Σ counts``),
    so its trade count varies.
    """
    x = np.asarray(x, dtype=np.float64)
    day = np.asarray(day)
    if len(x) != len(day):
        raise ValueError(f"day_block_bootstrap: {len(x)} values but {len(day)} day labels")
    if len(x) == 0:
        return np.zeros(0)
    _, inverse = np.unique(day, return_inverse=True)
    sums = np.bincount(inverse, weights=x)
    counts = np.bincount(inverse).astype(np.float64)
    d = len(sums)
    out = []
    for rows in _chunks(B, d, chunk):
        k = _draw(rng, rows, d, d)
        out.append(sums[k].sum(axis=1) / counts[k].sum(axis=1))
    return np.concatenate(out)


def sharpe(ret: np.ndarray) -> float:
    """Annualized daily Sharpe ``sqrt(365) × mean / std`` (ddof 1); NaN for fewer than 2 days or zero std."""
    r = np.asarray(ret, dtype=np.float64)
    if len(r) < 2:
        return float("nan")
    sd = r.std(ddof=1)
    return float(ANNUALIZE * r.mean() / sd) if sd > 0 else float("nan")


def _row_sharpe(x: np.ndarray) -> np.ndarray:
    sd = x.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sd > 0, ANNUALIZE * x.mean(axis=1) / sd, np.nan)


def block_indices(
    T: int, B: int, rng: np.random.Generator, block_len: int = 10, *, chunk: int | None = None,
) -> Iterator[np.ndarray]:
    """Moving-block resamples of ``range(T)``, as chunks of ``rows × T`` index arrays.

    Each row concatenates ``ceil(T / L)`` blocks of ``L = min(block_len, T)``
    consecutive days, each starting uniformly in ``[0, T − L]``, cut to ``T``.
    """
    if T <= 0:
        return
    L = max(1, min(int(block_len), T))
    nb = -(-T // L)
    offsets = np.arange(L)
    for rows in _chunks(B, nb * L, chunk):
        starts = _draw(rng, rows, nb, T - L + 1)
        yield (starts[:, :, None] + offsets).reshape(rows, nb * L)[:, :T]


def block_bootstrap_sharpe(ret: np.ndarray, B: int, rng: np.random.Generator, block_len: int = 10) -> np.ndarray:
    """``B`` Sharpe ratios of moving-block resamples of ``ret`` (NaN where a resample has zero std)."""
    r = np.asarray(ret, dtype=np.float64)
    if len(r) < 2:
        return np.full(B, np.nan)
    return np.concatenate([_row_sharpe(r[k]) for k in block_indices(len(r), B, rng, block_len)])


def paired_sharpe_diff(
    a: np.ndarray, b: np.ndarray, B: int, rng: np.random.Generator, block_len: int = 10,
) -> dict:
    """``Sharpe(a) − Sharpe(b)`` with a paired moving-block CI and the one-sided ``p`` of spec §5.1.

    Both series are resampled with the same blocks. ``p = (1 + #{replicates ≤ 0}) / (B' + 1)``
    over the ``B'`` finite replicates (None when there are none).
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape != b.shape or a.ndim != 1:
        raise ValueError(f"paired_sharpe_diff: series must be aligned 1-D arrays, got {a.shape} and {b.shape}")
    sa, sb = sharpe(a), sharpe(b)
    if len(a) < 2:
        reps = np.full(B, np.nan)
    else:
        reps = np.concatenate([_row_sharpe(a[k]) - _row_sharpe(b[k]) for k in block_indices(len(a), B, rng, block_len)])
    lo, hi = percentile_ci(reps)
    finite = reps[np.isfinite(reps)]
    p = (1.0 + float(np.sum(finite <= 0.0))) / (len(finite) + 1.0) if len(finite) else None
    return {"sharpe_a": sa, "sharpe_b": sb, "diff": sa - sb, "ci_lo": lo, "ci_hi": hi, "p": p}
