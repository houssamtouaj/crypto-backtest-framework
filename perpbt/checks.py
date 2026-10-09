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
