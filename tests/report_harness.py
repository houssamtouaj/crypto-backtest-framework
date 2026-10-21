"""A synthetic runs directory for the reporting tests: Phase 8's protocol through the CLI.

``build_runs`` writes the synthetic markets (``experiment_harness``), then
runs freeze, the primaries, their baselines, Holm, the grid, its baselines
(5 runs), the DSR pass and, with ``holdout``, the holdout. ``full`` uses
3 pairs × 3 sessions (the nine primary cells); otherwise 2 × 2.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from perpbt.cli import main
from perpbt.experiments.prereg import Prereg
from tests.experiment_harness import make_world

FULL = {"pairs": ("BTCUSDT", "ETHUSDT", "SOLUSDT"), "sessions": ("utc", "ny", "london"), "sol_listing": "2019-09-08"}


@dataclass
class World:
    prereg_path: Path
    prereg: Prereg
    data_dir: str
    runs: Path

    @property
    def common(self) -> list[str]:
        return ["--prereg", str(self.prereg_path), "--data-dir", self.data_dir, "--runs-dir", str(self.runs)]


def build_runs(root: Path, *, full: bool = False, holdout: bool = True, grid: bool = True) -> World:
    path, prereg, data_cfg, runs = make_world(root, download="2020-01-19", holdout_end="2020-01-18",
                                              **(FULL if full else {}))
    w = World(path, prereg, data_cfg.data_dir, runs)
    pool = [*w.common, "--workers", "1"]
    steps = [["prereg", "freeze", *w.common], ["run", "--primary", *pool], ["baselines", "--primary", *pool],
             ["stats", "holm", *w.common]]
    if grid:
        steps += [["grid", "--run", *pool], ["baselines", "--grid", "--runs", "5", *pool], ["stats", "dsr", *w.common]]
    if holdout:
        steps.append(["holdout", *w.common])
    for argv in steps:
        assert main(argv) == 0, argv
    return w
