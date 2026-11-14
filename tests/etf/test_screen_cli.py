"""`perpbt etf prereg validate|freeze` and `perpbt etf screen` on a synthetic store (ETF spec Phase 2 §2.2-§2.3, §2.5)."""
import dataclasses
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest
import yaml

from perpbt.cli import main
from perpbt.config import read_yaml_unique, to_dict
from perpbt.etf.prereg import load_prereg
from perpbt.etf.screen import screen_from_store
from tests.etf.synth import make_config, make_data, write_store

ROOT = Path(__file__).resolve().parents[2]
HOLDOUT_END, DOWNLOAD = "2018-01-30", "2018-01-31"


@pytest.fixture(scope="module")
def data():
    return make_data(end=date(2018, 1, 31))


@pytest.fixture
def env(tmp_path, data):
    """A synthetic store through 2018-01-31, its data config, and the real prereg pinned to it (50 screen paths)."""
    store = write_store(tmp_path / "etf", data, download_date=DOWNLOAD, holdout_end=HOLDOUT_END)
    cfg = make_config(data_dir=str(tmp_path / "etf"), holdout_end=HOLDOUT_END, download_date=DOWNLOAD)
    (tmp_path / "data.yaml").write_text(yaml.safe_dump(to_dict(cfg), sort_keys=False), encoding="utf-8")
    raw = read_yaml_unique(ROOT / "configs" / "etf" / "prereg.yaml")
    raw["periods"]["holdout"]["end"] = HOLDOUT_END
    raw["data"] = {"download_date": DOWNLOAD, "manifest_sha256": store.manifest_sha256()}
    raw["screen"]["baseline_runs"] = 50
    (tmp_path / "prereg.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return tmp_path


def args(env, *cmd):
    base = ["--prereg", str(env / "prereg.yaml"), "--data-config", str(env / "data.yaml")]
    return [*cmd, *base] if cmd[1] == "prereg" else [*cmd, *base, "--runs-dir", str(env / "runs")]


def test_prereg_validate_prints_hash(env, capsys):
    assert main(args(env, "etf", "prereg", "validate")) == 0
    assert load_prereg(env / "prereg.yaml").prereg_hash() in capsys.readouterr().out


def test_prereg_validate_refuses_another_snapshot(env, capsys):
    manifest = env / "etf" / "manifest.json"
    manifest.write_text(manifest.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert main(args(env, "etf", "prereg", "validate")) == 2
    assert "snapshot changed" in capsys.readouterr().err
    assert main(args(env, "etf", "prereg", "freeze")) == 2
    assert not (env / "prereg.lock").exists()


def test_prereg_freeze_once(env):
    assert main(args(env, "etf", "prereg", "freeze")) == 0
    lock = json.loads((env / "prereg.lock").read_text(encoding="utf-8"))
    assert lock["prereg_hash"] == load_prereg(env / "prereg.yaml").prereg_hash()
    assert main(args(env, "etf", "prereg", "freeze")) == 1


def test_screen_refuses_without_lock(env, capsys):
    assert main(args(env, "etf", "screen")) == 1
    assert "not frozen" in capsys.readouterr().err
    assert not (env / "runs").exists()


def test_screen_runs_once(env, capsys):
    assert main(args(env, "etf", "prereg", "freeze")) == 0
    assert main(args(env, "etf", "screen")) == 0
    out = capsys.readouterr().out
    assert "p_screen" in out
    result = json.loads((env / "runs" / "etf" / "screen.json").read_text(encoding="utf-8"))
    assert result["prereg_hash"] == load_prereg(env / "prereg.yaml").prereg_hash()
    assert result["tickers"] == ["SPY", "QQQ", "IWM", "GLD"] and result["period"] == ["2005-08-01", "2017-12-31"]
    assert result["first_decision_day"] == "2005-07-29" and result["last_return_day"] == "2017-12-29"
    assert result["paths"]["n"] == 50 and result["decision"] in ("go", "stop")
    assert (env / "runs" / "etf" / "summary.md").exists() == (result["decision"] == "stop")
    before = (env / "runs" / "etf" / "screen.json").read_bytes()
    assert main(args(env, "etf", "screen")) == 1
    assert "runs once" in capsys.readouterr().err
    assert (env / "runs" / "etf" / "screen.json").read_bytes() == before


def test_screen_refuses_an_edited_prereg(env, capsys):
    assert main(args(env, "etf", "prereg", "freeze")) == 0
    raw = read_yaml_unique(env / "prereg.yaml")
    raw["rebalance"]["drift_band"] = 0.25
    (env / "prereg.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert main(args(env, "etf", "screen")) == 1
    assert "changed since the freeze" in capsys.readouterr().err
    assert not (env / "runs" / "etf" / "screen.json").exists()


def test_screen_refuses_a_changed_snapshot(env, capsys):
    assert main(args(env, "etf", "prereg", "freeze")) == 0
    manifest = env / "etf" / "manifest.json"
    manifest.write_text(manifest.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert main(args(env, "etf", "screen")) == 1
    assert "snapshot changed" in capsys.readouterr().err
    assert not (env / "runs" / "etf" / "screen.json").exists()


def test_screen_refuses_frames_that_end_early(env, capsys):
    short = make_data(end=date(2017, 12, 15))  # the store lists holdout_end 2018-01-30 but stops on 2017-12-15
    store = write_store(env / "short", short, download_date=DOWNLOAD, holdout_end=HOLDOUT_END)
    cfg = make_config(data_dir=str(env / "short"), holdout_end=HOLDOUT_END, download_date=DOWNLOAD)
    (env / "data.yaml").write_text(yaml.safe_dump(to_dict(cfg), sort_keys=False), encoding="utf-8")
    raw = read_yaml_unique(env / "prereg.yaml")
    raw["data"]["manifest_sha256"] = store.manifest_sha256()
    (env / "prereg.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert main(args(env, "etf", "prereg", "freeze")) == 0
    assert main(args(env, "etf", "screen")) == 1
    assert "2017-12-15" in capsys.readouterr().err
    assert not (env / "runs" / "etf" / "screen.json").exists()


def test_screen_is_seeded_from_master_seed_and_hash(env):
    prereg = load_prereg(env / "prereg.yaml")
    cfg = make_config(data_dir=str(env / "etf"), holdout_end=HOLDOUT_END, download_date=DOWNLOAD)
    a, b = screen_from_store(prereg, cfg), screen_from_store(prereg, cfg)
    assert a == b
    other = dataclasses.replace(prereg, stats=dataclasses.replace(prereg.stats, master_seed=1))
    c = screen_from_store(other, cfg)
    assert c["paths"] != a["paths"] and c["observed_sharpe"] == a["observed_sharpe"]


def test_the_screen_result_is_not_gitignored():
    for rel in ("runs/etf/screen.json", "runs/etf/summary.md"):
        r = subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT)
        assert r.returncode == 1, f"{rel} is ignored by .gitignore"


def test_screen_refuses_a_different_data_config(env, capsys):
    assert main(args(env, "etf", "prereg", "freeze")) == 0
    cfg = make_config(data_dir=str(env / "etf"), holdout_end="2018-01-29", download_date=DOWNLOAD)
    (env / "data.yaml").write_text(yaml.safe_dump(to_dict(cfg), sort_keys=False), encoding="utf-8")
    assert main(args(env, "etf", "screen")) == 1
    assert "holdout.end" in capsys.readouterr().err
