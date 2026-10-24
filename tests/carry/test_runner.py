"""Variants and the variant runner (carry spec Phase 5 §5.1-§5.2), on synthetic stores."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from perpbt.carry.prereg import load_prereg
from perpbt.carry.runner import (
    PORTFOLIO, REGISTRY_KEYS, SIGNAL_COLUMNS, CarryRun, Period, Variant, holdout_period, insample_period, load_frames,
    skip_reason, variants,
)
from perpbt.carry.signals import compute_signals
from perpbt.experiments.registry_log import Registry
from tests.carry.runenv import PAIRS, data_config, make_env

ROOT = Path(__file__).resolve().parents[2]
FILES = ("daily.parquet", "daily_pair.parquet", "fills.parquet", "events.parquet", "signals.parquet", "reprice.parquet",
         "variant.json", "stats.json")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    return make_env(tmp_path_factory.mktemp("runner"), last_day={"CCCUSDT": "2023-12-20"})


@pytest.fixture(scope="module")
def prereg(env):
    return load_prereg(env / "prereg.yaml")


@pytest.fixture(scope="module")
def frames(env, prereg):
    return load_frames(data_config(env), PAIRS, end=prereg.periods.insample_end)


def make_run(prereg, frames, tmp_path, period=None, **kw) -> CarryRun:
    root = tmp_path / "runs" / "carry"
    return CarryRun(prereg=prereg, frames=frames, period=period or insample_period(prereg), out_root=root,
                    registry=Registry(root), kind=kw.pop("kind", "run"), batch="b1", code_version="c" * 64, **kw)


def by_name(vs, name) -> Variant:
    return next(v for v in vs if v.name == name)


# --- variants ---------------------------------------------------------------------------------------------------

def test_real_prereg_has_77_distinct_variants():
    prereg = load_prereg(ROOT / "configs" / "carry" / "prereg.yaml")
    vs = variants(prereg)
    assert len(vs) == 7 * 11
    assert [v.name for v in vs[:3]] == ["primary", "primary/BTCUSDT", "primary/ETHUSDT"]
    h = prereg.prereg_hash()
    for period in (insample_period(prereg), holdout_period(prereg)):
        ids = {v.variant_id(h, period) for v in vs}
        assert len(ids) == 77 and all(len(i) == 64 for i in ids)
    assert holdout_period(prereg) == Period("holdout", "2024-01-01", "2026-10-20")
    assert insample_period(prereg) == Period("insample", "2020-07-01", "2023-12-31")


def test_variant_rows_and_identity(prereg):
    vs = variants(prereg)
    h = prereg.prereg_hash()
    p = by_name(vs, "primary")
    assert p.scope == PORTFOLIO and p.pairs == tuple(PAIRS) and p.signal == prereg.signal
    lev = by_name(vs, "lev_1/BBBUSDT")
    assert lev.pairs == ("BBBUSDT",) and lev.sleeve.perp_leverage == 1.0 and lev.row == "lev_1"
    ins, hold = insample_period(prereg), holdout_period(prereg)
    assert p.variant_id(h, ins) != p.variant_id(h, hold)
    assert p.variant_id(h, ins) != by_name(vs, "always_on").variant_id(h, ins)
    assert p.variant_id(h, ins) == by_name(variants(prereg, ["primary"]), "primary").variant_id(h, ins)
    content = p.content(h, ins)
    assert content["period"] == {"name": "insample", "start": "2020-07-01", "end": "2023-12-31"}
    assert "code_version" not in json.dumps(content)
    assert [v.name for v in variants(prereg, ["lev_1"])] == ["lev_1", *(f"lev_1/{x}" for x in PAIRS)]
    with pytest.raises(ValueError, match="unknown row"):
        variants(prereg, ["nope"])


def test_period_checks():
    with pytest.raises(ValueError):
        Period("later", "2024-01-01", "2024-02-01")
    with pytest.raises(ValueError):
        Period("holdout", "2024-02-01", "2024-01-01")


def test_skip_reason_only_for_a_pair_ending_before_the_period(prereg, frames):
    vs = variants(prereg, ["primary"])
    ins = insample_period(prereg)
    assert skip_reason(frames, by_name(vs, "primary"), ins) is None
    assert skip_reason(frames, by_name(vs, "primary/AAAUSDT"), ins) is None
    assert "2023-12-20" in skip_reason(frames, by_name(vs, "primary/CCCUSDT"), ins)


# --- run --------------------------------------------------------------------------------------------------------

def test_run_writes_the_folder_byte_identically(prereg, frames, tmp_path):
    run = make_run(prereg, frames, tmp_path)
    v = by_name(variants(prereg, ["primary"]), "primary")
    out = run.run(v)
    assert out == tmp_path / "runs" / "carry" / v.variant_id(prereg.prereg_hash(), run.period)
    assert sorted(p.name for p in out.iterdir()) == sorted((*FILES, "random_timing.parquet"))
    first = {p.name: p.read_bytes() for p in out.iterdir()}
    stats = json.loads(first["stats.json"])
    assert stats["schema"] == "carry-stats/1" and stats["row"] == "primary"
    assert stats["period"] == {"name": "insample", "start": "2020-07-01", "end": "2023-12-31"}
    assert list(stats["diagnostics"]) == ["annual", "decomposition", "margin", "reprice", "filter_value",
                                          "random_timing", "dsr"]
    assert json.loads(first["variant.json"]) == v.content(prereg.prereg_hash(), run.period)
    timing = pd.read_parquet(out / "random_timing.parquet")
    assert len(timing) == prereg.stats.random_timing_runs
    reprice = pd.read_parquet(out / "reprice.parquet")
    assert len(reprice) == 8 and {"slippage", "spot_taker", "ann_return", "p", "reading"} <= set(reprice.columns)
    daily = pd.read_parquet(out / "daily.parquet")
    assert daily["date"].iloc[0] == "2020-07-01" and daily["date"].iloc[-1] == "2023-12-31"
    run.run(v)  # rerun into the same folder
    assert {p.name: p.read_bytes() for p in out.iterdir()} == first


def test_per_pair_variant(prereg, frames, tmp_path):
    run = make_run(prereg, frames, tmp_path)
    out = run.run(by_name(variants(prereg, ["primary"]), "primary/AAAUSDT"))
    assert sorted(p.name for p in out.iterdir()) == sorted(FILES)
    stats = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    assert stats["row"] == "primary/AAAUSDT" and list(stats["diagnostics"]) == ["annual", "decomposition", "margin",
                                                                                 "reprice"]
    assert set(pd.read_parquet(out / "daily_pair.parquet")["pair"]) == {"AAAUSDT"}
    assert set(pd.read_parquet(out / "signals.parquet")["pair"]) == {"AAAUSDT"}


def test_signals_table_is_the_frames_signal_and_basis_on_the_period_days(prereg, frames, tmp_path):
    run = make_run(prereg, frames, tmp_path)
    v = by_name(variants(prereg, ["entry_20"]), "entry_20")
    sig = pd.read_parquet(run.run(v) / "signals.parquet")
    assert tuple(sig.columns) == SIGNAL_COLUMNS and sig["on"].dtype == bool
    for pair in PAIRS:
        rows = sig[sig["pair"] == pair]
        pf = frames[pair]
        days = pd.to_datetime(pf.day_ms, unit="ms").strftime("%Y-%m-%d")
        sel = (days >= "2020-07-01") & (days <= "2023-12-31")
        assert rows["date"].tolist() == list(days[sel])
        s = compute_signals(pf, v.signal)
        assert np.array_equal(rows["fbar"].to_numpy(), s.fbar[sel], equal_nan=True)
        assert np.array_equal(rows["on"].to_numpy(), s.on[sel])
        assert np.array_equal(rows["basis"].to_numpy(), pf.basis[sel], equal_nan=True)
    assert sig[sig["pair"] == "CCCUSDT"]["date"].iloc[-1] == "2023-12-20"  # a frame ending early stops there


def test_registry_rows_started_then_ok(prereg, frames, tmp_path):
    run = make_run(prereg, frames, tmp_path, reason="why")
    v = by_name(variants(prereg, ["entry_5"]), "entry_5/BBBUSDT")
    run.run(v)
    rows = run.registry.rows()
    assert [r["status"] for r in rows] == ["started", "ok"]
    for r in rows:
        assert sorted(r) == sorted(REGISTRY_KEYS)  # the registry writes keys sorted
        assert r["variant_id"] == v.variant_id(prereg.prereg_hash(), run.period) and r["name"] == "entry_5/BBBUSDT"
        assert r["kind"] == "run" and r["period"] == "insample" and r["market"] == "um+spot"
        assert r["code_version"] == "c" * 64 and r["prereg_hash"] == prereg.prereg_hash() and r["batch"] == "b1"
        assert r["reason"] == "why" and r["period_start"] == "2020-07-01" and r["period_end"] == "2023-12-31"
    assert rows[0]["finished_ms"] is None and rows[1]["finished_ms"] >= rows[1]["started_ms"] > 0
    assert rows[1]["error"] is None


def test_failed_variant_records_the_traceback_and_raises(prereg, frames, tmp_path, monkeypatch):
    import perpbt.carry.runner as runner

    def boom(*a, **k):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(runner, "run_path", boom)
    run = make_run(prereg, frames, tmp_path)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        run.run(by_name(variants(prereg, ["primary"]), "primary/AAAUSDT"))
    rows = run.registry.rows()
    assert [r["status"] for r in rows] == ["started", "failed"]
    assert "synthetic failure" in rows[1]["error"] and "Traceback" in rows[1]["error"]


def test_skip_writes_a_skipped_row_and_no_folder(prereg, frames, tmp_path):
    run = make_run(prereg, frames, tmp_path)
    v = by_name(variants(prereg, ["primary"]), "primary/CCCUSDT")
    run.skip(v, skip_reason(frames, v, run.period))
    (row,) = run.registry.rows()
    assert row["status"] == "skipped" and "2023-12-20" in row["error"]
    assert not run.folder(v).exists()


def test_portfolio_runs_with_a_pair_ending_early(prereg, frames, tmp_path):
    # the portfolio treats CCCUSDT's early end as a delisting (Phase 3 §3.4)
    out = make_run(prereg, frames, tmp_path).run(by_name(variants(prereg, ["always_on"]), "always_on"))
    events = pd.read_parquet(out / "events.parquet")
    assert (events[events["kind"] == "delisting"]["pair"] == "CCCUSDT").any()
