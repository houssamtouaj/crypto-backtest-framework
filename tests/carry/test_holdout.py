"""The one-shot carry holdout (carry spec Phase 5 §5.4), on synthetic stores through the holdout end."""
import json
from datetime import date

import numpy as np
import pytest

import perpbt.carry.holdout as holdout_mod
import perpbt.carry.runner as runner
from perpbt.carry.holdout import HoldoutRefused, done_path, run_holdout
from perpbt.carry.prereg import carry_code_version, lock_code, lock_path
from perpbt.carry.runner import DataNotCaughtUp, check_caught_up, run_insample
from perpbt.cli import main
from perpbt.data.store import DAY_MS, date_ms
from perpbt.experiments.registry_log import Registry
from tests.carry.execframes import pairframe
from tests.carry.runenv import copy_env, data_config, make_env


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    # CCCUSDT ends 2025-05-21, inside the holdout (as EOSUSDT does): a delisting
    return make_env(tmp_path_factory.mktemp("holdout"), end="2026-10-20", last_day={"CCCUSDT": "2025-05-21"})


@pytest.fixture
def env(store, tmp_path):
    return copy_env(store, tmp_path)


def locked_with_insample(env, *, lock_cv=None, run_cv=None):
    lock_code(env / "prereg.yaml", today=date(2026, 10, 25), code_version=lock_cv)
    run_insample(env / "prereg.yaml", data_config(env), env / "runs", rows=["primary"], code_version=run_cv)


def holdout(env, **kw):
    return run_holdout(env / "prereg.yaml", data_config(env), runs_dir=env / "runs", **kw)


@pytest.fixture
def no_loading(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a refused holdout must not load data")

    monkeypatch.setattr(holdout_mod, "load_frames", refuse)


# --- refusals -----------------------------------------------------------------------------------------------------

def test_refuses_unfrozen_or_changed_prereg(env, no_loading):
    locked_with_insample(env)
    text = (env / "prereg.yaml").read_text(encoding="utf-8")
    (env / "prereg.yaml").write_text(text.replace("entry_annual: 0.1", "entry_annual: 0.11", 1), encoding="utf-8")
    with pytest.raises(HoldoutRefused, match="changed since the freeze"):
        holdout(env)
    lock_path(env / "prereg.yaml").unlink()
    with pytest.raises(HoldoutRefused, match="not frozen"):
        holdout(env)
    assert not done_path(env / "runs").exists()


def test_refuses_without_code_lock(env, no_loading):
    with pytest.raises(HoldoutRefused, match="lock-code"):
        holdout(env)


def test_refuses_without_insample_primary(env, no_loading):
    lock_code(env / "prereg.yaml", today=date(2026, 10, 25))
    with pytest.raises(HoldoutRefused, match="carry run --primary"):
        holdout(env)
    assert not done_path(env / "runs").exists()


def test_refuses_when_done_exists(env, no_loading):
    locked_with_insample(env)
    done_path(env / "runs").parent.mkdir(parents=True)
    done_path(env / "runs").write_text("{}", encoding="utf-8")
    with pytest.raises(HoldoutRefused, match="DONE"):
        holdout(env)


def test_refuses_a_changed_code_version(env, no_loading, monkeypatch):
    locked_with_insample(env, lock_cv="f" * 64, run_cv="f" * 64)
    monkeypatch.setattr(holdout_mod, "insample_primary_identical", lambda *a, **k: False)
    monkeypatch.setattr(holdout_mod, "load_insample", lambda *a, **k: {})
    with pytest.raises(HoldoutRefused, match="allow-code-change"):
        holdout(env)
    with pytest.raises(HoldoutRefused, match="reason"):
        holdout(env, allow_code_change=True)
    assert not done_path(env / "runs").exists()


def test_check_caught_up():
    days = 30
    pf = pairframe("AAAUSDT", spot=np.full(days, 100.0), perp=np.full(days, 100.0))  # 2020-01-01 .. 2020-01-30
    check_caught_up({"AAAUSDT": pf}, "2020-01-30")  # through the end
    check_caught_up({"AAAUSDT": pf}, "2020-02-07")  # 8 days early: a delisting
    for end in ("2020-01-31", "2020-02-06"):  # 1 to 7 days early: the data was not fetched
        with pytest.raises(DataNotCaughtUp, match="AAAUSDT"):
            check_caught_up({"AAAUSDT": pf}, end)
    short = pairframe("AAAUSDT", spot=np.full(days, 100.0), perp=np.full(days, 100.0), drop_spot=[(29, 95)])
    assert short.last_common_ms < date_ms("2020-01-30") + DAY_MS - 900_000
    with pytest.raises(DataNotCaughtUp, match="partial"):
        check_caught_up({"AAAUSDT": short}, "2020-01-30")


# --- runs -------------------------------------------------------------------------------------------------------

def test_done_written_when_variants_fail(env, monkeypatch):
    locked_with_insample(env)

    def boom(*a, **k):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(runner, "evaluate_variant", boom)
    content = holdout(env)
    done = json.loads(done_path(env / "runs").read_text(encoding="utf-8"))
    assert done == content and done["status"] == "failed"
    assert len(done["failed"]) == 7 * 4 - 7 and done["skipped"] == [f"{r}/CCCUSDT" for r in (
        "primary", "always_on", "entry_5", "entry_20", "lookback_3d", "lookback_14d", "lev_1")]
    rows = [r for r in Registry(env / "runs" / "carry").rows() if r["kind"] == "holdout"]
    assert {r["status"] for r in rows} == {"started", "failed", "skipped"}
    with pytest.raises(HoldoutRefused, match="DONE"):
        holdout(env)


def test_done_written_when_the_run_is_interrupted(env, monkeypatch):
    locked_with_insample(env)

    def interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(holdout_mod, "run_variants", interrupt)
    with pytest.raises(KeyboardInterrupt):
        holdout(env)
    assert json.loads(done_path(env / "runs").read_text(encoding="utf-8"))["status"] == "failed"


def test_holdout_output_neutral_end_to_end(env, capsys):
    # the in-sample primary ran under the "locked" version; the current code gives the same bytes
    locked_with_insample(env, lock_cv="f" * 64, run_cv="f" * 64)
    assert main(["carry", "holdout", "--prereg", str(env / "prereg.yaml"), "--data-config", str(env / "data.yaml"),
                 "--runs-dir", str(env / "runs")]) == 0
    done = json.loads(done_path(env / "runs").read_text(encoding="utf-8"))
    assert done["status"] == "ok" and done["failed"] == [] and len(done["variant_ids"]) == 28
    assert done["output_neutral"] is True and done["code_version"] == carry_code_version()
    assert done["lock_code_version"] == "f" * 64 and done["reason"] is None
    rows = Registry(env / "runs" / "carry").rows()
    neutral = [r for r in rows if r["status"] == "output_neutral"]
    assert len(neutral) == 1 and neutral[0]["kind"] == "holdout" and neutral[0]["period"] == "insample"
    folders = [p for p in (env / "runs" / "carry" / "holdout").iterdir() if p.is_dir()]
    assert len(folders) == 28 - 7
    stats = json.loads((folders[0] / "stats.json").read_text(encoding="utf-8"))
    assert stats["period"] == {"name": "holdout", "start": "2024-01-01", "end": "2026-10-20"}
    assert main(["carry", "holdout", "--prereg", str(env / "prereg.yaml"), "--data-config",
                 str(env / "data.yaml"), "--runs-dir", str(env / "runs")]) == 1
    assert "DONE" in capsys.readouterr().err
