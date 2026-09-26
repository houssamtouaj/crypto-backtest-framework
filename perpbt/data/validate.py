"""Validation of stored candles and funding (spec §1.4).

Pure functions over timestamp arrays and ``Candles``: grid and ordering,
gap report, 1m→15m consistency, funding interval report. ``run_validate``
drives them over the stores and writes the results into the manifests.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from perpbt.data.store import Candles

STEP_15M_MS = 900_000
STEP_1M_MS = 60_000
ALLOWED_INTERVALS = (1, 4, 8)


@dataclass(frozen=True)
class Gap:
    """A run of missing grid slots: ``[start_ms, end_ms)``, ``missing`` slots."""

    start_ms: int
    end_ms: int
    missing: int

    def as_dict(self) -> dict:
        return asdict(self)


def check_grid(ts: np.ndarray, step_ms: int) -> None:
    """Raise unless ``ts`` is strictly increasing and every value is a multiple of ``step_ms``."""
    ts = np.asarray(ts)
    if ts.ndim != 1:
        raise ValueError(f"expected a 1-D array, got shape {ts.shape}")
    if not np.issubdtype(ts.dtype, np.integer):
        raise TypeError(f"expected an integer dtype (UTC ms), got {ts.dtype}")
    if len(ts) > 1:
        bad = np.flatnonzero(np.diff(ts) <= 0)
        if bad.size:
            i = int(bad[0])
            raise ValueError(
                f"timestamps are not strictly increasing at index {i + 1}: {int(ts[i])} then {int(ts[i + 1])}"
            )
    off = np.flatnonzero(ts % step_ms != 0)
    if off.size:
        raise ValueError(
            f"{off.size} timestamps are off the {step_ms} ms grid, first at index {int(off[0])}: {int(ts[off[0]])}"
        )


def gap_report(ts: np.ndarray, step_ms: int) -> list[Gap]:
    """Every run of missing grid slots between the first and the last timestamp, in order."""
    ts = np.asarray(ts)
    check_grid(ts, step_ms)
    if len(ts) < 2:
        return []
    idx = np.flatnonzero(np.diff(ts) > step_ms)
    return [
        Gap(int(ts[i] + step_ms), int(ts[i + 1]), int((ts[i + 1] - ts[i]) // step_ms) - 1)
        for i in idx
    ]


def consistency_1m_15m(c1m: Candles, c15: Candles, *, rtol: float = 1e-9) -> dict:
    """Compare every 15m candle with the aggregate of its fifteen 1m constituents.

    Only 15m candles whose fifteen constituents are all present are compared
    (open = first open, high = max, low = min, close = last close, volume =
    sum). A candle mismatches when any field differs by more than ``rtol``
    relative. Mismatches are reported, never fixed.
    """
    if c1m.tf != "1m" or c15.tf != "15m":
        raise ValueError(f"expected a 1m and a 15m series, got {c1m.tf!r} and {c15.tf!r}")
    if c1m.pair != c15.pair:
        raise ValueError(f"pair mismatch: {c1m.pair!r} vs {c15.pair!r}")
    a = np.searchsorted(c1m.ts, c15.ts, side="left")
    b = np.searchsorted(c1m.ts, c15.ts + STEP_15M_MS, side="left")
    complete = (b - a) == 15
    idx = a[complete][:, None] + np.arange(15)[None, :]
    aggregated = {
        "open": c1m.o[idx[:, 0]],
        "high": c1m.h[idx].max(axis=1),
        "low": c1m.l[idx].min(axis=1),
        "close": c1m.c[idx[:, -1]],
        "volume": c1m.v[idx].sum(axis=1),
    }
    published = {
        "open": c15.o[complete], "high": c15.h[complete], "low": c15.l[complete],
        "close": c15.c[complete], "volume": c15.v[complete],
    }
    bad = np.zeros(int(complete.sum()), dtype=bool)
    fields: dict[str, int] = {}
    for name, agg in aggregated.items():
        m = ~np.isclose(agg, published[name], rtol=rtol, atol=0.0)
        fields[name] = int(m.sum())
        bad |= m
    months = np.unique(c15.ts[complete].astype("datetime64[ms]").astype("datetime64[M]"))
    return {
        "months_checked": int(len(months)),
        "candles_compared": int(complete.sum()),
        "mismatching_candles": int(bad.sum()),
        "mismatch_fields": fields,
    }


def check_funding(ts: np.ndarray, interval_h: np.ndarray) -> dict:
    """Funding timestamps must be unique and minute-rounded; report the interval values seen."""
    ts = np.asarray(ts)
    if len(ts) > 1 and not np.all(np.diff(ts) > 0):
        raise ValueError("funding timestamps must be strictly increasing (unique after rounding)")
    if np.any(ts % STEP_1M_MS != 0):
        raise ValueError("funding timestamps must be minute-rounded")
    values, counts = np.unique(np.asarray(interval_h), return_counts=True)
    intervals = {int(v): int(n) for v, n in zip(values, counts)}
    return {
        "intervals": intervals,
        "bad_intervals": sorted(v for v in intervals if v not in ALLOWED_INTERVALS),
    }
