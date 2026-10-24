"""A synthetic carry runs directory for the Phase 6 report tests: the Phase 5 protocol end to end.

``build_world`` writes synthetic stores through 2029-10-28 (CCCUSDT "delisted" on 2025-05-21, as EOSUSDT), then
lock-code (forward start 2026-10-26), ``carry run`` (28 in-sample variants), ``carry holdout`` (21 run, 7 per-pair
skipped) and both forward reviews (12 and 36 months). Nothing here is market data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

import perpbt.carry.runner as runner
from perpbt.carry.forward import run_forward
from perpbt.carry.prereg import lock_code
from perpbt.cli import main
from tests.carry.runenv import data_config, make_env

END = "2029-10-28"
DELISTED = {"CCCUSDT": "2025-05-21"}


@dataclass
class World:
    env: Path

    @property
    def prereg_path(self) -> Path:
        return self.env / "prereg.yaml"

    @property
    def runs(self) -> Path:
        return self.env / "runs"

    @property
    def root(self) -> Path:
        return self.runs / "carry"

    def cli(self, *cmd: str) -> int:
        return main(["carry", *cmd, "--prereg", str(self.prereg_path), "--data-config", str(self.env / "data.yaml"),
                     "--runs-dir", str(self.runs)])


def build_world(tmp: Path) -> World:
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(runner.KNOWN_DELISTINGS, "CCCUSDT", DELISTED["CCCUSDT"])
        env = make_env(tmp, end=END, last_day=DELISTED)
        lock_code(env / "prereg.yaml", today=date(2026, 10, 25))
        w = World(env)
        assert w.cli("run") == 0
        assert w.cli("holdout") == 0
        for review, today in ((12, date(2027, 10, 27)), (36, date(2029, 10, 27))):
            run_forward(w.prereg_path, data_config(env), runs_dir=w.runs, review=review, today=today)
    return w
