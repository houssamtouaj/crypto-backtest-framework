"""The trend pre-registration, its hash and its JSON lock (trend spec Phase 2 §2.1-§2.2).

``load_prereg`` parses ``configs/trend/prereg.yaml`` into a ``TrendPrereg``:
every block goes through a frozen config class, so values are validated and
normalised (``0.3`` and ``0.30`` are the same), unknown or missing keys raise,
and the YAML loader rejects duplicate keys. ``prereg_hash`` is the SHA-256 of
the canonical JSON of the normalised content without
``periods.forward.start``, which the code lock (Phase 5) sets later.

``freeze`` writes ``configs/trend/prereg.lock`` once, after checking the
universe against ``configs/trend/universe.json``; ``check_frozen`` is the
gate every later step (the screen first) passes through.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from perpbt.config import (
    ConfigError,
    _check_iso_date,
    _check_literal,
    _check_non_negative,
    _Config,
    config_hash,
    from_dict,
    read_yaml_unique,
    to_dict,
)
from perpbt.trend.config import CostConfig, RebalanceConfig, SignalConfig, SizingConfig
from perpbt.version import git_commit

TOP_KEYS = (
    "registered_on", "universe", "periods", "signal", "sizing", "rebalance", "costs", "exploratory", "reprice",
    "stats", "screen", "verdict_rule",
)
LOCK_KEYS = ("frozen_on", "prereg_hash", "git_commit", "code_version", "code_locked_on", "code_git_commit")
VERDICT_KEYS = ("t1", "t2", "edge", "t3", "reported_for", "forward_12m")
MARKETS = ("um", "spot")
OVERRIDABLE = {"signal": SignalConfig, "rebalance": RebalanceConfig}  # exploratory entries may change these only


# --- parts ---------------------------------------------------------------------------------------

def _mapping(x: Any, where: str) -> Mapping:
    if not isinstance(x, Mapping):
        raise ConfigError(f"{where}: expected a mapping, got {type(x).__name__}")
    return x


def _exact_keys(m: Any, keys: tuple[str, ...], where: str) -> Mapping:
    m = _mapping(m, where)
    unknown = [k for k in m if k not in keys]
    missing = [k for k in keys if k not in m]
    if unknown:
        raise ConfigError(f"{where}: unknown keys {unknown}")
    if missing:
        raise ConfigError(f"{where}: missing keys {missing}")
    return m


def _iso(x: Any, where: str) -> str:
    if isinstance(x, date) and not isinstance(x, datetime):
        x = x.isoformat()
    if not isinstance(x, str):
        raise ConfigError(f"{where}: expected an ISO date, got {x!r}")
    _check_iso_date(x, where)
    return x


@dataclass(frozen=True)
class Periods(_Config):
    warmup_start: str
    warmup_end: str
    insample_start: str  # first day with a return; decisions are made on the day before
    insample_end: str
    holdout_market: str
    holdout_end: str
    forward_start: str | None  # set by the code lock; excluded from the hash
    forward_reviews: tuple[int, ...]  # months after forward_start

    def _validate(self) -> None:
        for name in ("warmup_start", "warmup_end", "insample_start", "insample_end", "holdout_end"):
            _check_iso_date(getattr(self, name), f"Periods.{name}")
        if self.forward_start is not None:
            _check_iso_date(self.forward_start, "Periods.forward_start")
        if not self.warmup_start <= self.warmup_end < self.insample_start <= self.insample_end:
            raise ConfigError(
                "Periods: need warmup.start <= warmup.end < insample.start <= insample.end, got "
                f"{self.warmup_start}, {self.warmup_end}, {self.insample_start}, {self.insample_end}"
            )
        _check_literal(self.holdout_market, ("spot",), "Periods.holdout_market")
        if not self.forward_reviews or any(m <= 0 for m in self.forward_reviews):
            raise ConfigError(f"Periods.forward_reviews: positive months expected, got {list(self.forward_reviews)}")


@dataclass(frozen=True)
class TrendStats(_Config):
    alpha: float
    baseline_runs: int
    shift_margin_days: int
    bootstrap_n: int
    block_len_days: int
    master_seed: int

    def _validate(self) -> None:
        if not 0 < self.alpha < 1:
            raise ConfigError(f"TrendStats.alpha: must be in (0, 1), got {self.alpha}")
        for name in ("baseline_runs", "bootstrap_n", "block_len_days"):
            if getattr(self, name) < 1:
                raise ConfigError(f"TrendStats.{name}: must be >= 1, got {getattr(self, name)}")
        if self.shift_margin_days < 0:
            raise ConfigError(f"TrendStats.shift_margin_days: must be >= 0, got {self.shift_margin_days}")


@dataclass(frozen=True)
class ScreenConfig(_Config):
    baseline_runs: int
    stop_if_p_at_least: float

    def _validate(self) -> None:
        if self.baseline_runs < 1:
            raise ConfigError(f"ScreenConfig.baseline_runs: must be >= 1, got {self.baseline_runs}")
        if not 0 < self.stop_if_p_at_least <= 1:
            raise ConfigError(f"ScreenConfig.stop_if_p_at_least: must be in (0, 1], got {self.stop_if_p_at_least}")


@dataclass(frozen=True)
class RepriceConfig(_Config):
    slippage: tuple[float, ...]
    fee_taker: tuple[float, ...]

    def _validate(self) -> None:
        for name in ("slippage", "fee_taker"):
            values = getattr(self, name)
            if not values:
                raise ConfigError(f"RepriceConfig.{name}: must not be empty")
            for k, x in enumerate(values):
                _check_non_negative(x, f"RepriceConfig.{name}[{k}]")


@dataclass(frozen=True)
class Exploratory(_Config):
    name: str
    signal: SignalConfig
    rebalance: RebalanceConfig


@dataclass(frozen=True, eq=False)
class TrendPrereg:
    registered_on: str
    universe: dict[str, dict]  # market -> {"rule": str, "members": tuple[str, ...]}
    periods: Periods
    signal: SignalConfig
    sizing: SizingConfig
    rebalance: RebalanceConfig
    costs: CostConfig
    exploratory: tuple[Exploratory, ...]
    reprice: RepriceConfig
    stats: TrendStats
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


def _universe(raw: Any) -> dict[str, dict]:
    out = {}
    for market, block in _exact_keys(raw, MARKETS, "universe").items():
        block = _exact_keys(block, ("rule", "members"), f"universe.{market}")
        rule, members = block["rule"], block["members"]
        if not isinstance(rule, str) or not rule:
            raise ConfigError(f"universe.{market}.rule: expected a non-empty string")
        if not isinstance(members, list) or not members or not all(isinstance(m, str) for m in members):
            raise ConfigError(f"universe.{market}.members: expected a non-empty list of pairs")
        if len(set(members)) != len(members):
            raise ConfigError(f"universe.{market}.members: duplicate pair in {members}")
        out[market] = {"rule": rule, "members": tuple(members)}
    return out


def _periods(raw: Any) -> Periods:
    raw = _exact_keys(raw, ("warmup", "insample", "holdout", "forward"), "periods")
    w = _exact_keys(raw["warmup"], ("start", "end"), "periods.warmup")
    i = _exact_keys(raw["insample"], ("start", "end"), "periods.insample")
    h = _exact_keys(raw["holdout"], ("market", "end"), "periods.holdout")
    f = _exact_keys(raw["forward"], ("start", "reviews"), "periods.forward")
    return Periods(
        warmup_start=_iso(w["start"], "periods.warmup.start"), warmup_end=_iso(w["end"], "periods.warmup.end"),
        insample_start=_iso(i["start"], "periods.insample.start"), insample_end=_iso(i["end"], "periods.insample.end"),
        holdout_market=h["market"], holdout_end=_iso(h["end"], "periods.holdout.end"),
        forward_start=None if f["start"] is None else _iso(f["start"], "periods.forward.start"),
        forward_reviews=f["reviews"],
    )


def _exploratory(raw: Any, signal: SignalConfig, rebalance: RebalanceConfig) -> tuple[Exploratory, ...]:
    if not isinstance(raw, list):
        raise ConfigError(f"exploratory: expected a list, got {raw!r}")
    base = {"signal": signal, "rebalance": rebalance}
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


def parse_prereg(raw: Any) -> TrendPrereg:
    raw = _exact_keys(raw, TOP_KEYS, "prereg")
    signal = from_dict(SignalConfig, _mapping(raw["signal"], "signal"))
    rebalance = from_dict(RebalanceConfig, _mapping(raw["rebalance"], "rebalance"))
    return TrendPrereg(
        registered_on=_iso(raw["registered_on"], "registered_on"),
        universe=_universe(raw["universe"]),
        periods=_periods(raw["periods"]),
        signal=signal,
        sizing=from_dict(SizingConfig, _mapping(raw["sizing"], "sizing")),
        rebalance=rebalance,
        costs=from_dict(CostConfig, _mapping(raw["costs"], "costs")),
        exploratory=_exploratory(raw["exploratory"], signal, rebalance),
        reprice=from_dict(RepriceConfig, _mapping(raw["reprice"], "reprice")),
        stats=from_dict(TrendStats, _mapping(raw["stats"], "stats")),
        screen=from_dict(ScreenConfig, _mapping(raw["screen"], "screen")),
        verdict_rule=_verdict(raw["verdict_rule"]),
    )


def load_prereg(path: str | Path) -> TrendPrereg:
    return parse_prereg(read_yaml_unique(path))


def check_universe(prereg: TrendPrereg, universe: Mapping) -> None:
    """The prereg's ``universe`` must equal ``universe.json``'s ``rule`` and ``members`` in both markets."""
    for market in MARKETS:
        block = universe.get(market) if isinstance(universe, Mapping) else None
        if not isinstance(block, Mapping):
            raise ConfigError(f"universe.json has no {market!r} block")
        mine = prereg.universe[market]
        if mine["rule"] != block.get("rule") or list(mine["members"]) != list(block.get("members", [])):
            raise ConfigError(
                f"prereg universe.{market} differs from universe.json: members {list(mine['members'])} "
                f"vs {block.get('members')}, or the rule text differs"
            )


# --- the lock ------------------------------------------------------------------------------------

def lock_path(prereg_path: str | Path) -> Path:
    """``configs/trend/prereg.lock`` next to ``configs/trend/prereg.yaml``."""
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


def check_frozen(prereg_path: str | Path) -> TrendPrereg:
    """The prereg, if its lock exists and its hash matches the lock's; ConfigError otherwise."""
    path = lock_path(prereg_path)
    if not path.exists():
        raise ConfigError(f"{prereg_path} is not frozen: {path} is missing (run `perpbt trend prereg freeze`)")
    prereg = load_prereg(prereg_path)
    lock = read_lock(path)
    if prereg.prereg_hash() != lock["prereg_hash"]:
        raise ConfigError(
            f"{prereg_path} changed since the freeze: hash {prereg.prereg_hash()} differs from the lock's "
            f"{lock['prereg_hash']}"
        )
    return prereg
