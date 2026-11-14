"""The ETF data config (ETF spec Phase 0 §0.1) and the corrections file (§0.9)."""
import re
from pathlib import Path

import pytest

from perpbt.config import ConfigError, config_hash, from_dict, to_dict
from perpbt.data.store import date_ms
from perpbt.etf.config import EtfSizingConfig, load_data_config
from perpbt.etf.corrections import Corrections, apply, load_corrections
from perpbt.etf.fetch import rates_start, set_fetch_dates
from perpbt.etf.store import day_iso
from tests.etf.synth import make_config, make_data

ROOT = Path(__file__).resolve().parents[2]
DATA_YAML = ROOT / "configs" / "etf" / "data.yaml"
CORRECTIONS = ROOT / "configs" / "etf" / "corrections.yaml"


def test_committed_data_config():
    cfg = load_data_config(DATA_YAML)
    assert cfg.tickers == ("SPY", "QQQ", "IWM", "GLD")
    assert (cfg.warmup_start, cfg.insample_start, cfg.insample_end, cfg.holdout_start) == (
        "2004-11-18", "2005-08-01", "2017-12-31", "2018-01-01")
    assert cfg.rates.series == "DTB3" and cfg.cross_check.source == "nasdaq" and cfg.cross_check.close_tol == 0.005
    assert cfg.corrections == "configs/etf/corrections.yaml"
    assert cfg.cross_check.dividend_tickers == ("QQQ",) and cfg.cross_check.min_span_days == 3450
    assert rates_start(cfg) == "2004-11-01"


@pytest.mark.parametrize("over, match", [
    ({"holdout_start": "2019-01-01", "insample_end": "2018-12-31"}, "store's guard"),
    ({"insample_end": "2017-12-30"}, "day after insample_end"),
    ({"insample_start": "2004-11-01"}, "warmup_start <= insample_start"),
    ({"holdout_end": "2026-10-23"}, "set together"),
    ({"holdout_end": "2026-10-24", "download_date": "2026-10-24"}, "before download_date"),
    ({"holdout_end": "2017-06-01", "download_date": "2026-10-24"}, ">= holdout_start"),
    ({"tickers": ["SPY", "SPY"]}, "duplicates"),
    ({"tickers": ["spy"]}, "upper-case"),
    ({"tickers": []}, "must not be empty"),
    ({"cross_check": {"source": "stooq", "close_tol": 0.005, "dividend_tickers": [], "min_span_days": 1}},
     "expected one of"),
    ({"cross_check": {"source": "nasdaq", "close_tol": 0, "dividend_tickers": [], "min_span_days": 1}}, r"\(0, 1\)"),
    ({"cross_check": {"source": "nasdaq", "close_tol": 0.005, "dividend_tickers": ["XLK"], "min_span_days": 1}},
     "not all in tickers"),
    ({"cross_check": {"source": "nasdaq", "close_tol": 0.005, "dividend_tickers": [], "min_span_days": 0}},
     "min_span_days"),
    ({"rates": {"source": "yahoo", "series": "DTB3"}}, "expected one of"),
])
def test_config_rejects(over, match):
    with pytest.raises(ConfigError, match=match):
        make_config(**over)


def test_duplicate_and_unknown_keys(tmp_path):
    text = DATA_YAML.read_text(encoding="utf-8")
    (tmp_path / "dup.yaml").write_text(text + "tickers: [SPY]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate key 'tickers'"):
        load_data_config(tmp_path / "dup.yaml")
    (tmp_path / "extra.yaml").write_text(text + "extra: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown keys"):
        load_data_config(tmp_path / "extra.yaml")


def test_set_fetch_dates_keeps_comments_and_can_reset(tmp_path):
    path = tmp_path / "data.yaml"
    text = DATA_YAML.read_text(encoding="utf-8")
    text = re.sub(r'^holdout_end: .*$', "holdout_end: null              # set by the fetch", text, flags=re.M)
    text = re.sub(r'^download_date: .*$', "download_date: null            # set by the fetch (UTC date)", text,
                  flags=re.M)
    path.write_text(text, encoding="utf-8")
    assert load_data_config(path).holdout_end is None
    set_fetch_dates(path, download_date="2026-10-24", holdout_end="2026-10-23")
    cfg = load_data_config(path)
    assert (cfg.holdout_end, cfg.download_date) == ("2026-10-23", "2026-10-24")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert 'holdout_end: "2026-10-23"      # set by the fetch' in lines  # the comment keeps its column
    assert 'download_date: "2026-10-24"    # set by the fetch (UTC date)' in lines
    set_fetch_dates(path, download_date="2026-11-02", holdout_end="2026-10-30")
    assert load_data_config(path).holdout_end == "2026-10-30"
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ConfigError):
        set_fetch_dates(path, download_date="2026-10-30", holdout_end="2026-10-30")  # not before the download
    assert path.read_text(encoding="utf-8") == before and not list(tmp_path.glob("*.tmp"))
    (tmp_path / "bad.yaml").write_text("data_dir: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="one 'holdout_end:' line"):
        set_fetch_dates(tmp_path / "bad.yaml", download_date="2026-10-24", holdout_end="2026-10-23")


# --- corrections -----------------------------------------------------------------------------------------------

def test_committed_corrections_load():
    c = load_corrections(CORRECTIONS)
    assert {(p.ticker, p.date) for p in c.prices} == {("SPY", "2016-11-07"), ("SPY", "2017-05-17")}
    assert [(d.ticker, d.date, d.amount, d.was) for d in c.dividends] == [
        ("QQQ", "2010-06-25", 0.0, 0.089), ("QQQ", "2020-09-21", 0.38824, 0.0)]
    assert ("GLD", "2026-04-20", "close") in c.accepted_keys() and len(c.accepted) == 6
    spy = c.prices[0]
    assert (spy.open, spy.high, spy.low, spy.close) == (211.45, None, 211.30, None)
    assert spy.was == {"open": 208.55, "low": 208.55}


def _corr(**over):
    base = {"prices": [], "dividends": [], "accepted": []}
    base.update(over)
    return Corrections(**base)


def test_corrections_reject_bad_entries():
    with pytest.raises(ConfigError, match="reason"):
        _corr(dividends=[{"ticker": "QQQ", "date": "2006-03-15", "amount": 0.1, "was": 0, "reason": " "}])
    with pytest.raises(ConfigError, match="sets no price"):
        _corr(prices=[{"ticker": "SPY", "date": "2006-03-15", "was": {}, "reason": "x"}])
    with pytest.raises(ConfigError, match="positive"):
        _corr(prices=[{"ticker": "SPY", "date": "2006-03-15", "open": -1, "was": {"open": 1}, "reason": "x"}])
    with pytest.raises(ConfigError, match="exactly"):
        _corr(prices=[{"ticker": "SPY", "date": "2006-03-15", "open": 1, "was": {"low": 1}, "reason": "x"}])
    with pytest.raises(ConfigError, match="missing required keys"):
        _corr(dividends=[{"ticker": "QQQ", "date": "2006-03-15", "amount": 0.1, "reason": "x"}])
    with pytest.raises(ConfigError, match="nothing to fix"):
        _corr(dividends=[{"ticker": "QQQ", "date": "2006-03-15", "amount": 0.1, "was": 0.1, "reason": "x"}])
    with pytest.raises(ConfigError, match=">= 0"):
        _corr(dividends=[{"ticker": "QQQ", "date": "2006-03-15", "amount": -0.1, "was": 0, "reason": "x"}])
    with pytest.raises(ConfigError, match="expected one of"):
        _corr(accepted=[{"ticker": "SPY", "date": "2006-03-15", "check": "high", "reason": "x"}])
    dup = {"ticker": "SPY", "date": "2006-03-15", "check": "open", "reason": "x"}
    with pytest.raises(ConfigError, match="duplicate"):
        _corr(accepted=[dup, dup])


def test_apply_price_and_dividend_fixes():
    data = make_data()
    spy_day = "2006-03-01"
    iwm = data.actions["IWM"]
    first_iwm, second_iwm = day_iso(iwm["day_ms"].iloc[0]), day_iso(iwm["day_ms"].iloc[1])
    row = data.prices["SPY"]["day_ms"] == date_ms(spy_day)
    yahoo_open = float(data.prices["SPY"].loc[row, "open"].item())
    c = _corr(prices=[{"ticker": "SPY", "date": spy_day, "open": 123.0, "was": {"open": yahoo_open}, "reason": "x"}],
              dividends=[{"ticker": "GLD", "date": "2006-03-01", "amount": 0.5, "was": 0, "reason": "x"},
                         {"ticker": "IWM", "date": first_iwm, "amount": 9.0, "was": float(iwm["dividend"].iloc[0]),
                          "reason": "x"},
                         {"ticker": "IWM", "date": second_iwm, "amount": 0, "was": float(iwm["dividend"].iloc[1]),
                          "reason": "a duplicate"}])
    out = apply(data, c)
    p = out.prices["SPY"]
    assert p.loc[row, "open"].item() == 123.0
    assert p.loc[row, "close"].item() == data.prices["SPY"].loc[row, "close"].item()
    assert out.actions["GLD"].to_dict("records") == [
        {"day_ms": date_ms("2006-03-01"), "dividend": 0.5, "split": 0.0, "capital_gain": 0.0}]
    assert out.actions["IWM"]["dividend"].iloc[0] == 9.0
    assert len(out.actions["IWM"]) == len(iwm) - 1 and date_ms(second_iwm) not in set(out.actions["IWM"]["day_ms"])
    assert data.prices["SPY"].loc[row, "open"].item() == yahoo_open  # the input is not modified
    with pytest.raises(ValueError, match="no price row"):
        apply(data, _corr(prices=[{"ticker": "SPY", "date": "2006-01-16", "close": 1.0, "was": {"close": 1.0},
                                   "reason": "x"}]))
    with pytest.raises(ValueError, match="unknown ticker"):
        apply(data, _corr(dividends=[{"ticker": "XLK", "date": "2006-03-01", "amount": 1.0, "was": 0,
                                      "reason": "x"}]))
    with pytest.raises(ValueError, match="stale price fix"):
        apply(data, _corr(prices=[{"ticker": "SPY", "date": spy_day, "open": 1.0, "was": {"open": yahoo_open + 1},
                                   "reason": "x"}]))
    with pytest.raises(ValueError, match="stale dividend fix"):
        apply(data, _corr(dividends=[{"ticker": "GLD", "date": "2006-03-01", "amount": 1.0, "was": 0.3,
                                      "reason": "x"}]))


# --- EtfSizingConfig (Phase 1 §1.5; the Phase 2 prereg sizing block) ---------------------------

SIZING = {"target_vol": 0.15, "vol_window": 60, "max_weight": 1.0, "annualise_days": 252}


def test_sizing_config_round_trip_and_null_target():
    s = from_dict(EtfSizingConfig, SIZING)
    assert (s.target_vol, s.vol_window, s.max_weight, s.annualise_days) == (0.15, 60, 1.0, 252)
    assert to_dict(s) == SIZING
    assert from_dict(EtfSizingConfig, {**SIZING, "target_vol": None}).target_vol is None
    assert config_hash(s) != config_hash(from_dict(EtfSizingConfig, {**SIZING, "target_vol": None}))


@pytest.mark.parametrize("over", [{"annualise_days": 365}, {"max_weight": 1.5}, {"max_weight": 0.0},
                                  {"target_vol": 0.0}, {"target_vol": -0.1}, {"vol_window": 1}, {"extra": 1}])
def test_sizing_config_refuses(over):
    with pytest.raises(ConfigError):
        from_dict(EtfSizingConfig, {**SIZING, **over})
