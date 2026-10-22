"""Spec §6.5 (tests 6.4, 6.5): the runner, resume, the spawn driver, the baselines pass and the family passes."""
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from perpbt.config import VariantConfig, load_yaml
from perpbt.data.store import HoldoutAccessError
from perpbt.experiments.grid import enumerate_grid, primary_variants
from perpbt.experiments.registry import RESULT_COLUMNS, Registry, read_results
from perpbt.experiments.runner import (
    FamilyIncomplete,
    apply_dsr,
    apply_holm,
    baselines_many,
    load_market,
    run_many,
    run_variant,
    variant_dir,
)
from perpbt.stats.multiplicity import holm
from perpbt.stats.variant import read_stats
from perpbt.version import code_version
from tests.experiment_harness import make_world, write_market

VARIANT_FILES = {"orders.parquet", "fills.parquet", "trades.parquet", "daily.parquet", "events.parquet",
                 "buyhold.parquet", "config.yaml", "stats.json"}
BASELINE_FILES = {"baseline_a.parquet", "baseline_a_runs.parquet", "baseline_b.parquet"}


def files(d: Path) -> dict[str, bytes]:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()
            and p.parent != d}  # variant folders only, not the registry or results


def ids(variants):
    cv = code_version()
    return [v.cfg.variant_id(cv) for v in variants]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("world"))


@pytest.fixture(scope="module")
def ran(world, tmp_path_factory):
    """The whole tiny grid (12 variants) run in-process."""
    _, prereg, data_cfg, _ = world
    runs = tmp_path_factory.mktemp("ran") / "runs"
    done = run_many(enumerate_grid(prereg), data_cfg, runs_dir=runs, workers=1)
    assert len(done["ok"]) == 12 and not done["failed"]
    return runs


def test_run_variant_writes_every_output_and_two_registry_rows(world, tmp_path):
    _, prereg, data_cfg, _ = world
    v = primary_variants(prereg)[0]
    out = run_variant(v.cfg, data_cfg, runs_dir=tmp_path, labels=v.labels())
    vid = v.cfg.variant_id(code_version())
    assert out == variant_dir(tmp_path, vid) and {p.name for p in out.iterdir()} == VARIANT_FILES
    assert load_yaml(out / "config.yaml", VariantConfig) == v.cfg
    st = read_stats(out / "stats.json")
    assert st["experiment"] == {"is_primary": True, "members": ["heatmap_1"], "code_version": code_version()}
    assert st["baselines"]["n_runs"] is None and st["summary"]["n_trades"] > 0
    trades = pd.read_parquet(out / "trades.parquet")
    assert {"regime_trend", "regime_vol"} <= set(trades.columns) and len(trades) == st["summary"]["n_trades"]
    bh = pd.read_parquet(out / "buyhold.parquet")
    assert len(bh) == st["buy_and_hold"]["n_days"] == 11
    rows = Registry(tmp_path).rows()
    assert [(r["variant_id"], r["kind"], r["status"]) for r in rows] == [(vid, "run", "started"), (vid, "run", "ok")]
    assert rows[1]["runtime_s"] > 0 and rows[1]["code_version"] == code_version()


def test_a_holdout_config_cannot_load_through_the_runner(world, tmp_path):
    _, prereg, data_cfg, _ = world
    cfg = primary_variants(prereg)[0].cfg
    hold = VariantConfig(**{**cfg.__dict__, "period_start": "2020-01-13", "period_end": "2020-01-18",
                            "is_holdout": True})
    with pytest.raises(HoldoutAccessError):
        load_market(hold, data_cfg)
    with pytest.raises(HoldoutAccessError):
        run_variant(hold, data_cfg, runs_dir=tmp_path)
    assert [r["status"] for r in Registry(tmp_path).rows()] == ["started", "failed"]
    assert "HoldoutAccessError" in Registry(tmp_path).rows()[1]["error"]


def test_failed_variants_resume_without_rerunning_finished_ones(tmp_path):
    _, prereg, data_cfg, runs = make_world(tmp_path, skip_1m=("ETHUSDT",))
    grid = enumerate_grid(prereg)
    done = run_many(grid, data_cfg, runs_dir=runs, workers=1)
    eth = {v for v, g in zip(ids(grid), grid) if g.cfg.pair == "ETHUSDT"}
    assert set(done["failed"]) == eth and len(done["ok"]) == 6
    reg = Registry(runs)
    assert all("FileNotFoundError" in r["error"] for r in reg.rows() if r["status"] == "failed")
    res = read_results(runs)
    assert set(res["variant_id"]) == set(reg.latest_ok()) == set(done["ok"])  # results match the ok set
    assert list(res.columns) == list(RESULT_COLUMNS)

    write_market(data_cfg, "ETHUSDT", seed=12)  # the 1m data arrive; resume
    again = run_many(grid, data_cfg, runs_dir=runs, workers=1)
    assert set(again["ok"]) == eth and set(again["skipped"]) == set(done["ok"]) and not again["failed"]
    started = pd.Series([r["variant_id"] for r in reg.rows() if r["status"] == "started"]).value_counts()
    assert all(started[v] == (2 if v in eth else 1) for v in ids(grid))  # one started row per attempt
    assert len(read_results(runs)) == 12


def test_force_reruns_and_outputs_are_byte_identical(world, ran, tmp_path):
    _, prereg, data_cfg, _ = world
    runs = tmp_path / "runs"
    shutil.copytree(ran, runs)
    before = files(runs)
    assert len(before) == 12 * len(VARIANT_FILES)
    done = run_many(enumerate_grid(prereg), data_cfg, runs_dir=runs, workers=1, force=True)
    assert len(done["ok"]) == 12 and not done["skipped"]
    assert files(runs) == before
    assert len(Registry(runs).rows()) == len(Registry(ran).rows()) + 24


def test_the_spawn_driver_gives_the_same_outputs(world, ran, tmp_path):
    _, prereg, data_cfg, _ = world
    runs = tmp_path / "runs"
    done = run_many(enumerate_grid(prereg), data_cfg, runs_dir=runs, workers=2)
    assert len(done["ok"]) == 12 and not done["failed"]
    assert files(runs) == files(ran)
    assert read_results(runs).drop(columns=["run_ms", "runtime_s", "git_commit", "artifacts_path"]).equals(
        read_results(ran).drop(columns=["run_ms", "runtime_s", "git_commit", "artifacts_path"]))


def test_baselines_pass_holm_dsr_and_results(world, ran, tmp_path):
    _, prereg, data_cfg, _ = world
    runs = tmp_path / "runs"
    shutil.copytree(ran, runs)
    prim = primary_variants(prereg)
    with pytest.raises(FamilyIncomplete, match="pre-registered 30 runs"):
        apply_holm(runs, ids(prim), family="insample", alpha=0.05, baseline_runs=30)
    baselines_many(prim, data_cfg, runs_dir=runs, workers=1, n_runs=3)
    with pytest.raises(FamilyIncomplete, match="pre-registered 30 runs"):  # a smaller budget is not the prereg's
        apply_holm(runs, ids(prim), family="insample", alpha=0.05, baseline_runs=30)

    done = baselines_many(prim, data_cfg, runs_dir=runs, workers=1)  # 3 runs stored: rerun at 30
    assert len(done["ok"]) == 4 and not done["failed"] and not done["missing"]
    for vid in ids(prim):
        d = variant_dir(runs, vid)
        assert BASELINE_FILES <= {p.name for p in d.iterdir()}
        st = read_stats(d / "stats.json")
        assert st["baselines"]["n_runs"] == 30 and 0 < st["baselines"]["p_a"] <= 1
        assert st["experiment"]["is_primary"] is True
        assert len(pd.read_parquet(d / "baseline_b.parquet")) == 30
    snapshot = files(runs)
    assert baselines_many(prim, data_cfg, runs_dir=runs, workers=1)["skipped"] == ids(prim)
    again = baselines_many(prim, data_cfg, runs_dir=runs, workers=1, force=True)  # same seed: same bytes
    assert len(again["ok"]) == 4 and files(runs) == snapshot

    grid_rest = [v for v in enumerate_grid(prereg) if not v.is_primary]
    assert len(baselines_many(grid_rest, data_cfg, runs_dir=runs, workers=1)["ok"]) == 8
    assert read_stats(variant_dir(runs, ids(grid_rest)[0]) / "stats.json")["baselines"]["n_runs"] == 10

    blocks = apply_holm(runs, ids(prim), family="insample", alpha=0.05, baseline_runs=30)
    assert blocks[0]["n_tested"] == {"a": 4, "b": 4, "bh": 4} and blocks[0]["baseline_runs"] == 30
    stats = [read_stats(variant_dir(runs, v) / "stats.json") for v in ids(prim)]
    expect = holm([s["baselines"]["p_a"] for s in stats])
    assert np.allclose([b["p_a_adj"] for b in blocks], expect)
    assert [s["holm"] for s in stats] == blocks and blocks[0]["family"] == "insample" and blocks[0]["n"] == 4

    cells = {}
    for v, vid in zip(enumerate_grid(prereg), ids(enumerate_grid(prereg))):
        cells.setdefault(v.cell, []).append(vid)
    out = apply_dsr(runs, cells)
    assert len(out) == 12
    assert {d["local"]["N"] for d in out.values()} == {3} and {d["global"]["N"] for d in out.values()} == {12}

    res = read_results(runs).set_index("variant_id")
    for s in stats:
        row = res.loc[s["variant_id"]]
        assert row["p_a"] == s["baselines"]["p_a"] and row["n_baseline_runs"] == 30
        assert row["p_a_adj"] == read_stats(variant_dir(runs, s["variant_id"]) / "stats.json")["holm"]["p_a_adj"]
        assert bool(row["is_primary"]) and row["n_trades"] == s["summary"]["n_trades"]
    assert res["dsr_global"].notna().all() and res.loc[~res["is_primary"], "p_a_adj"].isna().all()


def test_baselines_need_a_stored_run(world, tmp_path):
    _, prereg, data_cfg, _ = world
    done = baselines_many(primary_variants(prereg), data_cfg, runs_dir=tmp_path, workers=1)
    assert len(done["missing"]) == 4 and not done["ok"]
    with pytest.raises(FamilyIncomplete, match="no stats.json"):
        apply_dsr(tmp_path, {("BTCUSDT", "utc"): ids(primary_variants(prereg))[:1]})


def test_a_run_without_a_final_row_reruns_and_a_rerun_drops_stale_baselines(world, ran, tmp_path):
    _, prereg, data_cfg, _ = world
    runs = tmp_path / "runs"
    shutil.copytree(ran, runs)
    prim = primary_variants(prereg)
    baselines_many(prim[:1], data_cfg, runs_dir=runs, workers=1)
    vid = ids(prim)[0]
    reg = Registry(runs)
    row = dict(reg.latest()[vid], status="started", error=None)
    reg.append(row)  # a killed process: started without ok/failed
    done = run_many(prim, data_cfg, runs_dir=runs, workers=1)
    assert done["ok"] == [vid] and len(done["skipped"]) == 3
    names = {p.name for p in variant_dir(runs, vid).iterdir()}
    assert names == VARIANT_FILES  # the baseline files went with the old stats.json
    assert read_stats(variant_dir(runs, vid) / "stats.json")["baselines"]["n_runs"] is None


def test_results_hold_the_current_code_generation_only(world, ran, tmp_path):
    _, prereg, data_cfg, _ = world
    runs = tmp_path / "runs"
    shutil.copytree(ran, runs)
    v = primary_variants(prereg)[0]
    old = run_variant(v.cfg, data_cfg, runs_dir=runs, labels=v.labels(), cv="0" * 64)  # before a bug fix
    run_many([v], data_cfg, runs_dir=runs, workers=1)
    assert set(read_results(runs)["code_version"]) == {code_version()} and len(read_results(runs)) == 12
    stale = read_results(runs, superseded=True)
    assert list(stale["variant_id"]) == [old.name] and list(stale["code_version"]) == ["0" * 64]
