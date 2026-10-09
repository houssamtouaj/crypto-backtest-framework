"""Spec §6.1 (test 6.1): the pre-registration loader, its hash and the lock."""
import copy
import re
from pathlib import Path

import pytest
import yaml

from perpbt.config import ConfigError, ExecConfig, StrategyParams
from perpbt.experiments.prereg import freeze, load_prereg, lock_path, parse_prereg, read_lock
from perpbt.version import code_version

ROOT = Path(__file__).resolve().parents[1]
PREREG = ROOT / "configs" / "prereg.yaml"
SPEC = ROOT / "docs" / "superpowers" / "specs" / "2026-09-26-ob-backtest" / "phase-6-experiments.md"


@pytest.fixture(scope="module")
def raw():
    return yaml.safe_load(PREREG.read_text(encoding="utf-8"))


def h(raw_):
    return parse_prereg(raw_).prereg_hash()


def test_committed_file_is_the_spec_text(raw):
    block = re.search(r"## 6\.8.*?```yaml\n(.*?)```", SPEC.read_text(encoding="utf-8"), re.S).group(1)
    assert yaml.safe_load(block) == raw


def test_committed_file_loads(raw):
    p = load_prereg(PREREG)
    assert p.pairs == ("BTCUSDT", "ETHUSDT", "SOLUSDT") and list(p.sessions) == ["utc", "ny", "london"]
    assert p.primary == StrategyParams() and p.data_download_date is None and p.holdout.end is None
    assert p.exec_for("BTCUSDT") == ExecConfig()
    assert p.exec_for("ETHUSDT") == ExecConfig(mmr=0.005)
    assert p.exec_for("SOLUSDT") == ExecConfig(mmr=0.010, slippage=0.0005)
    assert (p.stats.baseline_runs_primary, p.stats.baseline_runs_grid) == (5000, 500)
    d = p.data_config("data")
    assert (d.insample_end, d.holdout_start, d.warmup_start, d.listing["SOLUSDT"]) == (
        "2025-12-31", "2026-01-01", "2019-11-01", "2020-09-14")


def test_key_order_comments_and_number_spelling_do_not_change_the_hash(raw, tmp_path):
    ref = load_prereg(PREREG).prereg_hash()
    reordered = tmp_path / "p.yaml"
    reordered.write_text("# another comment\n" + yaml.safe_dump(raw, sort_keys=True), encoding="utf-8")
    assert load_prereg(reordered).prereg_hash() == ref
    r = copy.deepcopy(raw)
    r["primary"]["r_target"] = 2  # int vs 2.0
    r["execution"]["max_leverage"] = 25.0
    r["grid"]["heatmaps"][0]["r_target"] = [1.0, 1.5, 2.0, 3.0]
    assert h(r) == ref


def _set(path, value):
    def mutate(r):
        d = r
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = value
    return mutate


HASHED = {
    "registered_on": _set(("registered_on",), "2026-09-27"),
    "insample.end": _set(("insample", "end"), "2025-12-30"),
    "holdout.start": _set(("holdout", "start"), "2026-01-02"),
    "warmup": _set(("warmup_start",), "2019-11-02"),
    "pairs order": _set(("pairs",), ["ETHUSDT", "BTCUSDT", "SOLUSDT"]),
    "listing": _set(("listing", "SOLUSDT"), "2020-09-15"),
    "session open": _set(("sessions", "ny", "open"), "09:45"),
    "primary": _set(("primary", "r_target"), 3),
    "execution": _set(("execution", "fee_taker"), 0.0004),
    "per_pair": _set(("execution", "per_pair", "SOLUSDT", "slippage"), 0.001),
    "heatmap": _set(("grid", "heatmaps", 1, "confirm_n"), [2, 3, 4]),
    "single": _set(("grid", "singles", 1, "pierce"), 0.001),
    "stats": _set(("stats", "master_seed"), 1),
    "family": _set(("primary_family", "correction"), "bonferroni"),
    "verdict": _set(("verdict_rule", "beats_baseline_a"), "raw p_a < alpha"),
}


@pytest.mark.parametrize("name", HASHED)
def test_every_hashed_field_changes_the_hash(raw, name):
    r = copy.deepcopy(raw)
    HASHED[name](r)
    assert h(r) != h(raw)


def test_download_date_and_holdout_end_are_excluded(raw):
    r = copy.deepcopy(raw)
    r["data_download_date"] = "2026-09-27"
    r["holdout"]["end"] = "2026-09-27"
    p = parse_prereg(r)
    assert p.prereg_hash() == h(raw) and p.holdout.end == "2026-09-27"


INVALID = {
    "unknown session": (_set(("sessions", "tokyo"), {"tz": "Asia/Tokyo", "open": "09:00", "close": "15:00",
                                                     "days": [0, 1, 2, 3, 4]}), "unknown session name"),
    "max_hold without hours (grid)": (_set(("grid", "heatmaps", 2, "hold_rule"), [{"kind": "max_hold"}]), "hours"),
    "max_hold without hours (primary)": (_set(("primary", "hold_rule"), {"kind": "max_hold"}), "hours"),
    "unknown top key": (_set(("extra",), 1), "unknown keys"),
    "unknown grid axis": (_set(("grid", "heatmaps", 0, "axes"), ["r_target", "nope"]), "nope"),
    "axis without values": (_set(("grid", "heatmaps", 0, "axes"), ["r_target", "entry_level", "zone"]), "missing"),
    "duplicate axis value": (_set(("grid", "heatmaps", 0, "r_target"), [1, 1.0]), "duplicate"),
    "bad single": (_set(("grid", "singles", 0), {"zone": "half"}), "zone"),
    "per_pair unknown pair": (_set(("execution", "per_pair", "XRPUSDT"), {"mmr": 0.01}), "not in pairs"),
    "per_pair unknown field": (_set(("execution", "per_pair", "ETHUSDT", "fee"), 0.1), "unknown keys"),
    "holdout overlaps": (_set(("holdout", "start"), "2025-12-31"), "after insample.end"),
    "listing missing": (_set(("pairs",), ["BTCUSDT", "XRPUSDT"]), "no listing date"),
    "bad date": (_set(("insample", "start"), "2020-13-01"), "date"),
    "negative fee": (_set(("execution", "fee_maker"), -0.1), "fee_maker"),
    "holdout end without download": (_set(("holdout", "end"), "2026-09-27"), "data_download_date"),
}


@pytest.mark.parametrize("name", INVALID)
def test_invalid_values_raise(raw, name):
    mutate, match = INVALID[name]
    r = copy.deepcopy(raw)
    mutate(r)
    with pytest.raises(ConfigError, match=match):
        parse_prereg(r)


def test_primary_must_name_every_parameter(raw):
    r = copy.deepcopy(raw)
    del r["primary"]["pierce"]
    with pytest.raises(ConfigError, match="missing keys"):
        parse_prereg(r)


def test_duplicate_yaml_keys_are_rejected(tmp_path):
    text = PREREG.read_text(encoding="utf-8").replace("warmup_start: 2019-11-01",
                                                      "warmup_start: 2019-11-01\nwarmup_start: 2019-10-01")
    path = tmp_path / "p.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate key 'warmup_start'"):
        load_prereg(path)


def test_freeze_writes_the_lock_once(raw, tmp_path):
    path = tmp_path / "prereg.yaml"
    path.write_text(PREREG.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ConfigError, match="data_download_date"):
        freeze(path)
    assert not lock_path(path).exists()
    path.write_text(path.read_text(encoding="utf-8").replace("data_download_date: null",
                                                             "data_download_date: 2026-09-27"), encoding="utf-8")
    lock = freeze(path)
    assert lock_path(path) == tmp_path / "prereg.lock"
    back = read_lock(lock_path(path))
    assert back == lock
    assert back["prereg_hash"] == load_prereg(PREREG).prereg_hash()  # the download date is not hashed
    assert back["code_version"] == code_version() and back["data_download_date"] == "2026-09-27"
    before = lock_path(path).read_bytes()
    with pytest.raises(FileExistsError):
        freeze(path)
    assert lock_path(path).read_bytes() == before
