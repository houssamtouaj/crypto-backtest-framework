"""CLI: data fetch/validate wiring, the Phase 6 experiment commands, and the remaining 'not implemented' stub."""
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
TOP_LEVEL = ["data", "prereg", "run", "grid", "baselines", "stats", "report", "holdout"]
STUB_ARGV = [["report"]]


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


# --- Phase 6 -----------------------------------------------------------------------------------------

def test_grid_dry_run_prints_324(capsys):
    """Phase 6 exit criterion: `perpbt grid --dry-run` prints 324."""
    assert main(["grid", "--dry-run", "--prereg", str(ROOT / "configs" / "prereg.yaml")]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-1].startswith("324 variants (36 per pair x session, 9 cells)")
    assert len(out) == 325 and sum(" primary " in line for line in out) == 9


def test_prereg_validate_on_the_committed_file(capsys):
    assert main(["prereg", "validate", "--prereg", str(ROOT / "configs" / "prereg.yaml")]) == 0
    out = capsys.readouterr().out
    assert "prereg_hash: " in out and "grid: 324 variants, 9 primary" in out


def test_experiment_commands_reject_a_bad_prereg_and_bad_flags(tmp_path, capsys):
    bad = tmp_path / "p.yaml"
    bad.write_text("registered_on: 2026-09-26\n", encoding="utf-8")
    assert main(["grid", "--dry-run", "--prereg", str(bad)]) == 2
    assert "missing keys" in capsys.readouterr().err
    assert main(["holdout", "--reason", "x", "--prereg", str(bad)]) == 2
    for argv in (["grid"], ["baselines", "--runs", "5"], ["run"], ["stats", "nope"]):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2


def test_the_full_pipeline_end_to_end_on_a_synthetic_data_directory(tmp_path, capsys):
    """Phase 6 exit criterion: Phase 8's protocol, command by command, on synthetic data."""
    from perpbt.experiments.registry import read_results
    from tests.experiment_harness import make_world

    path, prereg, data_cfg, runs = make_world(tmp_path, download="2020-01-19", holdout_end="2020-01-18")
    common = ["--prereg", str(path), "--data-dir", data_cfg.data_dir, "--runs-dir", str(runs)]
    pool = [*common, "--workers", "1"]
    assert main(["prereg", "validate", *common]) == 0
    assert main(["holdout", *common]) == 1  # not frozen yet
    assert "refused" in capsys.readouterr().err
    assert main(["prereg", "freeze", *common]) == 0
    assert main(["prereg", "freeze", *common]) == 1
    assert main(["run", "--primary", "--pair", "BTCUSDT", "--session", "utc", *pool]) == 0
    assert main(["run", "--primary", *pool]) == 0
    assert "skipped 1" in capsys.readouterr().out
    assert main(["stats", "holm", *common]) == 1  # no baselines yet
    assert main(["baselines", "--primary", *pool]) == 0
    assert main(["stats", "holm", *common]) == 0
    assert main(["stats", "dsr", *common]) == 1  # the grid has not run
    assert main(["grid", "--run", *pool]) == 0
    assert main(["baselines", "--grid", "--runs", "5", *pool]) == 0
    assert main(["stats", "dsr", *common]) == 0
    assert main(["stats", "results", *common]) == 0
    assert main(["prereg", "validate", *common]) == 0
    assert main(["holdout", *common]) == 0
    assert main(["holdout", *common]) == 1  # touched once
    res = read_results(runs)
    assert len(res) == 12 + 4 and res["is_holdout"].sum() == 4
    ins = res[~res["is_holdout"]]
    assert ins.loc[ins["is_primary"], "p_a_adj"].notna().all() and ins["dsr_local"].notna().all()
    assert set(ins.loc[~ins["is_primary"], "n_baseline_runs"]) == {5}
