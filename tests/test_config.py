"""Configuration types: construction, coercion, validation, from_dict/to_dict."""
import dataclasses
import json
from datetime import date

import pytest

from perpbt.config import (
    ConfigError,
    DataConfig,
    ExecConfig,
    HoldRule,
    SessionSpec,
    StatsConfig,
    StopBuffer,
    StrategyParams,
    VariantConfig,
    from_dict,
    to_dict,
)

NY = SessionSpec("ny", "America/New_York", "09:30", "16:00", (0, 1, 2, 3, 4))
UTC = SessionSpec("utc", "UTC", "00:00", "24:00", (0, 1, 2, 3, 4, 5, 6))


def data_config(**overrides) -> DataConfig:
    kw = dict(
        data_dir="data",
        insample_start="2020-01-01",
        insample_end="2025-12-31",
        holdout_start="2026-01-01",
        holdout_end=None,
        warmup_start="2019-11-01",
        listing={"BTCUSDT": "2019-09-08", "ETHUSDT": "2019-11-27", "SOLUSDT": "2020-09-14"},
    )
    kw.update(overrides)
    return DataConfig(**kw)


def primary_variant(**overrides) -> VariantConfig:
    kw = dict(
        pair="BTCUSDT",
        session=NY,
        params=StrategyParams(),
        exec=ExecConfig(),
        stats=StatsConfig(),
        period_start="2020-01-01",
        period_end="2025-12-31",
        is_holdout=False,
    )
    kw.update(overrides)
    return VariantConfig(**kw)


# --- defaults and immutability ------------------------------------------------


def test_strategy_defaults_match_spec():
    p = StrategyParams()
    assert (p.swing_k, p.confirm_n, p.zone, p.entry_level) == (2, 3, "full", "top")
    assert p.stop_buffer == StopBuffer("atr", 0.1)
    assert p.r_target == 2.0
    assert p.hold_rule == HoldRule("none")
    assert (p.trend_filter, p.pierce) == (False, 0.0)
    assert (p.structure_break, p.skip_mitigated) == ("fresh", "continue")


def test_exec_defaults_match_spec():
    e = ExecConfig()
    assert (e.fee_maker, e.fee_taker, e.slippage, e.mmr) == (0.0002, 0.0005, 0.0002, 0.004)
    assert (e.risk_per_trade, e.max_leverage, e.start_equity, e.use_1m) == (0.01, 25.0, 10_000.0, True)


def test_stats_defaults_match_spec():
    s = StatsConfig()
    assert (s.bootstrap_n, s.block_len_days, s.baseline_runs) == (10_000, 10, 5_000)
    assert (s.alpha, s.master_seed) == (0.05, 20260926)
    assert s.reprice_slippage == (0.0, 0.0002, 0.0005, 0.001)
    assert s.reprice_maker == (0.0, 0.0002)


def test_dataclasses_are_frozen():
    p = StrategyParams()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.swing_k = 3  # type: ignore[misc]


# --- coercion ---------------------------------------------------------------


def test_int_for_float_field_is_coerced():
    p = StrategyParams(r_target=2)
    assert isinstance(p.r_target, float) and p.r_target == 2.0
    e = ExecConfig(start_equity=10_000)
    assert isinstance(e.start_equity, float)


def test_list_for_tuple_field_is_coerced():
    s = SessionSpec("ny", "America/New_York", "09:30", "16:00", [0, 1])
    assert s.days == (0, 1) and isinstance(s.days, tuple)
    st = StatsConfig(reprice_maker=[0, 0.0002])
    assert st.reprice_maker == (0.0, 0.0002)
    assert all(isinstance(x, float) for x in st.reprice_maker)


def test_nested_mapping_builds_nested_dataclass():
    p = StrategyParams(stop_buffer={"kind": "pct", "value": 0.001})
    assert p.stop_buffer == StopBuffer("pct", 0.001)
    v = primary_variant(session={"name": "utc", "tz": "UTC", "open": "00:00", "close": "24:00", "days": [0, 1, 2, 3, 4, 5, 6]})
    assert v.session == UTC


def test_str_field_accepts_date_object():
    d = data_config(insample_start=date(2020, 1, 1), listing={"BTCUSDT": date(2019, 9, 8)})
    assert d.insample_start == "2020-01-01"
    assert d.listing == {"BTCUSDT": "2019-09-08"}


def test_str_field_rejects_int_with_quoting_hint():
    with pytest.raises(ConfigError, match="quote"):
        SessionSpec("utc", "UTC", "00:00", 1440, (0,))


@pytest.mark.parametrize("bad", ["2", 2.5, True, None])
def test_int_field_rejects_non_integers(bad):
    with pytest.raises(ConfigError, match="swing_k"):
        StrategyParams(swing_k=bad)


def test_bool_field_rejects_int():
    with pytest.raises(ConfigError, match="trend_filter"):
        StrategyParams(trend_filter=1)


@pytest.mark.parametrize("bad", [True, "0.1", None])
def test_float_field_rejects_bool_str_none(bad):
    with pytest.raises(ConfigError, match="fee_maker"):
        ExecConfig(fee_maker=bad)


def test_optional_field_accepts_none_and_value():
    assert data_config(holdout_end=None).holdout_end is None
    assert data_config(holdout_end="2026-09-27").holdout_end == "2026-09-27"
    assert HoldRule("max_hold", hours=24).hours == 24.0


# --- validation -------------------------------------------------------------


def test_hold_rule_max_hold_requires_hours():
    with pytest.raises(ConfigError, match="hours"):
        HoldRule("max_hold")


def test_hold_rule_hours_only_with_max_hold():
    with pytest.raises(ConfigError, match="hours"):
        HoldRule("none", hours=24)
    with pytest.raises(ConfigError, match="hours"):
        HoldRule("session_end", hours=24)


def test_hold_rule_hours_must_be_positive():
    with pytest.raises(ConfigError, match="hours"):
        HoldRule("max_hold", hours=0)


@pytest.mark.parametrize(
    "build",
    [
        lambda: StopBuffer("atrr", 0.1),
        lambda: HoldRule("forever"),
        lambda: StrategyParams(zone="wick"),
        lambda: StrategyParams(entry_level="bottom"),
        lambda: StrategyParams(structure_break="loose"),
        lambda: StrategyParams(skip_mitigated="retry"),
    ],
)
def test_literal_fields_are_validated(build):
    with pytest.raises(ConfigError, match="expected one of"):
        build()


def test_literal_alternatives_accepted_but_not_in_grid():
    assert StrategyParams(structure_break="literal").structure_break == "literal"
    assert StrategyParams(skip_mitigated="stop").skip_mitigated == "stop"


@pytest.mark.parametrize("days", [(7,), (-1,), (0, 0), ()])
def test_session_days_validated(days):
    with pytest.raises(ConfigError, match="days"):
        SessionSpec("x", "UTC", "00:00", "24:00", days)


@pytest.mark.parametrize("open_, close", [("9:30", "16:00"), ("24:00", "24:00"), ("09:30", "16:60"), ("09:30", "25:00"), ("09:30", "24:30")])
def test_session_time_format_validated(open_, close):
    with pytest.raises(ConfigError, match="(open|close)"):
        SessionSpec("x", "UTC", open_, close, (0,))


def test_session_close_2400_allowed():
    assert UTC.close == "24:00"


def test_session_tz_validated():
    with pytest.raises(ConfigError, match="tz"):
        SessionSpec("x", "Mars/Olympus_Mons", "00:00", "24:00", (0,))


@pytest.mark.parametrize("field, value", [("insample_start", "2020/01/01"), ("warmup_start", "20191101"), ("holdout_end", "yesterday")])
def test_data_config_dates_validated(field, value):
    with pytest.raises(ConfigError, match=field):
        data_config(**{field: value})


def test_data_config_listing_dates_validated():
    with pytest.raises(ConfigError, match="listing"):
        data_config(listing={"BTCUSDT": "not a date"})


def test_data_config_insample_order_validated():
    with pytest.raises(ConfigError, match="insample"):
        data_config(insample_start="2026-01-01", insample_end="2025-12-31")


def test_variant_period_validated():
    with pytest.raises(ConfigError, match="period"):
        primary_variant(period_start="2025-12-31", period_end="2020-01-01")
    with pytest.raises(ConfigError, match="period_end"):
        primary_variant(period_end="2025-13-01")


@pytest.mark.parametrize(
    "build",
    [
        lambda: StrategyParams(swing_k=0),
        lambda: StrategyParams(confirm_n=0),
        lambda: StrategyParams(r_target=0),
        lambda: StrategyParams(pierce=-0.1),
        lambda: ExecConfig(risk_per_trade=0),
        lambda: ExecConfig(risk_per_trade=1.5),
        lambda: ExecConfig(max_leverage=0),
        lambda: ExecConfig(start_equity=0),
        lambda: ExecConfig(fee_taker=-1),
        lambda: StatsConfig(bootstrap_n=0),
        lambda: StatsConfig(alpha=0),
        lambda: StatsConfig(alpha=1),
        lambda: StatsConfig(reprice_slippage=()),
    ],
)
def test_numeric_ranges_validated(build):
    with pytest.raises(ConfigError):
        build()


# --- from_dict / to_dict ----------------------------------------------------


def test_from_dict_builds_nested_config():
    v = from_dict(VariantConfig, to_dict(primary_variant()))
    assert v == primary_variant()


def test_from_dict_unknown_key_raises():
    with pytest.raises(ConfigError, match="unknown.*vlaue"):
        from_dict(StopBuffer, {"kind": "atr", "value": 0.1, "vlaue": 1})


def test_from_dict_nested_unknown_key_raises():
    with pytest.raises(ConfigError, match="unknown.*extra"):
        from_dict(StrategyParams, {"stop_buffer": {"kind": "atr", "value": 0.1, "extra": 1}})


def test_from_dict_missing_required_key_raises():
    with pytest.raises(ConfigError, match="missing.*value"):
        from_dict(StopBuffer, {"kind": "atr"})


def test_from_dict_rejects_non_mapping():
    with pytest.raises(ConfigError, match="mapping"):
        from_dict(StopBuffer, ["atr", 0.1])


def test_to_dict_is_plain_json_serialisable():
    d = to_dict(primary_variant())
    assert isinstance(d["session"]["days"], list)
    assert isinstance(d["stats"]["reprice_maker"], list)
    assert d["params"]["hold_rule"] == {"kind": "none", "hours": None}
    assert d["exec"]["start_equity"] == 10_000.0
    json.dumps(d)  # must not raise
