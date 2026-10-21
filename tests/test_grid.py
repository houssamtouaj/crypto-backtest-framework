"""Spec §6.4 (test 6.3): grid enumeration from the pre-registration."""
from collections import Counter
from pathlib import Path

import pytest

from perpbt.experiments.grid import cell_params, differing_fields, enumerate_grid, primary_variants
from perpbt.experiments.prereg import load_prereg
from perpbt.version import code_version

PREREG = Path(__file__).resolve().parents[1] / "configs" / "prereg.yaml"


@pytest.fixture(scope="module")
def prereg():
    return load_prereg(PREREG)


@pytest.fixture(scope="module")
def grid(prereg):
    return enumerate_grid(prereg)


def test_324_unique_variants_36_per_cell(prereg, grid):
    assert len(grid) == 324
    cv = code_version()
    assert len({v.cfg.variant_id(cv) for v in grid}) == 324
    per_cell = Counter(v.cell for v in grid)
    assert len(per_cell) == 9 and set(per_cell.values()) == {36}


def test_primary_flagged_once_per_cell(prereg, grid):
    prim = [v for v in grid if v.is_primary]
    assert len(prim) == 9 and len({v.cell for v in prim}) == 9
    assert all(v.cfg.params == prereg.primary for v in prim)
    assert set(prim[0].members) == {"heatmap_1", "heatmap_2", "heatmap_3"}


def test_each_variant_differs_from_the_primary_only_on_its_axes(prereg, grid):
    axes = {f"heatmap_{n}": set(h.axes) for n, h in enumerate(prereg.grid.heatmaps, start=1)}
    axes.update({f"single_{n}": set(s) for n, s in enumerate(prereg.grid.singles, start=1)})
    for v in grid:
        diff = differing_fields(v.cfg.params, prereg.primary)
        if v.is_primary:
            assert diff == set()
            continue
        assert len(v.members) == 1, v.members
        assert diff and diff <= axes[v.members[0]], (v.members, diff)
        if v.members[0].startswith("single"):
            assert diff == axes[v.members[0]]


def test_heatmap_and_single_sizes(prereg):
    count = Counter(m for _, members in cell_params(prereg) for m in members)
    assert count == {"heatmap_1": 8, "heatmap_2": 9, "heatmap_3": 16,
                     **{f"single_{n}": 1 for n in range(1, 6)}}


def test_variant_configs_carry_costs_budget_and_period(prereg, grid):
    for v in grid:
        assert v.cfg.exec == prereg.exec_for(v.cfg.pair)
        assert v.cfg.stats.baseline_runs == (5000 if v.is_primary else 500)
        assert (v.cfg.period_start, v.cfg.period_end, v.cfg.is_holdout) == ("2020-01-01", "2025-12-31", False)
    sol = next(v for v in grid if v.cfg.pair == "SOLUSDT")
    assert sol.cfg.exec.slippage == 0.0005 and sol.cfg.exec.mmr == 0.010


def test_primary_variants_in_sample_and_holdout(prereg):
    ins = primary_variants(prereg)
    assert [(v.cfg.pair, v.cfg.session.name) for v in ins] == [
        (p, s) for p in ("BTCUSDT", "ETHUSDT", "SOLUSDT") for s in ("utc", "ny", "london")]
    with pytest.raises(Exception, match="holdout.end"):
        primary_variants(prereg, holdout=True)  # not set until the data download date
