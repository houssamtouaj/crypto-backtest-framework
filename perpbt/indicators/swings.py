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
