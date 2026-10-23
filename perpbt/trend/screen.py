"""The gross signal screen (trend spec Phase 2 §2.3) and its random-days paths (Phase 4 §4.1).

A deliberately simple return model, independent of the Phase 3 executor:
the decision on day ``d`` earns ``w*_d × (c_{d+1} / c_d − 1)`` on day ``d+1``,
with no fill delay, drift band, fees, slippage or funding. The portfolio
return is the mean over the pairs active on ``d``. Decision days run from
the day before ``insample.start`` to the day before ``insample.end``, so the
returns fall on the in-sample days.

Each random path draws one offset ``k`` on ``[margin, n − margin]`` and
shifts every pair's exposure by it within the pair's own active days
(``e'_t = e_{(t + k) mod n_i}``); weights are recomputed from the shifted
exposure with the actual vol forecast. Time in market, exposure levels and
the number of exposure changes are kept; only the timing moves.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from perpbt.data.store import DAY_MS, date_ms
from perpbt.stats.bootstrap import sharpe
from perpbt.trend.config import SizingConfig
from perpbt.trend.daily import DayFrame
from perpbt.trend.signals import Signals, target_weight


@dataclass(frozen=True, eq=False)  # eq=False: the generated __eq__ raises on ndarray fields
class ScreenInputs:
    """Pairs aligned on decision days; arrays are ``(pairs, days)``. NaN where a pair has no row."""

    pairs: tuple[str, ...]
    day_ms: np.ndarray
    ret_next: np.ndarray  # c_{d+1} / c_d − 1: price only, no funding
    exposure: np.ndarray
    sigma: np.ndarray
    active: np.ndarray  # signal valid on d and day d+1 in the frame


def screen_inputs(
    frames: dict[str, DayFrame], signals: dict[str, Signals], *, start: str, end: str,
) -> ScreenInputs:
    """Align every pair on the decision days ``start − 1 .. end − 1`` (ISO dates, returns on ``start .. end``)."""
    first, last = date_ms(start) - DAY_MS, date_ms(end) - DAY_MS
    if last < first:
        raise ValueError(f"screen period {start}..{end} is empty")
    day_ms = np.arange(first, last + DAY_MS, DAY_MS, dtype=np.int64)
    pairs = tuple(frames)
    shape = (len(pairs), len(day_ms))
    ret_next, exposure, sigma = (np.full(shape, np.nan) for _ in range(3))
    active = np.zeros(shape, dtype=bool)
    for i, pair in enumerate(pairs):
        f, s = frames[pair], signals[pair]
        idx = (day_ms - f.day_ms[0]) // DAY_MS
        ok = (idx >= 0) & (idx + 1 < len(f))
        j = idx[ok]
        ret_next[i, ok] = f.c[j + 1] / f.c[j] - 1.0
        exposure[i, ok] = s.exposure[j]
        sigma[i, ok] = s.sigma[j]
        active[i, ok] = s.valid[j]
    return ScreenInputs(pairs, day_ms, ret_next, exposure, sigma, active)


def portfolio_returns(weight: np.ndarray, inputs: ScreenInputs) -> np.ndarray:
    """Per day, the mean of ``weight × ret_next`` over active pairs (0 when none is active).

    ``weight`` is ``(pairs, days)`` or ``(paths, pairs, days)``; the result drops the pairs axis.
    """
    contrib = np.where(inputs.active, np.asarray(weight, dtype=np.float64) * inputs.ret_next, 0.0)
    count = inputs.active.sum(axis=0)
    return np.where(count > 0, contrib.sum(axis=-2) / np.maximum(count, 1), 0.0)


def shift_offsets(n: int, M: int, margin: int, rng: np.random.Generator) -> np.ndarray:
    """``M`` offsets uniform on ``[margin, n − margin]`` (both ends included)."""
    if n - margin < margin:
        raise ValueError(f"no offset in [{margin}, {n - margin}]: {n} days are too few for a {margin}-day margin")
    return rng.integers(margin, n - margin, size=M, endpoint=True).astype(np.int64)


def shifted_exposure(inputs: ScreenInputs, k: np.ndarray) -> np.ndarray:
    """``(paths, pairs, days)``: each pair's active exposures rotated by ``k mod n_i``; NaN where not active."""
    k = np.asarray(k, dtype=np.int64)
    out = np.full((len(k), *inputs.exposure.shape), np.nan)
    for i in range(len(inputs.pairs)):
        idx = np.flatnonzero(inputs.active[i])
        if len(idx) == 0:
            continue
        e = inputs.exposure[i, idx]
        out[:, i, idx] = e[(np.arange(len(idx))[None, :] + k[:, None]) % len(idx)]
    return out


def _summary(x: np.ndarray) -> dict:
    return {
        "n": int(len(x)), "mean": float(np.nanmean(x)), "std": float(np.nanstd(x, ddof=1)),
        "p05": float(np.nanpercentile(x, 5)), "p50": float(np.nanpercentile(x, 50)),
        "p95": float(np.nanpercentile(x, 95)),
    }


def run_screen(
    inputs: ScreenInputs, sizing: SizingConfig, *, paths: int, margin: int, rng: np.random.Generator,
    stop_if_p_at_least: float, chunk: int = 100,
) -> dict:
    """Observed gross Sharpe against ``paths`` shifted paths; ``p_screen`` and the go/stop decision.

    ``bh_sharpe`` is the same model with ``e ≡ 1`` on active days (vol-targeted buy-and-hold), descriptive only.
    """
    observed = sharpe(portfolio_returns(target_weight(inputs.exposure, inputs.sigma, sizing), inputs))
    k = shift_offsets(len(inputs.day_ms), paths, margin, rng)
    path_sharpe = np.empty(paths)
    for a in range(0, paths, chunk):
        e = shifted_exposure(inputs, k[a:a + chunk])
        r = portfolio_returns(target_weight(e, inputs.sigma[None], sizing), inputs)
        path_sharpe[a:a + chunk] = [sharpe(row) for row in r]
    p = (1 + int(np.sum(path_sharpe >= observed))) / (paths + 1)
    ones = np.where(inputs.active, 1.0, np.nan)
    return {
        "n_days": int(len(inputs.day_ms)),
        "n_pairs": len(inputs.pairs),
        "observed_sharpe": observed,
        "paths": _summary(path_sharpe),
        "p_screen": p,
        "stop_if_p_at_least": stop_if_p_at_least,
        "decision": "stop" if p >= stop_if_p_at_least else "go",
        "bh_sharpe": sharpe(portfolio_returns(target_weight(ones, inputs.sigma, sizing), inputs)),
        "mean_active_pairs": float(inputs.active.sum(axis=0).mean()),
    }
