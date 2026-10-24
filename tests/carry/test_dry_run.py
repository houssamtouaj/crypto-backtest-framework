"""Phase 5 exit criterion: a full dry run on a synthetic dataset, end to end (carry spec Phase 5 §5.8).

Freeze → lock-code → `carry run` (all 28 in-sample variants of three synthetic pairs) → a rerun that changes
nothing → `carry holdout` → the 12-month forward review. No market data is read.
"""
import json
from datetime import date
from pathlib import Path

import pandas as pd

import perpbt.carry.runner as runner
from perpbt.carry.forward import REVIEW_SCHEMA, run_forward
from perpbt.carry.holdout import done_path
from perpbt.carry.prereg import lock_code
from perpbt.carry.runner import KINDS, REGISTRY_KEYS
from perpbt.cli import main
from perpbt.experiments.registry_log import Registry
from tests.carry.runenv import data_config, make_env

ROOT = Path(__file__).resolve().parents[2]


def cli(env, *cmd):
    return main(["carry", *cmd, "--prereg", str(env / "prereg.yaml"), "--data-config", str(env / "data.yaml"),
                 "--runs-dir", str(env / "runs")])


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and p.parent != root}


def test_dry_run_end_to_end(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(runner.KNOWN_DELISTINGS, "CCCUSDT", "2025-05-21")  # as EOSUSDT
    env = make_env(tmp_path, end="2027-10-27", last_day={"CCCUSDT": "2025-05-21"})
    lock = lock_code(env / "prereg.yaml", today=date(2026, 10, 25))
    root = env / "runs" / "carry"

    assert cli(env, "run") == 0
    insample = [p for p in root.iterdir() if p.is_dir()]
    assert len(insample) == 7 * 4
    before = snapshot(root)
    capsys.readouterr()
    assert cli(env, "run") == 0
    assert "ok 0, exists 28, skipped 0, failed 0" in capsys.readouterr().out
    assert snapshot(root) == before

    assert cli(env, "holdout") == 0
    done = json.loads(done_path(env / "runs").read_text(encoding="utf-8"))
    assert done["status"] == "ok" and done["code_version"] == lock["code_version"]
    assert len(done["skipped"]) == 7 and all(name.endswith("/CCCUSDT") for name in done["skipped"])
    assert len([p for p in (root / "holdout").iterdir() if p.is_dir()]) == 21

    out = run_forward(env / "prereg.yaml", data_config(env), runs_dir=env / "runs", review=12,
                      today=date(2027, 10, 27))
    assert json.loads((out / "stats.json").read_text(encoding="utf-8"))["schema"] == REVIEW_SCHEMA

    rows = Registry(root).rows()
    assert all(sorted(r) == sorted(REGISTRY_KEYS) for r in rows)
    assert {r["kind"] for r in rows} == set(KINDS)
    started = [(r["batch"], r["variant_id"]) for r in rows if r["status"] == "started"]
    finished = [(r["batch"], r["variant_id"]) for r in rows if r["status"] == "ok"]
    assert sorted(started) == sorted(finished)  # every attempt completed
    assert len(started) == 28 + 21 + 1

    results = pd.read_parquet(root / "results.parquet")
    assert results["kind"].value_counts().to_dict() == {"run": 28, "holdout": 21, "forward": 1}
    assert results["code_version"].eq(lock["code_version"]).all()
    for path in root.rglob("stats.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["schema"] in ("carry-stats/1", REVIEW_SCHEMA)
        assert doc["prereg_hash"] == lock["prereg_hash"]
