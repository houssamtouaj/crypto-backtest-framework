"""`perpbt carry prereg validate|freeze` and `perpbt carry screen` (carry spec Phase 2 §2.2-§2.4)."""
import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from perpbt.carry.prereg import load_prereg
from perpbt.carry.screen import screen_from_store, write_screen
from perpbt.cli import main
from perpbt.config import DataConfig, from_dict, read_yaml_unique
from perpbt.data.store import DAY_MS, CandleStore, FundingStore, date_ms

ROOT = Path(__file__).resolve().parents[2]
PAIRS = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
RULE = "synthetic rule"
START = "2020-01-01"


def make_env(tmp_path: Path, *, annual: float, last_day: dict[str, str] | None = None) -> Path:
    """Perp, spot and funding stores (one 15m candle per day, funding every 8 h at ``annual``) and the configs."""
    data = tmp_path / "data"
    cfg = {"data_dir": str(data), "insample_start": START, "insample_end": "2023-12-31",
           "holdout_start": "2024-01-01", "holdout_end": "2026-10-20", "warmup_start": START,
           "listing": {p: START for p in PAIRS}}
    (tmp_path / "data.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    dc = from_dict(DataConfig, cfg)
    for j, pair in enumerate(PAIRS):
        end = (last_day or {}).get(pair, "2024-01-05")
        days = (date_ms(end) - date_ms(START)) // DAY_MS + 1
        rng = np.random.default_rng(j)
        c = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.03, days)))
        ts = date_ms(START) + DAY_MS * np.arange(days, dtype=np.int64) + DAY_MS - 900_000
        for market, px in (("um", c * (1 + 1e-3 * rng.normal(size=days))), ("spot", c)):
            CandleStore(dc, market=market).write(pair, "15m", pd.DataFrame({
                "open_ms": ts, "open": px, "high": px, "low": px, "close": px, "volume": 1.0, "quote_volume": 1.0,
                "trades": 1, "taker_buy_volume": 0.0, "source": "bulk_monthly"}))
        fts = date_ms(START) + 8 * 3_600_000 * np.arange(3 * days, dtype=np.int64)
        FundingStore(dc).write(pair, pd.DataFrame({"funding_ms": fts, "rate": annual / 365 / 3, "interval_h": 8,
                                                   "source": "bulk_monthly"}))
    universe = {"um": {"rule": RULE, "members": PAIRS}, "spot": {"rule": RULE, "members": ["BTCUSDT"]}}
    (tmp_path / "universe.json").write_text(json.dumps(universe), encoding="utf-8")
    raw = read_yaml_unique(ROOT / "configs" / "carry" / "prereg.yaml")
    raw["universe"] = {"um": copy.deepcopy(universe["um"])}
    raw["screen"]["bootstrap_n"] = 300
    (tmp_path / "prereg.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return tmp_path


@pytest.fixture(scope="module")
def go_env(tmp_path_factory):
    return make_env(tmp_path_factory.mktemp("go"), annual=0.30)


@pytest.fixture
def env(go_env, tmp_path):
    """A fresh copy of the configs over the shared go store (so a freeze or a run starts clean)."""
    for name in ("data.yaml", "universe.json", "prereg.yaml"):
        (tmp_path / name).write_bytes((go_env / name).read_bytes())
    return tmp_path


def args(env, *cmd, runs="runs"):
    if cmd[1] == "prereg":
        return [*cmd, "--prereg", str(env / "prereg.yaml"), "--universe", str(env / "universe.json")]
    return [*cmd, "--prereg", str(env / "prereg.yaml"), "--data-config", str(env / "data.yaml"),
            "--runs-dir", str(env / runs)]


def test_prereg_validate_prints_hash(env, capsys):
    assert main(args(env, "carry", "prereg", "validate")) == 0
    assert load_prereg(env / "prereg.yaml").prereg_hash() in capsys.readouterr().out


def test_prereg_validate_universe_mismatch_is_exit_2(env):
    u = json.loads((env / "universe.json").read_text(encoding="utf-8"))
    u["um"]["members"].pop()
    (env / "universe.json").write_text(json.dumps(u), encoding="utf-8")
    assert main(args(env, "carry", "prereg", "validate")) == 2


def test_prereg_freeze_once(env):
    assert main(args(env, "carry", "prereg", "freeze")) == 0
    assert (env / "prereg.lock").exists()
    assert main(args(env, "carry", "prereg", "freeze")) == 1


def test_screen_refuses_without_lock(env):
    assert main(args(env, "carry", "screen")) == 1
    assert not (env / "runs" / "carry" / "screen.json").exists()


def test_screen_refuses_hash_mismatch(env):
    assert main(args(env, "carry", "prereg", "freeze")) == 0
    raw = read_yaml_unique(env / "prereg.yaml")
    raw["costs"]["spot_taker"] = 0.00075
    (env / "prereg.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert main(args(env, "carry", "screen")) == 1
    assert not (env / "runs" / "carry" / "screen.json").exists()


def test_screen_writes_json_then_refuses_to_overwrite(env, capsys):
    assert main(args(env, "carry", "prereg", "freeze")) == 0
    assert main(args(env, "carry", "screen")) == 0
    out = capsys.readouterr().out
    path = env / "runs" / "carry" / "screen.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["decision"] == "go" and result["p_screen"] < 0.10 and "p_screen" in out
    assert result["period"] == ["2020-07-01", "2023-12-31"] and result["n_days"] == 1279
    assert result["prereg_hash"] == load_prereg(env / "prereg.yaml").prereg_hash()
    assert result["pair_last_day"] == {p: "2023-12-31" for p in PAIRS}
    assert set(result["rows"]) == {"primary", "always_on"} and "data_config_hash" in result
    assert not (env / "runs" / "carry" / "summary.md").exists()  # a summary only on stop
    before = path.read_bytes()
    assert main(args(env, "carry", "screen")) == 1
    assert path.read_bytes() == before


def test_screen_stop_writes_summary(tmp_path):
    env = make_env(tmp_path, annual=0.02)
    assert main(args(env, "carry", "prereg", "freeze")) == 0
    assert main(args(env, "carry", "screen")) == 0
    result = json.loads((env / "runs" / "carry" / "screen.json").read_text(encoding="utf-8"))
    assert result["decision"] == "stop" and result["p_screen"] >= 0.10
    summary = (env / "runs" / "carry" / "summary.md").read_text(encoding="utf-8")
    assert "Decision: stop" in summary and f"{result['p_screen']:.4f}" in summary


def test_screen_json_byte_identical(env):
    prereg = load_prereg(env / "prereg.yaml")
    dc = from_dict(DataConfig, yaml.safe_load((env / "data.yaml").read_text(encoding="utf-8")))
    texts = []
    for run in ("a", "b"):
        result = {**screen_from_store(prereg, dc), "computed_on": "2026-10-23", "git_commit": "abc"}
        texts.append(write_screen(result, env / run)[0].read_bytes())
    assert texts[0] == texts[1]


@pytest.mark.parametrize("key, value", [("insample_end", "2023-06-30"), ("warmup_start", "2019-12-01")])
def test_screen_refuses_data_config_mismatch(env, key, value):
    assert main(args(env, "carry", "prereg", "freeze")) == 0
    cfg = yaml.safe_load((env / "data.yaml").read_text(encoding="utf-8"))
    cfg[key] = value
    (env / "data.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    assert main(args(env, "carry", "screen")) == 2
    assert not (env / "runs" / "carry" / "screen.json").exists()


def test_screen_refuses_insample_mismatch(env):
    # a prereg reaching past the carry in-sample end does not load (and so never reads past 2023-12-31)
    raw = read_yaml_unique(env / "prereg.yaml")
    raw["periods"]["insample"]["end"], raw["periods"]["holdout"]["start"] = "2024-06-30", "2024-07-01"
    (env / "prereg.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert main(args(env, "carry", "prereg", "freeze")) == 2
    assert main(args(env, "carry", "screen")) == 1
    assert not (env / "runs" / "carry" / "screen.json").exists()


def test_screen_refuses_pair_ending_before_period_end(tmp_path):
    env = make_env(tmp_path, annual=0.30, last_day={"BBBUSDT": "2023-12-20"})
    assert main(args(env, "carry", "prereg", "freeze")) == 0
    assert main(args(env, "carry", "screen")) == 2
    assert not (env / "runs" / "carry" / "screen.json").exists()


def test_screen_refuses_unlisted_member(env):
    assert main(args(env, "carry", "prereg", "freeze")) == 0
    cfg = yaml.safe_load((env / "data.yaml").read_text(encoding="utf-8"))
    del cfg["listing"]["CCCUSDT"]
    (env / "data.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    assert main(args(env, "carry", "screen")) == 2
