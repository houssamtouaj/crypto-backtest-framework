"""The ETF pre-registration, its hash and its JSON lock (ETF spec Phase 2 §2.1-§2.2).

``load_prereg`` parses ``configs/etf/prereg.yaml`` into an ``EtfPrereg``: every
block goes through a frozen config class, so values are validated and
normalised (``0.2`` and ``0.20`` are the same), unknown or missing keys raise,
and the YAML loader rejects duplicate keys. The ``signal`` block is
``trend.config.SignalConfig`` and the ``sizing`` block ``EtfSizingConfig``
(Phase 1 §1.8); the exact-key helpers, the ``stats`` and ``screen`` blocks and
the verdict text check are the trend prereg's, imported. ``prereg_hash`` is
the SHA-256 of the canonical JSON of the normalised content without
``periods.forward_start``, which the code lock (Phase 5) sets later.

``check_snapshot`` ties the prereg to the fetched data: its tickers and dates
must equal ``configs/etf/data.yaml``'s and ``data/etf/manifest.json`` must hash
to ``data.manifest_sha256``. ``freeze`` runs it, then writes
``configs/etf/prereg.lock`` once; ``check_frozen`` is the gate every later
step (the screen first) passes through.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from perpbt.config import ConfigError, _check_iso_date, _Config, config_hash, from_dict, read_yaml_unique, to_dict
from perpbt.data.store import DAY_MS, date_ms
from perpbt.etf.config import (
    EtfCashConfig, EtfCostConfig, EtfDataConfig, EtfRebalanceConfig, EtfRepriceConfig, EtfSizingConfig,
)
from perpbt.etf.store import ETF_HOLDOUT_START, EtfStore
from perpbt.trend.config import SignalConfig
from perpbt.trend.prereg import LOCK_KEYS, ScreenConfig, TrendStats, _exact_keys, _iso, _mapping, _verdict
from perpbt.version import git_commit

TOP_KEYS = (
    "registered_on", "universe", "periods", "data", "signal", "sizing", "rebalance", "costs", "cash", "exploratory",
    "reprice", "stats", "screen", "verdict_rule",
)
OVERRIDABLE = {"signal": SignalConfig, "sizing": EtfSizingConfig, "rebalance": EtfRebalanceConfig}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

__all__ = [
    "DataSnapshot", "EtfPrereg", "Exploratory", "LOCK_KEYS", "Periods", "Universe", "check_frozen",
    "check_snapshot", "freeze", "load_prereg", "lock_path", "parse_prereg", "read_lock",
]


@dataclass(frozen=True)
class Universe(_Config):
    tickers: tuple[str, ...]
    rule: str

    def _validate(self) -> None:
        if not self.tickers or len(set(self.tickers)) != len(self.tickers):
            raise ConfigError(f"Universe.tickers: expected distinct tickers, got {list(self.tickers)}")
        if not self.rule:
            raise ConfigError("Universe.rule: must not be empty")


@dataclass(frozen=True)
class Periods(_Config):
    warmup_start: str
    insample_start: str  # first day with a return; decisions are made on the trading day before
    insample_end: str
    holdout_start: str
    holdout_end: str
    forward_start: str | None  # set by the code lock; excluded from the hash
    forward_reviews: tuple[int, ...]  # months after forward_start

    def _validate(self) -> None:
        names = ("warmup_start", "insample_start", "insample_end", "holdout_start", "holdout_end")
        for name in names:
            _check_iso_date(getattr(self, name), f"Periods.{name}")
        if self.forward_start is not None:
            _check_iso_date(self.forward_start, "Periods.forward_start")
        w0, i0, i1, h0, h1 = (getattr(self, n) for n in names)
        if not w0 < i0 <= i1 < h0 <= h1:
            raise ConfigError(
                f"Periods: need warmup.start < insample.start <= insample.end < holdout.start <= holdout.end, "
                f"got {w0}, {i0}, {i1}, {h0}, {h1}"
            )
        if h0 != ETF_HOLDOUT_START:
            raise ConfigError(f"Periods.holdout_start: must be {ETF_HOLDOUT_START} (the store's guard), got {h0}")
        if date_ms(h0) != date_ms(i1) + DAY_MS:
            raise ConfigError(f"Periods.holdout_start: must be the day after insample.end ({i1}), got {h0}")
        if self.forward_start is not None and self.forward_start <= h1:
            raise ConfigError(f"Periods.forward_start: must be after holdout.end ({h1}), got {self.forward_start}")
        if not self.forward_reviews or any(m <= 0 for m in self.forward_reviews) \
                or list(self.forward_reviews) != sorted(set(self.forward_reviews)):
            raise ConfigError(
                f"Periods.forward_reviews: increasing positive months expected, got {list(self.forward_reviews)}"
            )


@dataclass(frozen=True)
class DataSnapshot(_Config):
    download_date: str
    manifest_sha256: str  # of data/etf/manifest.json, as committed (LF, .gitattributes)

    def _validate(self) -> None:
        _check_iso_date(self.download_date, "DataSnapshot.download_date")
        if not _SHA256.fullmatch(self.manifest_sha256):
            raise ConfigError(f"DataSnapshot.manifest_sha256: expected 64 lower-case hex digits, "
                              f"got {self.manifest_sha256!r}")


@dataclass(frozen=True)
class Exploratory(_Config):
    name: str
    signal: SignalConfig
    sizing: EtfSizingConfig
    rebalance: EtfRebalanceConfig


@dataclass(frozen=True, eq=False)
class EtfPrereg:
    registered_on: str
    universe: Universe
    periods: Periods
    data: DataSnapshot
    signal: SignalConfig
    sizing: EtfSizingConfig
    rebalance: EtfRebalanceConfig
    costs: EtfCostConfig
    cash: EtfCashConfig
    exploratory: tuple[Exploratory, ...]
    reprice: EtfRepriceConfig
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


def _periods(raw: Any) -> Periods:
    raw = _exact_keys(raw, ("warmup", "insample", "holdout", "forward"), "periods")
    w = _exact_keys(raw["warmup"], ("start",), "periods.warmup")
    i = _exact_keys(raw["insample"], ("start", "end"), "periods.insample")
    h = _exact_keys(raw["holdout"], ("start", "end"), "periods.holdout")
    f = _exact_keys(raw["forward"], ("start", "reviews"), "periods.forward")
    if not isinstance(f["reviews"], list):
        raise ConfigError(f"periods.forward.reviews: expected a list of months, got {f['reviews']!r}")
    return Periods(
        warmup_start=_iso(w["start"], "periods.warmup.start"),
        insample_start=_iso(i["start"], "periods.insample.start"), insample_end=_iso(i["end"], "periods.insample.end"),
        holdout_start=_iso(h["start"], "periods.holdout.start"), holdout_end=_iso(h["end"], "periods.holdout.end"),
        forward_start=None if f["start"] is None else _iso(f["start"], "periods.forward.start"),
        forward_reviews=f["reviews"],
    )


def _data(raw: Any) -> DataSnapshot:
    raw = _exact_keys(raw, ("download_date", "manifest_sha256"), "data")
    return DataSnapshot(download_date=_iso(raw["download_date"], "data.download_date"),
                        manifest_sha256=raw["manifest_sha256"])


def _long_flat(signal: SignalConfig, where: str) -> SignalConfig:
    if signal.direction != "long_flat":
        raise ConfigError(f"{where}.direction: a cash account cannot short (ED13), got {signal.direction!r}")
    return signal


def _exploratory(raw: Any, base: Mapping[str, _Config]) -> tuple[Exploratory, ...]:
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
        if all(parts[key] == base[key] for key in OVERRIDABLE):
            raise ConfigError(f"{where}: changes nothing; it would repeat the primary")
        _long_flat(parts["signal"], f"{where}.signal")
        out.append(Exploratory(name=entry["name"], **parts))
    names = [e.name for e in out]
    if len(set(names)) != len(names):
        raise ConfigError(f"exploratory: duplicate name in {names}")
    return tuple(out)


def parse_prereg(raw: Any) -> EtfPrereg:
    raw = _exact_keys(raw, TOP_KEYS, "prereg")
    base = {
        "signal": _long_flat(from_dict(SignalConfig, _mapping(raw["signal"], "signal")), "signal"),
        "sizing": from_dict(EtfSizingConfig, _mapping(raw["sizing"], "sizing")),
        "rebalance": from_dict(EtfRebalanceConfig, _mapping(raw["rebalance"], "rebalance")),
    }
    universe = from_dict(Universe, _mapping(raw["universe"], "universe"))
    costs = from_dict(EtfCostConfig, _mapping(raw["costs"], "costs"))
    stray = [k for k in costs.slippage if k != "default" and k not in universe.tickers]
    if stray:  # a misspelt ticker would silently give the real one the default
        raise ConfigError(f"costs.slippage: {stray} not in universe.tickers {list(universe.tickers)}")
    return EtfPrereg(
        registered_on=_iso(raw["registered_on"], "registered_on"),
        universe=universe,
        periods=_periods(raw["periods"]),
        data=_data(raw["data"]),
        **base,
        costs=costs,
        cash=from_dict(EtfCashConfig, _mapping(raw["cash"], "cash")),
        exploratory=_exploratory(raw["exploratory"], base),
        reprice=from_dict(EtfRepriceConfig, _mapping(raw["reprice"], "reprice")),
        stats=from_dict(TrendStats, _mapping(raw["stats"], "stats")),
        screen=from_dict(ScreenConfig, _mapping(raw["screen"], "screen")),
        verdict_rule=_verdict(raw["verdict_rule"]),
    )


def load_prereg(path: str | Path) -> EtfPrereg:
    return parse_prereg(read_yaml_unique(path))


def check_snapshot(prereg: EtfPrereg, cfg: EtfDataConfig, store: EtfStore) -> None:
    """The prereg must describe the data config and the stored snapshot; ConfigError naming the first difference.

    Tickers (in order), ``cash.series``, the warmup, in-sample and holdout dates
    and the download date must equal ``cfg``'s, and the store's
    ``manifest.json`` must hash to ``data.manifest_sha256``.
    """
    p = prereg.periods
    pairs = (
        ("universe.tickers", list(prereg.universe.tickers), list(cfg.tickers)),
        ("cash.series", prereg.cash.series, cfg.rates.series),
        ("periods.warmup.start", p.warmup_start, cfg.warmup_start),
        ("periods.insample.start", p.insample_start, cfg.insample_start),
        ("periods.insample.end", p.insample_end, cfg.insample_end),
        ("periods.holdout.start", p.holdout_start, cfg.holdout_start),
        ("periods.holdout.end", p.holdout_end, cfg.holdout_end),
        ("data.download_date", prereg.data.download_date, cfg.download_date),
    )
    for name, mine, theirs in pairs:
        if mine != theirs:
            raise ConfigError(f"prereg {name} {mine!r} differs from the data config's {theirs!r}")
    if not store.manifest_path.is_file():
        raise ConfigError(f"{store.manifest_path} is missing; the prereg pins its hash")
    got = store.manifest_sha256()
    if got != prereg.data.manifest_sha256:
        raise ConfigError(f"{store.manifest_path} has sha256 {got}, the prereg pins {prereg.data.manifest_sha256}: "
                          "the snapshot changed")


# --- the lock ------------------------------------------------------------------------------------

def lock_path(prereg_path: str | Path) -> Path:
    """``configs/etf/prereg.lock`` next to ``configs/etf/prereg.yaml``."""
    return Path(prereg_path).with_suffix(".lock")


def freeze(prereg_path: str | Path, *, data_cfg: EtfDataConfig, today: date | None = None) -> dict:
    """Write the JSON lock once, after ``check_snapshot``; the code fields stay null (Phase 5)."""
    path = lock_path(prereg_path)
    if path.exists():
        raise FileExistsError(f"{path} exists; the pre-registration is frozen once")
    prereg = load_prereg(prereg_path)
    check_snapshot(prereg, data_cfg, EtfStore(data_cfg.data_dir))
    lock = {
        "frozen_on": (today or datetime.now(timezone.utc).date()).isoformat(),
        "prereg_hash": prereg.prereg_hash(),
        "git_commit": git_commit(),
        "code_version": None,
        "code_locked_on": None,
        "code_git_commit": None,
    }
    text = json.dumps(lock, indent=2) + "\n"
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return lock


def read_lock(path: str | Path) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return dict(_exact_keys(raw, LOCK_KEYS, str(path)))


def check_frozen(prereg_path: str | Path) -> EtfPrereg:
    """The prereg, if its lock exists and its hash matches the lock's; ConfigError otherwise."""
    path = lock_path(prereg_path)
    if not path.exists():
        raise ConfigError(f"{prereg_path} is not frozen: {path} is missing (run `perpbt etf prereg freeze`)")
    prereg = load_prereg(prereg_path)
    lock = read_lock(path)
    if prereg.prereg_hash() != lock["prereg_hash"]:
        raise ConfigError(
            f"{prereg_path} changed since the freeze: hash {prereg.prereg_hash()} differs from the lock's "
            f"{lock['prereg_hash']}"
        )
    return prereg
