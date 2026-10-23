"""The trend pre-registration: parse, hash, universe check, JSON lock (trend spec Phase 2 §2.1-§2.2, §2.4)."""
import copy
import json
from pathlib import Path

import pytest
import yaml

from perpbt.config import ConfigError, from_dict, read_yaml_unique
from perpbt.trend.config import MEMBERS, SignalConfig, SizingConfig
from perpbt.trend.prereg import (
    LOCK_KEYS, check_frozen, check_universe, freeze, load_prereg, lock_path, parse_prereg, read_lock,
)

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "configs" / "trend" / "prereg.yaml"
UNIVERSE = ROOT / "configs" / "trend" / "universe.json"


@pytest.fixture
def raw():
    return read_yaml_unique(PREREG)


def write(tmp_path, data, name="prereg.yaml") -> Path:
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return p


def h(data) -> str:
    return parse_prereg(copy.deepcopy(data)).prereg_hash()


def test_round_trip():
    p = load_prereg(PREREG)
    assert p.signal == from_dict(SignalConfig, {"members": list(MEMBERS), "lookbacks": [20, 40, 80, 160],
                                                "donchian_exit": "half", "direction": "long_flat"})
    assert p.sizing == from_dict(SizingConfig, {"target_vol": 0.30, "vol_window": 60, "max_weight": 2.0,
                                                "max_gross": 2.0})
    assert p.rebalance.fill_offset_min == 15 and p.rebalance.drift_band == 0.20
    assert [e.name for e in p.exploratory] == ["long_short", "weekly", "fill_0100", "fill_0400", "fill_0800"]
    ex = {e.name: e for e in p.exploratory}
    assert ex["long_short"].signal.direction == "long_short" and ex["long_short"].rebalance == p.rebalance
    assert ex["fill_0800"].rebalance.fill_offset_min == 480 and ex["fill_0800"].signal == p.signal
    assert ex["weekly"].rebalance.frequency == "weekly"
    assert p.periods.insample_start == "2020-07-01" and p.periods.insample_end == "2026-10-20"
    assert p.periods.forward_start is None and p.periods.forward_reviews == (12, 36)
    assert p.universe["um"]["members"][0] == "BTCUSDT" and len(p.universe["um"]["members"]) == 10
    assert p.stats.master_seed == 20261023 and p.screen.baseline_runs == 1000
    assert p.costs.slippage_for("ETHUSDT") == 0.0002
    assert p.reprice.fee_taker == (0.0004, 0.0005)


def test_hash_stable_under_formatting(raw, tmp_path):
    base = h(raw)
    shuffled = write(tmp_path, dict(reversed(list(raw.items()))))
    assert load_prereg(shuffled).prereg_hash() == base
    text = PREREG.read_text(encoding="utf-8").replace("target_vol: 0.30", "target_vol: 0.3")
    text = text.replace("max_weight: 2.0", "max_weight: 2").replace("signal:  #", "# a new comment\nsignal:  #")
    (tmp_path / "b.yaml").write_text(text, encoding="utf-8")
    assert load_prereg(tmp_path / "b.yaml").prereg_hash() == base
    assert len(base) == 64


def test_hash_changes_on_content(raw):
    base = h(raw)
    for edit in (
        lambda d: d["rebalance"].update(drift_band=0.25),
        lambda d: d["signal"].update(lookbacks=[10, 20, 40, 80, 160]),
        lambda d: d["exploratory"].pop(),
        lambda d: d["stats"].update(master_seed=1),
        lambda d: d["verdict_rule"].update(t3="reported"),
    ):
        d = copy.deepcopy(raw)
        edit(d)
        assert h(d) != base


def test_forward_start_excluded_from_hash(raw):
    d = copy.deepcopy(raw)
    d["periods"]["forward"]["start"] = "2026-11-01"
    assert h(d) == h(raw)
    p = parse_prereg(d)
    assert p.periods.forward_start == "2026-11-01"
    assert p.content()["periods"]["forward_start"] == "2026-11-01"


@pytest.mark.parametrize("edit", [
    lambda d: d.update(extra=1),
    lambda d: d.pop("screen"),
    lambda d: d["periods"]["insample"].update(extra=1),
    lambda d: d["exploratory"].append({"name": "cheap", "costs": {"fee_taker": 0.0}}),
    lambda d: d["exploratory"].append({"name": "weekly", "rebalance": {"frequency": "weekly"}}),
    lambda d: d["exploratory"].append({"signal": {"direction": "long_short"}}),
    lambda d: d["verdict_rule"].pop("t3"),
    lambda d: d["periods"]["holdout"].update(market="um"),
    lambda d: d["periods"]["warmup"].update(end="2020-07-01"),
    lambda d: d["universe"].pop("spot"),
    lambda d: d["stats"].update(alpha=1.5),
    lambda d: d["screen"].update(stop_if_p_at_least=0.0),
    lambda d: d["reprice"].update(slippage=[]),
])
def test_unknown_missing_and_invalid(raw, edit):
    d = copy.deepcopy(raw)
    edit(d)
    with pytest.raises(ConfigError):
        parse_prereg(d)


def test_duplicate_yaml_key_rejected(tmp_path):
    text = PREREG.read_text(encoding="utf-8").replace("  donchian_exit: half\n", "  donchian_exit: half\n  donchian_exit: half\n")
    (tmp_path / "dup.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate key"):
        load_prereg(tmp_path / "dup.yaml")


def test_universe_must_match_universe_json(raw):
    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    check_universe(load_prereg(PREREG), universe)
    for edit in (
        lambda d: d["universe"]["um"]["members"].reverse(),
        lambda d: d["universe"]["um"]["members"].pop(),
        lambda d: d["universe"]["spot"].update(rule="edited"),
    ):
        d = copy.deepcopy(raw)
        edit(d)
        with pytest.raises(ConfigError, match="universe"):
            check_universe(parse_prereg(d), universe)


def test_freeze_writes_json_lock_once(raw, tmp_path):
    path = write(tmp_path, raw)
    lock = freeze(path, universe_path=UNIVERSE)
    assert lock_path(path) == tmp_path / "prereg.lock"
    on_disk = json.loads(lock_path(path).read_text(encoding="utf-8"))
    assert list(on_disk) == list(LOCK_KEYS) and on_disk == lock
    assert lock["prereg_hash"] == load_prereg(path).prereg_hash()
    assert lock["code_version"] is None and lock["code_locked_on"] is None and lock["code_git_commit"] is None
    assert read_lock(lock_path(path)) == lock
    with pytest.raises(FileExistsError):
        freeze(path, universe_path=UNIVERSE)


def test_freeze_refuses_universe_mismatch(raw, tmp_path):
    d = copy.deepcopy(raw)
    d["universe"]["um"]["members"].pop()
    path = write(tmp_path, d)
    with pytest.raises(ConfigError, match="universe"):
        freeze(path, universe_path=UNIVERSE)
    assert not lock_path(path).exists()


def test_read_lock_exact_keys(tmp_path):
    p = tmp_path / "x.lock"
    p.write_text(json.dumps({"frozen_on": "2026-10-23"}), encoding="utf-8")
    with pytest.raises(ConfigError):
        read_lock(p)


def test_check_frozen(raw, tmp_path):
    path = write(tmp_path, raw)
    with pytest.raises(ConfigError, match="not frozen"):
        check_frozen(path)
    freeze(path, universe_path=UNIVERSE)
    assert check_frozen(path).prereg_hash() == h(raw)
    d = copy.deepcopy(raw)
    d["periods"]["forward"]["start"] = "2026-11-01"
    write(tmp_path, d)
    assert check_frozen(path).periods.forward_start == "2026-11-01"
    d["rebalance"]["drift_band"] = 0.25
    write(tmp_path, d)
    with pytest.raises(ConfigError, match="hash"):
        check_frozen(path)
