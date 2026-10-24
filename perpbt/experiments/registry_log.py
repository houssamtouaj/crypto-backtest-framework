"""The append-only run registry (``registry.jsonl``) and its OS file lock, without the first strategy's modules.

``experiments.registry`` re-exports everything here (the first strategy's import paths are unchanged); the carry
strategy imports this module directly, so its scoped code version (carry spec Phase 5 §5.3) does not reach
``stats.variant`` or ``strategy.order_block``.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

REGISTRY_FILE = "registry.jsonl"


def now_ms() -> int:
    return time.time_ns() // 1_000_000


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """An exclusive OS lock on ``<path>.lock``, held across processes; released when the holder dies."""
    fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if os.name == "nt":
            import msvcrt

            while True:
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)  # retries for about 10 s, then raises
                    break
                except OSError:
                    continue
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class Registry:
    """``<runs_dir>/registry.jsonl``: append and read."""

    def __init__(self, runs_dir: str | Path) -> None:
        self.runs_dir = Path(runs_dir)
        self.path = self.runs_dir / REGISTRY_FILE

    def append(self, row: dict) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
        with file_lock(self.path), open(self.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def latest(self, rows: list[dict] | None = None, *, kind: str = "run") -> dict[str, dict]:
        """The last row of ``kind`` per variant id."""
        out: dict[str, dict] = {}
        for r in self.rows() if rows is None else rows:
            if r.get("kind", "run") == kind:
                out[r["variant_id"]] = r
        return out

    def latest_ok(self, *, kind: str = "run") -> dict[str, dict]:
        """The last ``ok`` row of ``kind`` per variant id."""
        out: dict[str, dict] = {}
        for r in self.rows():
            if r.get("kind", "run") == kind and r["status"] == "ok":
                out[r["variant_id"]] = r
        return out
