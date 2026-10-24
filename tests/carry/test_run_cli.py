"""`perpbt carry lock-code` and `perpbt carry run`, the code check and results.parquet (carry spec Phase 5)."""
import json
from datetime import date

import pandas as pd
import pytest

from perpbt.carry.prereg import load_prereg, lock_code, lock_path, read_lock
from perpbt.carry.runner import (
    RESULT_COLUMNS, CodeRefused, code_check, insample_period, insample_primary_identical, load_frames,
    rebuild_results, run_insample, variants,
)
from perpbt.cli import main
from perpbt.experiments.registry_log import Registry
from tests.carry.runenv import PAIRS, copy_env, data_config, make_env


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    return make_env(tmp_path_factory.mktemp("store"))


@pytest.fixture
def env(store, tmp_path):
    return copy_env(store, tmp_path)


def run_args(env, *extra):
    return ["carry", "run", *extra, "--prereg", str(env / "prereg.yaml"), "--data-config", str(env / "data.yaml"),
            "--runs-dir", str(env / "runs")]


def folder_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
            and p.parent != root}


# --- code_check ---------------------------------------------------------------------------------------------------

LOCK = {"code_version": "a" * 64}


def never():
    raise AssertionError("the output-neutral check must not run")


def test_code_check_equal_and_unlocked():
    assert code_check(LOCK, "a" * 64, allow_code_change=False, reason=None, neutral=never) == {
        "code_version": "a" * 64, "lock_code_version": "a" * 64, "output_neutral": False, "reason": None}
    with pytest.raises(CodeRefused, match="lock-code"):
        code_check({"code_version": None}, "a" * 64, allow_code_change=False, reason=None, neutral=never)


def test_code_check_changed():
    with pytest.raises(CodeRefused, match="allow-code-change"):
        code_check(LOCK, "b" * 64, allow_code_change=False, reason=None, neutral=lambda: False)
    out = code_check(LOCK, "b" * 64, allow_code_change=False, reason=None, neutral=lambda: True)
    assert out["output_neutral"] is True and out["reason"] is None and out["code_version"] == "b" * 64
    out = code_check(LOCK, "b" * 64, allow_code_change=True, reason=" fix a crash ", neutral=lambda: False)
    assert out == {"code_version": "b" * 64, "lock_code_version": "a" * 64, "output_neutral": False,
                   "reason": "fix a crash"}
    for reason in (None, "  "):
        with pytest.raises(CodeRefused, match="reason"):
            code_check(LOCK, "b" * 64, allow_code_change=True, reason=reason, neutral=never)


# --- lock-code and run --------------------------------------------------------------------------------------------

def test_run_refuses_before_lock_code(env, capsys):
    assert main(run_args(env, "--primary")) == 1
    assert "lock-code" in capsys.readouterr().err
    assert not (env / "runs").exists()


def test_lock_code_cli_once(env, capsys):
    assert main(["carry", "lock-code", "--prereg", str(env / "prereg.yaml")]) == 0
    lock = read_lock(lock_path(env / "prereg.yaml"))
    assert lock["code_version"] is not None and load_prereg(env / "prereg.yaml").periods.forward_start is not None
    assert main(["carry", "lock-code", "--prereg", str(env / "prereg.yaml")]) == 1
    assert "already" in capsys.readouterr().err
    assert read_lock(lock_path(env / "prereg.yaml")) == lock


def test_run_primary_then_rerun_changes_nothing(env, capsys):
    lock_code(env / "prereg.yaml", today=date(2026, 10, 25))
    assert main(run_args(env, "--primary")) == 0
    root = env / "runs" / "carry"
    prereg = load_prereg(env / "prereg.yaml")
    ids = {v.variant_id(prereg.prereg_hash(), insample_period(prereg)) for v in variants(prereg, ["primary"])}
    assert {p.name for p in root.iterdir() if p.is_dir()} == ids  # the portfolio and each of the 3 pairs
    results = pd.read_parquet(root / "results.parquet")
    assert list(results.columns) == list(RESULT_COLUMNS)
    assert sorted(results["name"]) == sorted(["primary", *(f"primary/{p}" for p in PAIRS)])
    assert set(results["kind"]) == {"run"} and set(results["period"]) == {"insample"}
    assert results["code_version"].notna().all() and results["p2"].notna().all()
    before = folder_bytes(root)
    rows = Registry(root).rows()
    assert [r["status"] for r in rows].count("ok") == 4
    capsys.readouterr()
    assert main(run_args(env, "--primary")) == 0
    assert "exists 4" in capsys.readouterr().out
    assert folder_bytes(root) == before and Registry(root).rows() == rows


def test_run_refuses_a_changed_code_version(env, capsys):
    lock_code(env / "prereg.yaml", today=date(2026, 10, 25), code_version="f" * 64)
    assert main(run_args(env, "--exploratory")) == 1
    assert "allow-code-change" in capsys.readouterr().err
    assert main(run_args(env, "--exploratory", "--allow-code-change")) == 1  # no reason
    assert not (env / "runs" / "carry").exists() or not any((env / "runs" / "carry").iterdir())


def test_run_refuses_a_changed_prereg(env, capsys):
    lock_code(env / "prereg.yaml", today=date(2026, 10, 25))
    text = (env / "prereg.yaml").read_text(encoding="utf-8")
    (env / "prereg.yaml").write_text(text.replace("entry_annual: 0.1", "entry_annual: 0.11", 1), encoding="utf-8")
    assert main(run_args(env, "--primary")) == 1
    assert "changed since the freeze" in capsys.readouterr().err


def test_output_neutral_check(env):
    lock = lock_code(env / "prereg.yaml", today=date(2026, 10, 25))
    prereg = load_prereg(env / "prereg.yaml")
    frames = load_frames(data_config(env), PAIRS, end=prereg.periods.insample_end)
    root = env / "runs" / "carry"
    cv = lock["code_version"]
    assert insample_primary_identical(prereg, frames, root, cv) is False  # no locked run yet
    assert main(run_args(env, "--primary")) == 0
    assert insample_primary_identical(prereg, frames, root, cv) is True
    primary = next(v for v in variants(prereg, ["primary"]) if v.name == "primary")
    stats = root / primary.variant_id(prereg.prereg_hash(), insample_period(prereg)) / "stats.json"
    original = stats.read_bytes()
    stats.write_bytes(original.replace(b'"row": "primary"', b'"row": "primary" '))
    assert insample_primary_identical(prereg, frames, root, cv) is False
    stats.write_bytes(original)
    assert not list(root.glob("**/neutral*"))


def test_output_neutral_reference_must_be_the_locked_code(env):
    # the folder's run was made by another code version (e.g. a fix run with --allow-code-change): not the lock's
    lock_code(env / "prereg.yaml", today=date(2026, 10, 25), code_version="a" * 64)
    run_insample(env / "prereg.yaml", data_config(env), env / "runs", rows=["primary"], code_version="b" * 64,
                 allow_code_change=True, reason="a crash fix")
    prereg = load_prereg(env / "prereg.yaml")
    frames = load_frames(data_config(env), PAIRS, end=prereg.periods.insample_end)
    root = env / "runs" / "carry"
    assert insample_primary_identical(prereg, frames, root, "b" * 64) is True
    assert insample_primary_identical(prereg, frames, root, "a" * 64) is False


def test_output_neutral_run_keeps_the_complete_primary(env):
    lock_code(env / "prereg.yaml", today=date(2026, 10, 25))
    assert main(run_args(env, "--primary")) == 0
    root = env / "runs" / "carry"
    before = folder_bytes(root)
    mtimes = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file() and p.parent != root}
    done = run_insample(env / "prereg.yaml", data_config(env), env / "runs", rows=["primary"],
                        code_version="b" * 64)  # a changed code version that the neutral rerun clears
    assert done == {"ok": [], "exists": ["primary", *(f"primary/{p}" for p in PAIRS)], "skipped": [], "failed": []}
    assert folder_bytes(root) == before
    assert {p: p.stat().st_mtime_ns for p in mtimes} == mtimes
    statuses = [r["status"] for r in Registry(root).rows()]
    assert statuses[-1] == "output_neutral" and statuses.count("ok") == 4


def test_cli_warns_about_a_reason_without_the_flag(env, capsys):
    lock_code(env / "prereg.yaml", today=date(2026, 10, 25))
    assert main(run_args(env, "--primary", "--reason", "ignored")) == 0
    assert "--reason is ignored without --allow-code-change" in capsys.readouterr().err


def test_cli_run_time_error_is_exit_1(env, capsys, monkeypatch):
    import perpbt.carry.runner as runner

    lock_code(env / "prereg.yaml", today=date(2026, 10, 25))

    def bad(*a, **k):
        raise ValueError("a PairFrame build error")

    monkeypatch.setattr(runner, "load_frames", bad)
    assert main(run_args(env, "--primary")) == 1
    assert "PairFrame build error" in capsys.readouterr().err


# --- results.parquet ----------------------------------------------------------------------------------------------

def test_results_cover_every_folder_kind(env):
    lock_code(env / "prereg.yaml", today=date(2026, 10, 25))
    assert main(run_args(env, "--primary")) == 0
    root = env / "runs" / "carry"
    prereg = load_prereg(env / "prereg.yaml")
    primary = next(v for v in variants(prereg, ["primary"]) if v.name == "primary")
    src = root / primary.variant_id(prereg.prereg_hash(), insample_period(prereg))
    for sub in ("holdout", "forward"):  # copies under the other folders read as those kinds
        dst = root / sub / ("0" * 64)
        dst.mkdir(parents=True)
        for f in ("stats.json", "variant.json"):
            (dst / f).write_bytes((src / f).read_bytes())
    review = json.loads((src / "stats.json").read_text(encoding="utf-8"))
    review = {"schema": "carry-forward-review/1", "prereg_hash": review["prereg_hash"],
              "period": {"name": "forward_12m", "start": "2026-10-26", "end": "2027-10-25"}, "row": "primary",
              "n_days": 365, **{k: review["diagnostics"][k] for k in ("annual", "decomposition", "margin")},
              "metrics": {}}
    dst = root / "forward" / ("1" * 64)
    dst.mkdir()
    (dst / "stats.json").write_text(json.dumps(review), encoding="utf-8")
    (dst / "variant.json").write_bytes((src / "variant.json").read_bytes())
    results = pd.read_parquet(rebuild_results(root))
    assert results["kind"].tolist()[-3:] == ["holdout", "forward", "forward"]
    last = results.iloc[-1]
    assert last["period"] == "forward_12m" and last["n_days"] == 365 and pd.isna(last["p1"]) and pd.isna(last["p2"])
    assert last["ann_return"] == review["annual"]["ann_return"] and pd.isna(last["edge"])
