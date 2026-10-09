"""The run registry and the results table (spec §6.3).

``runs/registry.jsonl`` is append-only: every attempt appends a ``started``
row and then an ``ok`` or ``failed`` row (with the traceback), so the
number of ``started`` rows is the number of attempts and a killed process
leaves a ``started`` row without a final one. ``kind`` tells a simulation
run (``run``) from a baselines pass (``baselines``); ``batch`` is one id
per CLI invocation; ``reason`` carries a holdout code-change reason.
Worker processes append under an OS file lock, so lines never interleave.

``runs/results.parquet`` is derived: ``rebuild_results`` reads every
``runs/<variant_id>/stats.json`` (with its ``config.yaml``) and takes
``run_ms``, ``git_commit`` and ``runtime_s`` from the variant's latest
``ok`` run row.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from perpbt.config import VariantConfig, load_yaml
from perpbt.stats.variant import read_stats
from perpbt.strategy.order_block import SKIP_REASONS

REGISTRY_FILE = "registry.jsonl"
RESULTS_FILE = "results.parquet"
STATUSES = ("started", "ok", "failed")
KINDS = ("run", "baselines")
SKIP_COLUMNS = tuple(f"skip_{r}" for r in (*SKIP_REASONS, "leverage"))

_S, _I, _F, _B = pa.string(), pa.int64(), pa.float64(), pa.bool_()
RESULTS_SCHEMA = pa.schema(
    [("variant_id", _S), ("run_ms", _I), ("code_version", _S), ("git_commit", _S), ("seed", _I),
     ("params_json", _S), ("pair", _S), ("session_variant", _S), ("period_start", _S), ("period_end", _S),
     ("is_holdout", _B), ("is_primary", _B), ("members", _S),
     ("n_sessions", _I), ("n_impulses", _I), ("n_blocks_seen", _I), ("n_orders", _I), ("n_filled", _I),
     ("fill_rate", _F), ("n_trades", _I), ("n_trades_r", _I)]
    + [(c, _I) for c in SKIP_COLUMNS]
    + [(c, _F) for c in ("win_rate", "mean_gross_r", "mean_net_r", "mean_net_r_ci_lo", "mean_net_r_ci_hi",
                         "mean_net_r_ci_block_lo", "mean_net_r_ci_block_hi", "sharpe_ann", "sharpe_ci_lo",
                         "sharpe_ci_hi", "max_dd", "exposure_time", "exposure_notional", "avg_hold_min", "avg_cost_r",
                         "share_cost_r_gt_1")]
    + [("max_concurrent", _I)]
    + [(c, _F) for c in ("p_a", "z_a", "p_b", "z_b")] + [("n_baseline_runs", _I)]
    + [(c, _F) for c in ("p_a_adj", "p_b_adj", "p_bh_adj", "bh_sharpe", "sharpe_diff", "sharpe_diff_ci_lo",
                         "sharpe_diff_ci_hi", "p_bh", "dsr_local", "dsr_global")]
    + [("reprice_json", _S), ("runtime_s", _F), ("artifacts_path", _S)]
)
RESULT_COLUMNS = tuple(RESULTS_SCHEMA.names)


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


def make_row(cfg: VariantConfig, variant_id: str, *, kind: str, status: str, code_version: str,
             git_commit: str | None, artifacts_path: str, batch: str, error: str | None = None,
             reason: str | None = None, runtime_s: float | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"make_row: kind must be one of {KINDS}, got {kind!r}")
    if status not in STATUSES:
        raise ValueError(f"make_row: status must be one of {STATUSES}, got {status!r}")
    return {
        "variant_id": variant_id, "kind": kind, "status": status, "run_ms": now_ms(),
        "code_version": code_version, "git_commit": git_commit, "seed": cfg.stats.master_seed,
        "is_holdout": cfg.is_holdout, "params_json": cfg.canonical_json(), "artifacts_path": artifacts_path,
        "error": error, "reason": reason, "batch": batch, "runtime_s": runtime_s,
    }


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


# --- results.parquet -----------------------------------------------------------------------------

def _get(d: dict | None, *keys):
    for k in keys:
        if d is None:
            return None
        d = d.get(k)
    return d


def results_row(stats: dict, cfg: VariantConfig, reg: dict | None, artifacts_path: str) -> dict:
    s, h, b, bh, skips = stats["summary"], stats["headline"], stats["baselines"], stats["buy_and_hold"], stats["skips"]
    exp, holm = stats.get("experiment") or {}, stats.get("holm")
    row = {
        "variant_id": stats["variant_id"], "run_ms": _get(reg, "run_ms"),
        "code_version": exp.get("code_version"), "git_commit": _get(reg, "git_commit"),
        "seed": cfg.stats.master_seed, "params_json": cfg.canonical_json(),
        "pair": stats["pair"], "session_variant": stats["session_variant"],
        "period_start": cfg.period_start, "period_end": cfg.period_end, "is_holdout": cfg.is_holdout,
        "is_primary": exp.get("is_primary"), "members": json.dumps(exp.get("members", [])),
        "n_sessions": stats["n_sessions"], "n_impulses": skips.get("impulses"),
        "n_blocks_seen": skips.get("blocks_seen"), "n_orders": s["n_orders"], "n_filled": s["n_filled"],
        "fill_rate": s["fill_rate"], "n_trades": s["n_trades"], "n_trades_r": s["n_trades_r"],
        **{c: skips.get(c[len("skip_"):]) for c in SKIP_COLUMNS},
        "win_rate": h["win_rate"], "mean_gross_r": h["mean_gross_r"], "mean_net_r": h["mean_net_r"],
        "mean_net_r_ci_lo": h["mean_net_r_ci"]["lo"], "mean_net_r_ci_hi": h["mean_net_r_ci"]["hi"],
        "mean_net_r_ci_block_lo": h["mean_net_r_ci_block"]["lo"],
        "mean_net_r_ci_block_hi": h["mean_net_r_ci_block"]["hi"],
        "sharpe_ann": h["sharpe_ann"], "sharpe_ci_lo": h["sharpe_ci"]["lo"], "sharpe_ci_hi": h["sharpe_ci"]["hi"],
        "max_dd": h["max_dd"], "exposure_time": h["exposure_time"], "exposure_notional": h["exposure_notional"],
        "avg_hold_min": h["avg_hold_min"], "avg_cost_r": h["avg_cost_r"],
        "share_cost_r_gt_1": stats["costs"]["share_cost_r_gt_1"], "max_concurrent": h["max_concurrent"],
        "p_a": b["p_a"], "z_a": b["z_a"], "p_b": b["p_b"], "z_b": b["z_b"], "n_baseline_runs": b["n_runs"],
        "p_a_adj": _get(holm, "p_a_adj"), "p_b_adj": _get(holm, "p_b_adj"), "p_bh_adj": _get(holm, "p_bh_adj"),
        "bh_sharpe": bh["sharpe"], "sharpe_diff": bh["sharpe_diff"], "sharpe_diff_ci_lo": bh["sharpe_diff_ci_lo"],
        "sharpe_diff_ci_hi": bh["sharpe_diff_ci_hi"], "p_bh": bh["p_bh"],
        "dsr_local": _get(stats, "dsr", "local", "dsr"), "dsr_global": _get(stats, "dsr", "global", "dsr"),
        "reprice_json": json.dumps(stats["repricing"], sort_keys=True),
        "runtime_s": _get(reg, "runtime_s"), "artifacts_path": artifacts_path,
    }
    return row


def results_rows(runs_dir: str | Path) -> list[dict]:
    """One row per ``runs/<variant_id>/stats.json``, sorted by holdout flag, pair, session, primary first, id."""
    runs_dir = Path(runs_dir)
    reg = Registry(runs_dir).latest_ok()
    rows = []
    for path in sorted(runs_dir.glob("*/stats.json")):
        stats = read_stats(path)
        cfg = load_yaml(path.parent / "config.yaml", VariantConfig)
        rows.append(results_row(stats, cfg, reg.get(stats["variant_id"]), path.parent.as_posix()))
    rows.sort(key=lambda r: (r["is_holdout"], r["pair"], r["session_variant"], not r["is_primary"], r["variant_id"]))
    return rows


def results_table(runs_dir: str | Path) -> pa.Table:
    return pa.Table.from_pylist(results_rows(runs_dir), schema=RESULTS_SCHEMA)


def rebuild_results(runs_dir: str | Path) -> Path:
    """Write ``runs/results.parquet`` from every ``stats.json`` (spec §6.3)."""
    runs_dir = Path(runs_dir)
    table = results_table(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    out = runs_dir / RESULTS_FILE
    pq.write_table(table, out)
    return out


def read_results(runs_dir: str | Path) -> pd.DataFrame:
    return pq.read_table(Path(runs_dir) / RESULTS_FILE).to_pandas()
