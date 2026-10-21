"""Code identity (D15).

``code_version`` is the SHA-256 of the ``perpbt`` source tree, so edits to
docs, tests, or configs do not change ``variant_id`` and the run cache stays
valid. ``perpbt/report/`` is left out: it only reads the run outputs, so a
report or summary edit neither invalidates the runs nor counts as a holdout
code change. ``git_commit`` is recorded alongside, best effort.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
EXCLUDED = ("report",)  # top-level subpackages that never affect a run's outputs


def code_version(root: Path | str = PACKAGE_DIR, *, exclude: tuple[str, ...] = EXCLUDED) -> str:
    """SHA-256 over every ``*.py`` under ``root`` outside the top-level directories ``exclude``.

    Files are visited in sorted order of their POSIX relative path (``/``
    separators on every OS). For each file the hash absorbs the relative path
    as UTF-8, a NUL byte, the file bytes, and a NUL byte. Nothing else counts:
    ``.pyc``, docs, tests, and configs leave the hash unchanged.
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"code_version: {root} is not a directory")
    entries = sorted(
        (p.relative_to(root).as_posix(), p) for p in root.rglob("*.py")
        if p.is_file() and p.suffix == ".py" and p.relative_to(root).parts[0] not in exclude
    )
    h = hashlib.sha256()
    for rel, path in entries:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def git_commit(cwd: Path | str | None = None) -> str | None:
    """``git rev-parse HEAD`` for the repository containing ``cwd`` (default: this package), or None."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd or PACKAGE_DIR,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
