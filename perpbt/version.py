"""Code identity (D15).

``code_version`` is the SHA-256 of the ``perpbt`` source tree, so edits to
docs, tests, or configs do not change ``variant_id`` and the run cache stays
valid. ``git_commit`` is recorded alongside, best effort.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def code_version(root: Path | str = PACKAGE_DIR) -> str:
    """SHA-256 over every ``*.py`` under ``root``.

    Files are visited in sorted order of their POSIX relative path (``/``
    separators on every OS). For each file the hash absorbs the relative path
    as UTF-8, a NUL byte, the file bytes, and a NUL byte. Nothing else counts:
    ``.pyc``, docs, tests, and configs leave the hash unchanged.
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"code_version: {root} is not a directory")
    entries = sorted(
        (p.relative_to(root).as_posix(), p) for p in root.rglob("*.py") if p.is_file() and p.suffix == ".py"
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
