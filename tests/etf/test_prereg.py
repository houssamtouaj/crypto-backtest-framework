"""The ETF pre-registration: parse, hash, snapshot check, JSON lock (ETF spec Phase 2 §2.1-§2.2, §2.4)."""
import copy
import json
from pathlib import Path

import pytest
import yaml

from perpbt.config import ConfigError, from_dict, read_yaml_unique
from perpbt.etf.config import EtfCostConfig, EtfRebalanceConfig, EtfSizingConfig, load_data_config
from perpbt.etf.prereg import (
    LOCK_KEYS, check_frozen, check_snapshot, freeze, load_prereg, lock_path, parse_prereg, read_lock,
)
from perpbt.etf.store import EtfStore
from perpbt.trend.config import MEMBERS, SignalConfig
from tests.etf.synth import make_config, make_data, write_store

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "configs" / "etf" / "prereg.yaml"
DATA = ROOT / "configs" / "etf" / "data.yaml"


@pytest.fixture
def raw():
    return read_yaml_unique(PREREG)


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    """A synthetic store and a data config with the real dates; the prereg pinned to its manifest."""
    root = tmp_path_factory.mktemp("snap")
    store = write_store(root / "etf", make_data())
    cfg = make_config(data_dir=str(root / "etf"), holdout_end="2026-10-23", download_date="2026-10-24")
    return store, cfg


def pinned(raw, store):
    d = copy.deepcopy(raw)
    d["data"]["manifest_sha256"] = store.manifest_sha256()
    return d


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
    assert p.sizing == from_dict(EtfSizingConfig, {"target_vol": 0.15, "vol_window": 60, "max_weight": 1.0,
                                                   "annualise_days": 252})
    assert p.rebalance == from_dict(EtfRebalanceConfig, {"frequency": "daily", "weekday": None, "drift_band": 0.2,
                                                         "fill": "next_open"})
    assert p.costs == from_dict(EtfCostConfig, {"commission": 0.0,
                                                "slippage": {"default": 0.0003, "SPY": 0.0002, "QQQ": 0.0002}})
    assert [p.costs.slippage_for(t) for t in ("SPY", "QQQ", "IWM", "GLD")] == [0.0002, 0.0002, 0.0003, 0.0003]
    assert p.universe.tickers == ("SPY", "QQQ", "IWM", "GLD")
    assert (p.periods.warmup_start, p.periods.insample_start, p.periods.insample_end) == \
        ("2004-11-18", "2005-08-01", "2017-12-31")
    assert (p.periods.holdout_start, p.periods.holdout_end) == ("2018-01-01", "2026-10-23")
    assert p.periods.forward_start is None and p.periods.forward_reviews == (12, 36)
    assert p.data.download_date == "2026-10-24"
    assert p.data.manifest_sha256 == "308f5012c5a274b62ee2d5f29e4b299cd225be3fa0cf2b02ff60a73f88c60658"
    assert (p.cash.series, p.cash.day_count) == ("DTB3", 360)
    assert p.reprice.slippage == (0.0, 0.0002, 0.0005, 0.001, 0.002) and p.reprice.commission == (0.0, 0.0005)
    assert p.stats.master_seed == 20261024 and p.stats.baseline_runs == 5000 and p.stats.shift_margin_days == 60
    assert p.screen.baseline_runs == 1000 and p.screen.stop_if_p_at_least == 0.10
    assert p.verdict_rule["reported_for"] == ["insample", "holdout", "forward_36m"]


def test_exploratory_rows_are_merged(raw):
    p = load_prereg(PREREG)
    ex = {e.name: e for e in p.exploratory}
    assert list(ex) == ["weekly", "fill_close", "no_vol_target", "no_band"]
    assert ex["weekly"].rebalance.frequency == "weekly" and ex["weekly"].rebalance.weekday == "friday"
    assert ex["weekly"].rebalance.drift_band == 0.2 and ex["weekly"].rebalance.fill == "next_open"
    assert ex["fill_close"].rebalance.fill == "next_close" and ex["fill_close"].rebalance.frequency == "daily"
    assert ex["no_vol_target"].sizing.target_vol is None and ex["no_vol_target"].sizing.vol_window == 60
    assert ex["no_band"].rebalance.drift_band == 0.0
    for e in ex.values():
        assert e.signal == p.signal
        assert e.sizing == p.sizing or e.name == "no_vol_target"
    content = p.content()["exploratory"]
    assert content[0]["sizing"] == content[3]["sizing"] and content[0]["signal"]["lookbacks"] == [20, 40, 80, 160]


def test_hash_stable_under_formatting(raw, tmp_path):
    base = h(raw)
    shuffled = write(tmp_path, dict(reversed(list(raw.items()))))
    assert load_prereg(shuffled).prereg_hash() == base
    text = PREREG.read_text(encoding="utf-8")
    edited = (text.replace("drift_band: 0.20", "drift_band: 0.2").replace("max_weight: 1.0", "max_weight: 1")
              .replace("commission: 0.0\n", "commission: 0\n").replace("signal:  #", "# a new comment\nsignal:  #"))
    assert edited.count("\n") == text.count("\n") + 1
    (tmp_path / "b.yaml").write_text(edited, encoding="utf-8")
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
        lambda d: d["data"].update(manifest_sha256="0" * 64),
        lambda d: d["costs"]["slippage"].update(GLD=0.0004),
        lambda d: d["periods"]["holdout"].update(end="2026-10-22"),
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
    lambda d: d.pop("cash"),
    lambda d: d.pop("data"),
    lambda d: d["periods"]["insample"].update(extra=1),
    lambda d: d["periods"]["warmup"].update(end="2005-07-31"),
    lambda d: d["periods"]["holdout"].update(start="2018-01-02"),
    lambda d: d["periods"]["insample"].update(end="2017-12-30"),
    lambda d: d["periods"]["holdout"].update(end="2017-12-31"),
    lambda d: d["periods"]["forward"].update(start="2026-10-23"),
    lambda d: d["periods"]["forward"].update(reviews=[36, 12]),
    lambda d: d["periods"]["forward"].update(reviews=12),
    lambda d: d["universe"].update(tickers=["SPY", "SPY"]),
    lambda d: d["universe"].pop("rule"),
    lambda d: d["data"].update(manifest_sha256="308F"),
    lambda d: d["data"].update(extra=1),
    lambda d: d["signal"].update(direction="long_short"),
    lambda d: d["signal"].update(members=["donchian", "return_sign", "ma_filter"]),
    lambda d: d["sizing"].update(max_weight=2.0),
    lambda d: d["sizing"].update(annualise_days=365),
    lambda d: d["sizing"].update(max_gross=1.0),
    lambda d: d["rebalance"].update(fill="next_vwap"),
    lambda d: d["rebalance"].update(weekday="friday"),
    lambda d: d["rebalance"].update(fill_offset_min=15),
    lambda d: d["costs"].update(fee_taker=0.0005),
    lambda d: d["costs"].update(commission=-0.001),
    lambda d: d["costs"].update(slippage={"SPY": 0.0002}),
    lambda d: d["costs"]["slippage"].update(spy=0.0002),
    lambda d: d["cash"].update(day_count=365),
    lambda d: d["cash"].update(series="dtb3"),
    lambda d: d["exploratory"].append({"name": "cheap", "costs": {"commission": 0.0}}),
    lambda d: d["exploratory"].append({"name": "weekly", "rebalance": {"drift_band": 0.1}}),
    lambda d: d["exploratory"].append({"signal": {"lookbacks": [20, 40]}}),
    lambda d: d["exploratory"].append({"name": "same"}),
    lambda d: d["exploratory"].append({"name": "short", "signal": {"direction": "long_short"}}),
    lambda d: d["exploratory"].append({"name": "monday", "rebalance": {"frequency": "weekly", "weekday": "monday"}}),
    lambda d: d["exploratory"].append({"name": "bad", "sizing": {"target": 0.1}}),
    lambda d: d["verdict_rule"].pop("t3"),
    lambda d: d["stats"].update(alpha=1.5),
    lambda d: d["screen"].update(stop_if_p_at_least=0.0),
    lambda d: d["reprice"].update(slippage=[]),
    lambda d: d["reprice"].update(commission=[0.0, 0.0]),
    lambda d: d["reprice"].update(fee_taker=[0.0]),
])
def test_unknown_missing_and_invalid(raw, edit):
    d = copy.deepcopy(raw)
    edit(d)
    with pytest.raises(ConfigError):
        parse_prereg(d)


def test_duplicate_yaml_key_rejected(tmp_path):
    text = PREREG.read_text(encoding="utf-8").replace("  donchian_exit: half\n",
                                                      "  donchian_exit: half\n  donchian_exit: half\n")
    (tmp_path / "dup.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate key"):
        load_prereg(tmp_path / "dup.yaml")


def test_the_real_prereg_describes_the_data_config():
    p, cfg = load_prereg(PREREG), load_data_config(DATA)
    assert list(p.universe.tickers) == list(cfg.tickers)
    assert (p.periods.warmup_start, p.periods.insample_start, p.periods.insample_end, p.periods.holdout_start,
            p.periods.holdout_end, p.data.download_date) == \
        (cfg.warmup_start, cfg.insample_start, cfg.insample_end, cfg.holdout_start, cfg.holdout_end, cfg.download_date)
    assert p.cash.series == cfg.rates.series


def test_check_snapshot(raw, snapshot):
    store, cfg = snapshot
    check_snapshot(parse_prereg(pinned(raw, store)), cfg, store)
    with pytest.raises(ConfigError, match="sha256"):
        check_snapshot(parse_prereg(raw), cfg, store)  # the real hash, not this store's
    for over, match in (
        ({"tickers": ["SPY", "QQQ", "GLD", "IWM"]}, "tickers"),
        ({"warmup_start": "2004-11-19"}, "warmup"),
        ({"insample_start": "2005-09-01"}, "insample.start"),
        ({"holdout_end": "2026-10-22"}, "holdout.end"),
        ({"holdout_end": "2026-10-22", "download_date": "2026-10-23"}, "holdout.end"),
        ({"rates": {"source": "fred", "series": "DTB6"}}, "cash.series"),
    ):
        other = make_config(**{"data_dir": cfg.data_dir, "holdout_end": "2026-10-23", "download_date": "2026-10-24",
                               **over})
        with pytest.raises(ConfigError, match=match):
            check_snapshot(parse_prereg(pinned(raw, store)), other, store)
    with pytest.raises(ConfigError, match="missing"):
        check_snapshot(parse_prereg(pinned(raw, store)), cfg, EtfStore(Path(cfg.data_dir) / "nowhere"))


def test_check_snapshot_refuses_a_changed_manifest(raw, tmp_path):
    store = write_store(tmp_path / "etf", make_data())
    cfg = make_config(data_dir=str(tmp_path / "etf"), holdout_end="2026-10-23", download_date="2026-10-24")
    prereg = parse_prereg(pinned(raw, store))
    check_snapshot(prereg, cfg, store)
    store.manifest_path.write_bytes(store.manifest_path.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(ConfigError, match="snapshot changed"):
        check_snapshot(prereg, cfg, store)


def test_freeze_writes_json_lock_once(raw, snapshot, tmp_path):
    store, cfg = snapshot
    path = write(tmp_path, pinned(raw, store))
    lock = freeze(path, data_cfg=cfg)
    assert lock_path(path) == tmp_path / "prereg.lock"
    on_disk = json.loads(lock_path(path).read_text(encoding="utf-8"))
    assert list(on_disk) == list(LOCK_KEYS) and on_disk == lock
    assert lock["prereg_hash"] == load_prereg(path).prereg_hash()
    assert lock["code_version"] is None and lock["code_locked_on"] is None and lock["code_git_commit"] is None
    assert read_lock(lock_path(path)) == lock
    with pytest.raises(FileExistsError):
        freeze(path, data_cfg=cfg)
    assert json.loads(lock_path(path).read_text(encoding="utf-8")) == lock


def test_freeze_refuses_a_snapshot_mismatch_and_writes_nothing(raw, snapshot, tmp_path):
    store, cfg = snapshot
    path = write(tmp_path, raw)  # pins the real manifest, not this store's
    with pytest.raises(ConfigError, match="sha256"):
        freeze(path, data_cfg=cfg)
    assert not lock_path(path).exists()
    d = pinned(raw, store)
    d["universe"]["tickers"] = ["SPY", "QQQ", "IWM"]
    path = write(tmp_path, d)
    with pytest.raises(ConfigError, match="tickers"):
        freeze(path, data_cfg=cfg)
    assert not lock_path(path).exists()


def test_read_lock_exact_keys(tmp_path):
    p = tmp_path / "x.lock"
    p.write_text(json.dumps({"frozen_on": "2026-10-24"}), encoding="utf-8")
    with pytest.raises(ConfigError):
        read_lock(p)


def test_check_frozen(raw, snapshot, tmp_path):
    store, cfg = snapshot
    d = pinned(raw, store)
    path = write(tmp_path, d)
    with pytest.raises(ConfigError, match="not frozen"):
        check_frozen(path)
    freeze(path, data_cfg=cfg)
    assert check_frozen(path).prereg_hash() == h(d)
    d["periods"]["forward"]["start"] = "2026-11-01"
    write(tmp_path, d)
    assert check_frozen(path).periods.forward_start == "2026-11-01"
    d["rebalance"]["drift_band"] = 0.25
    write(tmp_path, d)
    with pytest.raises(ConfigError, match="hash"):
        check_frozen(path)
