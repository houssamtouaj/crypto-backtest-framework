"""The basis sanity check (carry spec Phase 0 §0.4)."""
import json

import numpy as np
import pandas as pd
import pytest

from perpbt.carry.basis import BASIS_END, BASIS_START, basis_check, basis_stats
from perpbt.cli import main
from perpbt.config import DataConfig
from perpbt.data.store import Candles, CandleStore, date_ms

STEP = 900_000
W0 = date_ms("2020-07-01")
W1 = date_ms("2024-01-01")  # exclusive end of the window
SLOTS = (W1 - W0) // STEP


def candles(ts, close, pair="BTCUSDT"):
    ts = np.asarray(ts, dtype=np.int64)
    c = np.asarray(close, dtype=np.float64)
    return Candles(pair, "15m", ts, c, c, c, c, np.ones(len(ts)))


def test_window_constants():
    assert (BASIS_START, BASIS_END) == ("2020-07-01", "2023-12-31")


def test_ratio_stats():
    ts = W0 + STEP * np.arange(5)
    s = basis_stats(candles(ts, [100.1, 100.1, 101.0, 100.1, 100.1]), candles(ts, [100.0] * 5), W0, W1)
    assert s["slots"] == SLOTS and s["common"] == 5 and s["perp_rows"] == 5 and s["spot_rows"] == 5
    assert s["coverage"] == pytest.approx(5 / SLOTS)
    assert s["median"] == pytest.approx(0.001)
    assert s["max_abs"] == pytest.approx(0.01)
    assert s["max_abs_at"] == "2020-07-01T00:30Z" and s["basis_at_max_abs"] == pytest.approx(0.01)
    assert s["p01"] == pytest.approx(0.001) and s["p99"] == pytest.approx(0.001 + 0.96 * 0.009)
    assert not s["flagged"] and s["reasons"] == []


def test_one_leg_slots_excluded():
    ts = W0 + STEP * np.arange(4)
    perp = candles(np.r_[ts, W0 + 10 * STEP], [100.1] * 4 + [500.0])
    spot = candles(np.r_[ts, W0 + 11 * STEP], [100.0] * 4 + [1.0])
    s = basis_stats(perp, spot, W0, W1)
    assert (s["perp_rows"], s["spot_rows"], s["common"]) == (5, 5, 4)
    assert s["coverage"] == pytest.approx(4 / SLOTS)
    assert s["median"] == pytest.approx(0.001) and s["max_abs"] == pytest.approx(0.001)


def test_rows_outside_the_window_are_ignored():
    ts = np.r_[W0 - STEP, W0, W1]
    s = basis_stats(candles(ts, [200.0, 100.0, 200.0]), candles(ts, [100.0] * 3), W0, W1)
    assert (s["perp_rows"], s["spot_rows"], s["common"]) == (1, 1, 1) and s["max_abs"] == 0.0


def test_flags():
    ts = W0 + STEP * np.arange(3)
    s = basis_stats(candles(ts, [100.6] * 3), candles(ts, [100.0] * 3), W0, W1)
    assert s["flagged"] and s["reasons"] == ["median"]
    s = basis_stats(candles(ts, [100.0, 125.0, 100.0]), candles(ts, [100.0] * 3), W0, W1)
    assert s["flagged"] and s["reasons"] == ["max_abs"]
    s = basis_stats(candles(ts, [100.0, 70.0, 100.0]), candles(ts, [100.0] * 3), W0, W1)
    assert s["max_abs"] == pytest.approx(0.30) and s["basis_at_max_abs"] == pytest.approx(-0.30)


def test_no_common_slots():
    s = basis_stats(candles([W0], [1.0]), candles([W0 + STEP], [1.0]), W0, W1)
    assert s["common"] == 0 and s["coverage"] == 0.0
    assert s["median"] is None and s["max_abs"] is None and s["max_abs_at"] is None and s["basis_at_max_abs"] is None
    assert s["flagged"] and s["reasons"] == ["no common slots"]
    json.dumps(s, allow_nan=False)


def _cfg(tmp_path, insample_end="2023-12-31"):
    return DataConfig(data_dir=str(tmp_path), insample_start="2020-01-01", insample_end=insample_end,
                      holdout_start="2024-01-01", holdout_end=None, warmup_start="2020-01-01",
                      listing={"BTCUSDT": "2020-01-01"})


def _write(cfg, market, ts, close):
    n = len(ts)
    CandleStore(cfg, market=market).write("BTCUSDT", "15m", pd.DataFrame({
        "open_ms": ts, "open": close, "high": close, "low": close, "close": close, "volume": 1.0,
        "quote_volume": 1.0, "trades": 1, "taker_buy_volume": 0.5, "source": ["bulk_monthly"] * n}))


def test_window_is_fixed_whatever_the_config(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, insample_end="2026-10-20")  # the trend perp config's reach
    ts = np.array([W0 - STEP, W0, W1 - STEP, W1, date_ms("2025-06-01")], dtype=np.int64)
    _write(cfg, "um", ts, np.array([1.0, 100.1, 100.1, 999.0, 999.0]))
    _write(cfg, "spot", ts, np.full(5, 100.0))
    calls = []
    real_load = CandleStore.load

    def spy(self, pair, tf, start_ms, end_ms, **kw):
        calls.append((self.market, start_ms, end_ms, kw))
        return real_load(self, pair, tf, start_ms, end_ms, **kw)

    monkeypatch.setattr(CandleStore, "load", spy)
    out = basis_check(cfg, ["BTCUSDT"])
    assert calls == [("um", W0, W1, {}), ("spot", W0, W1, {})]  # the loads themselves stop at the window end
    assert out["window"] == ["2020-07-01", "2023-12-31"]
    s = out["pairs"]["BTCUSDT"]
    assert (s["perp_rows"], s["spot_rows"], s["common"]) == (2, 2, 2) and not s["flagged"]


def test_cli_basis_writes_json(tmp_path, monkeypatch):
    cfg_path = tmp_path / "data.yaml"
    cfg_path.write_text(f"data_dir: {tmp_path.as_posix()}\ninsample_start: '2020-01-01'\n"
                        "insample_end: '2023-12-31'\nholdout_start: '2024-01-01'\nholdout_end: null\n"
                        "warmup_start: '2020-01-01'\nlisting:\n  BTCUSDT: '2020-01-01'\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    ts = W0 + STEP * np.arange(3)
    _write(cfg, "um", ts, np.full(3, 100.1))
    _write(cfg, "spot", ts, np.full(3, 100.0))
    out = tmp_path / "runs" / "basis_check.json"
    assert main(["carry", "basis", "--config", str(cfg_path), "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["pairs"]["BTCUSDT"]["common"] == 3
    assert data["limits"] == {"median_abs": 0.005, "max_abs": 0.2}


def test_cli_basis_missing_pair_exit_2(tmp_path, capsys):
    cfg_path = tmp_path / "data.yaml"
    cfg_path.write_text(f"data_dir: {tmp_path.as_posix()}\ninsample_start: '2020-01-01'\n"
                        "insample_end: '2023-12-31'\nholdout_start: '2024-01-01'\nholdout_end: null\n"
                        "warmup_start: '2020-01-01'\nlisting:\n  BTCUSDT: '2020-01-01'\n", encoding="utf-8")
    out = tmp_path / "basis_check.json"
    assert main(["carry", "basis", "--config", str(cfg_path), "--out", str(out)]) == 2
    assert "BTCUSDT" in capsys.readouterr().err and not out.exists()


def _cfg_file(tmp_path, insample_end="2023-12-31", insample_start="2020-01-01"):
    path = tmp_path / "data.yaml"
    path.write_text(f"data_dir: {tmp_path.as_posix()}\ninsample_start: '{insample_start}'\n"
                    f"insample_end: '{insample_end}'\nholdout_start: '2024-01-01'\nholdout_end: null\n"
                    f"warmup_start: '{insample_start}'\nlisting:\n  BTCUSDT: '2020-01-01'\n", encoding="utf-8")
    return path


def test_cli_basis_refused_load_exit_1(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    ts = W0 + STEP * np.arange(3)
    _write(cfg, "um", ts, np.full(3, 100.1))
    _write(cfg, "spot", ts, np.full(3, 100.0))
    out = tmp_path / "basis_check.json"
    path = _cfg_file(tmp_path, insample_end="2019-12-31", insample_start="2017-08-01")
    assert main(["carry", "basis", "--config", str(path), "--out", str(out)]) == 1
    assert "insample_end" in capsys.readouterr().err and not out.exists()


def test_cli_basis_spot_missing_exit_2(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    _write(cfg, "um", W0 + STEP * np.arange(3), np.full(3, 100.1))
    out = tmp_path / "basis_check.json"
    assert main(["carry", "basis", "--config", str(_cfg_file(tmp_path)), "--out", str(out)]) == 2
    assert "spot" in capsys.readouterr().err and not out.exists()


def test_cli_basis_non_finite_exit_1(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    ts = W0 + STEP * np.arange(3)
    _write(cfg, "um", ts, np.full(3, 100.1))
    _write(cfg, "spot", ts, np.array([100.0, 0.0, 100.0]))
    out = tmp_path / "basis_check.json"
    assert main(["carry", "basis", "--config", str(_cfg_file(tmp_path)), "--out", str(out)]) == 1
    assert "non-finite" in capsys.readouterr().err and not out.exists()
