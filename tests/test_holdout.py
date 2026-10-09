"""Spec §6.6 (test 6.6): the one-shot holdout runner and its guards."""
import io
import json
import tokenize
from pathlib import Path

import pytest
import yaml

from perpbt.config import VariantConfig
from perpbt.data.store import CandleStore, FundingStore, HoldoutAccessError, date_ms
from perpbt.experiments.grid import enumerate_grid, primary_variants
from perpbt.experiments.holdout import HoldoutRefused, check_holdout_config, done_path, run_holdout
from perpbt.experiments.prereg import freeze, load_prereg, lock_path
from perpbt.experiments.registry import Registry, read_results
from perpbt.experiments.runner import load_market, run_many, variant_dir
from perpbt.stats.variant import read_stats
from perpbt.version import code_version
from tests.experiment_harness import make_world, prereg_text

KW = {"download": "2020-01-19", "holdout_end": "2020-01-18"}


@pytest.fixture
def world(tmp_path):
    """Frozen prereg, data and the in-sample primary runs."""
    path, prereg, data_cfg, runs = make_world(tmp_path, **KW)
    freeze(path)
    assert not run_many(primary_variants(prereg), data_cfg, runs_dir=runs, workers=1)["failed"]
    return path, prereg, data_cfg, runs


def go(world, **kw):
    path, _, data_cfg, runs = world
    return run_holdout(path, data_dir=data_cfg.data_dir, runs_dir=runs, **kw)


def test_holdout_runs_the_nine_primary_cells_once(world):
    path, prereg, data_cfg, runs = world
    done = go(world)
    assert done["status"] == "ok" and done["reason"] is None and done["prior_attempts"] == []
    cv = code_version()
    expect = [v.cfg.variant_id(cv) for v in primary_variants(prereg, holdout=True)]
    assert done["variant_ids"] == expect and done["prereg_hash"] == prereg.prereg_hash()
    assert yaml.safe_load(done_path(runs).read_text(encoding="utf-8"))["variant_ids"] == expect
    ins = [v.cfg.variant_id(cv) for v in primary_variants(prereg)]
    for vid, ref_id in zip(expect, ins):
        st = read_stats(variant_dir(runs, vid) / "stats.json")
        assert st["is_holdout"] is True and st["baselines"]["n_runs"] == prereg.stats.baseline_runs_primary
        assert st["holm"]["family"] == "holdout" and st["holm"]["variant_ids"] == expect
        assert st["insample_ref"] == read_stats(variant_dir(runs, ref_id) / "stats.json")["insample_ref"]
        assert st["period_start_ms"] == date_ms("2020-01-13") and st["period_end_ms"] == date_ms("2020-01-19")
    res = read_results(runs)
    assert res["is_holdout"].sum() == 4 and res.loc[res["is_holdout"], "p_a_adj"].notna().all()

    with pytest.raises(HoldoutRefused, match="touched once"):  # second invocation
        go(world)
    # the guard is not globally disabled
    with pytest.raises(HoldoutAccessError):
        CandleStore(data_cfg).load("BTCUSDT", "15m", date_ms("2020-01-01"), date_ms("2020-01-14"))
    with pytest.raises(HoldoutAccessError):
        FundingStore(data_cfg).load("BTCUSDT", date_ms("2020-01-01"), date_ms("2020-01-14"))
    with pytest.raises(HoldoutAccessError):
        load_market(primary_variants(prereg, holdout=True)[0].cfg, data_cfg)
    assert CandleStore(data_cfg).load("BTCUSDT", "15m", date_ms("2020-01-01"), date_ms("2020-01-13")).ts.size


def test_a_deleted_done_is_reported_as_a_second_touch(world):
    first = go(world)
    done_path(world[3]).unlink()
    second = go(world)
    assert second["prior_attempts"] == [first["batch"]]
    batches = {r["batch"] for r in Registry(world[3]).rows() if r["is_holdout"]}
    assert batches == {first["batch"], second["batch"]}


def test_refuses_without_a_lock_or_when_the_prereg_changed(world):
    path, _, _, runs = world
    original = path.read_text(encoding="utf-8")
    path.write_text(prereg_text(r_target="3.0", **KW), encoding="utf-8")
    with pytest.raises(HoldoutRefused, match="prereg_hash mismatch"):
        go(world)
    path.write_text(original.replace("2020-01-18", "2020-01-17"), encoding="utf-8")  # holdout.end is not hashed
    lock_path(path).rename(path.with_suffix(".bak"))
    with pytest.raises(HoldoutRefused, match="freeze"):
        go(world)
    assert not done_path(runs).exists() and Registry(runs).rows()[-1]["is_holdout"] is False


def test_code_change_refuses_without_the_flag_and_records_the_reason(world):
    _, _, _, runs = world
    with pytest.raises(HoldoutRefused, match="changed since the freeze"):
        go(world, current_code_version="0" * 64)
    with pytest.raises(HoldoutRefused, match="needs a --reason"):
        go(world, current_code_version="0" * 64, allow_code_change=True, reason="  ")
    assert not done_path(runs).exists()
    done = go(world, current_code_version="0" * 64, allow_code_change=True, reason="fix funding sign")
    assert done["reason"] == "fix funding sign" and done["code_version"] == "0" * 64
    rows = [r for r in Registry(runs).rows() if r["is_holdout"]]
    assert len(rows) == 8 and {r["reason"] for r in rows} == {"fix funding sign"}


def test_refuses_without_the_in_sample_primary_runs(tmp_path):
    path, _, data_cfg, runs = make_world(tmp_path, **KW)
    freeze(path)
    with pytest.raises(HoldoutRefused, match="in-sample primary"):
        run_holdout(path, data_dir=data_cfg.data_dir, runs_dir=runs)
    assert not done_path(runs).exists()


def test_only_primary_holdout_configs_are_accepted(world):
    _, prereg, _, _ = world
    for v in primary_variants(prereg, holdout=True):
        check_holdout_config(v.cfg, prereg)
    grid_cfg = next(v for v in enumerate_grid(prereg) if not v.is_primary).cfg
    as_holdout = VariantConfig(**{**grid_cfg.__dict__, "period_start": "2020-01-13", "period_end": "2020-01-18",
                                  "is_holdout": True})
    for cfg in (grid_cfg, as_holdout, primary_variants(prereg)[0].cfg):
        with pytest.raises(HoldoutRefused, match="not a primary holdout"):
            check_holdout_config(cfg, prereg)


def _passes_allow_holdout_true(path: Path) -> bool:
    """True when the code (not a string or comment) contains the keyword argument ``allow_holdout=True``."""
    toks = [t.string for t in tokenize.generate_tokens(io.StringIO(path.read_text("utf-8")).readline)
            if t.type in (tokenize.NAME, tokenize.OP)]
    return any(toks[k:k + 3] == ["allow_holdout", "=", "True"] for k in range(len(toks)))


def test_allow_holdout_true_appears_only_in_holdout_py():
    """Phase 8 §8.3: the grep the step-5 review runs, over code tokens."""
    root = Path(__file__).resolve().parents[1] / "perpbt"
    hits = [p.relative_to(root).as_posix() for p in sorted(root.rglob("*.py")) if _passes_allow_holdout_true(p)]
    assert hits == ["experiments/holdout.py"], json.dumps(hits)
