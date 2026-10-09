"""The pre-registration file, its hash and its lock (spec §6.1).

``load_prereg`` parses ``configs/prereg.yaml`` into a ``Prereg``: every value
that maps to a Phase 0 dataclass goes through that dataclass, so it is
validated and normalised (``r_target: 2`` and ``2.0`` are the same value).
``prereg_hash`` is the SHA-256 of the canonical JSON of that normalised
content without ``data_download_date`` and ``holdout.end``, so comments,
key order and number spelling do not matter while any other edit does.
The YAML loader rejects duplicate keys.

``freeze`` writes ``configs/prereg.lock`` once (Phase 8); ``read_lock``
reads it back for the holdout runner.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from perpbt.config import (
    ConfigError,
    DataConfig,
    ExecConfig,
    SessionSpec,
    StatsConfig,
    StrategyParams,
    VariantConfig,
    _check_iso_date,
    _Config,
    canonical_json,
    config_hash,
    from_dict,
    to_dict,
)
from perpbt.version import code_version, git_commit

__all__ = [
    "KNOWN_SESSIONS",
    "Period",
    "PreregStats",
    "Heatmap",
    "Grid",
    "Prereg",
    "load_prereg",
    "parse_prereg",
    "lock_path",
    "freeze",
    "read_lock",
]

KNOWN_SESSIONS = ("utc", "ny", "london")
TOP_KEYS = (
    "registered_on", "data_download_date", "insample", "holdout", "warmup_start", "pairs", "listing", "sessions",
    "primary", "execution", "grid", "stats", "primary_family", "verdict_rule",
)
LOCK_KEYS = ("frozen_on", "prereg_hash", "code_version", "git_commit", "data_download_date")
PARAM_FIELDS = tuple(f.name for f in fields(StrategyParams))
EXEC_FIELDS = tuple(f.name for f in fields(ExecConfig))


# --- YAML without duplicate keys -----------------------------------------------------------------

class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise ConfigError(f"duplicate key {key!r} at line {key_node.start_mark.line + 1}")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _read_yaml(path: str | Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return yaml.load(f, Loader=_UniqueKeyLoader)  # noqa: S506 - a SafeLoader subclass


# --- parts ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Period(_Config):
    start: str
    end: str | None = None

    def _validate(self) -> None:
        _check_iso_date(self.start, "Period.start")
        if self.end is not None:
            _check_iso_date(self.end, "Period.end")
            if self.end < self.start:
                raise ConfigError(f"Period: end {self.end} is before start {self.start}")


@dataclass(frozen=True)
class PreregStats(_Config):
    bootstrap_n: int
    block_len_days: int
    baseline_runs_primary: int
    baseline_runs_grid: int
    alpha: float
    master_seed: int
    reprice_slippage: tuple[float, ...]
    reprice_maker: tuple[float, ...]

    def _validate(self) -> None:
        self.stats_config(self.baseline_runs_primary)  # StatsConfig validates the shared fields
        self.stats_config(self.baseline_runs_grid)

    def stats_config(self, baseline_runs: int) -> StatsConfig:
        return StatsConfig(
            bootstrap_n=self.bootstrap_n, block_len_days=self.block_len_days, baseline_runs=baseline_runs,
            alpha=self.alpha, master_seed=self.master_seed, reprice_slippage=self.reprice_slippage,
            reprice_maker=self.reprice_maker,
        )


@dataclass(frozen=True)
class Heatmap:
    """``axes`` and, per axis, its normalised values (plain, as ``to_dict(StrategyParams)`` renders them)."""

    axes: tuple[str, ...]
    values: tuple[tuple[Any, ...], ...]

    def content(self) -> dict:
        return {"axes": list(self.axes), **{a: list(v) for a, v in zip(self.axes, self.values, strict=True)}}


@dataclass(frozen=True)
class Grid:
    heatmaps: tuple[Heatmap, ...]
    singles: tuple[dict, ...]  # each a normalised {field: value} override

    def content(self) -> dict:
        return {"heatmaps": [h.content() for h in self.heatmaps], "singles": [dict(s) for s in self.singles]}


def _normalise(primary: StrategyParams, override: Mapping[str, Any], where: str) -> dict:
    """``override`` validated through ``StrategyParams`` and rendered back as plain values."""
    unknown = [k for k in override if k not in PARAM_FIELDS]
    if unknown:
        raise ConfigError(f"{where}: unknown strategy parameters {unknown}")
    params = StrategyParams(**{**{f: getattr(primary, f) for f in PARAM_FIELDS}, **override})
    plain = to_dict(params)
    return {k: plain[k] for k in override}


def _parse_grid(raw: Any, primary: StrategyParams) -> Grid:
    _require_mapping(raw, "grid")
    _exact_keys(raw, ("heatmaps", "singles"), "grid")
    heatmaps = []
    for n, h in enumerate(_require_list(raw["heatmaps"], "grid.heatmaps"), start=1):
        where = f"grid.heatmaps[{n}]"
        _require_mapping(h, where)
        axes = _require_list(h.get("axes"), f"{where}.axes")
        if not axes or len(set(axes)) != len(axes):
            raise ConfigError(f"{where}.axes: expected distinct parameter names, got {axes!r}")
        unknown = [a for a in axes if a not in PARAM_FIELDS]
        if unknown:
            raise ConfigError(f"{where}.axes: unknown strategy parameters {unknown}")
        _exact_keys(h, ("axes", *axes), where)
        values = []
        for a in axes:
            vals = _require_list(h[a], f"{where}.{a}")
            if not vals:
                raise ConfigError(f"{where}.{a}: must not be empty")
            norm = tuple(_normalise(primary, {a: v}, f"{where}.{a}")[a] for v in vals)
            if len({canonical_json(v) for v in norm}) != len(norm):
                raise ConfigError(f"{where}.{a}: duplicate value in {vals!r}")
            values.append(norm)
        heatmaps.append(Heatmap(tuple(axes), tuple(values)))
    singles = []
    for n, s in enumerate(_require_list(raw["singles"], "grid.singles"), start=1):
        _require_mapping(s, f"grid.singles[{n}]")
        if not s:
            raise ConfigError(f"grid.singles[{n}]: must not be empty")
        singles.append(_normalise(primary, s, f"grid.singles[{n}]"))
    return Grid(tuple(heatmaps), tuple(singles))


# --- helpers -------------------------------------------------------------------------------------

def _require_mapping(x: Any, where: str) -> None:
    if not isinstance(x, Mapping):
        raise ConfigError(f"{where}: expected a mapping, got {type(x).__name__}")


def _require_list(x: Any, where: str) -> list:
    if not isinstance(x, list):
        raise ConfigError(f"{where}: expected a list, got {x!r}")
    return x


def _exact_keys(m: Mapping, keys: tuple[str, ...], where: str) -> None:
    unknown = [k for k in m if k not in keys]
    missing = [k for k in keys if k not in m]
    if unknown:
        raise ConfigError(f"{where}: unknown keys {unknown}")
    if missing:
        raise ConfigError(f"{where}: missing keys {missing}")


def _iso(x: Any, where: str, *, nullable: bool = False) -> str | None:
    if x is None and nullable:
        return None
    if isinstance(x, date) and not isinstance(x, datetime):
        x = x.isoformat()
    if not isinstance(x, str):
        raise ConfigError(f"{where}: expected an ISO date, got {x!r}")
    _check_iso_date(x, where)
    return x


# --- the pre-registration ------------------------------------------------------------------------

@dataclass(frozen=True, eq=False)
class Prereg:
    registered_on: str
    data_download_date: str | None
    insample: Period
    holdout: Period
    warmup_start: str
    pairs: tuple[str, ...]
    listing: dict[str, str]
    sessions: dict[str, SessionSpec]  # in file order
    primary: StrategyParams
    execution: ExecConfig
    per_pair: dict[str, dict]  # pair -> normalised ExecConfig overrides
    grid: Grid
    stats: PreregStats
    primary_family: dict
    verdict_rule: dict

    # --- content and hash ---

    def content(self) -> dict:
        """The normalised file content as plain values, in the file's shape."""
        return {
            "registered_on": self.registered_on,
            "data_download_date": self.data_download_date,
            "insample": to_dict(self.insample),
            "holdout": to_dict(self.holdout),
            "warmup_start": self.warmup_start,
            "pairs": list(self.pairs),
            "listing": dict(self.listing),
            "sessions": {k: {f: v for f, v in to_dict(s).items() if f != "name"} for k, s in self.sessions.items()},
            "primary": to_dict(self.primary),
            "execution": {**to_dict(self.execution), "per_pair": {p: dict(o) for p, o in self.per_pair.items()}},
            "grid": self.grid.content(),
            "stats": to_dict(self.stats),
            "primary_family": to_dict(self.primary_family),
            "verdict_rule": to_dict(self.verdict_rule),
        }

    def hashed_content(self) -> dict:
        """``content()`` without the two fields set after the freeze (``data_download_date``, ``holdout.end``)."""
        c = self.content()
        del c["data_download_date"]
        del c["holdout"]["end"]
        return c

    def prereg_hash(self) -> str:
        return config_hash(self.hashed_content())

    # --- derived configs ---

    def exec_for(self, pair: str) -> ExecConfig:
        return ExecConfig(**{**to_dict(self.execution), **self.per_pair.get(pair, {})})

    def data_config(self, data_dir: str | Path) -> DataConfig:
        return DataConfig(
            data_dir=str(data_dir), insample_start=self.insample.start, insample_end=self.insample.end,
            holdout_start=self.holdout.start, holdout_end=self.holdout.end, warmup_start=self.warmup_start,
            listing=dict(self.listing),
        )

    def is_primary_params(self, params: StrategyParams) -> bool:
        return config_hash(params) == config_hash(self.primary)

    def variant(self, pair: str, session: str, params: StrategyParams, *, holdout: bool = False) -> VariantConfig:
        """The ``VariantConfig`` of one cell: per-pair costs, the primary or grid baseline budget, the period."""
        if pair not in self.pairs:
            raise ConfigError(f"Prereg.variant: unknown pair {pair!r}")
        if session not in self.sessions:
            raise ConfigError(f"Prereg.variant: unknown session {session!r}")
        primary = self.is_primary_params(params)
        runs = self.stats.baseline_runs_primary if primary else self.stats.baseline_runs_grid
        period = self.holdout if holdout else self.insample
        if period.end is None:
            raise ConfigError("Prereg.variant: holdout.end is not set (it is the data download date)")
        return VariantConfig(
            pair=pair, session=self.sessions[session], params=params, exec=self.exec_for(pair),
            stats=self.stats.stats_config(runs), period_start=period.start, period_end=period.end,
            is_holdout=holdout,
        )


def parse_prereg(raw: Any) -> Prereg:
    """Validate a parsed prereg mapping (spec §6.8) into a ``Prereg``; ConfigError on anything invalid."""
    _require_mapping(raw, "prereg")
    _exact_keys(raw, TOP_KEYS, "prereg")

    registered_on = _iso(raw["registered_on"], "registered_on")
    download = _iso(raw["data_download_date"], "data_download_date", nullable=True)
    for key in ("insample", "holdout"):
        _require_mapping(raw[key], key)
        _exact_keys(raw[key], ("start", "end"), key)
    insample = from_dict(Period, raw["insample"])
    holdout = from_dict(Period, raw["holdout"])
    if insample.end is None:
        raise ConfigError("insample.end: required")
    if holdout.start <= insample.end:
        raise ConfigError(f"holdout.start {holdout.start} must be after insample.end {insample.end}")
    if holdout.end is not None and (download is None or holdout.end > download):
        raise ConfigError(f"holdout.end {holdout.end} must not be after data_download_date {download}")
    warmup = _iso(raw["warmup_start"], "warmup_start")
    if warmup > insample.start:
        raise ConfigError(f"warmup_start {warmup} is after insample.start {insample.start}")

    pairs = _require_list(raw["pairs"], "pairs")
    if not pairs or len(set(pairs)) != len(pairs) or not all(isinstance(p, str) and p for p in pairs):
        raise ConfigError(f"pairs: expected distinct pair names, got {pairs!r}")
    _require_mapping(raw["listing"], "listing")
    listing = {str(p): _iso(d, f"listing[{p!r}]") for p, d in raw["listing"].items()}
    missing = [p for p in pairs if p not in listing]
    if missing:
        raise ConfigError(f"listing: no listing date for {missing}")

    _require_mapping(raw["sessions"], "sessions")
    if not raw["sessions"]:
        raise ConfigError("sessions: must not be empty")
    sessions = {}
    for name, spec in raw["sessions"].items():
        if name not in KNOWN_SESSIONS:
            raise ConfigError(f"sessions: unknown session name {name!r}; expected one of {list(KNOWN_SESSIONS)}")
        _require_mapping(spec, f"sessions.{name}")
        if "name" in spec:
            raise ConfigError(f"sessions.{name}: the name is the key; remove 'name'")
        sessions[name] = from_dict(SessionSpec, {"name": name, **spec})

    _require_mapping(raw["primary"], "primary")
    _exact_keys(raw["primary"], PARAM_FIELDS, "primary")
    primary = from_dict(StrategyParams, raw["primary"])

    _require_mapping(raw["execution"], "execution")
    _exact_keys(raw["execution"], (*EXEC_FIELDS, "per_pair"), "execution")
    execution = from_dict(ExecConfig, {k: v for k, v in raw["execution"].items() if k != "per_pair"})
    per_pair_raw = raw["execution"]["per_pair"] or {}
    _require_mapping(per_pair_raw, "execution.per_pair")
    per_pair = {}
    for pair, override in per_pair_raw.items():
        if pair not in pairs:
            raise ConfigError(f"execution.per_pair: {pair!r} is not in pairs")
        _require_mapping(override, f"execution.per_pair.{pair}")
        unknown = [k for k in override if k not in EXEC_FIELDS]
        if unknown:
            raise ConfigError(f"execution.per_pair.{pair}: unknown keys {unknown}")
        plain = to_dict(ExecConfig(**{**to_dict(execution), **override}))
        per_pair[pair] = {k: plain[k] for k in override}

    grid = _parse_grid(raw["grid"], primary)

    _require_mapping(raw["stats"], "stats")
    stats = from_dict(PreregStats, raw["stats"])
    for key in ("primary_family", "verdict_rule"):
        _require_mapping(raw[key], key)
        if not raw[key]:
            raise ConfigError(f"{key}: must not be empty")

    return Prereg(
        registered_on=registered_on, data_download_date=download, insample=insample, holdout=holdout,
        warmup_start=warmup, pairs=tuple(pairs), listing=listing, sessions=sessions, primary=primary,
        execution=execution, per_pair=per_pair, grid=grid, stats=stats,
        primary_family=dict(raw["primary_family"]), verdict_rule=dict(raw["verdict_rule"]),
    )


def load_prereg(path: str | Path) -> Prereg:
    return parse_prereg(_read_yaml(path))


# --- the lock ------------------------------------------------------------------------------------

def lock_path(prereg_path: str | Path) -> Path:
    """``configs/prereg.lock`` next to ``configs/prereg.yaml``."""
    return Path(prereg_path).with_suffix(".lock")


def freeze(prereg_path: str | Path, *, today: date | None = None, package_dir: Path | None = None) -> dict:
    """Write the lock once: freeze date, ``prereg_hash``, ``code_version``, git commit, download date.

    Refuses when the lock exists or ``data_download_date`` is not set yet.
    """
    path = lock_path(prereg_path)
    if path.exists():
        raise FileExistsError(f"{path} exists; the pre-registration is frozen once")
    prereg = load_prereg(prereg_path)
    if prereg.data_download_date is None:
        raise ConfigError("freeze: set data_download_date (and holdout.end) in the prereg first")
    lock = {
        "frozen_on": (today or datetime.now(timezone.utc).date()).isoformat(),
        "prereg_hash": prereg.prereg_hash(),
        "code_version": code_version() if package_dir is None else code_version(package_dir),
        "git_commit": git_commit(),
        "data_download_date": prereg.data_download_date,
    }
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        yaml.safe_dump(lock, f, sort_keys=False, default_flow_style=False)
    return lock


def read_lock(path: str | Path) -> dict:
    raw = _read_yaml(path)
    _require_mapping(raw, str(path))
    _exact_keys(raw, LOCK_KEYS, str(path))
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in raw.items()}
