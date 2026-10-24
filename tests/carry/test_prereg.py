"""The carry pre-registration: parse, hash, universe check, JSON lock (carry spec Phase 2 §2.1-§2.2, §2.4)."""
import copy
import json
from pathlib import Path

import pytest
import yaml

from perpbt.carry.config import CarryCostConfig, CarrySignalConfig, MarginConfig, SleeveConfig
from perpbt.carry.prereg import (
    LOCK_KEYS, check_frozen, check_universe, freeze, load_prereg, lock_path, parse_prereg, read_lock,
)
from perpbt.config import ConfigError, from_dict, read_yaml_unique

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "configs" / "carry" / "prereg.yaml"
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
    assert p.registered_on == "2026-10-23"
    assert p.signal == from_dict(CarrySignalConfig, {"lookback_days": 7, "entry_annual": 0.10, "exit_annual": 0.0})
    assert p.sleeve == from_dict(SleeveConfig, {"perp_leverage": 2.0})
    assert p.margin == from_dict(MarginConfig, {"rebalance_below": 0.5, "liquidation_fee": 0.01,
                                                "mmr": {"default": 0.01, "BTCUSDT": 0.004, "ETHUSDT": 0.005}})
    assert p.costs == from_dict(CarryCostConfig, {"perp_taker": 0.0005, "spot_taker": 0.001, "slippage": {
        "default": 0.0005, "BTCUSDT": 0.0002, "ETHUSDT": 0.0002}})
    assert p.rebalance.fill_offset_min == 15
    assert [e.name for e in p.exploratory] == ["always_on", "entry_5", "entry_20", "lookback_3d", "lookback_14d",
                                               "lev_1"]
    ex = {e.name: e for e in p.exploratory}
    assert ex["always_on"].signal.entry_annual == ex["always_on"].signal.exit_annual == -1.0e9
    assert ex["always_on"].signal.lookback_days == 7 and ex["always_on"].sleeve == p.sleeve
    assert ex["entry_5"].signal.entry_annual == 0.05 and ex["entry_5"].signal.exit_annual == 0.0
    assert ex["entry_20"].signal.entry_annual == 0.20
    assert ex["lookback_3d"].signal.lookback_days == 3 and ex["lookback_3d"].signal.entry_annual == 0.10
    assert ex["lookback_14d"].signal.lookback_days == 14
    assert ex["lev_1"].sleeve.perp_leverage == 1.0 and ex["lev_1"].signal == p.signal
    pe = p.periods
    assert (pe.warmup_start, pe.warmup_end, pe.insample_start, pe.insample_end, pe.holdout_start, pe.holdout_end) \
        == ("2020-01-01", "2020-06-30", "2020-07-01", "2023-12-31", "2024-01-01", "2026-10-20")
    assert pe.forward_start == "2026-10-25" and pe.forward_reviews == (12, 36)  # set by the 2026-10-24 code lock
    assert p.universe["um"]["members"][0] == "BTCUSDT" and len(p.universe["um"]["members"]) == 10
    assert p.reprice.slippage == (0.0, 0.0002, 0.0005, 0.001) and p.reprice.spot_taker == (0.00075, 0.001)
    assert p.hurdle.annual == 0.05 and p.hurdle.descriptive == (0.0, 0.03, 0.05, 0.08)
    s = p.stats
    assert (s.alpha, s.bootstrap_n, s.block_len_days, s.random_timing_runs, s.shift_margin_days, s.master_seed) \
        == (0.05, 10000, 30, 1000, 60, 202610233)
    assert p.screen.bootstrap_n == 10000 and p.screen.stop_if_p_at_least == 0.10
    assert p.verdict_rule["reported_for"] == ["insample", "holdout", "forward_36m"]
    assert p.exploratory_row("lev_1") is ex["lev_1"]


def test_hash_stable_under_formatting(raw, tmp_path):
    base = h(raw)
    shuffled = write(tmp_path, dict(reversed(list(raw.items()))))
    assert load_prereg(shuffled).prereg_hash() == base
    text = PREREG.read_text(encoding="utf-8").replace("entry_annual: 0.10,", "entry_annual: 0.1,")
    text = text.replace("perp_leverage: 2.0}", "perp_leverage: 2}").replace("sleeve: {", "# a new comment\nsleeve: {")
    assert text != PREREG.read_text(encoding="utf-8")
    (tmp_path / "b.yaml").write_text(text, encoding="utf-8")
    assert load_prereg(tmp_path / "b.yaml").prereg_hash() == base
    assert len(base) == 64


def test_hash_changes_on_content(raw):
    base = h(raw)
    for edit in (
        lambda d: d["signal"].update(entry_annual=0.11),
        lambda d: d["margin"]["mmr"].update(BTCUSDT=0.005),
        lambda d: d["costs"].update(spot_taker=0.00075),
        lambda d: d["exploratory"].pop(),
        lambda d: d["hurdle"].update(annual=0.04),
        lambda d: d["stats"].update(master_seed=1),
        lambda d: d["verdict_rule"].update(forward_12m="reported"),
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
    lambda d: d.pop("hurdle"),
    lambda d: d["periods"]["insample"].update(extra=1),
    lambda d: d["periods"]["holdout"].pop("start"),
    lambda d: d["exploratory"].append({"name": "cheap", "costs": {"spot_taker": 0.0}}),
    lambda d: d["exploratory"].append({"name": "margin", "margin": {"rebalance_below": 0.2}}),
    lambda d: d["exploratory"].append({"signal": {"entry_annual": 0.3}}),
    lambda d: d["exploratory"].append({"name": "entry_5", "signal": {"entry_annual": 0.3}}),
    lambda d: d["exploratory"].append({"name": "bad", "signal": {"exit_annual": 0.5}}),
    lambda d: d["verdict_rule"].pop("v2"),
    lambda d: d["verdict_rule"].update(t3="extra"),
    lambda d: d["periods"]["warmup"].update(end="2020-07-01"),
    lambda d: d["periods"]["holdout"].update(start="2024-01-02"),
    lambda d: d["universe"].update(spot={"rule": "x", "members": ["BTCUSDT"]}),
    lambda d: d["universe"]["um"]["members"].append("BTCUSDT"),
    lambda d: d["sleeve"].update(perp_leverage=0),
    lambda d: d["margin"]["mmr"].update(BTCUSD=0.004),
    lambda d: d["costs"]["slippage"].update(SOLUSDT=0.0002),
    lambda d: d["rebalance"].update(fill_offset_min=30),
    lambda d: d["stats"].update(alpha=1.5),
    lambda d: d["stats"].update(block_len_days=0),
    lambda d: d["hurdle"].update(annual=-0.01),
    lambda d: d["hurdle"].update(descriptive=[]),
    lambda d: d["screen"].update(stop_if_p_at_least=0.0),
    lambda d: d["reprice"].update(slippage=[]),
])
def test_unknown_missing_and_invalid(raw, edit):
    d = copy.deepcopy(raw)
    edit(d)
    with pytest.raises(ConfigError):
        parse_prereg(d)


@pytest.mark.parametrize("end, start", [("2024-06-30", "2024-07-01"), ("2023-06-30", "2023-07-01")])
def test_periods_insample_end_is_carry_end(raw, end, start):
    # the store guard and load_pairframe stop at 2023-12-31; a prereg reaching past it, or stopping short, is invalid
    d = copy.deepcopy(raw)
    d["periods"]["insample"]["end"], d["periods"]["holdout"]["start"] = end, start
    with pytest.raises(ConfigError, match="carry in-sample end"):
        parse_prereg(d)


def test_duplicate_yaml_key_rejected(tmp_path):
    text = PREREG.read_text(encoding="utf-8").replace("  liquidation_fee: 0.01\n",
                                                      "  liquidation_fee: 0.01\n  liquidation_fee: 0.02\n")
    (tmp_path / "dup.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate key"):
        load_prereg(tmp_path / "dup.yaml")


def test_universe_must_match_universe_json(raw):
    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    check_universe(load_prereg(PREREG), universe)
    for edit in (
        lambda d: d["universe"]["um"]["members"].reverse(),
        lambda d: d["universe"]["um"]["members"].pop(),
        lambda d: d["universe"]["um"].update(rule="edited"),
    ):
        d = copy.deepcopy(raw)
        edit(d)
        with pytest.raises(ConfigError, match="universe"):
            check_universe(parse_prereg(d), universe)
    with pytest.raises(ConfigError, match="universe"):
        check_universe(load_prereg(PREREG), {"spot": universe["spot"]})


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
    d["costs"]["perp_taker"] = 0.0004
    write(tmp_path, d)
    with pytest.raises(ConfigError, match="hash"):
        check_frozen(path)
