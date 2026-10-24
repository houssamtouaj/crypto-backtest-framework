"""The carry variant set and runner (carry spec Phase 5 §5.1-§5.2).

**Variants.** Every pre-registered row (``diagnostics.row_names``: the primary and the six exploratory rows) for the
portfolio and for each universe member alone: 7 × 11 = 77 per period. A per-pair variant is named ``<row>/<pair>``
(the ``stats.json`` ``row`` and the seed purposes use that name). ``variant_id`` is the SHA-256 of the canonical
JSON of the variant's normalised content (strategy, prereg hash, row, scope, pairs, signal, sleeve) plus the period;
the code version is not part of it (it is in the registry rows), so the locked in-sample run keeps its folder.

**Runner.** ``CarryRun.run(variant)`` runs one variant on PairFrames the caller loaded (``load_frames``, through
``carry.frames.load_pairframe`` only): the executor (``run_path``), V1/V2 and the verdict (``evaluate_row``), every
row's diagnostics (``row_diagnostics``) and, for the portfolio primary, ``primary_diagnostics`` (filter value,
random timing, DSR; it re-runs the seven rows on the portfolio). It writes ``<out_root>/<variant_id>/``:
``daily``, ``daily_pair``, ``fills``, ``events``, ``reprice``, ``random_timing`` (portfolio primary only)
``.parquet``, ``variant.json`` and, last, ``stats.json``. Nothing time- or commit-dependent goes into the folder, so
a rerun is byte-identical. The registry (``runs/carry/registry.jsonl``, ``experiments.registry_log.Registry``) gets
a ``started`` row, then ``ok`` or ``failed`` (with the traceback); ``skip`` records a per-pair variant whose pair
is not live through the period (``skip_reason``). Rows: ``REGISTRY_KEYS``; ``kind`` ``run | holdout | forward``;
``status`` ``started | ok | failed | skipped | output_neutral``.

Folders: in-sample ``runs/carry/<id>/``, holdout ``runs/carry/holdout/<id>/``, forward ``runs/carry/forward/<id>/``.
"""
from __future__ import annotations

import functools
import json
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from perpbt.carry.config import CarrySignalConfig, SleeveConfig
from perpbt.carry.diagnostics import primary_diagnostics, row_diagnostics, row_names
from perpbt.carry.executor import exec_spec, run_path
from perpbt.carry.frames import PairFrame, load_pairframe
from perpbt.carry.prereg import MARKET, CarryPrereg
from perpbt.carry.signals import compute_signals
from perpbt.carry.verdict import PERIODS, Seeds, evaluate_row, stats_document, stats_json
from perpbt.config import ConfigError, DataConfig, _check_iso_date, config_hash, to_dict
from perpbt.data.store import date_ms
from perpbt.experiments.registry_log import Registry, now_ms
from perpbt.version import git_commit

PORTFOLIO = "portfolio"
CARRY_DIR = "carry"
HOLDOUT_DIR = "holdout"
FORWARD_DIR = "forward"
STATS_FILE = "stats.json"
VARIANT_FILE = "variant.json"
TABLES = ("daily", "daily_pair", "fills", "events")
REPRICE_FILE = "reprice.parquet"
TIMING_FILE = "random_timing.parquet"
OUTPUT_FILES = (*(f"{t}.parquet" for t in TABLES), REPRICE_FILE, TIMING_FILE, VARIANT_FILE, STATS_FILE)
KINDS = ("run", "holdout", "forward")
STATUSES = ("started", "ok", "failed", "skipped", "output_neutral")
RUN_MARKET = "um+spot"
REGISTRY_KEYS = (
    "variant_id", "name", "kind", "period", "period_start", "period_end", "market", "status", "code_version",
    "git_commit", "prereg_hash", "batch", "started_ms", "finished_ms", "reason", "error",
)
REPRICE_COLUMNS = (
    "slippage", "spot_taker", "perp_taker", "ann_return", "sharpe", "ann_excess", "ann_excess_ci_lo",
    "ann_excess_ci_hi", "p", "passes", "reading", "fees", "slippage_cost", "entries",
)


@functools.lru_cache(maxsize=1)
def _git_commit() -> str | None:
    return git_commit()


def carry_root(runs_dir: str | Path) -> Path:
    """``<runs_dir>/carry``: the registry, ``results.parquet`` and the in-sample variant folders."""
    return Path(runs_dir) / CARRY_DIR


# --- periods and variants --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Period:
    """A named run window of return days (``name`` in ``verdict.PERIODS``)."""

    name: str
    start: str
    end: str

    def __post_init__(self) -> None:
        if self.name not in PERIODS:
            raise ValueError(f"Period.name: expected one of {list(PERIODS)}, got {self.name!r}")
        _check_iso_date(self.start, "Period.start")
        _check_iso_date(self.end, "Period.end")
        if self.end < self.start:
            raise ValueError(f"Period: end {self.end} is before start {self.start}")

    def content(self) -> dict:
        return {"name": self.name, "start": self.start, "end": self.end}


def insample_period(prereg: CarryPrereg) -> Period:
    return Period("insample", prereg.periods.insample_start, prereg.periods.insample_end)


def holdout_period(prereg: CarryPrereg) -> Period:
    return Period("holdout", prereg.periods.holdout_start, prereg.periods.holdout_end)


@dataclass(frozen=True)
class Variant:
    """One pre-registered row on the portfolio (``scope == PORTFOLIO``) or on one pair (``scope`` the pair)."""

    row: str
    scope: str
    pairs: tuple[str, ...]
    signal: CarrySignalConfig
    sleeve: SleeveConfig

    @property
    def name(self) -> str:
        return self.row if self.scope == PORTFOLIO else f"{self.row}/{self.scope}"

    @property
    def is_primary_portfolio(self) -> bool:
        return self.row == "primary" and self.scope == PORTFOLIO

    def content(self, prereg_hash: str, period: Period) -> dict:
        return {"strategy": "carry", "prereg_hash": prereg_hash, "row": self.row, "scope": self.scope,
                "pairs": list(self.pairs), "signal": to_dict(self.signal), "sleeve": to_dict(self.sleeve),
                "period": period.content()}

    def variant_id(self, prereg_hash: str, period: Period) -> str:
        return config_hash(self.content(prereg_hash, period))


def row_configs(prereg: CarryPrereg) -> dict[str, tuple[CarrySignalConfig, SleeveConfig]]:
    """``row -> (signal, sleeve)`` for every pre-registered row, in ``row_names`` order."""
    out = {"primary": (prereg.signal, prereg.sleeve)}
    out.update({e.name: (e.signal, e.sleeve) for e in prereg.exploratory})
    return {row: out[row] for row in row_names(prereg)}


def variants(prereg: CarryPrereg, rows: Sequence[str] | None = None) -> list[Variant]:
    """The rows (all by default) in ``row_names`` order, each for the portfolio and then each member alone."""
    configs = row_configs(prereg)
    unknown = [r for r in (rows or ()) if r not in configs]
    if unknown:
        raise ValueError(f"unknown row {unknown}; the pre-registered rows are {list(configs)}")
    members = tuple(prereg.universe[MARKET]["members"])
    out = []
    for row, (signal, sleeve) in configs.items():
        if rows is not None and row not in rows:
            continue
        out.append(Variant(row, PORTFOLIO, members, signal, sleeve))
        out.extend(Variant(row, pair, (pair,), signal, sleeve) for pair in members)
    return out


# --- data ------------------------------------------------------------------------------------------------------

def check_data_config(prereg: CarryPrereg, data_cfg: DataConfig) -> None:
    """ConfigError unless the data config's ``insample_end`` and ``warmup_start`` are the prereg's and every member
    has a listing date (as the screen)."""
    p = prereg.periods
    for name in ("insample_end", "warmup_start"):
        if getattr(data_cfg, name) != getattr(p, name):
            raise ConfigError(f"data config {name} {getattr(data_cfg, name)} differs from the prereg's {getattr(p, name)}")
    missing = [pair for pair in prereg.universe[MARKET]["members"] if pair not in data_cfg.listing]
    if missing:
        raise ConfigError(f"data config has no listing date for {missing}")


def load_frames(data_cfg: DataConfig, pairs: Sequence[str], *, end: str,
                allow_holdout: bool = False) -> dict[str, PairFrame]:
    """Every pair's PairFrame through ``end`` (``carry.frames.load_pairframe``; ``allow_holdout`` passed through)."""
    return {pair: load_pairframe(data_cfg, pair, end=end, allow_holdout=allow_holdout) for pair in pairs}


def _iso(ms: int) -> str:
    return pd.Timestamp(int(ms), unit="ms").strftime("%Y-%m-%d")


def skip_reason(frames: Mapping[str, PairFrame], variant: Variant, period: Period) -> str | None:
    """Why a per-pair variant cannot run on ``period``: its pair's frame ends before the period's last day (the
    executor refuses frames that all end early; the portfolio treats such a pair as a delisting). None otherwise."""
    if variant.scope == PORTFOLIO:
        return None
    last = int(frames[variant.scope].day_ms[-1])
    if last < date_ms(period.end):
        return f"{variant.scope}: its frame ends {_iso(last)}, before the period end {period.end}"
    return None


# --- one variant -----------------------------------------------------------------------------------------------

def states(frames: Mapping[str, PairFrame], signal: CarrySignalConfig) -> dict[str, np.ndarray]:
    return {pair: np.asarray(compute_signals(pf, signal).on) for pair, pf in frames.items()}


def reprice_table(cells: Sequence[Mapping]) -> pd.DataFrame:
    """``reprice.parquet``: one row per re-pricing cell (``diagnostics.reprice``), V2 flattened.

    Note for the report (Phase 6, from Phase 4): no cell equals the verdict's costs, because the pre-registered
    slippage is per pair (0.02% BTC/ETH, 0.05% the others) while a cell's slippage applies to every pair.
    """
    def num(x) -> float:
        return float("nan") if x is None else float(x)

    cols: dict[str, list] = {c: [] for c in REPRICE_COLUMNS}
    for c in cells:
        v2 = c["v2"]
        for key, value in (("slippage", c["slippage"]), ("spot_taker", c["spot_taker"]),
                           ("perp_taker", c["perp_taker"]), ("ann_return", c["ann_return"]), ("sharpe", c["sharpe"]),
                           ("ann_excess", v2["ann_excess"]), ("ann_excess_ci_lo", v2["ann_excess_ci"][0]),
                           ("ann_excess_ci_hi", v2["ann_excess_ci"][1]), ("p", v2["p"]), ("fees", c["fees"]),
                           ("slippage_cost", c["slippage_cost"])):
            cols[key].append(num(value))
        cols["passes"].append(bool(v2["passes"]))
        cols["reading"].append(str(v2["reading"]))
        cols["entries"].append(int(c["entries"]))
    dtypes = {"passes": bool, "reading": object, "entries": np.int64}
    return pd.DataFrame({c: np.asarray(cols[c], dtype=dtypes.get(c, np.float64)) for c in REPRICE_COLUMNS})


def evaluate_variant(prereg: CarryPrereg, variant: Variant, period: Period,
                     frames: Mapping[str, PairFrame]) -> tuple[dict[str, pd.DataFrame], dict]:
    """The variant's tables (``TABLES``, ``reprice``, and ``random_timing`` for the portfolio primary) and its
    ``stats.json`` document."""
    fr = {pair: frames[pair] for pair in variant.pairs}
    state = states(fr, variant.signal)
    cfg = exec_spec(prereg, sleeve=variant.sleeve, start=period.start, end=period.end)
    result = run_path(fr, state, cfg)
    seeds = Seeds.of(prereg, period.name)
    verdict = evaluate_row(result.daily["ret"].to_numpy(), prereg, seeds, variant.name)
    diag = row_diagnostics(result, fr, state, cfg, prereg, seeds, variant.name, v1=verdict["v1"])
    tables = {"daily": result.daily, "daily_pair": result.daily_pair, "fills": result.fills, "events": result.events,
              "reprice": reprice_table(diag["reprice"])}
    if variant.is_primary_portfolio:
        results = {}
        for row, (signal, sleeve) in row_configs(prereg).items():
            results[row] = result if row == "primary" else run_path(
                fr, states(fr, signal), exec_spec(prereg, sleeve=sleeve, start=period.start, end=period.end))
        extra, timing = primary_diagnostics(fr, state, cfg, prereg, seeds, results)
        diag = {**diag, **extra}
        tables["random_timing"] = timing
    doc = stats_document(prereg_hash=prereg.prereg_hash(), period=period.name, start=period.start, end=period.end,
                         row=variant.name, verdict=verdict, diagnostics=diag)
    return tables, doc


def _write_frame(df: pd.DataFrame, path: Path) -> None:
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


def _write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


@dataclass
class CarryRun:
    """Runs variants of one period on loaded frames into ``out_root`` and records them in ``registry``."""

    prereg: CarryPrereg
    frames: Mapping[str, PairFrame]
    period: Period
    out_root: Path
    registry: Registry
    kind: str
    batch: str
    code_version: str
    reason: str | None = None
    prereg_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"CarryRun.kind: expected one of {list(KINDS)}, got {self.kind!r}")
        self.out_root = Path(self.out_root)
        self.prereg_hash = self.prereg.prereg_hash()

    def folder(self, variant: Variant) -> Path:
        return self.out_root / variant.variant_id(self.prereg_hash, self.period)

    def row(self, variant: Variant, status: str, *, started_ms: int, finished_ms: int | None = None,
            error: str | None = None, reason: str | None = None) -> dict:
        if status not in STATUSES:
            raise ValueError(f"status: expected one of {list(STATUSES)}, got {status!r}")
        return {
            "variant_id": variant.variant_id(self.prereg_hash, self.period), "name": variant.name, "kind": self.kind,
            "period": self.period.name, "period_start": self.period.start, "period_end": self.period.end,
            "market": RUN_MARKET, "status": status, "code_version": self.code_version, "git_commit": _git_commit(),
            "prereg_hash": self.prereg_hash, "batch": self.batch, "started_ms": started_ms,
            "finished_ms": finished_ms, "reason": self.reason if reason is None else reason, "error": error,
        }

    def run(self, variant: Variant) -> Path:
        """Run and write ``variant``; a failure is recorded (``failed`` with the traceback) and re-raised."""
        out = self.folder(variant)
        started = now_ms()
        self.registry.append(self.row(variant, "started", started_ms=started))
        try:
            tables, doc = evaluate_variant(self.prereg, variant, self.period, self.frames)
            text = stats_json(doc)
            out.mkdir(parents=True, exist_ok=True)
            for name in OUTPUT_FILES:  # nothing stale survives a rerun
                (out / name).unlink(missing_ok=True)
            for name, df in tables.items():
                _write_frame(df, out / f"{name}.parquet")
            _write_text(out / VARIANT_FILE, json.dumps(variant.content(self.prereg_hash, self.period), indent=2) + "\n")
            _write_text(out / STATS_FILE, text)  # last: its presence marks a complete folder
        except Exception:
            self.registry.append(self.row(variant, "failed", started_ms=started, finished_ms=now_ms(),
                                          error=traceback.format_exc()))
            raise
        self.registry.append(self.row(variant, "ok", started_ms=started, finished_ms=now_ms()))
        return out

    def skip(self, variant: Variant, why: str) -> None:
        """Record a variant that is not run (``skip_reason``); nothing is written to its folder."""
        t = now_ms()
        self.registry.append(self.row(variant, "skipped", started_ms=t, finished_ms=t, error=why))

