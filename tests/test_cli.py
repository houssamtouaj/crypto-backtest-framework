"""CLI skeleton: every subcommand exists and reports 'not implemented'."""
import subprocess
import sys
from pathlib import Path

import pytest

from perpbt.cli import main

ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL = ["data", "run", "grid", "baselines", "report", "holdout"]
ALL_ARGV = [
    ["data", "fetch"],
    ["data", "validate"],
    ["run"],
    ["grid"],
    ["baselines"],
    ["report"],
    ["holdout"],
]


def test_help_lists_every_subcommand(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name in TOP_LEVEL:
        assert name in out


def test_data_help_lists_fetch_and_validate(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["data", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "fetch" in out
    assert "validate" in out


def test_python_dash_m_perpbt_help_runs():
    result = subprocess.run(
        [sys.executable, "-m", "perpbt", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for name in TOP_LEVEL:
        assert name in result.stdout


@pytest.mark.parametrize("argv", ALL_ARGV, ids=lambda a: " ".join(a))
def test_stub_reports_not_implemented_and_fails(argv, capsys):
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert "not implemented" in err
    assert " ".join(argv) in err


def test_no_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
