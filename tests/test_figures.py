"""Spec §7.1: every figure renders a non-empty PNG from a synthetic SimResult; missing inputs give placeholders."""
import pandas as pd
import pytest

from perpbt.report import figures as fg
from perpbt.stats.baselines import r_subset
from perpbt.stats.buyhold import bh_daily
from perpbt.stats.variant import compute_stats, run_baselines
from tests.test_variant import cfg_for, variant

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def sim():
    res, market = variant()
    cfg = cfg_for()
    base = run_baselines(res, market, cfg, variant_id="v1")
    st = compute_stats(res, market, cfg, variant_id="v1", baselines=base)
    s = res.summary
    bh = bh_daily(market.candles15, market.funding, int(s["first_idx"]), int(s["last_idx"]))
    return res, cfg, base, st, bh


def is_png(path) -> bool:
    data = path.read_bytes()
    return data.startswith(PNG) and len(data) > 2_000


def cells_frame(st):
    rows = [{"x": x, "y": y, "mean_net_r": 0.1 * (i - 2), "sharpe_ann": 0.3 * (i - 1), "n_trades_r": 10 + i,
             "is_primary": (x, y) == ("2", "top")}
            for i, (x, y) in enumerate((x, y) for x in ("1", "2", "3") for y in ("top", "mid"))]
    return pd.DataFrame(rows[:-1])  # one cell has not run


def render_all(sim, out, *, empty=False):
    res, cfg, base, st, bh = sim
    tr = r_subset(res.trades)
    if empty:
        tr = tr.iloc[:0]
    costs = (cfg.exec.fee_maker, cfg.exec.fee_taker, cfg.exec.slippage)
    b = st["baselines"]
    later = lambda df: df.assign(date=[d + pd.Timedelta(days=len(df)) for d in pd.to_datetime(df["date"])])
    hold = {"daily": later(res.daily), "bh": later(bh), "scale": st["buy_and_hold"]["scale"], "start_equity": 10_000.0}
    return {
        "equity": fg.equity(res.daily, bh, out / "equity.png", start_equity=10_000.0,
                            scale=st["buy_and_hold"]["scale"], holdout=hold),
        "drawdown": fg.drawdown(res.daily, bh, out / "drawdown.png", start_equity=10_000.0,
                                scale=st["buy_and_hold"]["scale"], holdout=hold),
        "rolling": fg.rolling({"windows": [{"end": "2020-02-01", "mean_net_r": 0.1}, {"end": "2020-03-01",
                                                                                      "mean_net_r": -0.2}],
                               "sharpe": st["rolling"]["sharpe"]}, out / "rolling.png"),
        "per_year": fg.per_year(st["per_year"], out / "per_year.png"),
        "baseline_a": fg.baseline_a(base.runs_a, out / "baseline_a.png", costs=costs, observed=b["observed_a"],
                                    p=b["p_a"], z=b["z_a"], n_runs=b["n_runs"]),
        "baseline_b": fg.baseline_b(base.runs_b, out / "baseline_b.png", costs=costs, observed=b["observed_b"],
                                    p=b["p_b"], z=b["z_b"]),
        "heatmap_1": fg.heatmap_1(cells_frame(st), out / "heatmap_1.png", x_values=["1", "2", "3"],
                                  y_values=["top", "mid"], x_axis="r_target", y_axis="entry_level"),
        "heatmap_2": fg.heatmap_2(cells_frame(st).assign(y=""), out / "heatmap_2.png", x_values=["1", "2", "3"],
                                  y_values=[], x_axis="r_target"),
        "heatmap_3": fg.heatmap_3(None, out / "heatmap_3.png", x_values=["a"], y_values=["b"], x_axis="x"),
        "reprice": fg.reprice(st["repricing"], out / "reprice.png"),
        "cost_r": fg.cost_r(tr, out / "cost_r.png", share_above_1=st["costs"]["share_cost_r_gt_1"]),
        "stop_dist": fg.stop_dist(tr, out / "stop_dist.png"),
        "mae_mfe": fg.mae_mfe(tr, out / "mae_mfe.png"),
        "hold_time": fg.hold_time(tr, out / "hold_time.png"),
        "regimes": fg.regimes(st["regimes"], out / "regimes.png"),
        "calendar": fg.calendar(st["breakdowns"], out / "calendar.png"),
        "funnel": fg.funnel(st["skips"], st["summary"], out / "funnel.png"),
        "concurrency": fg.concurrency(res.daily, out / "concurrency.png", max_concurrent=st["headline"]["max_concurrent"]),
    }


def test_every_figure_renders_a_png(sim, tmp_path):
    assert len(sim[0].trades) > 5
    paths = render_all(sim, tmp_path)
    assert set(paths) == set(fg.FIGURES)
    for name, path in paths.items():
        assert path == tmp_path / f"{name}.png" and is_png(path), name


def test_missing_inputs_render_placeholders(sim, tmp_path):
    res, cfg, base, st, bh = sim
    costs = (0.0002, 0.0005, 0.0002)
    out = [
        fg.baseline_a(None, tmp_path / "a.png", costs=costs, observed=None, p=None),
        fg.baseline_b(base.runs_b.iloc[:0], tmp_path / "b.png", costs=costs, observed=0.1, p=0.5),
        fg.equity(res.daily, None, tmp_path / "e.png", start_equity=10_000.0, scale=None, holdout=None),
        fg.equity(res.daily.iloc[:0], bh, tmp_path / "e0.png", start_equity=10_000.0),
        fg.drawdown(res.daily, bh, tmp_path / "d.png", start_equity=10_000.0, scale=float("nan")),
        fg.rolling({"windows": [], "sharpe": []}, tmp_path / "r.png"),
        fg.per_year([], tmp_path / "y.png"),
        fg.heatmap_1(pd.DataFrame(), tmp_path / "h.png", x_values=["1"], y_values=["top"], x_axis="r"),
        fg.reprice([], tmp_path / "p.png"),
        fg.regimes({"trend": [], "vol": []}, tmp_path / "g.png"),
        fg.calendar({"dow": [], "entry_hour_utc": []}, tmp_path / "c.png"),
        fg.concurrency(res.daily.iloc[:0], tmp_path / "k.png"),
    ]
    assert all(p.read_bytes().startswith(PNG) and p.stat().st_size > 1_000 for p in out)
    empty = render_all(sim, tmp_path / "empty", empty=True)
    for name in ("cost_r", "stop_dist", "mae_mfe", "hold_time"):
        assert empty[name].read_bytes().startswith(PNG), name
