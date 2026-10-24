"""A whole stats.json on synthetic frames, end to end (carry spec Phase 4 §4.5: same seed, same bytes).

The real pre-registration supplies the settings (B = 10,000, blocks of 30, 1,000 random-timing paths, the seven
rows, the re-pricing grid); the frames are synthetic, so no market return is computed.
"""
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from perpbt.carry import verdict as verdict_mod
from perpbt.carry.diagnostics import TIMING_COLUMNS, primary_diagnostics, row_diagnostics, row_names
from perpbt.carry.executor import exec_spec, run_path
from perpbt.carry.prereg import load_prereg
from perpbt.carry.signals import compute_signals
from perpbt.carry.verdict import SCHEMA, Seeds, evaluate_row, stats_document, stats_json
from tests.carry.test_executor import messy_frames

ROOT = Path(__file__).resolve().parents[2]
START, END = "2020-01-02", "2020-07-18"  # 199 return days of messy_frames(days=200)


@pytest.fixture(scope="module")
def setup():
    prereg = load_prereg(ROOT / "configs" / "carry" / "prereg.yaml")
    frames = messy_frames(days=200)
    rows = {"primary": (prereg.signal, prereg.sleeve)}
    rows.update({e.name: (e.signal, e.sleeve) for e in prereg.exploratory})
    states = {row: {pair: np.asarray(compute_signals(pf, sig).on) for pair, pf in frames.items()}
              for row, (sig, _) in rows.items()}
    cfgs = {row: exec_spec(prereg, sleeve=sleeve, start=START, end=END) for row, (_, sleeve) in rows.items()}
    results = {row: run_path(frames, states[row], cfgs[row]) for row in rows}
    return prereg, frames, states, cfgs, results


def primary_document(setup, master_seed: int) -> tuple[str, object]:
    prereg, frames, states, cfgs, results = setup
    seeds = Seeds(master_seed, prereg.prereg_hash(), "insample")
    r = results["primary"]
    v = evaluate_row(r.daily["ret"].to_numpy(), prereg, seeds, "primary")
    diag = row_diagnostics(r, frames, states["primary"], cfgs["primary"], prereg, seeds, "primary", v1=v["v1"])
    extra, table = primary_diagnostics(frames, states["primary"], cfgs["primary"], prereg, seeds, results)
    doc = stats_document(prereg_hash=prereg.prereg_hash(), period="insample", start=START, end=END, row="primary",
                         verdict=v, diagnostics={**diag, **extra})
    return stats_json(doc), table


def test_same_seed_same_bytes_other_seed_differs(setup):
    prereg = setup[0]
    assert sum(r.counts["entries"] for r in setup[4].values()) > 0  # the rows trade
    a, ta = primary_document(setup, prereg.stats.master_seed)
    b, tb = primary_document(setup, prereg.stats.master_seed)
    assert a == b and ta.to_csv() == tb.to_csv()
    c, tc = primary_document(setup, prereg.stats.master_seed + 1)
    assert c != a and tc["offset"].tolist() != ta["offset"].tolist()


def test_schema(setup):
    prereg = setup[0]
    text, table = primary_document(setup, prereg.stats.master_seed)
    doc = json.loads(text)
    assert doc["schema"] == SCHEMA and doc["row"] == "primary" and doc["n_days"] == 199
    assert doc["reading"] in ("edge", "positive_below_hurdle", "no_edge")
    assert [h["hurdle_annual"] for h in doc["hurdles"]] == list(prereg.hurdle.descriptive)
    assert list(doc["diagnostics"]) == ["annual", "decomposition", "margin", "reprice", "filter_value",
                                        "random_timing", "dsr"]
    assert len(doc["diagnostics"]["reprice"]) == 8 and doc["diagnostics"]["dsr"]["rows"] == list(row_names(prereg))
    rt = doc["diagnostics"]["random_timing"]
    assert rt["runs"] == prereg.stats.random_timing_runs == len(table) and rt["shift_margin_days"] == 60
    assert tuple(table.columns) == TIMING_COLUMNS
    assert table["offset"].between(60, 199 - 60).all()
    assert doc["diagnostics"]["annual"]["sharpe_ci"] == doc["v1"]["sharpe_ci"]


def test_seed_purposes_are_distinct_and_labelled(setup, monkeypatch):
    seen = Counter()
    real = verdict_mod.rng_for

    def recording(master_seed, prereg_hash, purpose):
        seen[purpose] += 1
        return real(master_seed, prereg_hash, purpose)

    monkeypatch.setattr(verdict_mod, "rng_for", recording)
    primary_document(setup, setup[0].stats.master_seed)
    assert seen == Counter({
        "insample/primary/v1": 1,
        "insample/primary/v2": 5,  # the verdict's and the four descriptive hurdles: the same blocks
        "insample/primary/reprice/v2": 8,  # one per cell: the same blocks
        "insample/filter_value": 1,
        "insample/random_timing": 1,
    })
    assert not any(p.startswith("screen") for p in seen)
