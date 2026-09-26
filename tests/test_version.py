"""code_version(): SHA-256 of the perpbt source tree; git_commit(): best effort."""
import hashlib
import subprocess
from pathlib import Path

import pytest

from perpbt.version import PACKAGE_DIR, code_version, git_commit

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def make_tree(root: Path) -> None:
    (root / "a.py").write_bytes(b"x = 1\n")
    (root / "sub").mkdir()
    (root / "sub" / "__init__.py").write_bytes(b"")
    (root / "sub" / "b.py").write_bytes(b"y = 2\r\n")


def test_empty_tree_hashes_to_sha256_of_nothing(tmp_path):
    assert code_version(tmp_path) == EMPTY_SHA256


def test_stable_across_calls(tmp_path):
    make_tree(tmp_path)
    assert code_version(tmp_path) == code_version(tmp_path)
    assert code_version(str(tmp_path)) == code_version(tmp_path)


def test_matches_hand_computed_format(tmp_path):
    make_tree(tmp_path)
    h = hashlib.sha256()
    for rel, data in [("a.py", b"x = 1\n"), ("sub/__init__.py", b""), ("sub/b.py", b"y = 2\r\n")]:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(data)
        h.update(b"\0")
    assert code_version(tmp_path) == h.hexdigest()


def test_editing_one_byte_changes_hash(tmp_path):
    make_tree(tmp_path)
    before = code_version(tmp_path)
    (tmp_path / "sub" / "b.py").write_bytes(b"y = 3\r\n")
    assert code_version(tmp_path) != before


def test_non_py_files_do_not_count(tmp_path):
    make_tree(tmp_path)
    before = code_version(tmp_path)
    (tmp_path / "README.md").write_text("docs\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("notes\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.cpython-311.pyc").write_bytes(b"\x00\x01")
    (tmp_path / "sub" / "b.pyc").write_bytes(b"\x00\x01")
    assert code_version(tmp_path) == before


def test_renaming_a_file_changes_hash(tmp_path):
    make_tree(tmp_path)
    before = code_version(tmp_path)
    (tmp_path / "a.py").rename(tmp_path / "a2.py")
    assert code_version(tmp_path) != before


def test_moving_a_file_between_directories_changes_hash(tmp_path):
    make_tree(tmp_path)
    before = code_version(tmp_path)
    (tmp_path / "sub" / "b.py").rename(tmp_path / "b.py")
    assert code_version(tmp_path) != before


def test_real_package_hash_is_hex64():
    cv = code_version()
    assert len(cv) == 64 and int(cv, 16) >= 0
    assert PACKAGE_DIR.name == "perpbt"
    assert (PACKAGE_DIR / "version.py").is_file()


def test_git_commit_on_this_repo():
    commit = git_commit()
    assert commit is not None
    assert len(commit) == 40 and int(commit, 16) >= 0


def test_git_commit_returns_none_when_git_missing(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", boom)
    assert git_commit() is None


def test_git_commit_returns_none_outside_a_repo(monkeypatch):
    def failed(*args, **kwargs):
        return subprocess.CompletedProcess(args, 128, stdout="", stderr="fatal: not a git repository")

    monkeypatch.setattr(subprocess, "run", failed)
    assert git_commit() is None
