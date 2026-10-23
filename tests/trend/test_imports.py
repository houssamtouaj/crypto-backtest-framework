"""Trend modules import only perpbt modules inside the trend code-version scope (trend spec Phase 5 §5.4).

Direct imports only; Phase 5 adds the scope constant to ``trend/prereg.py``
and the transitive check.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCOPE = (
    "perpbt.trend", "perpbt.data", "perpbt.indicators.daily", "perpbt.stats.bootstrap", "perpbt.stats.dsr",
    "perpbt.stats.diagnostics", "perpbt.stats.seeds", "perpbt.experiments.registry", "perpbt.config",
    "perpbt.version", "perpbt.checks",
)


def _perpbt_imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("perpbt"):
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out |= {a.name for a in node.names if a.name.startswith("perpbt")}
    return out


def test_trend_imports_stay_in_scope():
    bad = {
        f"{p.relative_to(ROOT).as_posix()}: {m}"
        for p in sorted((ROOT / "perpbt" / "trend").glob("*.py"))
        for m in _perpbt_imports(p)
        if not any(m == s or m.startswith(s + ".") for s in SCOPE)
    }
    assert not bad, sorted(bad)
