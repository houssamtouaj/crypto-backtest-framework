"""A synthetic carry environment for the Phase 5 tests: perp, spot and funding stores, data/universe/prereg configs.

Each pair has four 15m candles a day (00:00, 08:00, 16:00 and 23:45, so the 00:15 fill, the funding prices and the
close exist) on a random walk, the perp a little off the spot, and funding every 8 h whose annualised level swings
between about -15% and +45% over a few months, so the on/off state switches. Nothing here is market data.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from perpbt.carry.prereg import freeze
from perpbt.config import DataConfig, from_dict
from perpbt.data.store import DAY_MS, CandleStore, FundingStore, date_ms
from tests.carry.prelock import prelock_raw

ROOT = Path(__file__).resolve().parents[2]
PAIRS = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
RULE = "synthetic rule"
START = "2020-01-01"
H_MS = 3_600_000
SLOTS_MS = (0, 8 * H_MS, 16 * H_MS, DAY_MS - 900_000)


def write_pair(dc: DataConfig, pair: str, seed: int, *, end: str, start: str = START) -> None:
    days = (date_ms(end) - date_ms(start)) // DAY_MS + 1
    rng = np.random.default_rng(seed)
    n = days * len(SLOTS_MS)
    spot = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n)))
    perp = spot * (1.0 + 5e-4 * rng.normal(size=n))
    ts = (date_ms(start) + DAY_MS * np.arange(days, dtype=np.int64)[:, None] + np.array(SLOTS_MS)[None, :]).ravel()
    for market, px in (("um", perp), ("spot", spot)):
        hi = px * (1.0 + 2e-3 * rng.random(n))
        CandleStore(dc, market=market).write(pair, "15m", pd.DataFrame({
            "open_ms": ts, "open": px, "high": hi, "low": px * 0.998, "close": px, "volume": 1.0,
            "quote_volume": 1.0, "trades": 1, "taker_buy_volume": 0.0, "source": "bulk_monthly"}))
    k = np.arange(3 * days)
    annual = 0.15 + 0.30 * np.sin(2 * np.pi * k / (3 * 97.0) + seed) + 0.02 * rng.normal(size=len(k))
    FundingStore(dc).write(pair, pd.DataFrame({
        "funding_ms": date_ms(start) + 8 * H_MS * k, "rate": annual / 365 / 3, "interval_h": 8,
        "source": "bulk_monthly"}))


def make_env(tmp: Path, *, end: str = "2024-01-10", last_day: dict[str, str] | None = None,
             bootstrap_n: int = 200, timing_runs: int = 20) -> Path:
    """Stores through ``end`` (``last_day`` per pair overrides), ``data.yaml``, ``universe.json`` and a frozen
    ``prereg.yaml`` (the real one with the synthetic members and small bootstrap/random-timing counts)."""
    cfg = {"data_dir": str(tmp / "data"), "insample_start": START, "insample_end": "2023-12-31",
           "holdout_start": "2024-01-01", "holdout_end": "2026-10-20", "warmup_start": START,
           "listing": {p: START for p in PAIRS}}
    (tmp / "data.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    dc = from_dict(DataConfig, cfg)
    for j, pair in enumerate(PAIRS):
        write_pair(dc, pair, j, end=(last_day or {}).get(pair, end))
    universe = {"um": {"rule": RULE, "members": PAIRS}, "spot": {"rule": RULE, "members": ["BTCUSDT"]}}
    (tmp / "universe.json").write_text(json.dumps(universe), encoding="utf-8")
    raw = prelock_raw()  # the committed prereg before its code lock
    raw["universe"] = {"um": copy.deepcopy(universe["um"])}
    raw["margin"]["mmr"] = {"default": 0.01, "AAAUSDT": 0.004}
    raw["costs"]["slippage"] = {"default": 0.0005, "AAAUSDT": 0.0002}
    raw["stats"]["bootstrap_n"] = bootstrap_n
    raw["stats"]["random_timing_runs"] = timing_runs
    raw["screen"]["bootstrap_n"] = bootstrap_n
    (tmp / "prereg.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    freeze(tmp / "prereg.yaml", universe_path=tmp / "universe.json")
    return tmp


def copy_env(src: Path, dst: Path) -> Path:
    """Fresh copies of the configs and the lock over ``src``'s stores (so a lock or a run starts clean)."""
    for name in ("data.yaml", "universe.json", "prereg.yaml", "prereg.lock"):
        (dst / name).write_bytes((src / name).read_bytes())
    return dst


def data_config(env: Path) -> DataConfig:
    return from_dict(DataConfig, yaml.safe_load((env / "data.yaml").read_text(encoding="utf-8")))
