"""`perpbt trend prereg validate|freeze` and `perpbt trend screen` (trend spec Phase 2 §2.2-§2.3, §2.5)."""
import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from perpbt.cli import main
from perpbt.config import read_yaml_unique
from perpbt.data.store import DAY_MS, CandleStore, FundingStore, date_ms
from perpbt.trend.prereg import load_prereg

ROOT = Path(__file__).resolve().parents[2]
PAIRS = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
RULE = "synthetic rule"


@pytest.fixture
def env(tmp_path):
    """A synthetic perp store (one 15m candle per day, funding every 8 h), its data config, universe and prereg."""
    data = tmp_path / "data"
    cfg = {"data_dir": str(data), "insample_start": "2020-01-01", "insample_end": "2021-03-31",
           "holdout_start": "2021-04-01", "holdout_end": None, "warmup_start": "2020-01-01",
           "listing": {p: "2020-01-01" for p in PAIRS}}
    (tmp_path / "data.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    from perpbt.config import DataConfig, from_dict
    dc = from_dict(DataConfig, cfg)
    days = (date_ms("2021-04-05") - date_ms("2020-01-01")) // DAY_MS
    for j, pair in enumerate(PAIRS):
        rng = np.random.default_rng(j)
        c = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.03, days)))
        ts = date_ms("2020-01-01") + DAY_MS * np.arange(days, dtype=np.int64) + DAY_MS - 900_000
        CandleStore(dc).write(pair, "15m", pd.DataFrame({
            "open_ms": ts, "open": c, "high": c, "low": c, "close": c, "volume": 1.0, "quote_volume": 1.0,
            "trades": 1, "taker_buy_volume": 0.0, "source": "bulk_monthly"}))
        fts = date_ms("2020-01-01") + 8 * 3_600_000 * np.arange(3 * days, dtype=np.int64)
        FundingStore(dc).write(pair, pd.DataFrame({"funding_ms": fts, "rate": 1e-4, "interval_h": 8,
                                                   "source": "bulk_monthly"}))
    universe = {"um": {"rule": RULE, "members": PAIRS}, "spot": {"rule": RULE, "members": ["BTCUSDT"]}}
    (tmp_path / "universe.json").write_text(json.dumps(universe), encoding="utf-8")
    raw = read_yaml_unique(ROOT / "configs" / "trend" / "prereg.yaml")
    raw["universe"] = copy.deepcopy(universe)
    raw["periods"]["insample"] = {"start": "2020-07-01", "end": "2021-03-31"}
    raw["signal"]["lookbacks"] = [10, 20]
    raw["sizing"]["vol_window"] = 20
    raw["screen"]["baseline_runs"] = 50
    (tmp_path / "prereg.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return tmp_path


def args(env, *cmd):
    base = {"prereg": ["--prereg", str(env / "prereg.yaml")], "universe": ["--universe", str(env / "universe.json")],
            "data": ["--data-config", str(env / "data.yaml")], "runs": ["--runs-dir", str(env / "runs")]}
    if cmd[1] == "prereg":
        return [*cmd, *base["prereg"], *base["universe"]]
    return [*cmd, *base["prereg"], *base["data"], *base["runs"]]


def test_prereg_validate_prints_hash(env, capsys):
    assert main(args(env, "trend", "prereg", "validate")) == 0
    assert load_prereg(env / "prereg.yaml").prereg_hash() in capsys.readouterr().out


def test_prereg_validate_universe_mismatch_is_exit_2(env):
    u = json.loads((env / "universe.json").read_text(encoding="utf-8"))
    u["um"]["members"] = PAIRS[:2]
    (env / "universe.json").write_text(json.dumps(u), encoding="utf-8")
    assert main(args(env, "trend", "prereg", "validate")) == 2


def test_prereg_freeze_once(env):
    assert main(args(env, "trend", "prereg", "freeze")) == 0
    lock = json.loads((env / "prereg.lock").read_text(encoding="utf-8"))
    assert lock["prereg_hash"] == load_prereg(env / "prereg.yaml").prereg_hash()
    assert main(args(env, "trend", "prereg", "freeze")) == 1


def test_screen_refuses_without_lock(env):
    assert main(args(env, "trend", "screen")) == 1
    assert not (env / "runs" / "trend" / "screen.json").exists()


def test_screen_refuses_hash_mismatch(env):
    assert main(args(env, "trend", "prereg", "freeze")) == 0
    text = (env / "prereg.yaml").read_text(encoding="utf-8").replace("drift_band: 0.2", "drift_band: 0.25")
    (env / "prereg.yaml").write_text(text, encoding="utf-8")
    assert main(args(env, "trend", "screen")) == 1
    assert not (env / "runs" / "trend" / "screen.json").exists()


def test_screen_writes_json_then_refuses_to_overwrite(env, capsys):
    assert main(args(env, "trend", "prereg", "freeze")) == 0
    assert main(args(env, "trend", "screen")) == 0
    out = capsys.readouterr().out
    path = env / "runs" / "trend" / "screen.json"
    res = json.loads(path.read_text(encoding="utf-8"))
    assert res["prereg_hash"] == load_prereg(env / "prereg.yaml").prereg_hash()
    assert res["pairs"] == PAIRS and res["period"] == ["2020-07-01", "2021-03-31"]
    assert res["n_days"] == (date_ms("2021-03-31") - date_ms("2020-07-01")) // DAY_MS + 1
    assert res["paths"]["n"] == 50 and res["decision"] in ("go", "stop")
    assert {"computed_on", "git_commit", "p_screen", "observed_sharpe", "bh_sharpe"} <= set(res)
    assert f"{res['p_screen']:.4f}" in out and res["decision"] in out
    before = path.read_bytes()
    assert main(args(env, "trend", "screen")) == 1
    assert path.read_bytes() == before


def test_screen_stop_writes_summary(env):
    raw = read_yaml_unique(env / "prereg.yaml")
    raw["screen"]["stop_if_p_at_least"] = 1e-9  # every p stops
    (env / "prereg.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert main(args(env, "trend", "prereg", "freeze")) == 0
    assert main(args(env, "trend", "screen")) == 0
    summary = (env / "runs" / "trend" / "summary.md").read_text(encoding="utf-8")
    assert "stop" in summary and "p_screen" in summary and "Phases 3" in summary


def test_screen_go_writes_no_summary(env):
    raw = read_yaml_unique(env / "prereg.yaml")
    raw["screen"]["stop_if_p_at_least"] = 1.0  # stops only if every path beats the strategy
    (env / "prereg.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert main(args(env, "trend", "prereg", "freeze")) == 0
    assert main(args(env, "trend", "screen")) == 0
    assert json.loads((env / "runs" / "trend" / "screen.json").read_text(encoding="utf-8"))["decision"] == "go"
    assert not (env / "runs" / "trend" / "summary.md").exists()


def test_screen_refuses_insample_mismatch(env):
    assert main(args(env, "trend", "prereg", "freeze")) == 0
    cfg = yaml.safe_load((env / "data.yaml").read_text(encoding="utf-8"))
    cfg["insample_end"] = "2021-03-30"
    (env / "data.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    assert main(args(env, "trend", "screen")) == 2


def test_screen_records_pair_last_days_and_data_config(env):
    assert main(args(env, "trend", "prereg", "freeze")) == 0
    assert main(args(env, "trend", "screen")) == 0
    res = json.loads((env / "runs" / "trend" / "screen.json").read_text(encoding="utf-8"))
    assert res["pair_last_day"] == {p: "2021-03-31" for p in PAIRS}
    assert len(res["data_config_hash"]) == 64


def test_screen_refuses_pair_ending_just_before_period_end(env):
    from perpbt.config import DataConfig, from_dict
    cfg = from_dict(DataConfig, yaml.safe_load((env / "data.yaml").read_text(encoding="utf-8")))
    import shutil
    shutil.rmtree(CandleStore(cfg).dir("CCCUSDT", "15m"))
    days = (date_ms("2021-03-28") - date_ms("2020-01-01")) // DAY_MS  # last day 2021-03-27: 4 days short
    c = 100.0 + np.arange(days, dtype=np.float64)
    ts = date_ms("2020-01-01") + DAY_MS * np.arange(days, dtype=np.int64) + DAY_MS - 900_000
    CandleStore(cfg).write("CCCUSDT", "15m", pd.DataFrame({
        "open_ms": ts, "open": c, "high": c, "low": c, "close": c, "volume": 1.0, "quote_volume": 1.0,
        "trades": 1, "taker_buy_volume": 0.0, "source": "bulk_monthly"}))
    assert main(args(env, "trend", "prereg", "freeze")) == 0
    assert main(args(env, "trend", "screen")) == 2
    assert not (env / "runs" / "trend" / "screen.json").exists()


def test_screen_refuses_warmup_mismatch(env):
    assert main(args(env, "trend", "prereg", "freeze")) == 0
    cfg = yaml.safe_load((env / "data.yaml").read_text(encoding="utf-8"))
    cfg["warmup_start"] = "2019-12-01"
    (env / "data.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    assert main(args(env, "trend", "screen")) == 2
