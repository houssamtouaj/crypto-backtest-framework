"""The committed carry pre-registration as it was before the Phase 7 code lock.

The code lock (2026-10-24) set ``periods.forward.start`` to 2026-10-25 in
``configs/carry/prereg.yaml`` and filled the lock's code fields. Tests of
``lock_code``, the runner and the holdout need the frozen-but-unlocked state,
so they build it from the committed files: the forward start back to null,
the code fields back to None. The prereg hash excludes the forward start, so
it still matches the lock.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from perpbt.carry.prereg import lock_path, read_lock

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "configs" / "carry" / "prereg.yaml"
LOCKED_FORWARD = "forward:  {start: 2026-10-25, reviews: [12, 36]}"
CODE_FIELDS = ("code_version", "code_locked_on", "code_git_commit")


def prelock_text() -> str:
    text = PREREG.read_text(encoding="utf-8")
    assert text.count(LOCKED_FORWARD) == 1, "the committed prereg's forward start is not the locked one"
    return text.replace(LOCKED_FORWARD, "forward:  {start: null, reviews: [12, 36]}")


def prelock_raw() -> dict:
    """The parsed pre-lock prereg (a fresh dict on every call)."""
    return yaml.safe_load(prelock_text())


def prelock_lock() -> dict:
    lock = read_lock(lock_path(PREREG))
    return {k: (None if k in CODE_FIELDS else v) for k, v in lock.items()}


def write_prelock(folder: Path) -> Path:
    """``prereg.yaml`` and ``prereg.lock`` in their pre-lock state under ``folder``; returns the prereg path."""
    (folder / "prereg.yaml").write_text(prelock_text(), encoding="utf-8", newline="\n")
    (folder / "prereg.lock").write_text(json.dumps(prelock_lock(), indent=2) + "\n", encoding="utf-8")
    return folder / "prereg.yaml"


__all__ = ["PREREG", "prelock_lock", "prelock_raw", "prelock_text", "write_prelock"]
