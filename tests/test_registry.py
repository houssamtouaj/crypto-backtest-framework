"""Spec §6.2, §6.3 (test 6.2): variant identity and the append-only registry."""
import json
import multiprocessing
import shutil
from concurrent.futures import ProcessPoolExecutor

import pytest

from perpbt.config import StatsConfig, StrategyParams, VariantConfig, from_dict, to_dict
from perpbt.experiments.registry import Registry, make_row
from perpbt.version import PACKAGE_DIR, code_version
from tests.sim_harness import WALK_COSTS
from tests.strategy_harness import UTC


def cfg():
    return VariantConfig(pair="BTCUSDT", session=UTC, params=StrategyParams(), exec=WALK_COSTS, stats=StatsConfig(),
                         period_start="2020-01-02", period_end="2020-01-12", is_holdout=False)


def _shuffled(d):
    if isinstance(d, dict):
        return {k: _shuffled(d[k]) for k in reversed(list(d))}
    return d


def test_same_config_in_another_key_order_has_the_same_id():
    plain = to_dict(cfg())
    other = from_dict(VariantConfig, _shuffled(plain))
    assert list(_shuffled(plain)) != list(plain)
    assert other.variant_id("cv") == cfg().variant_id("cv")
    changed = from_dict(VariantConfig, {**plain, "pair": "ETHUSDT"})
    assert changed.variant_id("cv") != cfg().variant_id("cv")


def test_a_source_byte_changes_the_id_and_docs_or_tests_do_not(tmp_path):
    tree = tmp_path / "perpbt"
    shutil.copytree(PACKAGE_DIR, tree, ignore=shutil.ignore_patterns("__pycache__"))
    base = code_version(tree)
    assert base == code_version()
    (tree / "NOTES.md").write_text("doc\n", encoding="utf-8")
    (tree / "tests_data.txt").write_text("x\n", encoding="utf-8")
    assert code_version(tree) == base
    assert cfg().variant_id(code_version(tree)) == cfg().variant_id(base)
    path = tree / "experiments" / "runner.py"
    path.write_bytes(path.read_bytes() + b"\n")
    assert code_version(tree) != base
    assert cfg().variant_id(code_version(tree)) != cfg().variant_id(base)


def test_rows_append_in_order_and_latest_picks_the_last(tmp_path):
    reg = Registry(tmp_path)
    c = cfg()
    row = dict(code_version="cv", git_commit=None, artifacts_path="x", batch="b")
    reg.append(make_row(c, "v1", kind="run", status="started", **row))
    reg.append(make_row(c, "v1", kind="run", status="failed", error="Trace", **row))
    reg.append(make_row(c, "v1", kind="run", status="started", **row))
    reg.append(make_row(c, "v1", kind="run", status="ok", runtime_s=1.5, **row))
    reg.append(make_row(c, "v1", kind="baselines", status="started", **row))
    rows = reg.rows()
    assert [r["status"] for r in rows] == ["started", "failed", "started", "ok", "started"]
    assert sum(r["status"] == "started" and r["kind"] == "run" for r in rows) == 2  # two attempts
    assert reg.latest()["v1"]["status"] == "ok" and reg.latest(kind="baselines")["v1"]["status"] == "started"
    r = rows[3]
    assert r["seed"] == c.stats.master_seed and r["is_holdout"] is False and json.loads(r["params_json"]) == to_dict(c)
    assert set(r) == {"variant_id", "kind", "status", "run_ms", "code_version", "git_commit", "seed", "is_holdout",
                      "params_json", "artifacts_path", "error", "reason", "batch", "runtime_s"}
    with pytest.raises(ValueError):
        make_row(c, "v1", kind="run", status="done", **row)


def _append_many(args):
    runs_dir, worker, n = args
    reg = Registry(runs_dir)
    for k in range(n):
        reg.append(make_row(cfg(), f"w{worker}-{k}", kind="run", status="started", code_version="cv",
                            git_commit=None, artifacts_path="x" * 2000, batch="b"))
    return n


def test_concurrent_appends_from_spawned_processes_never_interleave(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=4, mp_context=ctx) as pool:
        assert sum(pool.map(_append_many, [(str(tmp_path), w, 50) for w in range(4)])) == 200
    rows = Registry(tmp_path).rows()  # every line parses
    assert len(rows) == 200 and len({r["variant_id"] for r in rows}) == 200
