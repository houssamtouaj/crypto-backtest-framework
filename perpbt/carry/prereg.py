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
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from perpbt.carry.config import (
    CarryCostConfig, CarryRebalanceConfig, CarrySignalConfig, MarginConfig, SleeveConfig,
)
from perpbt.carry.frames import CARRY_INSAMPLE_END
from perpbt.config import ConfigError, _check_iso_date, _Config, config_hash, from_dict, read_yaml_unique, to_dict
from perpbt.data.store import DAY_MS, date_ms
from perpbt.trend.prereg import LOCK_KEYS, _exact_keys, _iso, _mapping
from perpbt.version import git_commit

TOP_KEYS = (
    "registered_on", "universe", "periods", "signal", "sleeve", "margin", "rebalance", "costs", "exploratory",
    "reprice", "hurdle", "stats", "screen", "verdict_rule",
)
VERDICT_KEYS = ("v1", "v2", "edge", "reported_for", "forward_12m")
MARKET = "um"
OVERRIDABLE = {"signal": CarrySignalConfig, "sleeve": SleeveConfig}  # exploratory entries may change these only

__all__ = [
    "LOCK_KEYS", "CarryPrereg", "check_frozen", "check_universe", "freeze", "load_prereg", "lock_path",
    "parse_prereg", "read_lock",
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
        out.append(Exploratory(name=entry["name"], **parts))
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


def parse_prereg(raw: Any) -> CarryPrereg:
    raw = _exact_keys(raw, TOP_KEYS, "prereg")
    signal = from_dict(CarrySignalConfig, _mapping(raw["signal"], "signal"))
    sleeve = from_dict(SleeveConfig, _mapping(raw["sleeve"], "sleeve"))
    return CarryPrereg(
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
