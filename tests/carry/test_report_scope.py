"""Carry spec Phase 6: the report code is outside the carry code version, loads no market data, and the files
Phase 7 commits are admitted by .gitignore."""
import ast
import subprocess
from pathlib import Path

import pytest

from tests.carry.test_code_lock import scoped_files

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "perpbt" / "report" / "carry"
ID = "ab" * 32
FORBIDDEN_MODULES = ("perpbt.data", "perpbt.carry.frames")
FORBIDDEN_NAMES = {"load_pairframe", "load_frames", "CandleStore", "FundingStore", "allow_holdout"}


def test_report_code_is_outside_the_carry_code_version():
    files = {f"report/carry/{p.name}" for p in REPORT_DIR.glob("*.py")}
    assert {"report/carry/figures.py", "report/carry/report.py", "report/carry/summary.py"} <= files
    assert not files & scoped_files()
    assert not any(entry.startswith("report") for entry in scoped_files())


@pytest.mark.parametrize("path", sorted(REPORT_DIR.glob("*.py")), ids=lambda p: p.name)
def test_report_code_loads_no_market_data(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(FORBIDDEN_MODULES), (path.name, node.module)
            assert not {a.name for a in node.names} & FORBIDDEN_NAMES, (path.name, node.module)
        elif isinstance(node, ast.Import):
            assert not any(a.name.startswith(FORBIDDEN_MODULES) for a in node.names), path.name
        elif isinstance(node, (ast.Name, ast.Attribute, ast.keyword)):
            name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else node.arg
            assert name not in FORBIDDEN_NAMES, (path.name, name)


def ignored(rel: str) -> bool:
    out = subprocess.run(["git", "check-ignore", "-q", "--no-index", rel], cwd=ROOT)
    assert out.returncode in (0, 1), out
    return out.returncode == 0


@pytest.mark.parametrize("rel", [
    f"runs/carry/{ID}/report.md", f"runs/carry/{ID}/figures/equity.png", f"runs/carry/holdout/{ID}/report.md",
    f"runs/carry/holdout/{ID}/figures/funding.png", f"runs/carry/forward/{ID}/report.md",
    f"runs/carry/forward/{ID}/figures/basis.png", "runs/carry/summary.md", f"runs/carry/{ID}/stats.json",
])
def test_gitignore_admits_the_reports_and_figures(rel):
    assert not ignored(rel)


@pytest.mark.parametrize("rel", [f"runs/carry/{ID}/daily.parquet", f"runs/carry/{ID}/signals.parquet",
                                 f"runs/carry/holdout/{ID}/random_timing.parquet", f"runs/carry/{ID}/variant.json"])
def test_gitignore_keeps_the_tables_out(rel):
    assert ignored(rel)
