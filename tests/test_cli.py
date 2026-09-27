"""CLI: data fetch/validate wiring and the remaining 'not implemented' stubs."""
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from perpbt import cli
from perpbt.cli import main
from perpbt.config import DataConfig, load_yaml
from perpbt.data.bulk import DownloadError

ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL = ["data", "run", "grid", "baselines", "report", "holdout"]
STUB_ARGV = [["run"], ["grid"], ["baselines"], ["report"], ["holdout"]]


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


def test_data_fetch_help_lists_every_option(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["data", "fetch", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for opt in ("--config", "--pairs", "--tfs", "--from", "--to", "--ccxt-head", "--ccxt-tail", "--ccxt-gaps"):
        assert opt in out


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


@pytest.mark.parametrize("argv", STUB_ARGV, ids=lambda a: " ".join(a))
def test_stub_reports_not_implemented_and_fails(argv, capsys):
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert "not implemented" in err
    assert " ".join(argv) in err


def test_no_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "data.yaml"
    path.write_text(
        f"data_dir: {(tmp_path / 'data').as_posix()}\n"
        "insample_start: '2020-01-01'\ninsample_end: '2025-12-31'\n"
        "holdout_start: '2026-01-01'\nholdout_end: null\nwarmup_start: '2019-11-01'\n"
        "listing:\n  BTCUSDT: '2019-09-08'\n  ETHUSDT: '2019-11-27'\n",
        encoding="utf-8",
    )
    return path


def test_committed_data_config_loads():
    cfg = load_yaml(ROOT / "configs" / "data.yaml", DataConfig)
    assert cfg.data_dir == "data" and cfg.insample_end == "2025-12-31" and cfg.holdout_end is None
    assert cfg.warmup_start == "2019-11-01"
    assert cfg.listing == {"BTCUSDT": "2019-09-08", "ETHUSDT": "2019-11-27", "SOLUSDT": "2020-09-14"}
    assert cli.DEFAULT_DATA_CONFIG == "configs/data.yaml"


def test_data_fetch_passes_arguments_to_the_pipeline(monkeypatch, config_path, capsys):
    calls = []

    def fake_run_fetch(cfg, **kw):
        calls.append((cfg, kw))
        return {"BTCUSDT": {"15m": {"rows": 5, "files": 1}, "funding": {"rows": 0}}}

    monkeypatch.setattr(cli, "run_fetch", fake_run_fetch)
    rc = main([
        "data", "fetch", "--config", str(config_path), "--pairs", "BTCUSDT", "--tfs", "15m",
        "--from", "2026-09-20", "--to", "2026-09-26", "--ccxt-tail", "--ccxt-gaps",
    ])
    assert rc == 0
    cfg, kw = calls[0]
    assert isinstance(cfg, DataConfig) and cfg.listing["BTCUSDT"] == "2019-09-08"
    assert kw == {
        "pairs": ["BTCUSDT"], "tfs": ("15m",), "from_date": date(2026, 9, 20), "to_date": date(2026, 9, 26),
        "ccxt_head": False, "ccxt_tail": True, "ccxt_gaps": True,
    }
    out = capsys.readouterr().out
    assert "BTCUSDT 15m: rows=5 files=1" in out and "BTCUSDT funding: rows=0" in out


def test_data_fetch_defaults(monkeypatch, config_path):
    calls = []
    monkeypatch.setattr(cli, "run_fetch", lambda cfg, **kw: calls.append(kw) or {})
    assert main(["data", "fetch", "--config", str(config_path)]) == 0
    assert calls == [{
        "pairs": ["BTCUSDT", "ETHUSDT"], "tfs": ("1m", "15m"), "from_date": None, "to_date": None,
        "ccxt_head": False, "ccxt_tail": False, "ccxt_gaps": False,
    }]


def test_data_fetch_rejects_unknown_timeframe_and_bad_date(config_path):
    with pytest.raises(SystemExit) as exc:
        main(["data", "fetch", "--config", str(config_path), "--tfs", "1h"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        main(["data", "fetch", "--config", str(config_path), "--from", "2026/09/20"])
    assert exc.value.code == 2


def test_data_fetch_missing_or_invalid_config_is_exit_2(tmp_path, capsys):
    assert main(["data", "fetch", "--config", str(tmp_path / "nope.yaml")]) == 2
    assert "nope.yaml" in capsys.readouterr().err
    bad = tmp_path / "bad.yaml"
    bad.write_text("data_dir: data\nunknown: 1\n", encoding="utf-8")
    assert main(["data", "fetch", "--config", str(bad)]) == 2
    assert "unknown" in capsys.readouterr().err


def test_data_fetch_download_error_is_exit_1(monkeypatch, config_path, capsys):
    def boom(cfg, **kw):
        raise DownloadError("GET x: HTTP 500")

    monkeypatch.setattr(cli, "run_fetch", boom)
    assert main(["data", "fetch", "--config", str(config_path)]) == 1
    assert "HTTP 500" in capsys.readouterr().err


def test_data_validate_prints_the_report(monkeypatch, config_path, capsys):
    calls = []

    def fake_run_validate(cfg, pairs):
        calls.append(pairs)
        return {"BTCUSDT": {"15m": {"rows": 3, "gaps": 1, "missing_slots": 3}}, "ETHUSDT": {}}

    monkeypatch.setattr(cli, "run_validate", fake_run_validate)
    assert main(["data", "validate", "--config", str(config_path)]) == 0
    assert calls == [["BTCUSDT", "ETHUSDT"]]
    out = capsys.readouterr().out
    assert "BTCUSDT 15m: rows=3 gaps=1 missing_slots=3" in out
    assert "ETHUSDT: nothing stored" in out


def test_data_validate_hard_failure_is_exit_1(monkeypatch, config_path, capsys):
    def boom(cfg, pairs):
        raise ValueError("timestamps are off the grid")

    monkeypatch.setattr(cli, "run_validate", boom)
    assert main(["data", "validate", "--config", str(config_path), "--pairs", "BTCUSDT"]) == 1
    assert "grid" in capsys.readouterr().err
