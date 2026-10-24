"""Forward reviews (carry spec Phase 5 §5.5; trend Phase 5 §5.6), on synthetic stores past the code lock."""
import json
from datetime import date

import pytest

import perpbt.carry.forward as forward_mod
from perpbt.carry.forward import (
    REVIEW_DAYS, REVIEW_SCHEMA, ForwardRefused, fetch_tails, review_metrics, review_period, run_forward,
)
from perpbt.carry.prereg import load_prereg, lock_code
from perpbt.carry.runner import Period, run_insample
from perpbt.experiments.registry_log import Registry
from tests.carry.runenv import PAIRS, copy_env, data_config, make_env

LOCK_DAY = date(2026, 10, 25)  # forward start 2026-10-26


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    return make_env(tmp_path_factory.mktemp("forward"), end="2029-10-26", last_day={"CCCUSDT": "2025-05-21"})


@pytest.fixture
def env(store, tmp_path):
    return copy_env(store, tmp_path)


@pytest.fixture
def ready(env):
    lock_code(env / "prereg.yaml", today=LOCK_DAY)
    run_insample(env / "prereg.yaml", data_config(env), env / "runs", rows=["primary"])
    return env


def fwd(env, review, today, **kw):
    return run_forward(env / "prereg.yaml", data_config(env), runs_dir=env / "runs", review=review, today=today, **kw)


def walk_keys(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from walk_keys(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk_keys(v)


# --- windows and refusals -----------------------------------------------------------------------------------------

def test_review_windows(env):
    lock_code(env / "prereg.yaml", today=LOCK_DAY)
    prereg = load_prereg(env / "prereg.yaml")
    assert REVIEW_DAYS == {12: 365, 36: 1095}
    assert review_period(prereg, 12) == Period("forward_12m", "2026-10-26", "2027-10-25")
    assert review_period(prereg, 36) == Period("forward_36m", "2026-10-26", "2029-10-24")
    with pytest.raises(ForwardRefused, match="pre-registered"):
        review_period(prereg, 24)


def test_refuses_before_the_code_lock(env):
    with pytest.raises(ForwardRefused, match="lock-code"):
        fwd(env, 12, date(2027, 11, 1))


def test_refuses_without_the_insample_primary(env):
    lock_code(env / "prereg.yaml", today=LOCK_DAY)
    with pytest.raises(ForwardRefused, match="carry run --primary"):
        fwd(env, 12, date(2027, 11, 1))


@pytest.mark.parametrize("review, today, have", [(12, date(2027, 10, 25), 364), (36, date(2029, 10, 24), 1094)])
def test_refuses_before_enough_days(ready, review, today, have, monkeypatch):
    monkeypatch.setattr(forward_mod, "load_frames", lambda *a, **k: pytest.fail("loaded data"))
    with pytest.raises(ForwardRefused, match=f"needs {REVIEW_DAYS[review]} days.*{have} exist"):
        fwd(ready, review, today)
    assert not (ready / "runs" / "carry" / "forward").exists()


def test_refuses_a_changed_code_version(ready, monkeypatch):
    monkeypatch.setattr(forward_mod, "insample_primary_identical", lambda *a, **k: False)
    with pytest.raises(ForwardRefused, match="allow-code-change"):
        fwd(ready, 12, date(2027, 10, 26), code_version="f" * 64)


# --- reviews ------------------------------------------------------------------------------------------------------

def test_review_12_is_descriptive_and_runs_once(ready):
    calls = []
    out = fwd(ready, 12, date(2027, 10, 26), fetch=lambda cfg, pairs: calls.append(list(pairs)))
    assert calls == [PAIRS]
    doc = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    assert doc["schema"] == REVIEW_SCHEMA and doc["row"] == "primary" and doc["n_days"] == 365
    assert doc["period"] == {"name": "forward_12m", "start": "2026-10-26", "end": "2027-10-25"}
    assert "p" not in set(walk_keys(doc)) and "v1" not in doc and "v2" not in doc
    assert doc["annual"]["sharpe_ci"] == [None, None]
    assert set(doc["metrics"]) == {"ann_return", "funding", "basis", "cost_drag", "entries_per_year", "share_on"}
    assert {p.name for p in out.iterdir()} == {"daily.parquet", "daily_pair.parquet", "fills.parquet",
                                               "events.parquet", "variant.json", "stats.json"}
    rows = [r for r in Registry(ready / "runs" / "carry").rows() if r["kind"] == "forward"]
    assert [r["status"] for r in rows] == ["started", "ok"] and rows[0]["period"] == "forward_12m"
    with pytest.raises(ForwardRefused, match="already"):
        fwd(ready, 12, date(2027, 12, 1))


def test_review_36_is_confirmatory(ready):
    out = fwd(ready, 36, date(2029, 10, 25))
    doc = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    assert doc["schema"] == "carry-stats/1" and doc["n_days"] == 1095
    assert doc["period"] == {"name": "forward_36m", "start": "2026-10-26", "end": "2029-10-24"}
    assert {"v1", "v2", "edge", "reading"} <= set(doc)
    assert (out / "random_timing.parquet").exists()
    # CCCUSDT delisted before the forward start: never live, the portfolio runs on the other two
    assert set(json.loads((out / "variant.json").read_text(encoding="utf-8"))["pairs"]) == set(PAIRS)


def test_review_metrics_against_a_hand_table():
    per_year = [
        {"year": "2020", "n_days": 100, "ret_sum": 0.02, "funding": 0.03, "basis": -0.005, "fees": 0.002,
         "slippage": 0.001, "entries": 5, "share_on": 0.4},
        {"year": "2021", "n_days": 365, "ret_sum": 0.10, "funding": 0.12, "basis": -0.01, "fees": 0.006,
         "slippage": 0.004, "entries": 20, "share_on": 0.8},
    ]
    fwd_total = {"n_days": 365, "ret_sum": 0.08, "funding": 0.2, "basis": -0.02, "fees": 0.005, "slippage": 0.002,
                 "entries": 2, "share_on": 0.5}
    m = review_metrics(fwd_total, per_year)
    assert m["ann_return"] == {"forward": pytest.approx(0.08), "insample_min": pytest.approx(0.073),
                               "insample_max": pytest.approx(0.10), "inside": True}
    assert m["funding"]["inside"] is False and m["funding"]["insample_max"] == pytest.approx(0.12)
    assert m["cost_drag"]["forward"] == pytest.approx(0.007) and m["cost_drag"]["inside"] is False
    assert m["cost_drag"]["insample_min"] == pytest.approx(0.01) and m["cost_drag"]["insample_max"] == \
        pytest.approx(0.01095)
    assert m["entries_per_year"]["forward"] == pytest.approx(2) and m["entries_per_year"]["inside"] is False
    assert m["share_on"] == {"forward": 0.5, "insample_min": 0.4, "insample_max": 0.8, "inside": True}


def test_fetch_tails_fetches_both_legs(monkeypatch):
    calls = []
    monkeypatch.setattr(forward_mod, "run_fetch", lambda cfg, **kw: calls.append(kw) or {})
    fetch_tails("cfg", ["AAAUSDT"])
    assert calls == [{"pairs": ["AAAUSDT"], "tfs": ("15m",), "ccxt_tail": True, "market": "um"},
                     {"pairs": ["AAAUSDT"], "tfs": ("15m",), "market": "spot"}]
