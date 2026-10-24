"""Carry spec Phase 6 §6.1: every figure renders a PNG from synthetic run tables; missing inputs give placeholders."""
import numpy as np
import pandas as pd
import pytest

from perpbt.report.carry import figures as cf

PNG = b"\x89PNG\r\n\x1a\n"
PAIRS = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]


def is_png(path, min_bytes=2_000) -> bool:
    data = path.read_bytes()
    return data.startswith(PNG) and len(data) > min_bytes


@pytest.fixture(scope="module")
def tables():
    rng = np.random.default_rng(7)
    dates = pd.date_range("2021-01-01", periods=500, freq="D").strftime("%Y-%m-%d").to_numpy(dtype=object)
    ret = 0.0003 + 0.002 * rng.normal(size=len(dates))
    daily = pd.DataFrame({"date": dates, "equity": 10_000.0 * np.cumprod(1 + ret), "ret": ret})
    ao_ret = 0.0002 + 0.003 * rng.normal(size=len(dates))
    always_on = pd.DataFrame({"date": dates, "equity": 10_000.0 * np.cumprod(1 + ao_ret), "ret": ao_ret})
    sig, dp = [], []
    for k, pair in enumerate(PAIRS):
        fbar = 0.15 + 0.3 * np.sin(np.arange(len(dates)) / 40.0 + k)
        fbar[:5] = np.nan
        on = fbar > 0.1
        sig.append(pd.DataFrame({"date": dates, "pair": pair, "fbar": fbar, "on": on,
                                 "basis": 1e-4 * rng.normal(size=len(dates))}))
        dp.append(pd.DataFrame({"date": dates, "pair": pair, "perp_qty": np.where(np.roll(on, 1), -1.0, 0.0)}))
    per_year = [{"year": y, "funding": f, "basis": b, "fees": 0.004, "slippage": 0.002, "ret_sum": f + b - 0.006,
                 "rebalance_cost": 0.001, "liquidation_cost": 0.0 if y != "2022" else 0.003}
                for y, f, b in (("2021", 0.12, -0.01), ("2022", 0.01, 0.004))]
    reps = 0.02 + 0.03 * rng.normal(size=1_000)
    v2 = {"hurdle_annual": 0.05, "ann_excess": 0.02, "ann_excess_ci": [-0.035, 0.08], "p": 0.25}
    timing = pd.DataFrame({"ann_return": 0.03 + 0.02 * rng.normal(size=50), "sharpe": 1 + rng.normal(size=50)})
    rt = {"ann_return": {"primary": 0.05, "rank": 3}, "sharpe": {"primary": 1.4, "rank": 12}}
    return {"daily": daily, "always_on": always_on, "signals": pd.concat(sig, ignore_index=True),
            "daily_pair": pd.concat(dp, ignore_index=True), "per_year": per_year, "reps": reps, "v2": v2,
            "timing": timing, "rt": rt}


def render(t, out, *, entry=0.10, exit=0.0):
    return {
        "equity": cf.equity(t["daily"], out / "equity.png", hurdle_annual=0.05, always_on=t["always_on"]),
        "funding": cf.funding(t["signals"], t["daily_pair"], out / "funding.png", entry=entry, exit=exit),
        "basis": cf.basis(t["signals"], t["daily_pair"], out / "basis.png"),
        "decomposition": cf.decomposition(t["per_year"], out / "decomposition.png"),
        "v2_bootstrap": cf.v2_bootstrap(t["reps"], t["v2"], out / "v2_bootstrap.png"),
        "random_timing": cf.random_timing(t["timing"], t["rt"], out / "random_timing.png"),
    }


def test_every_figure_renders(tables, tmp_path):
    paths = render(tables, tmp_path)
    assert tuple(paths) == cf.FIGURES
    for name, p in paths.items():
        assert p == tmp_path / f"{name}.png" and is_png(p, 10_000), name


def test_same_inputs_same_bytes(tables, tmp_path):
    a = {k: p.read_bytes() for k, p in render(tables, tmp_path / "a").items()}
    b = {k: p.read_bytes() for k, p in render(tables, tmp_path / "b").items()}
    assert a == b


def test_always_on_thresholds_are_not_drawn(tables, tmp_path):
    p = cf.funding(tables["signals"], tables["daily_pair"], tmp_path / "f.png", entry=-1e9, exit=-1e9)
    q = cf.funding(tables["signals"], tables["daily_pair"], tmp_path / "g.png", entry=0.10, exit=0.0)
    assert is_png(p, 10_000) and p.read_bytes() != q.read_bytes()


def test_one_pair_and_no_hedged_days(tables, tmp_path):
    sig = tables["signals"][tables["signals"]["pair"] == "AAAUSDT"]
    assert is_png(cf.funding(sig, None, tmp_path / "f.png", entry=0.1, exit=0.0))
    assert is_png(cf.basis(sig, tables["daily_pair"].iloc[:0], tmp_path / "b.png"))


def test_equity_without_always_on_and_for_always_on_itself(tables, tmp_path):
    assert is_png(cf.equity(tables["daily"], tmp_path / "e.png", hurdle_annual=0.05))
    assert is_png(cf.equity(tables["always_on"], tmp_path / "a.png", hurdle_annual=0.05,
                            always_on=tables["always_on"], row="always_on"))
    assert cf.equity0(tables["daily"]) == pytest.approx(10_000.0)


@pytest.mark.parametrize("name", ["equity", "funding", "basis", "decomposition", "v2_bootstrap", "random_timing"])
def test_missing_inputs_give_placeholders(name, tmp_path):
    p = tmp_path / f"{name}.png"
    out = {
        "equity": lambda: cf.equity(None, p, hurdle_annual=0.05),
        "funding": lambda: cf.funding(None, None, p, entry=0.1, exit=0.0),
        "basis": lambda: cf.basis(pd.DataFrame(), None, p),
        "decomposition": lambda: cf.decomposition([], p),
        "v2_bootstrap": lambda: cf.v2_bootstrap(None, None, p),
        "random_timing": lambda: cf.random_timing(None, None, p),
    }[name]()
    assert out == p and is_png(p, 1_000)
