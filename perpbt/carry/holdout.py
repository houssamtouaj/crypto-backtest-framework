"""The one-shot carry holdout (carry spec Phase 5 §5.4): the seven rows on 2024-01-01 .. 2026-10-20, once.

With ``carry/forward.py``, the only carry module that passes ``allow_holdout=True`` (to ``load_pairframe``, through
``runner.load_frames``). ``run_holdout`` refuses (HoldoutRefused), before loading any holdout data, unless:

- the pre-registration is frozen and still hashes to the lock, and the data config matches it;
- ``runs/carry/holdout/DONE`` is absent;
- the code is locked and the in-sample portfolio primary has run (its ``stats.json`` exists);
- the carry code version equals the lock's, or the in-sample primary rerun with the current code is byte-identical
  (output-neutral, recorded in the registry), or ``--allow-code-change`` comes with a ``--reason`` (recorded in
  every registry row and in ``DONE``).

It then loads every member through the holdout end and refuses when a pair's data ends within
``DELISTED_AFTER_DAYS`` of it (a stale fetch; nothing has run, so no ``DONE``). Every variant of every row (the
portfolio and each pair; ``runner.variants``) runs into ``runs/carry/holdout/<id>/``; a per-pair variant whose pair
delisted in the holdout is skipped. The primary's V1/V2 use the frozen rule and costs; everything else is
descriptive. A failing variant does not stop the others, and ``DONE`` (JSON) is written at the end whatever
happened, so a second touch always needs its manual deletion.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from perpbt.carry.prereg import MARKET, check_frozen, carry_code_version, lock_path, read_lock
from perpbt.carry.runner import (
    HOLDOUT_DIR, STATS_FILE, CarryRun, CodeRefused, DataNotCaughtUp, carry_root, check_caught_up,
    check_data_config, code_check, holdout_period, insample_period, insample_primary_identical, load_frames,
    new_batch, primary_variant, rebuild_results, record_code, run_variants, variants,
)
from perpbt.config import ConfigError, DataConfig
from perpbt.experiments.registry_log import Registry

log = logging.getLogger(__name__)

DONE_FILE = "DONE"


class HoldoutRefused(RuntimeError):
    """A precondition of the holdout run does not hold; nothing was run and no DONE was written."""


def done_path(runs_dir: str | Path) -> Path:
    return carry_root(runs_dir) / HOLDOUT_DIR / DONE_FILE


def load_insample(data_cfg: DataConfig, pairs, end: str) -> dict:
    """The in-sample frames for the output-neutral rerun (no holdout data)."""
    return load_frames(data_cfg, pairs, end=end)


def prior_attempts(registry: Registry) -> list[str]:
    """Batches of earlier holdout runs in the registry, in order."""
    out: list[str] = []
    for r in registry.rows():
        if r.get("kind") == "holdout" and r["status"] == "started" and r["batch"] not in out:
            out.append(r["batch"])
    return out


def run_holdout(prereg_path: str | Path, data_cfg: DataConfig, *, runs_dir: str | Path,
                allow_code_change: bool = False, reason: str | None = None,
                code_version: str | None = None) -> dict:
    """Run the holdout once; returns the ``DONE`` content. HoldoutRefused on a failed precondition."""
    try:
        prereg = check_frozen(prereg_path)
        check_data_config(prereg, data_cfg)
    except ConfigError as e:
        raise HoldoutRefused(f"holdout: {e}") from e
    lock = read_lock(lock_path(prereg_path))
    done = done_path(runs_dir)
    if done.exists():
        raise HoldoutRefused(f"holdout: {done.as_posix()} exists; the holdout is touched once")
    if lock["code_version"] is None:
        raise HoldoutRefused("holdout: the carry code is not locked; run `perpbt carry lock-code` first")
    root = carry_root(runs_dir)
    h = prereg.prereg_hash()
    if not (root / primary_variant(prereg).variant_id(h, insample_period(prereg)) / STATS_FILE).exists():
        raise HoldoutRefused("holdout: no in-sample primary stats.json; run `perpbt carry run --primary` first")
    members = prereg.universe[MARKET]["members"]
    cv = code_version or carry_code_version()
    try:
        code = code_check(lock, cv, allow_code_change=allow_code_change, reason=reason,
                          neutral=lambda: insample_primary_identical(
                              prereg, load_insample(data_cfg, members, prereg.periods.insample_end), root))
    except CodeRefused as e:
        raise HoldoutRefused(f"holdout: {e}") from e

    period = holdout_period(prereg)
    frames = load_frames(data_cfg, members, end=period.end, allow_holdout=True)
    try:
        check_caught_up(frames, period.end)
    except DataNotCaughtUp as e:
        raise HoldoutRefused(f"holdout: {e}") from e

    registry = Registry(root)
    prior = prior_attempts(registry)
    if prior:
        log.warning("holdout: %d earlier holdout attempt(s) in the registry: %s", len(prior), ", ".join(prior))
    batch = new_batch()
    record_code(registry, prereg, kind="holdout", batch=batch, code=code)
    run = CarryRun(prereg, frames, period, root / HOLDOUT_DIR, registry, "holdout", batch, cv, reason=code["reason"])
    vs = variants(prereg)
    result = {"ok": [], "skipped": [], "failed": []}
    status = "failed"
    try:
        result = run_variants(run, vs, skip_complete=False)
        status = "failed" if result["failed"] else "ok"
    finally:
        content = {
            "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": status,
            "failed": result["failed"], "skipped": result["skipped"], "batch": batch, "prereg_hash": h,
            "period": [period.start, period.end], "code_version": cv, "lock_code_version": lock["code_version"],
            "output_neutral": code["output_neutral"], "reason": code["reason"], "prior_attempts": prior,
            "variant_ids": [v.variant_id(h, period) for v in vs],
        }
        done.parent.mkdir(parents=True, exist_ok=True)
        with open(done, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(content, indent=2) + "\n")
        rebuild_results(root)
    return content
