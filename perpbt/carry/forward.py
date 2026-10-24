"""Forward reviews of the carry primary (carry spec Phase 5 §5.5; trend Phase 5 §5.6).

With ``carry/holdout.py``, the only carry module that passes ``allow_holdout=True``. ``perpbt carry forward
--review 12|36`` runs the portfolio primary on a fixed window from ``periods.forward.start`` (set by the code lock):
the first 365 days for ``--review 12`` and the first 1,095 for ``--review 36`` (``REVIEW_DAYS``), allowed once the
last complete UTC day reaches the window's end. The window is fixed so that when a review is run cannot choose its
end. The forward period is never pooled with earlier ones: the executor starts flat with the starting equity on the
window's first day. The universe is fixed; a member delisted earlier is never live, one delisting inside the window
exits by the delisting rule; no new members.

- ``--review 12`` (``forward_12m``): descriptive only (``verdict_rule.forward_12m``), no p-value. ``stats.json``
  has schema ``REVIEW_SCHEMA``: ``annual`` (the point Sharpe, no CI), ``decomposition``, ``margin`` and
  ``metrics`` (``review_metrics``): the annualised return, funding, basis, cost drag (fees + slippage), entries a
  year and the share of pair-days on, each against the in-sample primary's per-year range (annualised by
  ``365 / n_days``) with ``inside``.
- ``--review 36`` (``forward_36m``): V1/V2 and the verdict, the rule unchanged; the full ``carry-stats/1``
  document of ``runner.evaluate_variant``.

Refusals (ForwardRefused), before any forward data is loaded: the prereg not frozen or changed; the code not
locked; no forward start; a review that is not pre-registered; too few days; the review already run (its folder
has a ``stats.json``); no in-sample primary ``stats.json``; the code check (as the holdout). Then the tails of both
legs are fetched (``fetch_tails``, unless ``--no-fetch``), the frames loaded, and a pair whose data ends within
``DELISTED_AFTER_DAYS`` of the window end refuses the run. Each run appends its own registry rows (``kind:
forward``) and writes ``runs/carry/forward/<id>/``; earlier forward results are never overwritten.
"""
from __future__ import annotations

import functools
import json
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from perpbt.carry.diagnostics import annual_stats, decomposition, margin_events
from perpbt.carry.executor import exec_spec, run_path
from perpbt.carry.frames import PairFrame
from perpbt.carry.prereg import MARKET, CarryPrereg, carry_code_version, check_frozen, lock_path, read_lock
from perpbt.carry.runner import (
    FORWARD_DIR, STATS_FILE, CarryRun, CodeRefused, DataNotCaughtUp, Period, Variant, carry_root, check_caught_up,
    check_data_config, code_check, insample_period, insample_primary_identical, load_frames, new_batch,
    primary_variant, rebuild_results, record_code, states,
)
from perpbt.carry.screen import DAYS_PER_YEAR
from perpbt.config import ConfigError, DataConfig
from perpbt.data.fetch import run_fetch
from perpbt.data.store import DAY_MS, date_ms
from perpbt.experiments.registry_log import Registry
from perpbt.stats.bootstrap import sharpe

REVIEW_DAYS = {12: 365, 36: 1095}
REVIEW_SCHEMA = "carry-forward-review/1"
METRICS = ("ann_return", "funding", "basis", "cost_drag", "entries_per_year", "share_on")


class ForwardRefused(RuntimeError):
    """A precondition of the forward review does not hold; nothing was run."""


def review_period(prereg: CarryPrereg, months: int) -> Period:
    """The review's fixed window: ``REVIEW_DAYS[months]`` days from ``periods.forward.start``."""
    if months not in prereg.periods.forward_reviews or months not in REVIEW_DAYS:
        raise ForwardRefused(f"forward: --review {months} is not pre-registered (reviews "
                             f"{list(prereg.periods.forward_reviews)})")
    start = prereg.periods.forward_start
    if start is None:
        raise ForwardRefused("forward: periods.forward.start is not set; run `perpbt carry lock-code` first")
    end = date.fromisoformat(start) + timedelta(days=REVIEW_DAYS[months] - 1)
    return Period(f"forward_{months}m", start, end.isoformat())


def fetch_tails(data_cfg: DataConfig, pairs: Sequence[str], **kw) -> dict:
    """Both legs' tails through now: the perps (bulk archive and the ccxt tail, funding included) and the spot pairs
    (bulk archive), 15m only (``data.fetch.run_fetch``)."""
    return {"um": run_fetch(data_cfg, pairs=list(pairs), tfs=("15m",), ccxt_tail=True, market="um", **kw),
            "spot": run_fetch(data_cfg, pairs=list(pairs), tfs=("15m",), market="spot", **kw)}


# --- the 12-month review document --------------------------------------------------------------------------------

def _annualised(block: Mapping) -> dict[str, float | None]:
    n = block["n_days"]
    scale = DAYS_PER_YEAR / n if n else math.nan
    return {"ann_return": block["ret_sum"] * scale, "funding": block["funding"] * scale,
            "basis": block["basis"] * scale, "cost_drag": (block["fees"] + block["slippage"]) * scale,
            "entries_per_year": block["entries"] * scale, "share_on": block["share_on"]}


def review_metrics(forward_total: Mapping, insample_years: Sequence[Mapping]) -> dict:
    """Each of ``METRICS`` on the forward window and the range of its in-sample per-year values (each year
    annualised by ``365 / n_days``; the share on is not annualised); ``inside`` is ``min <= forward <= max``."""
    fwd = _annualised(forward_total)
    years = [_annualised(y) for y in insample_years]
    out = {}
    for name in METRICS:
        vals = [y[name] for y in years if y[name] is not None and math.isfinite(y[name])]
        x = fwd[name]
        lo, hi = (min(vals), max(vals)) if vals else (None, None)
        ok = None if x is None or lo is None or not math.isfinite(x) else bool(lo <= x <= hi)
        out[name] = {"forward": x, "insample_min": lo, "insample_max": hi, "inside": ok}
    return out


def evaluate_review(prereg: CarryPrereg, variant: Variant, period: Period, frames: Mapping[str, PairFrame], *,
                    insample_stats: Mapping) -> tuple[dict, dict]:
    """The 12-month review's tables and document (``REVIEW_SCHEMA``; no p-value)."""
    fr = {pair: frames[pair] for pair in variant.pairs}
    cfg = exec_spec(prereg, sleeve=variant.sleeve, start=period.start, end=period.end)
    result = run_path(fr, states(fr, variant.signal), cfg)
    s = sharpe(result.daily["ret"].to_numpy(dtype=np.float64))
    dec = decomposition(result, equity0=cfg.equity0)
    doc = {
        "schema": REVIEW_SCHEMA, "prereg_hash": prereg.prereg_hash(), "period": period.content(),
        "row": variant.name, "n_days": len(result.daily),
        "note": "descriptive only (verdict_rule.forward_12m): no p-value; the forward period is not pooled",
        "annual": annual_stats(result.daily, equity0=cfg.equity0,
                               v1={"sharpe": s if math.isfinite(s) else None, "sharpe_ci": [None, None]}),
        "decomposition": dec,
        "margin": margin_events(result, equity0=cfg.equity0),
        "metrics": review_metrics(dec["total"], insample_stats["diagnostics"]["decomposition"]["per_year"]),
        "insample_ref": {"period": insample_stats["period"], "years": [
            y["year"] for y in insample_stats["diagnostics"]["decomposition"]["per_year"]]},
    }
    tables = {"daily": result.daily, "daily_pair": result.daily_pair, "fills": result.fills, "events": result.events}
    return tables, doc


# --- the run -----------------------------------------------------------------------------------------------------

def run_forward(prereg_path: str | Path, data_cfg: DataConfig, *, runs_dir: str | Path, review: int,
                today: date | None = None, allow_code_change: bool = False, reason: str | None = None,
                code_version: str | None = None,
                fetch: Callable[[DataConfig, Sequence[str]], object] | None = None) -> Path:
    """Run one forward review of the portfolio primary; returns its folder. ForwardRefused on a failed
    precondition. ``fetch(data_cfg, pairs)`` fetches the tails first (the CLI passes ``fetch_tails``)."""
    try:
        prereg = check_frozen(prereg_path)
        check_data_config(prereg, data_cfg)
    except ConfigError as e:
        raise ForwardRefused(f"forward: {e}") from e
    lock = read_lock(lock_path(prereg_path))
    if lock["code_version"] is None:
        raise ForwardRefused("forward: the carry code is not locked; run `perpbt carry lock-code` first")
    period = review_period(prereg, review)
    today = today or datetime.now(timezone.utc).date()
    last_complete = today - timedelta(days=1)
    if date_ms(period.end) > date_ms(last_complete.isoformat()):
        have = max(0, (date_ms(last_complete.isoformat()) - date_ms(period.start)) // DAY_MS + 1)
        raise ForwardRefused(f"forward: the {review}-month review needs {REVIEW_DAYS[review]} days from "
                             f"{period.start} (through {period.end}); {have} exist through {last_complete}")
    root = carry_root(runs_dir)
    h = prereg.prereg_hash()
    v = primary_variant(prereg)
    out_root = root / FORWARD_DIR
    if (out_root / v.variant_id(h, period) / STATS_FILE).exists():
        raise ForwardRefused(f"forward: the {review}-month review has already run ({period.start}..{period.end})")
    insample_stats_path = root / v.variant_id(h, insample_period(prereg)) / STATS_FILE
    if not insample_stats_path.exists():
        raise ForwardRefused("forward: no in-sample primary stats.json; run `perpbt carry run --primary` first")
    members = prereg.universe[MARKET]["members"]
    cv = code_version or carry_code_version()
    try:
        code = code_check(lock, cv, allow_code_change=allow_code_change, reason=reason,
                          neutral=lambda: insample_primary_identical(
                              prereg, load_frames(data_cfg, members, end=prereg.periods.insample_end), root))
    except CodeRefused as e:
        raise ForwardRefused(f"forward: {e}") from e

    if fetch is not None:
        fetch(data_cfg, members)
    frames = load_frames(data_cfg, members, end=period.end, allow_holdout=True)
    try:
        check_caught_up(frames, period.end)
    except DataNotCaughtUp as e:
        raise ForwardRefused(f"forward: {e}") from e

    registry = Registry(root)
    batch = new_batch()
    record_code(registry, prereg, kind="forward", batch=batch, code=code)
    run = CarryRun(prereg, frames, period, out_root, registry, "forward", batch, cv, reason=code["reason"])
    if review == 12:
        insample_stats = json.loads(insample_stats_path.read_text(encoding="utf-8"))
        out = run.run(v, functools.partial(evaluate_review, insample_stats=insample_stats))
    else:
        out = run.run(v)
    rebuild_results(root)
    return out
