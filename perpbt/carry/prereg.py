"""The carry pre-registration, its hash and its JSON lock (carry spec Phase 2 §2.1-§2.2).

The trend pattern (``trend/prereg.py``): ``load_prereg`` parses
``configs/carry/prereg.yaml`` into a ``CarryPrereg``; every block goes through
a frozen config class, so values are validated and normalised, unknown or
missing keys raise, and the YAML loader rejects duplicate keys.
``prereg_hash`` is the SHA-256 of the canonical JSON of the normalised content
without ``periods.forward.start``, which the code lock (Phase 5) sets later.
Exploratory rows are stored merged and may override only ``signal`` and
``sleeve``.

``freeze`` writes ``configs/carry/prereg.lock`` once (the trend lock's keys),
after checking the universe against ``configs/trend/universe.json``'s ``um``
block; ``check_frozen`` is the gate every later step (the screen first)
passes through.

The code lock (Phase 5 §5.3): ``lock_code`` fills the lock's code fields once with ``carry_code_version()`` (the
SHA-256 of ``CARRY_CODE_PATHS`` only, so later work elsewhere in the repo is not a carry code change) and sets
``periods.forward.start`` to the next UTC day in the YAML text (outside the hash).
"""
from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from perpbt.carry.config import (
    CarryCostConfig, CarryRebalanceConfig, CarrySignalConfig, MarginConfig, SleeveConfig,
)
from perpbt.carry.frames import CARRY_INSAMPLE_END
from perpbt.config import ConfigError, _check_iso_date, _Config, config_hash, from_dict, read_yaml_unique, to_dict
from perpbt.data.store import DAY_MS, date_ms
from perpbt.trend.prereg import LOCK_KEYS, _exact_keys, _iso, _mapping
from perpbt.version import PACKAGE_DIR, files_code_version, git_commit

# The files carry runs execute, relative to perpbt/ ("x/" = every *.py under x): spec §5.3's list made exact by
# the transitive import test (tests/carry/test_code_lock.py), which adds trend/prereg.py and trend/config.py
# (the lock helpers), the package __init__ files (they run on import) and experiments/registry_log.py (the
# registry without the first strategy's modules) in place of experiments/registry.py.
CARRY_CODE_PATHS = (
    "__init__.py", "carry/", "trend/__init__.py", "trend/config.py", "trend/daily.py", "trend/prereg.py", "data/",
    "indicators/__init__.py", "indicators/daily.py", "indicators/atr.py", "stats/__init__.py", "stats/bootstrap.py",
    "stats/dsr.py", "stats/diagnostics.py", "stats/seeds.py", "experiments/__init__.py",
    "experiments/registry_log.py", "config.py", "version.py", "checks.py",
)

TOP_KEYS = (
    "registered_on", "universe", "periods", "signal", "sleeve", "margin", "rebalance", "costs", "exploratory",
    "reprice", "hurdle", "stats", "screen", "verdict_rule",
)
VERDICT_KEYS = ("v1", "v2", "edge", "reported_for", "forward_12m")
MARKET = "um"
OVERRIDABLE = {"signal": CarrySignalConfig, "sleeve": SleeveConfig}  # exploratory entries may change these only

__all__ = [
    "CARRY_CODE_PATHS", "LOCK_KEYS", "CarryPrereg", "LockRefused", "carry_code_version", "check_frozen",
    "check_universe", "freeze", "load_prereg", "lock_code", "lock_path", "parse_prereg", "read_lock",
]


def _fraction(x: float, where: str, *, low_open: bool = True) -> None:
    if not math.isfinite(x) or not (0 < x < 1 if low_open else 0 <= x < 1):
        raise ConfigError(f"{where}: must be in {'(0' if low_open else '[0'}, 1), got {x!r}")


@dataclass(frozen=True)
class Periods(_Config):
    warmup_start: str
    warmup_end: str
    insample_start: str  # first day with a return; decisions are made on the day before
    insample_end: str
    holdout_start: str
    holdout_end: str
    forward_start: str | None  # set by the code lock; excluded from the hash
    forward_reviews: tuple[int, ...]  # months after forward_start

    def _validate(self) -> None:
        names = ("warmup_start", "warmup_end", "insample_start", "insample_end", "holdout_start", "holdout_end")
        for name in names:
            _check_iso_date(getattr(self, name), f"Periods.{name}")
        if self.forward_start is not None:
            _check_iso_date(self.forward_start, "Periods.forward_start")
        w0, w1, i0, i1, h0, h1 = (getattr(self, n) for n in names)
        if not w0 <= w1 < i0 <= i1 < h0 <= h1:
            raise ConfigError(
                "Periods: need warmup.start <= warmup.end < insample.start <= insample.end < holdout.start "
                f"<= holdout.end, got {w0}, {w1}, {i0}, {i1}, {h0}, {h1}"
            )
        if i1 != CARRY_INSAMPLE_END:
            raise ConfigError(f"Periods.insample_end: must be the carry in-sample end {CARRY_INSAMPLE_END}, got {i1}")
        if date_ms(h0) != date_ms(i1) + DAY_MS:
            raise ConfigError(f"Periods.holdout_start: must be the day after insample.end ({i1}), got {h0}")
        if not self.forward_reviews or any(m <= 0 for m in self.forward_reviews):
            raise ConfigError(f"Periods.forward_reviews: positive months expected, got {list(self.forward_reviews)}")


@dataclass(frozen=True)
class Hurdle(_Config):
    annual: float  # V2 hurdle, compounded daily: h_d = (1 + annual)^(1/365) - 1
    descriptive: tuple[float, ...]  # reported only

    def _validate(self) -> None:
        _fraction(self.annual, "Hurdle.annual", low_open=False)
        if not self.descriptive:
            raise ConfigError("Hurdle.descriptive: must not be empty")
        for k, x in enumerate(self.descriptive):
            _fraction(x, f"Hurdle.descriptive[{k}]", low_open=False)


@dataclass(frozen=True)
class CarryStats(_Config):
    alpha: float
    bootstrap_n: int
    block_len_days: int
    random_timing_runs: int
    shift_margin_days: int
    master_seed: int

    def _validate(self) -> None:
        _fraction(self.alpha, "CarryStats.alpha")
        for name in ("bootstrap_n", "block_len_days", "random_timing_runs"):
            if getattr(self, name) < 1:
                raise ConfigError(f"CarryStats.{name}: must be >= 1, got {getattr(self, name)}")
        if self.shift_margin_days < 0:
            raise ConfigError(f"CarryStats.shift_margin_days: must be >= 0, got {self.shift_margin_days}")


@dataclass(frozen=True)
class ScreenConfig(_Config):
    bootstrap_n: int
    stop_if_p_at_least: float

    def _validate(self) -> None:
        if self.bootstrap_n < 1:
            raise ConfigError(f"ScreenConfig.bootstrap_n: must be >= 1, got {self.bootstrap_n}")
        if not 0 < self.stop_if_p_at_least <= 1:
            raise ConfigError(f"ScreenConfig.stop_if_p_at_least: must be in (0, 1], got {self.stop_if_p_at_least}")


@dataclass(frozen=True)
class RepriceConfig(_Config):
    slippage: tuple[float, ...]
    spot_taker: tuple[float, ...]

    def _validate(self) -> None:
        for name in ("slippage", "spot_taker"):
            values = getattr(self, name)
            if not values:
                raise ConfigError(f"RepriceConfig.{name}: must not be empty")
            for k, x in enumerate(values):
                if not math.isfinite(x) or x < 0:
                    raise ConfigError(f"RepriceConfig.{name}[{k}]: must be finite and >= 0, got {x!r}")


@dataclass(frozen=True)
class Exploratory(_Config):
    name: str
    signal: CarrySignalConfig
    sleeve: SleeveConfig


@dataclass(frozen=True, eq=False)
class CarryPrereg:
    registered_on: str
    universe: dict[str, dict]  # "um" -> {"rule": str, "members": tuple[str, ...]}
    periods: Periods
    signal: CarrySignalConfig
    sleeve: SleeveConfig
    margin: MarginConfig
    rebalance: CarryRebalanceConfig
    costs: CarryCostConfig
    exploratory: tuple[Exploratory, ...]
    reprice: RepriceConfig
    hurdle: Hurdle
    stats: CarryStats
    screen: ScreenConfig
    verdict_rule: dict[str, Any]

    def content(self) -> dict:
        """The normalised content as plain dicts and lists (what the hash is taken over, plus forward_start)."""
        return {f.name: to_dict(getattr(self, f.name)) for f in fields(self)}

    def prereg_hash(self) -> str:
        """SHA-256 of the canonical JSON of ``content()`` without ``periods.forward_start``."""
        c = self.content()
        c["periods"] = {k: v for k, v in c["periods"].items() if k != "forward_start"}
        return config_hash(c)

    def exploratory_row(self, name: str) -> Exploratory:
        for e in self.exploratory:
            if e.name == name:
                return e
        raise KeyError(f"no exploratory row {name!r}")


def _universe(raw: Any) -> dict[str, dict]:
    block = _exact_keys(_exact_keys(raw, (MARKET,), "universe")[MARKET], ("rule", "members"), f"universe.{MARKET}")
    rule, members = block["rule"], block["members"]
    if not isinstance(rule, str) or not rule:
        raise ConfigError(f"universe.{MARKET}.rule: expected a non-empty string")
    if not isinstance(members, list) or not members or not all(isinstance(m, str) for m in members):
        raise ConfigError(f"universe.{MARKET}.members: expected a non-empty list of pairs")
    if len(set(members)) != len(members):
        raise ConfigError(f"universe.{MARKET}.members: duplicate pair in {members}")
    return {MARKET: {"rule": rule, "members": tuple(members)}}


def _periods(raw: Any) -> Periods:
    raw = _exact_keys(raw, ("warmup", "insample", "holdout", "forward"), "periods")
    w = _exact_keys(raw["warmup"], ("start", "end"), "periods.warmup")
    i = _exact_keys(raw["insample"], ("start", "end"), "periods.insample")
    h = _exact_keys(raw["holdout"], ("start", "end"), "periods.holdout")
    f = _exact_keys(raw["forward"], ("start", "reviews"), "periods.forward")
    return Periods(
        warmup_start=_iso(w["start"], "periods.warmup.start"), warmup_end=_iso(w["end"], "periods.warmup.end"),
        insample_start=_iso(i["start"], "periods.insample.start"), insample_end=_iso(i["end"], "periods.insample.end"),
        holdout_start=_iso(h["start"], "periods.holdout.start"), holdout_end=_iso(h["end"], "periods.holdout.end"),
        forward_start=None if f["start"] is None else _iso(f["start"], "periods.forward.start"),
        forward_reviews=f["reviews"],
    )


def _exploratory(raw: Any, base: dict[str, _Config]) -> tuple[Exploratory, ...]:
    if not isinstance(raw, list):
        raise ConfigError(f"exploratory: expected a list, got {raw!r}")
    out = []
    for k, entry in enumerate(raw):
        where = f"exploratory[{k}]"
        entry = _mapping(entry, where)
        unknown = [key for key in entry if key != "name" and key not in OVERRIDABLE]
        if unknown:
            raise ConfigError(f"{where}: unknown keys {unknown}; an exploratory row may override {list(OVERRIDABLE)}")
        if not isinstance(entry.get("name"), str) or not entry["name"]:
            raise ConfigError(f"{where}: needs a name")
        parts = {
            key: from_dict(cls, {**to_dict(base[key]), **_mapping(entry.get(key, {}), f"{where}.{key}")})
            for key, cls in OVERRIDABLE.items()
        }
        out.append(Exploratory(name=entry["name"], signal=parts["signal"], sleeve=parts["sleeve"]))
    names = [e.name for e in out]
    if len(set(names)) != len(names):
        raise ConfigError(f"exploratory: duplicate name in {names}")
    return tuple(out)


def _verdict(raw: Any) -> dict[str, Any]:
    raw = _exact_keys(raw, VERDICT_KEYS, "verdict_rule")
    for key in VERDICT_KEYS:
        value = raw[key]
        ok = (isinstance(value, list) and all(isinstance(v, str) for v in value)) if key == "reported_for" \
            else isinstance(value, str)
        if not ok:
            raise ConfigError(f"verdict_rule.{key}: unexpected value {value!r}")
    return {key: list(raw[key]) if key == "reported_for" else raw[key] for key in VERDICT_KEYS}


def _check_pair_tables(prereg: CarryPrereg) -> None:
    """Per-pair ``mmr`` and ``slippage`` keys are ``default`` or a universe member (a misspelt pair would fall
    back to ``default`` silently)."""
    members = set(prereg.universe[MARKET]["members"])
    for where, table in (("margin.mmr", prereg.margin.mmr), ("costs.slippage", prereg.costs.slippage)):
        unknown = sorted(k for k in table if k != "default" and k not in members)
        if unknown:
            raise ConfigError(f"{where}: {unknown} are not universe.{MARKET} members")


def parse_prereg(raw: Any) -> CarryPrereg:
    raw = _exact_keys(raw, TOP_KEYS, "prereg")
    signal = from_dict(CarrySignalConfig, _mapping(raw["signal"], "signal"))
    sleeve = from_dict(SleeveConfig, _mapping(raw["sleeve"], "sleeve"))
    prereg = CarryPrereg(
        registered_on=_iso(raw["registered_on"], "registered_on"),
        universe=_universe(raw["universe"]),
        periods=_periods(raw["periods"]),
        signal=signal,
        sleeve=sleeve,
        margin=from_dict(MarginConfig, _mapping(raw["margin"], "margin")),
        rebalance=from_dict(CarryRebalanceConfig, _mapping(raw["rebalance"], "rebalance")),
        costs=from_dict(CarryCostConfig, _mapping(raw["costs"], "costs")),
        exploratory=_exploratory(raw["exploratory"], {"signal": signal, "sleeve": sleeve}),
        reprice=from_dict(RepriceConfig, _mapping(raw["reprice"], "reprice")),
        hurdle=from_dict(Hurdle, _mapping(raw["hurdle"], "hurdle")),
        stats=from_dict(CarryStats, _mapping(raw["stats"], "stats")),
        screen=from_dict(ScreenConfig, _mapping(raw["screen"], "screen")),
        verdict_rule=_verdict(raw["verdict_rule"]),
    )
    _check_pair_tables(prereg)
    return prereg


def load_prereg(path: str | Path) -> CarryPrereg:
    return parse_prereg(read_yaml_unique(path))


def check_universe(prereg: CarryPrereg, universe: Mapping) -> None:
    """The prereg's ``universe.um`` must equal ``universe.json``'s ``um`` ``rule`` and ``members``."""
    block = universe.get(MARKET) if isinstance(universe, Mapping) else None
    if not isinstance(block, Mapping):
        raise ConfigError(f"universe.json has no {MARKET!r} block")
    mine = prereg.universe[MARKET]
    if mine["rule"] != block.get("rule") or list(mine["members"]) != list(block.get("members", [])):
        raise ConfigError(
            f"prereg universe.{MARKET} differs from universe.json: members {list(mine['members'])} "
            f"vs {block.get('members')}, or the rule text differs"
        )


# --- the lock ------------------------------------------------------------------------------------

def lock_path(prereg_path: str | Path) -> Path:
    """``configs/carry/prereg.lock`` next to ``configs/carry/prereg.yaml``."""
    return Path(prereg_path).with_suffix(".lock")


def freeze(prereg_path: str | Path, *, universe_path: str | Path, today: date | None = None) -> dict:
    """Write the JSON lock once: freeze date, ``prereg_hash``, git commit; the code fields stay null (Phase 5)."""
    path = lock_path(prereg_path)
    if path.exists():
        raise FileExistsError(f"{path} exists; the pre-registration is frozen once")
    prereg = load_prereg(prereg_path)
    check_universe(prereg, json.loads(Path(universe_path).read_text(encoding="utf-8")))
    lock = {
        "frozen_on": (today or datetime.now(timezone.utc).date()).isoformat(),
        "prereg_hash": prereg.prereg_hash(),
        "git_commit": git_commit(),
        "code_version": None,
        "code_locked_on": None,
        "code_git_commit": None,
    }
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        json.dump(lock, f, indent=2)
        f.write("\n")
    return lock


def read_lock(path: str | Path) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return dict(_exact_keys(raw, LOCK_KEYS, str(path)))


def check_frozen(prereg_path: str | Path) -> CarryPrereg:
    """The prereg, if its lock exists and its hash matches the lock's; ConfigError otherwise."""
    path = lock_path(prereg_path)
    if not path.exists():
        raise ConfigError(f"{prereg_path} is not frozen: {path} is missing (run `perpbt carry prereg freeze`)")
    prereg = load_prereg(prereg_path)
    lock = read_lock(path)
    if prereg.prereg_hash() != lock["prereg_hash"]:
        raise ConfigError(
            f"{prereg_path} changed since the freeze: hash {prereg.prereg_hash()} differs from the lock's "
            f"{lock['prereg_hash']}"
        )
    return prereg


# --- the code lock (Phase 5 §5.3) ----------------------------------------------------------------

class LockRefused(RuntimeError):
    """``lock_code`` refused: not frozen, changed since the freeze, or already locked; nothing was written."""


def carry_code_version(root: str | Path = PACKAGE_DIR) -> str:
    """The carry code version: ``version.files_code_version`` over ``CARRY_CODE_PATHS``."""
    return files_code_version(CARRY_CODE_PATHS, root)


_FORWARD_KEY = re.compile(r"^[ \t]*forward:", re.MULTILINE)
_START_NULL = re.compile(r"\bstart:[ \t]*null\b")


def _with_forward_start(text: str, day: str) -> str:
    """The prereg text with the first ``start: null`` after the ``forward:`` key set to ``day`` (flow or block)."""
    key = _FORWARD_KEY.search(text)
    m = _START_NULL.search(text, key.end()) if key else None
    if m is None:
        raise LockRefused("lock-code: no `start: null` under periods.forward in the pre-registration text")
    return text[:m.start()] + f"start: {day}" + text[m.end():]


def _replace_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def lock_code(prereg_path: str | Path, *, today: date | None = None, code_version: str | None = None) -> dict:
    """Lock the carry code once: the lock's ``code_version``, ``code_locked_on``, ``code_git_commit``, and
    ``periods.forward.start`` = the next UTC day (spec §5.3). Run immediately before the in-sample primary run.

    Refuses (LockRefused, nothing written) unless the prereg is frozen and unchanged, the lock has no code version
    and the forward start is null. The new YAML text is checked before anything is written: same hash, the new
    forward start. Both files are replaced whole (``os.replace``), the prereg first.
    """
    prereg_path = Path(prereg_path)
    try:
        prereg = check_frozen(prereg_path)
    except ConfigError as e:
        raise LockRefused(f"lock-code: {e}") from e
    path = lock_path(prereg_path)
    lock = read_lock(path)
    if lock["code_version"] is not None or lock["code_locked_on"] is not None:
        raise LockRefused(f"lock-code: the code is already locked ({lock['code_locked_on']}, {lock['code_version']})")
    if prereg.periods.forward_start is not None:
        raise LockRefused(f"lock-code: periods.forward.start is already set ({prereg.periods.forward_start})")
    today = today or datetime.now(timezone.utc).date()
    start = (today + timedelta(days=1)).isoformat()
    text = _with_forward_start(prereg_path.read_text(encoding="utf-8"), start)
    check = prereg_path.with_name(prereg_path.name + ".check")
    try:
        check.write_text(text, encoding="utf-8", newline="\n")
        after = load_prereg(check)
    finally:
        check.unlink(missing_ok=True)
    if after.prereg_hash() != lock["prereg_hash"] or after.periods.forward_start != start:
        raise LockRefused("lock-code: setting periods.forward.start would change the pre-registration's content")
    new_lock = {**lock, "code_version": code_version or carry_code_version(), "code_locked_on": today.isoformat(),
                "code_git_commit": git_commit()}
    _replace_text(prereg_path, text)
    _replace_text(path, json.dumps(new_lock, indent=2) + "\n")
    return new_lock
