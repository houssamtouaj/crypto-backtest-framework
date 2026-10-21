"""Run variants end to end (spec §6.5).

``run_variant`` loads one variant's market through the guarded stores,
simulates it, computes the Phase 5 statistics and writes
``runs/<variant_id>/``: ``orders, fills, trades, daily, events,
buyhold.parquet``, ``config.yaml`` and, last, ``stats.json`` (the trades
carry the regime labels; with ``baseline_runs`` also the baseline files).
It appends a ``started`` row to the registry, then ``ok`` or ``failed``
with the traceback. Nothing time- or commit-dependent goes into the
variant folder, so a rerun of the same ``variant_id`` is byte-identical.

``run_many`` drives many variants through a ``spawn`` process pool, one
variant per task; each worker caches the loaded data of one pair. A
variant whose ``stats.json`` exists and whose latest run row is ``ok`` is
skipped unless ``force``.

``add_baselines`` is the separate baselines pass: it re-simulates
(deterministic), checks the result against the stored ``stats.json``,
runs baselines A and B, writes ``baseline_a.parquet`` (the slot table),
``baseline_a_runs.parquet`` and ``baseline_b.parquet`` (per-run component
means) and rewrites ``stats.json``. ``apply_holm`` and ``apply_dsr`` add
the family values to ``stats.json`` once their family is complete; any
later rewrite of a ``stats.json`` (a rerun or a baselines pass) drops
them, so the family passes run last.
"""
from __future__ import annotations

import dataclasses
import functools
import logging
import multiprocessing
import time
import traceback
import uuid
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from perpbt.config import DataConfig, VariantConfig, dump_yaml
from perpbt.data.sessions import SessionCalendar
from perpbt.data.store import DAY_MS, CandleStore, FundingStore, date_ms
from perpbt.execution.simulator import SimResult, run
from perpbt.experiments.grid import GridVariant
from perpbt.experiments.registry import Registry, make_row, rebuild_results
from perpbt.stats.buyhold import bh_daily
from perpbt.stats.dsr import dsr_from_trials
from perpbt.stats.multiplicity import bonferroni, holm
from perpbt.stats.regimes import label_regimes
from perpbt.stats.variant import Baselines, Market, compute_stats, read_stats, run_baselines, sanitize, write_stats
from perpbt.strategy.order_block import OrderBlockStrategy
from perpbt.version import code_version, git_commit

log = logging.getLogger(__name__)

STATS_FILE = "stats.json"
CONFIG_FILE = "config.yaml"
BASELINE_FILES = ("baseline_a.parquet", "baseline_a_runs.parquet", "baseline_b.parquet")


class FamilyIncomplete(RuntimeError):
    """A family pass (Holm, DSR) found members without the inputs it needs."""


def new_batch() -> str:
    """One id per CLI invocation, recorded on every registry row it writes."""
    return uuid.uuid4().hex[:12]


@functools.lru_cache(maxsize=1)
def _git_commit() -> str | None:
    return git_commit()


@functools.lru_cache(maxsize=1)
def _code_version() -> str:
    return code_version()


def variant_dir(runs_dir: str | Path, variant_id: str) -> Path:
    return Path(runs_dir) / variant_id


# --- loading -------------------------------------------------------------------------------------

_CACHE: dict[tuple, object] = {}  # per process: the loaded arrays of one pair


def _cached(key: tuple, load):
    pair = key[1]
    for k in [k for k in _CACHE if k[1] != pair]:
        del _CACHE[k]  # keep one pair in memory; tasks are sorted by pair
    if key not in _CACHE:
        _CACHE[key] = load()
    return _CACHE[key]


def load_market(cfg: VariantConfig, data_cfg: DataConfig, *, allow_holdout: bool = False) -> Market:
    """15m candles from ``warmup_start`` (1m from ``period_start`` when ``use_1m``) and funding to ``period_end``.

    The stores' holdout guard applies unless ``allow_holdout`` (passed only
    by ``experiments/holdout.py``); the cache key includes the flag.
    """
    pair = cfg.pair
    start, end = date_ms(cfg.period_start), date_ms(cfg.period_end) + DAY_MS
    warm = min(date_ms(data_cfg.warmup_start), start)
    candles, funding = CandleStore(data_cfg), FundingStore(data_cfg)
    root = data_cfg.data_dir
    c15 = _cached(("15m", pair, root, warm, end, allow_holdout),
                  lambda: candles.load(pair, "15m", warm, end, allow_holdout=allow_holdout))
    m1 = None
    if cfg.exec.use_1m:
        m1 = _cached(("1m", pair, root, start, end, allow_holdout),
                     lambda: candles.load(pair, "1m", start, end, allow_holdout=allow_holdout))
    fund = _cached(("funding", pair, root, start, end, allow_holdout),
                   lambda: funding.load(pair, start, end, allow_holdout=allow_holdout))
    for tf, cd in (("15m", c15), ("1m", m1)):
        if cd is not None and len(cd.slice(start, end)) == 0:
            raise ValueError(f"load_market: no {pair} {tf} candles in {cfg.period_start}..{cfg.period_end}")
    listing = data_cfg.listing.get(pair)
    return Market(c15, m1, fund, SessionCalendar(cfg.session, c15.ts), start, end,
                  listing_ms=date_ms(listing) if listing else None)


def simulate(cfg: VariantConfig, market: Market, variant_id: str) -> SimResult:
    return run(
        market.candles15, market.candles1m if cfg.exec.use_1m else None, market.funding, market.calendar,
        OrderBlockStrategy(cfg.params), cfg.exec, market.period_start_ms, market.period_end_ms,
        variant_id=variant_id,
    )


# --- writing -------------------------------------------------------------------------------------

def _write_frame(df, path: Path) -> None:
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


def _write_baselines(out: Path, base: Baselines) -> None:
    _write_frame(base.table_a.to_frame(), out / "baseline_a.parquet")
    _write_frame(base.runs_a, out / "baseline_a_runs.parquet")
    _write_frame(base.runs_b, out / "baseline_b.parquet")


def _write_variant(out: Path, cfg: VariantConfig, result: SimResult, market: Market, stats: dict,
                   base: Baselines | None) -> None:
    labelled = label_regimes(result.trades, market.candles15, stats["insample_ref"]["vol_median"])
    dataclasses.replace(result, trades=labelled).to_parquet(out)
    s = result.summary
    _write_frame(bh_daily(market.candles15, market.funding, int(s["first_idx"]), int(s["last_idx"])),
                 out / "buyhold.parquet")
    if base is not None:
        _write_baselines(out, base)
    dump_yaml(cfg, out / CONFIG_FILE)
    write_stats(stats, out / STATS_FILE)  # last: its presence marks a complete folder


# --- one variant ---------------------------------------------------------------------------------

def run_variant(
    cfg: VariantConfig,
    data_cfg: DataConfig,
    *,
    runs_dir: str | Path,
    labels: dict | None = None,
    baseline_runs: int | None = None,
    insample_ref: dict | None = None,
    allow_holdout: bool = False,
    reason: str | None = None,
    batch: str = "",
    cv: str | None = None,
) -> Path:
    """Simulate, compute statistics (and baselines when ``baseline_runs``) and write ``runs/<variant_id>/``.

    The master seed is ``cfg.stats.master_seed`` (part of the variant id).
    ``insample_ref`` is the in-sample cell's reference for a holdout run.
    ``cv`` is the code version the caller computed (a driver passes its own,
    so every worker writes under the ids the driver looks up). Raises after
    recording a ``failed`` row.
    """
    cv = cv or _code_version()
    vid = cfg.variant_id(cv)
    out = variant_dir(runs_dir, vid)
    reg = Registry(runs_dir)
    row = functools.partial(make_row, cfg, vid, kind="run", code_version=cv, git_commit=_git_commit(),
                            artifacts_path=out.as_posix(), batch=batch, reason=reason)
    reg.append(row(status="started"))
    t0 = time.perf_counter()
    try:
        out.mkdir(parents=True, exist_ok=True)
        for name in (STATS_FILE, *BASELINE_FILES):  # nothing stale survives a rerun
            (out / name).unlink(missing_ok=True)
        market = load_market(cfg, data_cfg, allow_holdout=allow_holdout)
        result = simulate(cfg, market, vid)
        base = run_baselines(result, market, cfg, variant_id=vid, n_runs=baseline_runs) if baseline_runs else None
        stats = compute_stats(result, market, cfg, variant_id=vid, baselines=base, insample_ref=insample_ref)
        stats["experiment"] = sanitize({**(labels or {}), "code_version": cv})
        _write_variant(out, cfg, result, market, stats, base)
    except BaseException:
        reg.append(row(status="failed", error=traceback.format_exc(), runtime_s=time.perf_counter() - t0))
        raise
    reg.append(row(status="ok", runtime_s=time.perf_counter() - t0))
    return out


def add_baselines(
    cfg: VariantConfig,
    data_cfg: DataConfig,
    *,
    runs_dir: str | Path,
    n_runs: int | None = None,
    batch: str = "",
    cv: str | None = None,
) -> Path:
    """The baselines pass for one stored in-sample variant (default budget ``cfg.stats.baseline_runs``)."""
    cv = cv or _code_version()
    vid = cfg.variant_id(cv)
    out = variant_dir(runs_dir, vid)
    old = read_stats(out / STATS_FILE)  # FileNotFoundError: run the variant first
    reg = Registry(runs_dir)
    row = functools.partial(make_row, cfg, vid, kind="baselines", code_version=cv, git_commit=_git_commit(),
                            artifacts_path=out.as_posix(), batch=batch)
    reg.append(row(status="started"))
    t0 = time.perf_counter()
    try:
        market = load_market(cfg, data_cfg)
        result = simulate(cfg, market, vid)
        if sanitize(result.summary) != old["summary"]:
            raise RuntimeError(f"{vid}: the re-simulation differs from the stored stats.json; rerun the variant")
        base = run_baselines(result, market, cfg, variant_id=vid, n_runs=n_runs or cfg.stats.baseline_runs)
        stats = compute_stats(result, market, cfg, variant_id=vid, baselines=base, insample_ref=old["insample_ref"])
        stats["experiment"] = old.get("experiment")
        (out / STATS_FILE).unlink()
        _write_baselines(out, base)
        write_stats(stats, out / STATS_FILE)
    except BaseException:
        reg.append(row(status="failed", error=traceback.format_exc(), runtime_s=time.perf_counter() - t0))
        raise
    reg.append(row(status="ok", runtime_s=time.perf_counter() - t0))
    return out


# --- many variants -------------------------------------------------------------------------------

@dataclass(frozen=True)
class Task:
    kind: str  # "run" | "baselines"
    cfg: VariantConfig
    labels: dict
    data_cfg: DataConfig
    runs_dir: str
    batch: str
    cv: str
    n_runs: int | None = None


def execute(task: Task) -> tuple[str, str, float, str | None]:
    """Run one task (a module-level function, so ``spawn`` workers can import it): ``(id, status, s, error)``."""
    t0 = time.perf_counter()
    vid = task.cfg.variant_id(task.cv)
    try:
        if task.kind == "run":
            run_variant(task.cfg, task.data_cfg, runs_dir=task.runs_dir, labels=task.labels,
                        baseline_runs=task.n_runs, batch=task.batch, cv=task.cv)
        else:
            add_baselines(task.cfg, task.data_cfg, runs_dir=task.runs_dir, n_runs=task.n_runs, batch=task.batch,
                          cv=task.cv)
    except Exception as e:  # recorded in the registry with the traceback
        return vid, "failed", time.perf_counter() - t0, f"{type(e).__name__}: {e}"
    return vid, "ok", time.perf_counter() - t0, None


def _drive(tasks: list[Task], workers: int) -> dict[str, list[str]]:
    done: dict[str, list[str]] = {"ok": [], "failed": []}
    n = len(tasks)

    def report(k: int, task: Task, res: tuple) -> None:
        vid, status, secs, err = res
        done[status].append(vid)
        log.info("[%d/%d] %s %s %s %s %.1fs%s", k, n, task.kind, status, task.cfg.pair, task.cfg.session.name,
                 secs, f"  {vid[:12]}: {err}" if err else f"  {vid[:12]}")

    if workers <= 1 or n <= 1:
        for k, task in enumerate(tasks, start=1):
            report(k, task, execute(task))
        return done
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=min(workers, n), mp_context=ctx) as pool:
        futures = {pool.submit(execute, t): t for t in tasks}
        for k, fut in enumerate(as_completed(futures), start=1):
            task = futures[fut]
            try:
                res = fut.result()
            except BrokenProcessPool as e:  # a worker died (e.g. out of memory): the rest fail, nothing aborts
                res = (task.cfg.variant_id(task.cv), "failed", 0.0, f"BrokenProcessPool: {e}")
            report(k, task, res)
    return done


def run_many(
    variants: Sequence[GridVariant],
    data_cfg: DataConfig,
    *,
    runs_dir: str | Path,
    workers: int = 4,
    force: bool = False,
    batch: str | None = None,
) -> dict[str, list[str]]:
    """Run every variant not already done (resume), then rebuild ``results.parquet``; ids by outcome."""
    runs_dir = Path(runs_dir)
    batch = batch or new_batch()
    cv = _code_version()
    latest = Registry(runs_dir).latest(kind="run")
    tasks, skipped = [], []
    for v in variants:
        vid = v.cfg.variant_id(cv)
        if (not force and (variant_dir(runs_dir, vid) / STATS_FILE).exists()
                and latest.get(vid, {}).get("status") == "ok"):
            skipped.append(vid)
            continue
        tasks.append(Task("run", v.cfg, v.labels(), data_cfg, str(runs_dir), batch, cv))
    tasks.sort(key=lambda t: t.cfg.pair)  # stable: a worker's cache sees one pair at a time
    log.info("run: %d to run, %d already done", len(tasks), len(skipped))
    done = _drive(tasks, workers)
    rebuild_results(runs_dir, cv)
    return {**done, "skipped": skipped}


def baselines_many(
    variants: Sequence[GridVariant],
    data_cfg: DataConfig,
    *,
    runs_dir: str | Path,
    n_runs: int | None = None,
    workers: int = 4,
    force: bool = False,
    batch: str | None = None,
) -> dict[str, list[str]]:
    """The baselines pass over stored variants; a variant whose ``stats.json`` has the budget already is skipped."""
    runs_dir = Path(runs_dir)
    batch = batch or new_batch()
    cv = _code_version()
    tasks, skipped, missing = [], [], []
    for v in variants:
        vid = v.cfg.variant_id(cv)
        path = variant_dir(runs_dir, vid) / STATS_FILE
        if not path.exists():
            missing.append(vid)
            continue
        m = n_runs or v.cfg.stats.baseline_runs
        if not force and read_stats(path)["baselines"]["n_runs"] == m:
            skipped.append(vid)
            continue
        tasks.append(Task("baselines", v.cfg, v.labels(), data_cfg, str(runs_dir), batch, cv, m))
    tasks.sort(key=lambda t: t.cfg.pair)
    log.info("baselines: %d to run, %d already done, %d without a run", len(tasks), len(skipped), len(missing))
    done = _drive(tasks, workers)
    rebuild_results(runs_dir, cv)
    return {**done, "skipped": skipped, "missing": missing}


# --- family passes -------------------------------------------------------------------------------

def _family_stats(runs_dir: Path, variant_ids: Sequence[str], what: str) -> list[dict]:
    missing = [v for v in variant_ids if not (variant_dir(runs_dir, v) / STATS_FILE).exists()]
    if missing:
        raise FamilyIncomplete(f"{what}: {len(missing)} of {len(variant_ids)} variants have no stats.json "
                               f"(first {missing[0][:12]})")
    return [read_stats(variant_dir(runs_dir, v) / STATS_FILE) for v in variant_ids]


def apply_holm(runs_dir: str | Path, variant_ids: Sequence[str], *, family: str, alpha: float,
               baseline_runs: int) -> list[dict]:
    """Holm (and Bonferroni, for reference) over one family per benchmark (A, B, buy-and-hold); spec §5.8, D11.

    Refuses until every member has baselines at the pre-registered budget
    ``baseline_runs``. Writes a ``holm`` block into each member's
    ``stats.json`` and returns the blocks in input order. ``n`` is the
    family size; ``n_tested`` counts, per benchmark, the members with a
    p-value (a missing one is left out of the family, spec §5.8).
    """
    runs_dir = Path(runs_dir)
    stats = _family_stats(runs_dir, variant_ids, "holm")
    without = [s["variant_id"] for s in stats if s["baselines"]["n_runs"] != baseline_runs]
    if without:
        raise FamilyIncomplete(f"holm: {len(without)} of {len(stats)} cells have no baselines at the "
                               f"pre-registered {baseline_runs} runs (first {without[0][:12]}); "
                               "run `perpbt baselines --primary` first")
    p = {"a": [s["baselines"]["p_a"] for s in stats], "b": [s["baselines"]["p_b"] for s in stats],
         "bh": [s["buy_and_hold"]["p_bh"] for s in stats]}
    adj = {k: holm(v) for k, v in p.items()}
    bonf = {k: bonferroni(v) for k, v in p.items()}
    blocks = []
    for i, s in enumerate(stats):
        block = sanitize({
            "family": family, "variant_ids": list(variant_ids), "n": len(variant_ids), "alpha": alpha,
            "baseline_runs": baseline_runs,
            "n_tested": {k: int(sum(x is not None for x in v)) for k, v in p.items()},
            **{f"p_{k}_adj": adj[k][i] for k in p}, **{f"p_{k}_bonf": bonf[k][i] for k in p},
        })
        s["holm"] = block
        write_stats(s, variant_dir(runs_dir, s["variant_id"]) / STATS_FILE)
        blocks.append(block)
    rebuild_results(runs_dir)
    return blocks


def apply_dsr(runs_dir: str | Path, cells: dict[tuple[str, str], Sequence[str]]) -> dict[str, dict]:
    """DSR of every grid variant against its pair × session's trials (``local``) and all of them (``global``).

    ``cells`` maps (pair, session) to the cell's grid variant ids. Refuses
    until every variant has a ``stats.json``. Uses the daily ``dsr.sr`` of
    the trials (spec §5.7).
    """
    runs_dir = Path(runs_dir)
    every = [v for ids in cells.values() for v in ids]
    stats = dict(zip(every, _family_stats(runs_dir, every, "dsr"), strict=True))
    global_srs = [stats[v]["dsr"]["sr"] for v in every]
    out = {}
    for ids in cells.values():
        local_srs = [stats[v]["dsr"]["sr"] for v in ids]
        for v in ids:
            s = stats[v]
            s["dsr"]["local"] = sanitize(dsr_from_trials(s["dsr"], local_srs))
            s["dsr"]["global"] = sanitize(dsr_from_trials(s["dsr"], global_srs))
            write_stats(s, variant_dir(runs_dir, v) / STATS_FILE)
            out[v] = {"local": s["dsr"]["local"], "global": s["dsr"]["global"]}
    rebuild_results(runs_dir)
    return out
