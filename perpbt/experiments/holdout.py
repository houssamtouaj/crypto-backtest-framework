"""The one-shot holdout runner (spec §6.6) — the only code that loads data with ``allow_holdout=True``.

``run_holdout`` refuses unless ``configs/prereg.lock`` exists and the
prereg still hashes to it, ``runs/holdout/DONE`` is absent, and the code
version matches the lock (or ``allow_code_change`` with a ``reason``,
which goes into every registry row and ``DONE``). It then runs the nine
primary cells, and only those, over ``holdout.start .. holdout.end`` with
baselines at the primary budget and the in-sample cell's ``insample_ref``
(the in-sample primary ``stats.json`` under the current code must exist),
applies Holm over the holdout family, and writes ``DONE`` with the time,
the status, both hashes and the variant ids. ``DONE`` is written even when
a cell fails, so a second touch always needs its manual deletion; earlier
holdout batches in the registry are reported.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

from perpbt.config import VariantConfig, canonical_json
from perpbt.experiments.grid import primary_variants
from perpbt.experiments.prereg import Prereg, load_prereg, lock_path, read_lock
from perpbt.experiments.registry import Registry
from perpbt.experiments.runner import STATS_FILE, apply_holm, new_batch, run_variant, variant_dir
from perpbt.stats.variant import read_stats
from perpbt.version import code_version

log = logging.getLogger(__name__)

HOLDOUT_DIR = "holdout"
DONE_FILE = "DONE"


class HoldoutRefused(RuntimeError):
    """A precondition of the holdout run does not hold; nothing was loaded."""


def done_path(runs_dir: str | Path) -> Path:
    return Path(runs_dir) / HOLDOUT_DIR / DONE_FILE


def check_holdout_config(cfg: VariantConfig, prereg: Prereg) -> None:
    """Raise HoldoutRefused unless ``cfg`` is one of the nine primary holdout configurations."""
    allowed = {canonical_json(v.cfg) for v in primary_variants(prereg, holdout=True)}
    if canonical_json(cfg) not in allowed:
        raise HoldoutRefused(f"holdout: {cfg.pair} {cfg.session.name} is not a primary holdout configuration")


def prior_attempts(runs_dir: str | Path) -> list[str]:
    """Batches of earlier holdout runs in the registry, in order."""
    batches: list[str] = []
    for r in Registry(runs_dir).rows():
        if r.get("is_holdout") and r.get("kind", "run") == "run" and r["status"] == "started":
            if r.get("batch") not in batches:
                batches.append(r.get("batch"))
    return batches


def run_holdout(
    prereg_path: str | Path,
    *,
    data_dir: str | Path,
    runs_dir: str | Path,
    allow_code_change: bool = False,
    reason: str | None = None,
    current_code_version: str | None = None,
) -> dict:
    """Run the holdout once; returns the ``DONE`` content. HoldoutRefused on a failed precondition."""
    runs_dir = Path(runs_dir)
    lock_file = lock_path(prereg_path)
    if not lock_file.exists():
        raise HoldoutRefused(f"holdout: {lock_file} is missing; freeze the pre-registration first")
    lock = read_lock(lock_file)
    prereg = load_prereg(prereg_path)
    if prereg.prereg_hash() != lock["prereg_hash"]:
        raise HoldoutRefused("holdout: the pre-registration changed after the freeze (prereg_hash mismatch)")
    done = done_path(runs_dir)
    if done.exists():
        raise HoldoutRefused(f"holdout: {done} exists; the holdout is touched once")
    cv = current_code_version or code_version()
    if allow_code_change and not (reason and reason.strip()):
        raise HoldoutRefused("holdout: --allow-code-change needs a --reason")
    if cv != lock["code_version"] and not allow_code_change:
        raise HoldoutRefused("holdout: perpbt/ changed since the freeze; rerun with --allow-code-change --reason")
    if prereg.holdout.end is None:
        raise HoldoutRefused("holdout: holdout.end is not set in the pre-registration")
    reason = reason.strip() if allow_code_change else None

    variants = primary_variants(prereg, holdout=True)
    for v in variants:
        check_holdout_config(v.cfg, prereg)
    refs = []
    for v in primary_variants(prereg):
        path = variant_dir(runs_dir, v.cfg.variant_id(code_version())) / STATS_FILE
        if not path.exists():
            raise HoldoutRefused(f"holdout: no in-sample primary stats.json for {v.cfg.pair} {v.cfg.session.name} "
                                 "under the current code; run `perpbt run --primary` first")
        refs.append(read_stats(path)["insample_ref"])

    prior = prior_attempts(runs_dir)
    if prior:
        log.warning("holdout: %d earlier holdout attempt(s) in the registry: %s", len(prior), ", ".join(prior))
    batch = new_batch()
    data_cfg = prereg.data_config(data_dir)
    ids = [v.cfg.variant_id(code_version()) for v in variants]
    status = "failed"
    try:
        for v, ref in zip(variants, refs, strict=True):
            log.info("holdout: %s %s", v.cfg.pair, v.cfg.session.name)
            run_variant(v.cfg, data_cfg, runs_dir=runs_dir, labels=v.labels(),
                        baseline_runs=prereg.stats.baseline_runs_primary, insample_ref=ref,
                        allow_holdout=True, reason=reason, batch=batch)
        apply_holm(runs_dir, ids, family="holdout", alpha=prereg.stats.alpha)
        status = "ok"
    finally:
        content = {
            "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": status, "batch": batch, "prereg_hash": lock["prereg_hash"], "code_version": cv,
            "lock_code_version": lock["code_version"], "reason": reason, "prior_attempts": prior,
            "variant_ids": ids,
        }
        done.parent.mkdir(parents=True, exist_ok=True)
        with open(done, "w", encoding="utf-8", newline="\n") as f:
            yaml.safe_dump(content, f, sort_keys=False, default_flow_style=False)
    return content
