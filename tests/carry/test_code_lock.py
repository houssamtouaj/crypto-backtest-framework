"""The scoped carry code version and `lock_code` (carry spec Phase 5 §5.3)."""
import ast
import json
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml

from perpbt.carry.prereg import (
    CARRY_CODE_PATHS, LockRefused, carry_code_version, load_prereg, lock_code, lock_path, read_lock,
)
from perpbt.config import read_yaml_unique
from perpbt.version import PACKAGE_DIR, files_code_version

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "configs" / "carry" / "prereg.yaml"


# --- the scope -------------------------------------------------------------------------------------------------

def _module_path(module: str) -> Path | None:
    p = ROOT.joinpath(*module.split("."))
    if (p / "__init__.py").exists():
        return p / "__init__.py"
    return p.with_suffix(".py") if p.with_suffix(".py").exists() else None


def _imports(path: Path) -> set[str]:
    """Every ``perpbt`` module a file imports, at any depth of the file (inside functions too)."""
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names if a.name.split(".")[0] == "perpbt"}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module \
                and node.module.split(".")[0] == "perpbt":
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names if _module_path(f"{node.module}.{a.name}")}
    return out


def carry_import_closure() -> set[str]:
    """The files the carry modules execute on import, transitively, with every parent package's ``__init__``."""
    todo = [f"perpbt.carry.{p.stem}" for p in sorted((ROOT / "perpbt" / "carry").glob("*.py"))]
    seen: set[str] = set()
    while todo:
        parts = todo.pop().split(".")
        for k in range(1, len(parts) + 1):
            module = ".".join(parts[:k])
            path = _module_path(module)
            if module in seen or path is None:
                continue
            seen.add(module)
            todo.extend(_imports(path))
    return {_module_path(m).relative_to(PACKAGE_DIR).as_posix() for m in seen}


def scoped_files() -> set[str]:
    out = set()
    for entry in CARRY_CODE_PATHS:
        if entry.endswith("/"):
            out |= {p.relative_to(PACKAGE_DIR).as_posix() for p in (PACKAGE_DIR / entry).rglob("*.py")}
        else:
            out.add(entry)
    return out


def test_carry_imports_stay_inside_the_scoped_code_version():
    # transitive: the trend Phase 1 review found indicators/daily -> indicators/atr
    outside = sorted(carry_import_closure() - scoped_files())
    assert not outside, f"carry imports perpbt files outside CARRY_CODE_PATHS: {outside}"


def test_the_closure_helper_follows_imports_transitively():
    closure = carry_import_closure()
    assert {"indicators/atr.py", "trend/daily.py", "__init__.py", "carry/__init__.py",
            "experiments/registry_log.py"} <= closure
    assert "experiments/registry.py" not in closure and "stats/variant.py" not in closure


def test_every_scoped_entry_exists():
    for entry in CARRY_CODE_PATHS:
        path = PACKAGE_DIR / entry
        assert path.is_dir() if entry.endswith("/") else path.is_file(), entry
    assert carry_code_version() == files_code_version(CARRY_CODE_PATHS)


def test_edits_outside_the_scope_keep_the_carry_code_version(tmp_path):
    tree = tmp_path / "perpbt"
    shutil.copytree(PACKAGE_DIR, tree, ignore=shutil.ignore_patterns("__pycache__"))
    before = carry_code_version(tree)
    assert len(before) == 64
    for rel in ("report/extra.py", "experiments/runner.py", "trend/screen.py", "cli.py", "stats/variant.py"):
        path = tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((path.read_bytes() if path.exists() else b"") + b"# edit")
        assert carry_code_version(tree) == before, rel
    for rel in ("carry/new_module.py", "data/store.py", "trend/daily.py", "experiments/registry_log.py"):
        path = tree / rel
        data = path.read_bytes() if path.exists() else None
        path.write_bytes((data or b"") + b"# edit")
        assert carry_code_version(tree) != before, rel
        path.write_bytes(data) if data is not None else path.unlink()
        assert carry_code_version(tree) == before, rel


# --- lock_code -------------------------------------------------------------------------------------------------

@pytest.fixture
def frozen(tmp_path) -> Path:
    """Copies of the frozen prereg and its lock (code fields null, forward start null)."""
    shutil.copyfile(PREREG, tmp_path / "prereg.yaml")
    shutil.copyfile(lock_path(PREREG), tmp_path / "prereg.lock")
    return tmp_path / "prereg.yaml"


def test_lock_code_once(frozen):
    before = load_prereg(frozen)
    lock = lock_code(frozen, today=date(2026, 10, 30), code_version="c" * 64)
    assert list(lock) == ["frozen_on", "prereg_hash", "git_commit", "code_version", "code_locked_on",
                          "code_git_commit"]
    assert lock["code_version"] == "c" * 64 and lock["code_locked_on"] == "2026-10-30"
    assert read_lock(lock_path(frozen)) == lock
    after = load_prereg(frozen)
    assert after.periods.forward_start == "2026-10-31"
    assert after.prereg_hash() == before.prereg_hash() == lock["prereg_hash"]
    text = frozen.read_text(encoding="utf-8")
    assert "forward:  {start: 2026-10-31, reviews: [12, 36]}" in text  # comments and layout kept
    assert text.replace("2026-10-31", "null", 1) == PREREG.read_text(encoding="utf-8")
    with pytest.raises(LockRefused, match="already"):
        lock_code(frozen, today=date(2026, 11, 1), code_version="d" * 64)
    assert read_lock(lock_path(frozen)) == lock


def test_lock_code_defaults_to_the_carry_code_version(frozen):
    assert lock_code(frozen, today=date(2026, 10, 30))["code_version"] == carry_code_version()


def test_lock_code_block_style_yaml(frozen):
    raw = read_yaml_unique(frozen)
    frozen.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    lock_code(frozen, today=date(2026, 12, 31), code_version="c" * 64)
    assert load_prereg(frozen).periods.forward_start == "2027-01-01"


def test_lock_code_refuses_a_set_forward_start(frozen):
    text = frozen.read_text(encoding="utf-8").replace("forward:  {start: null", "forward:  {start: 2026-11-01", 1)
    frozen.write_text(text, encoding="utf-8")
    with pytest.raises(LockRefused, match="forward"):
        lock_code(frozen, today=date(2026, 10, 30), code_version="c" * 64)
    assert read_lock(lock_path(frozen))["code_version"] is None


def test_lock_code_refuses_unfrozen_or_changed(frozen):
    text = frozen.read_text(encoding="utf-8")
    frozen.write_text(text.replace("entry_annual: 0.10", "entry_annual: 0.11", 1), encoding="utf-8")
    with pytest.raises(LockRefused, match="changed since the freeze"):
        lock_code(frozen, today=date(2026, 10, 30), code_version="c" * 64)
    frozen.write_text(text, encoding="utf-8")
    lock_path(frozen).unlink()
    with pytest.raises(LockRefused, match="not frozen"):
        lock_code(frozen, today=date(2026, 10, 30), code_version="c" * 64)


def test_lock_code_completes_an_interrupted_lock_the_same_day(frozen):
    # a crash between the two writes left forward.start = tomorrow with no code lock
    text = frozen.read_text(encoding="utf-8").replace("forward:  {start: null", "forward:  {start: 2026-10-31", 1)
    frozen.write_text(text, encoding="utf-8")
    lock = lock_code(frozen, today=date(2026, 10, 30), code_version="c" * 64)
    assert lock["code_version"] == "c" * 64 and load_prereg(frozen).periods.forward_start == "2026-10-31"
    assert frozen.read_text(encoding="utf-8") == text


def test_lock_code_writes_nothing_when_refused(frozen):
    lock = json.loads(lock_path(frozen).read_text(encoding="utf-8"))
    lock["code_version"] = "e" * 64
    lock_path(frozen).write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    text = frozen.read_text(encoding="utf-8")
    with pytest.raises(LockRefused, match="already"):
        lock_code(frozen, today=date(2026, 10, 30), code_version="c" * 64)
    assert frozen.read_text(encoding="utf-8") == text
