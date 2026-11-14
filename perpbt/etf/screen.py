"""The gross ETF screen (ETF spec Phase 2 §2.3) and its random-days paths (Phase 4 §4.1).

A deliberately simple return model, independent of the Phase 3 executor. The
weight decided at day ``d``'s close earns, on the next trading day::

    r_{i,d+1} = w*_{i,d} × ret_{i,d+1} + (1 − w*_{i,d}) × cash_{d+1}

with no fill delay, drift band or costs: ``ret`` is the close-to-close total
return (dividends included) and ``cash`` the T-bill return over the same
step, both from the ``EtfFrame``. The portfolio return is the mean over the
ETFs; the statistic is the Sharpe of its excess over ``cash_{d+1}`` at √252.
Decision days run from the trading day before ``insample.start`` to the one
before the last in-sample day, so the returns fall on the in-sample days.

Each random path draws one offset ``k`` on ``[margin, n − margin]`` and rotates
every ETF's exposure series by it (``e'_t = e_{(t + k) mod n}``); weights are
recomputed from the shifted exposure with the actual ``σ̂``. Time in market,
exposure levels and the number of exposure changes are kept; only the timing
moves. Every ETF is valid on every decision day (``summarise`` checks on the
real store, ``screen_inputs`` here), so no mask is needed.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from perpbt.config import ConfigError
from perpbt.data.store import DAY_MS, date_ms
from perpbt.etf.config import EtfDataConfig, EtfSizingConfig
from perpbt.etf.frame import EtfFrame, check_aligned, load_frames
from perpbt.etf.prereg import EtfPrereg, check_snapshot
from perpbt.etf.signals import ANNUALISE, EtfSignals, compute_signals, etf_target_weight, summarise
from perpbt.etf.store import EtfStore, day_iso
from perpbt.stats.seeds import rng_for

BENCHMARK = "SPY"  # the plain 100% benchmark (§2.3), descriptive
MAX_END_GAP_DAYS = 5  # the frames' last day may sit this many calendar days before insample.end (weekend, holiday)

__all__ = ["BENCHMARK", "ScreenInputs", "portfolio_excess", "run_screen", "screen_from_store", "screen_inputs",
           "screen_path", "sharpe252", "shift_offsets", "shifted_exposure", "write_screen"]


@dataclass(frozen=True, eq=False)  # eq=False: the generated __eq__ raises on ndarray fields
class ScreenInputs:
    """The ETFs aligned on decision days; arrays are ``(tickers, days)``, ``cash_next`` is ``(days,)``."""

    tickers: tuple[str, ...]
    day_ms: np.ndarray  # decision days d
    ret_next: np.ndarray  # ret_{d+1}: close-to-close total return of the next trading day
    cash_next: np.ndarray  # cash_{d+1}: the T-bill return over the same step
    exposure: np.ndarray  # e_d
    sigma: np.ndarray  # σ̂_d
    weight: np.ndarray  # w*_d (EtfSignals.weight)


def screen_inputs(frames: Mapping[str, EtfFrame], signals: Mapping[str, EtfSignals], *, start: str,
                  end: str) -> ScreenInputs:
    """Decision days from the trading day before ``start`` to the one before the last day ``<= end``.

    Raises when the frames are not aligned, when no day before ``start``
    exists, when the window is empty, or when an ETF is not valid on a
    decision day.
    """
    check_aligned(frames)
    tickers = tuple(frames)
    day_ms = frames[tickers[0]].day_ms
    first = int(np.searchsorted(day_ms, date_ms(start), side="left"))
    last = int(np.searchsorted(day_ms, date_ms(end), side="right")) - 1
    if last < first:
        raise ValueError(f"screen_inputs: no trading day in {start}..{end}")
    if first == 0:
        raise ValueError(f"screen_inputs: no decision day before {start}")
    j = np.arange(first - 1, last)
    cash = frames[tickers[0]].cash
    for t in tickers:
        if not np.array_equal(frames[t].cash, cash, equal_nan=True):
            raise ValueError(f"screen_inputs: {t}'s cash differs from {tickers[0]}'s")
        bad = ~signals[t].valid[j]
        if bad.any():
            raise ValueError(f"screen_inputs: {t} is not valid on decision day {day_iso(day_ms[j][bad][0])}")
    return ScreenInputs(
        tickers=tickers, day_ms=day_ms[j].copy(),
        ret_next=np.stack([frames[t].ret[j + 1] for t in tickers]), cash_next=cash[j + 1].copy(),
        exposure=np.stack([signals[t].exposure[j] for t in tickers]),
        sigma=np.stack([signals[t].sigma[j] for t in tickers]),
        weight=np.stack([signals[t].weight[j] for t in tickers]),
    )


def etf_returns(weight: np.ndarray, inputs: ScreenInputs) -> np.ndarray:
    """Per ETF and return day, ``w × ret + (1 − w) × cash``; ``weight`` is ``(..., tickers, days)``."""
    w = np.asarray(weight, dtype=np.float64)
    return w * inputs.ret_next + (1.0 - w) * inputs.cash_next


def portfolio_excess(weight: np.ndarray, inputs: ScreenInputs) -> np.ndarray:
    """The equal-weight portfolio's return in excess of cash per return day; drops the tickers axis."""
    return etf_returns(weight, inputs).mean(axis=-2) - inputs.cash_next


def sharpe252(x: np.ndarray) -> np.ndarray | float:
    """``√252 × mean / std`` (ddof 1) along the last axis; NaN for fewer than two days or zero std.

    ``stats.bootstrap.sharpe`` annualises at √365 and is in CARRY_CODE_PATHS (not edited; overview §7).
    """
    x = np.asarray(x, dtype=np.float64)
    if x.shape[-1] < 2:
        out = np.full(x.shape[:-1], np.nan)
    else:
        sd = x.std(axis=-1, ddof=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            out = np.where(sd > 0, ANNUALISE * x.mean(axis=-1) / sd, np.nan)
    return float(out) if out.ndim == 0 else out


def shift_offsets(n: int, M: int, margin: int, rng: np.random.Generator) -> np.ndarray:
    """``M`` offsets uniform on ``[margin, n − margin]`` (both ends included)."""
    if n - margin < margin:
        raise ValueError(f"no offset in [{margin}, {n - margin}]: {n} days are too few for a {margin}-day margin")
    return rng.integers(margin, n - margin, size=M, endpoint=True).astype(np.int64)


def shifted_exposure(inputs: ScreenInputs, k: np.ndarray) -> np.ndarray:
    """``(paths, tickers, days)``: every ETF's exposure rotated by the same ``k`` per path."""
    k = np.asarray(k, dtype=np.int64)
    n = inputs.exposure.shape[-1]
    return inputs.exposure[:, (np.arange(n)[None, :] + k[:, None]) % n].transpose(1, 0, 2)


def _summary(x: np.ndarray) -> dict:
    return {
        "n": int(len(x)), "mean": float(np.mean(x)), "std": float(np.std(x, ddof=1)) if len(x) > 1 else None,
        "p05": float(np.percentile(x, 5)), "p50": float(np.percentile(x, 50)), "p95": float(np.percentile(x, 95)),
    }


def run_screen(inputs: ScreenInputs, sizing: EtfSizingConfig, *, paths: int, margin: int, rng: np.random.Generator,
               stop_if_p_at_least: float, chunk: int = 100) -> dict:
    """Observed gross excess Sharpe against ``paths`` shifted paths; ``p_screen`` and the go/stop decision.

    Descriptive only: the vol-targeted buy-and-hold (exposure 1, same sizing),
    100% ``BENCHMARK``, and per ETF the strategy's and the buy-and-hold's
    excess Sharpe and mean weight.
    """
    if BENCHMARK not in inputs.tickers:
        raise ValueError(f"run_screen: the benchmark {BENCHMARK} is not among {list(inputs.tickers)}")
    observed = sharpe252(portfolio_excess(inputs.weight, inputs))
    k = shift_offsets(inputs.exposure.shape[-1], paths, margin, rng)
    path_sharpe = np.empty(paths)
    for a in range(0, paths, chunk):
        e = shifted_exposure(inputs, k[a:a + chunk])
        path_sharpe[a:a + chunk] = sharpe252(portfolio_excess(etf_target_weight(e, inputs.sigma[None], sizing), inputs))
    bh_weight = etf_target_weight(np.ones_like(inputs.exposure), inputs.sigma, sizing)
    excess_by_etf = etf_returns(inputs.weight, inputs) - inputs.cash_next
    bh_by_etf = etf_returns(bh_weight, inputs) - inputs.cash_next
    per_etf = {
        t: {"sharpe": sharpe252(excess_by_etf[i]), "bh_sharpe": sharpe252(bh_by_etf[i]),
            "mean_weight": float(inputs.weight[i].mean()), "mean_exposure": float(inputs.exposure[i].mean())}
        for i, t in enumerate(inputs.tickers)
    }
    bh = sharpe252(portfolio_excess(bh_weight, inputs))
    spy = sharpe252(inputs.ret_next[inputs.tickers.index(BENCHMARK)] - inputs.cash_next)
    values = [observed, bh, spy, *(v for row in per_etf.values() for v in (row["sharpe"], row["bh_sharpe"]))]
    bad = int(np.sum(~np.isfinite(path_sharpe)))
    if bad or not np.isfinite(values).all():
        # a NaN compares False with everything and would read as "go": refuse instead of deciding
        raise ValueError(f"screen Sharpe not finite: observed {observed}, buy-and-hold {bh}, {BENCHMARK} {spy}, "
                         f"per ETF {per_etf}, {bad} of {paths} paths")
    p = (1 + int(np.sum(path_sharpe >= observed))) / (paths + 1)
    return {
        "n_days": int(inputs.exposure.shape[-1]),
        "observed_sharpe": observed,
        "paths": _summary(path_sharpe),
        "p_screen": p,
        "stop_if_p_at_least": stop_if_p_at_least,
        "decision": "stop" if p >= stop_if_p_at_least else "go",
        "bh_sharpe": bh,
        "spy_sharpe": spy,
        "per_etf": per_etf,
        "cash_annual_mean": float(inputs.cash_next.mean() * 252),
    }


def screen_from_store(prereg: EtfPrereg, cfg: EtfDataConfig) -> dict:
    """The screen on the stored in-sample basket: the prereg's tickers, primary signal and sizing.

    Refuses a prereg that does not describe ``cfg`` and the stored snapshot
    (``check_snapshot``). Seeded from ``(stats.master_seed, prereg_hash, "screen")``.
    """
    store = EtfStore(cfg.data_dir)
    check_snapshot(prereg, cfg, store)
    p = prereg.periods
    frames = load_frames(store, cfg, end=p.insample_end)
    last = frames[cfg.tickers[0]].day_ms[-1]
    if (date_ms(p.insample_end) - int(last)) // DAY_MS > MAX_END_GAP_DAYS:
        raise ConfigError(f"the stored frames end on {day_iso(last)}, more than {MAX_END_GAP_DAYS} days before "
                          f"{p.insample_end}")
    signals = {t: compute_signals(f, prereg.signal, prereg.sizing) for t, f in frames.items()}
    summarise(signals, p.insample_start, p.insample_end)  # every return day and the decision day before are valid
    inputs = screen_inputs(frames, signals, start=p.insample_start, end=p.insample_end)
    h = prereg.prereg_hash()
    result = run_screen(
        inputs, prereg.sizing, paths=prereg.screen.baseline_runs, margin=prereg.stats.shift_margin_days,
        rng=rng_for(prereg.stats.master_seed, h, "screen"), stop_if_p_at_least=prereg.screen.stop_if_p_at_least,
    )
    return {
        "prereg_hash": h, "manifest_sha256": prereg.data.manifest_sha256,
        "period": [p.insample_start, p.insample_end], "tickers": list(inputs.tickers),
        "first_decision_day": day_iso(inputs.day_ms[0]), "last_return_day": day_iso(last), **result,
    }


def screen_path(runs_dir: str | Path) -> Path:
    return Path(runs_dir) / "etf" / "screen.json"


def _summary_md(result: dict) -> str:
    q = result["paths"]
    rows = [f"| {t} | {r['sharpe']:.3f} | {r['bh_sharpe']:.3f} | {r['mean_weight']:.3f} |"
            for t, r in result["per_etf"].items()]
    return "\n".join([
        "# ETF trend following — summary",
        "",
        f"**Decision: {result['decision']}.** The gross screen (ETF spec Phase 2 §2.3) gave "
        f"p_screen = {result['p_screen']:.4f}, at or above the pre-registered stop threshold "
        f"{result['stop_if_p_at_least']}. Phases 3–7 are not built.",
        "",
        "| | value |",
        "|---|---|",
        f"| period (return days) | {result['period'][0]} .. {result['last_return_day']} ({result['n_days']} days) |",
        f"| ETFs | {' '.join(result['tickers'])} (equal weight) |",
        f"| observed gross excess Sharpe | {result['observed_sharpe']:.3f} |",
        f"| random-days paths | {q['n']}: mean {q['mean']:.3f}, std {q['std']:.3f}, "
        f"5/50/95% {q['p05']:.3f} / {q['p50']:.3f} / {q['p95']:.3f} |",
        f"| p_screen | {result['p_screen']:.4f} |",
        f"| vol-targeted buy-and-hold gross excess Sharpe (descriptive) | {result['bh_sharpe']:.3f} |",
        f"| 100% {BENCHMARK} excess Sharpe (descriptive) | {result['spy_sharpe']:.3f} |",
        "",
        "| ETF | strategy excess Sharpe | buy-and-hold excess Sharpe | mean w* |",
        "|---|---|---|---|",
        *rows,
        "",
        f"Pre-registration hash {result['prereg_hash']}; data manifest {result['manifest_sha256']}; computed "
        f"{result['computed_on']} at {result['git_commit']}. Gross model: close-to-close total return, idle cash at "
        "DTB3, no fill delay, drift band or costs; Sharpe on the excess over T-bills at √252. This is the fourth "
        "strategy tested in this repo.",
        "",
    ])


def write_screen(result: dict, runs_dir: str | Path) -> list[Path]:
    """Write ``runs/etf/screen.json`` once (and ``summary.md`` on stop); refuses to overwrite."""
    text = json.dumps(result, indent=2, allow_nan=False) + "\n"  # serialise first: a failure leaves no file
    summary = _summary_md(result) if result["decision"] == "stop" else None
    path = screen_path(runs_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        f.write(text)
    written = [path]
    if summary is not None:
        out = path.parent / "summary.md"
        out.write_text(summary, encoding="utf-8", newline="\n")
        written.append(out)
    return written
