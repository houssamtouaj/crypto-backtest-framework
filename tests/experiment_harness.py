"""A synthetic data directory and a tiny pre-registration for the experiments tests.

``write_market`` stores a seeded 1m random walk, its 15m aggregate and
8-hourly funding for one pair through the real stores, so the runner reads
them exactly as it reads the archive. ``write_prereg`` writes a prereg
with the registered structure but a small grid (3 variants per cell),
short periods inside January 2020, and small bootstrap and baseline
budgets: in-sample 2020-01-02..2020-01-12, holdout 2020-01-13..2020-01-18.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from perpbt.data.store import CandleStore, FundingStore
from perpbt.experiments.prereg import load_prereg
from tests.synthetic import T0_MS, aggregate, random_walk

DAYS = 19  # 2020-01-01 .. 2020-01-19
PAIRS = ("BTCUSDT", "ETHUSDT")

PREREG = """\
registered_on: 2026-09-26
data_download_date: {download}
insample: {{start: 2020-01-02, end: 2020-01-12}}
holdout:  {{start: 2020-01-13, end: {holdout_end}}}
warmup_start: 2020-01-01
pairs: [{pairs}]
listing: {{BTCUSDT: 2019-09-08, ETHUSDT: 2019-11-27, SOLUSDT: 2020-09-14}}
sessions:
  utc:    {{tz: UTC,              open: "00:00", close: "24:00", days: [0,1,2,3,4,5,6]}}
  ny:     {{tz: America/New_York, open: "09:30", close: "16:00", days: [0,1,2,3,4]}}
primary:
  swing_k: 2
  confirm_n: 3
  zone: full
  entry_level: top
  stop_buffer: {{kind: atr, value: 0.1}}
  r_target: {r_target}
  hold_rule: {{kind: none}}
  trend_filter: false
  pierce: 0.0
  structure_break: fresh
  skip_mitigated: continue
execution:
  fee_maker: 0.0002
  fee_taker: 0.0005
  slippage: 0.0002
  mmr: 0.004
  risk_per_trade: 0.01
  max_leverage: 25
  start_equity: 10000
  use_1m: true
  per_pair:
    ETHUSDT: {{mmr: 0.005}}
grid:
  heatmaps:
    - {{axes: [r_target], r_target: [1.5, 2]}}
  singles:
    - {{zone: body}}
stats:
  bootstrap_n: 100
  block_len_days: 3
  baseline_runs_primary: 30
  baseline_runs_grid: 10
  alpha: 0.05
  master_seed: 7
  reprice_slippage: [0.0, 0.0002]
  reprice_maker: [0.0, 0.0002]
primary_family:
  cells: all pairs x all sessions at the primary parameters
  correction: holm
verdict_rule:
  beats_baseline_a: "holm-adjusted one-sided p_a < alpha"
"""


def prereg_text(*, download: str = "null", holdout_end: str = "null", pairs=PAIRS, r_target: str = "2.0") -> str:
    return PREREG.format(download=download, holdout_end=holdout_end, pairs=", ".join(pairs), r_target=r_target)


def write_prereg(path: Path, **kw) -> Path:
    path.write_text(prereg_text(**kw), encoding="utf-8")
    return path


def _candle_frame(cd) -> pd.DataFrame:
    n = len(cd)
    return pd.DataFrame({
        "open_ms": cd.ts, "open": cd.o, "high": cd.h, "low": cd.l, "close": cd.c, "volume": cd.v,
        "quote_volume": cd.v * cd.c, "trades": np.ones(n, dtype=np.int64), "taker_buy_volume": cd.v / 2,
        "source": "bulk_monthly",
    })


def write_market(data_cfg, pair: str, *, seed: int, with_1m: bool = True) -> None:
    m1 = random_walk(DAYS * 1440, seed=seed, start_ms=T0_MS, step_ms=60_000, tf="1m", step_sigma=0.0006)
    store = CandleStore(data_cfg)
    store.write(pair, "15m", _candle_frame(aggregate(m1, 15, tf="15m")))
    if with_1m:
        store.write(pair, "1m", _candle_frame(m1))
    times = np.arange(T0_MS, T0_MS + DAYS * 86_400_000, 8 * 3_600_000, dtype=np.int64)
    rates = np.random.default_rng(seed).normal(0.0001, 0.0002, len(times))
    FundingStore(data_cfg).write(pair, pd.DataFrame({
        "funding_ms": times, "rate": rates, "interval_h": np.full(len(times), 8, dtype=np.int8),
        "source": "bulk_monthly",
    }))


def make_world(root: Path, *, skip_1m: tuple[str, ...] = (), **prereg_kw):
    """``(prereg_path, prereg, data_cfg, runs_dir)`` under ``root`` with both pairs' markets stored."""
    path = write_prereg(root / "prereg.yaml", **prereg_kw)
    prereg = load_prereg(path)
    data_cfg = prereg.data_config(root / "data")
    for k, pair in enumerate(prereg.pairs):
        write_market(data_cfg, pair, seed=11 + k, with_1m=pair not in skip_1m)
    return path, prereg, data_cfg, root / "runs"
