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
