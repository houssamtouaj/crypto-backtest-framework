"""V1, V2 and the verdict of a carry row (carry spec Phase 4 §4.1-§4.3), and its ``stats.json`` document.

On a row's in-period net daily returns (the executor's ``daily["ret"]``):

- **V1** (§4.1): the Sharpe, its moving-block bootstrap (``stats.bootstrap.block_bootstrap_sharpe``, B =
  ``stats.bootstrap_n``, blocks of ``stats.block_len_days``) and ``p1 = (1 + #{replicates ≤ 0}) / (B' + 1)`` over
  the ``B'`` finite replicates; passes iff ``p1 < alpha``.
- **V2** (§4.2): the daily excess ``e = ret − h_d``, ``h_d = (1 + h)^(1/365) − 1``; replicates ``365 × mean(e*)``
  of moving-block resamples (``block_bootstrap_mean``); ``p2 = (1 + #{replicates ≤ 0}) / (B + 1)``; passes iff
  ``p2 < alpha``. Its reading: ``above_hurdle`` (passes), ``below_hurdle`` (CI upper < 0) or ``not_proven``.
- **Verdict** (§4.3): ``edge = V1 and V2``, unadjusted; ``reading`` is ``edge``, ``positive_below_hurdle`` (V1
  passes, V2 does not) or ``no_edge`` (anything else, V2 passing without V1 included).

``v1_stats``, ``v2_stats`` and ``daily_hurdle`` are the screen's (``carry/screen.py``), imported, not copied.

Seeds: every generator is ``rng_for(stats.master_seed, prereg_hash, purpose)`` with ``purpose =
"<period>/<part>/…"`` (``Seeds``). Purposes used by Phase 4:

- ``<period>/<row>/v1``: V1 (the annual table's Sharpe CI is V1's);
- ``<period>/<row>/v2``: V2 at ``hurdle.annual`` and at every ``hurdle.descriptive`` value, a fresh generator
  each (the same draws, so the descriptive row at the verdict's hurdle equals the verdict);
- ``<period>/<row>/reprice/v2``: V2 of every re-pricing cell (one stream for all cells);
- ``<period>/filter_value``: the paired Sharpe difference primary − always_on;
- ``<period>/random_timing``: the circular-shift offsets.

``stats.json`` (``stats_document`` builds it, ``stats_json`` serialises it: ``indent=2``, keys in this order,
non-finite numbers as ``null``, ``"\\n"`` at the end, so the same inputs and seeds give the same bytes)::

    {
      "schema": "carry-stats/1",
      "prereg_hash": str,
      "period": {"name": "insample" | "holdout" | "forward_12m" | "forward_36m", "start": ISO, "end": ISO},
      "row": str,                          # "primary", an exploratory row's name, or "<row>/<pair>"
      "n_days": int,                       # return days
      "v1": {"sharpe": float | null, "sharpe_ci": [lo, hi], "p": float | null, "passes": bool},
      "v2": {"hurdle_annual", "ann_excess", "ann_excess_ci": [lo, hi], "p", "passes": bool,
             "reading": "above_hurdle" | "not_proven" | "below_hurdle", "ann_return", "ann_return_ci": [lo, hi]},
      "edge": bool,
      "reading": "edge" | "positive_below_hurdle" | "no_edge",
      "hurdles": [v2-shaped block at each hurdle.descriptive value, in order],   # descriptive
      "diagnostics": {                     # descriptive (carry/diagnostics.py); the blocks the caller computed
        "annual": {...},                   # annual_stats
        "decomposition": {...},            # decomposition: "total", "per_year", "per_pair"
        "margin": {...},                   # margin_events: "rebalances", "liquidations"
        "reprice": [...],                  # reprice cells
        "filter_value": {...},             # primary only
        "random_timing": {...},            # primary only (summary; the per-path table is the caller's)
        "dsr": {...}                       # primary only
      }
    }

``ann_return`` is ``365 × mean(ret)`` (arithmetic, V2's statistic); its CI is the excess CI shifted by
``365 × h_d``. The diagnostics blocks' fields are documented on their functions in ``carry/diagnostics.py``.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from perpbt.carry.prereg import CarryPrereg
from perpbt.carry.screen import DAYS_PER_YEAR, daily_hurdle, v1_stats, v2_stats
from perpbt.stats.seeds import rng_for

SCHEMA = "carry-stats/1"
READINGS = ("edge", "positive_below_hurdle", "no_edge")
PERIODS = ("insample", "holdout", "forward_12m", "forward_36m")

__all__ = [
    "DAYS_PER_YEAR", "PERIODS", "READINGS", "SCHEMA", "Seeds", "daily_hurdle", "evaluate", "evaluate_row",
    "reading", "stats_document", "stats_json", "v1_stats", "v2_stats",
]


def reading(v1_passes: bool, v2_passes: bool) -> str:
    """``edge`` when both pass, ``positive_below_hurdle`` when only V1 passes, else ``no_edge``."""
    if v1_passes and v2_passes:
        return "edge"
    return "positive_below_hurdle" if v1_passes else "no_edge"


@dataclass(frozen=True)
class Seeds:
    """The generators of one period: ``rng_for(master_seed, prereg_hash, "<period>/<parts…>")``."""

    master_seed: int
    prereg_hash: str
    period: str

    def __post_init__(self) -> None:
        if self.period not in PERIODS:
            raise ValueError(f"Seeds.period: expected one of {list(PERIODS)}, got {self.period!r}")

    @classmethod
    def of(cls, prereg: CarryPrereg, period: str) -> Seeds:
        return cls(prereg.stats.master_seed, prereg.prereg_hash(), period)

    def purpose(self, *parts: str) -> str:
        if not parts or not all(isinstance(p, str) and p for p in parts):
            raise ValueError(f"a purpose needs non-empty parts, got {parts!r}")
        return "/".join((self.period, *parts))

    def rng(self, *parts: str) -> np.random.Generator:
        return rng_for(self.master_seed, self.prereg_hash, self.purpose(*parts))


def _returns(ret) -> np.ndarray:
    r = np.asarray(ret, dtype=np.float64)
    if r.ndim != 1 or len(r) < 2 or not np.isfinite(r).all():
        raise ValueError(f"daily returns must be a finite 1-D series of at least 2 days, got shape {r.shape}")
    return r


def evaluate(
    ret, *, alpha: float, B: int, block_len: int, hurdle: float, descriptive: Sequence[float],
    rng_v1: Callable[[], np.random.Generator], rng_v2: Callable[[], np.random.Generator],
) -> dict:
    """V1, V2, the verdict and the descriptive hurdle table of one row's daily net returns.

    ``rng_v1()`` / ``rng_v2()`` return a fresh generator of the row's V1 / V2 purpose; V2 and every descriptive
    hurdle draw from their own fresh ``rng_v2()``, so they resample the same blocks.
    """
    r = _returns(ret)
    v1 = v1_stats(r, alpha=alpha, B=B, block_len=block_len, rng=rng_v1())
    v2 = v2_stats(r, hurdle=hurdle, alpha=alpha, B=B, block_len=block_len, rng=rng_v2())
    hurdles = [v2_stats(r, hurdle=h, alpha=alpha, B=B, block_len=block_len, rng=rng_v2()) for h in descriptive]
    return {"n_days": len(r), "v1": v1, "v2": v2, "edge": bool(v1["passes"] and v2["passes"]),
            "reading": reading(v1["passes"], v2["passes"]), "hurdles": hurdles}


def evaluate_row(ret, prereg: CarryPrereg, seeds: Seeds, row: str) -> dict:
    """``evaluate`` with the prereg's ``stats`` and ``hurdle`` blocks and the row's seeds."""
    s, h = prereg.stats, prereg.hurdle
    return evaluate(ret, alpha=s.alpha, B=s.bootstrap_n, block_len=s.block_len_days, hurdle=h.annual,
                    descriptive=h.descriptive, rng_v1=lambda: seeds.rng(row, "v1"),
                    rng_v2=lambda: seeds.rng(row, "v2"))


def stats_document(*, prereg_hash: str, period: str, start: str, end: str, row: str, verdict: Mapping,
                   diagnostics: Mapping) -> dict:
    """The ``stats.json`` content of one row (schema in the module docstring)."""
    if period not in PERIODS:
        raise ValueError(f"period: expected one of {list(PERIODS)}, got {period!r}")
    return {
        "schema": SCHEMA, "prereg_hash": prereg_hash, "period": {"name": period, "start": start, "end": end},
        "row": row, "n_days": verdict["n_days"], "v1": verdict["v1"], "v2": verdict["v2"],
        "edge": verdict["edge"], "reading": verdict["reading"], "hurdles": verdict["hurdles"],
        "diagnostics": dict(diagnostics),
    }


def _plain(x):
    """JSON-ready: numpy scalars to Python, tuples and arrays to lists, non-finite floats to None."""
    if isinstance(x, Mapping):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, np.ndarray)):
        return [_plain(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(x) else None
    return x


def stats_json(doc: Mapping) -> str:
    """``doc`` as canonical text: ``indent=2``, insertion order, no NaN or infinity, a final newline."""
    return json.dumps(_plain(doc), indent=2, allow_nan=False) + "\n"
