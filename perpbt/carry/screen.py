"""The simplified net screen (carry spec Phase 2 §2.3) and its V2 statistic (Phase 4 §4.2).

A deliberately simple model, independent of the Phase 3 executor. A pair's
sleeve that is on at day ``d``'s close earns on day ``d+1``

    x × (r^spot_{d+1} − r^perp_{d+1} + F_{d+1}),   x = L / (L + 1),

with ``r`` the close-to-close price return of each leg (the perp's from
``perp.c``, never ``DayFrame.ret``) and ``F_{d+1}`` the sum of the perp's
funding rates on day ``d+1`` (``perp.funding``, the DayFrame's per-day sum;
the short receives positive rates). On a day
where the state differs from the previous decision's (every pair starts off),
``x × (spot_taker + perp_taker + 2 × slippage_pair)`` is subtracted. No fill
delay, margin, rebalancing or liquidation. The portfolio return is the mean
over the pairs live on ``d`` (the frame has ``d`` and ``d+1``); an off sleeve
contributes 0.

V2: the daily excess over the hurdle ``e = ret − h_d``, ``h_d = (1 + h)^(1/365)
− 1``; replicates ``365 × mean(e*)`` of moving-block resamples;
``p = (1 + #{replicates ≤ 0}) / (B + 1)``. The screen stops when the primary's
``p ≥ screen.stop_if_p_at_least``. V1 and the ``always_on`` row are
descriptive.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from perpbt.carry.frames import PairFrame, load_pairframe
from perpbt.carry.prereg import CarryPrereg
from perpbt.carry.signals import compute_signals
from perpbt.config import ConfigError, DataConfig, config_hash, to_dict
from perpbt.data.store import DAY_MS, date_ms
from perpbt.stats.bootstrap import block_bootstrap_mean, block_bootstrap_sharpe, percentile_ci, sharpe
from perpbt.stats.seeds import rng_for

DAYS_PER_YEAR = 365
ROWS = ("primary", "always_on")


@dataclass(frozen=True, eq=False)  # eq=False: the generated __eq__ raises on ndarray fields
class ScreenInputs:
    """Pairs aligned on decision days; arrays are ``(pairs, days)``, NaN (or -1) where a pair is not live.

    ``index`` is the pair's PairFrame row of decision day ``d``; ``r_spot``, ``r_perp`` the legs' price returns
    from ``d`` to ``d+1``; ``funding`` the sum of the perp's rates on day ``d+1``.
    """

    pairs: tuple[str, ...]
    day_ms: np.ndarray
    index: np.ndarray
    r_spot: np.ndarray
    r_perp: np.ndarray
    funding: np.ndarray
    live: np.ndarray

    def align(self, per_pair: dict[str, np.ndarray], fill=False) -> np.ndarray:
        """``(pairs, days)`` of a per-frame-row array of each pair at the decision days; ``fill`` where not live."""
        first = np.asarray(per_pair[self.pairs[0]])
        out = np.full(self.index.shape, fill, dtype=first.dtype)
        for i, pair in enumerate(self.pairs):
            out[i, self.live[i]] = np.asarray(per_pair[pair])[self.index[i, self.live[i]]]
        return out


def screen_inputs(frames: dict[str, PairFrame], *, start: str, end: str) -> ScreenInputs:
    """Align every pair on the decision days ``start − 1 .. end − 1`` (ISO dates, returns on ``start .. end``)."""
    first, last = date_ms(start) - DAY_MS, date_ms(end) - DAY_MS
    if last < first:
        raise ValueError(f"screen period {start}..{end} is empty")
    day_ms = np.arange(first, last + DAY_MS, DAY_MS, dtype=np.int64)
    pairs = tuple(frames)
    shape = (len(pairs), len(day_ms))
    index = np.full(shape, -1, dtype=np.int64)
    r_spot, r_perp, funding = (np.full(shape, np.nan) for _ in range(3))
    live = np.zeros(shape, dtype=bool)
    for i, pair in enumerate(pairs):
        pf = frames[pair]
        idx = (day_ms - pf.day_ms[0]) // DAY_MS
        ok = (idx >= 0) & (idx + 1 < len(pf))
        j = idx[ok]
        index[i, ok] = j
        r_spot[i, ok] = pf.spot.c[j + 1] / pf.spot.c[j] - 1.0
        r_perp[i, ok] = pf.perp.c[j + 1] / pf.perp.c[j] - 1.0
        funding[i, ok] = pf.perp.funding[j + 1]
        live[i, ok] = True
    return ScreenInputs(pairs, day_ms, index, r_spot, r_perp, funding, live)


def sleeve_returns(on: np.ndarray, inputs: ScreenInputs, *, leverage: float, cost: np.ndarray) -> np.ndarray:
    """``(pairs, days)`` net sleeve returns on day ``d+1`` of the states ``on`` at ``d``; NaN where not live.

    ``cost[i]`` is pair ``i``'s round of both legs (``spot_taker + perp_taker + 2 × slippage``), charged
    ``× x`` on every change of state; the state before the first decision day, and before a pair is live, is off.
    """
    on = np.asarray(on, dtype=bool) & inputs.live
    x = leverage / (leverage + 1.0)
    prev = np.zeros_like(on)
    prev[:, 1:] = on[:, :-1]
    gross = np.where(on, x * (inputs.r_spot - inputs.r_perp + inputs.funding), 0.0)
    charged = np.where(on != prev, x * np.asarray(cost, dtype=np.float64)[:, None], 0.0)
    return np.where(inputs.live, gross - charged, np.nan)


def portfolio_returns(sleeve: np.ndarray, inputs: ScreenInputs) -> np.ndarray:
    """Per decision day, the mean over live pairs (0 when none is live)."""
    count = inputs.live.sum(axis=0)
    total = np.where(inputs.live, sleeve, 0.0).sum(axis=0)
    return np.where(count > 0, total / np.maximum(count, 1), 0.0)


def daily_hurdle(annual: float) -> float:
    """The daily rate that compounds to ``annual`` over 365 days."""
    return (1.0 + annual) ** (1.0 / DAYS_PER_YEAR) - 1.0


def _finite_or_none(x: float) -> float | None:
    return float(x) if np.isfinite(x) else None


def v2_stats(ret: np.ndarray, *, hurdle: float, alpha: float, B: int, block_len: int,
             rng: np.random.Generator) -> dict:
    """V2 (Phase 4 §4.2): the annualised excess over the hurdle, its block-bootstrap CI, ``p`` and reading."""
    ret = np.asarray(ret, dtype=np.float64)
    h_d = daily_hurdle(hurdle)
    e = ret - h_d
    reps = DAYS_PER_YEAR * block_bootstrap_mean(e, B, rng, block_len)
    excess = DAYS_PER_YEAR * float(np.mean(e)) if len(e) else float("nan")
    if not np.isfinite(excess) or not np.isfinite(reps).all():
        raise ValueError(f"V2 statistic not finite: excess {excess}, {int(np.sum(~np.isfinite(reps)))} of {B} "
                         "replicates")
    p = (1 + int(np.sum(reps <= 0.0))) / (B + 1)
    lo, hi = percentile_ci(reps)
    passes = p < alpha
    reading = "above_hurdle" if passes else ("below_hurdle" if hi < 0 else "not_proven")
    shift = DAYS_PER_YEAR * h_d
    return {
        "hurdle_annual": hurdle, "ann_excess": excess, "ann_excess_ci": [lo, hi], "p": p, "passes": passes,
        "reading": reading, "ann_return": excess + shift, "ann_return_ci": [lo + shift, hi + shift],
    }


def v1_stats(ret: np.ndarray, *, alpha: float, B: int, block_len: int, rng: np.random.Generator) -> dict:
    """V1 (Phase 4 §4.1), descriptive here: Sharpe, block-bootstrap CI and ``p`` over the finite replicates."""
    reps = block_bootstrap_sharpe(ret, B, rng, block_len)
    finite = reps[np.isfinite(reps)]
    p = (1.0 + float(np.sum(finite <= 0.0))) / (len(finite) + 1.0) if len(finite) else None
    lo, hi = percentile_ci(reps)
    return {"sharpe": _finite_or_none(sharpe(ret)), "sharpe_ci": [lo, hi], "p": p,
            "passes": p is not None and p < alpha}


def screen_row(on: np.ndarray, inputs: ScreenInputs, *, leverage: float, cost: np.ndarray, hurdle: float,
               alpha: float, B: int, block_len: int, rng_v2: np.random.Generator,
               rng_v1: np.random.Generator) -> dict:
    """One row of the screen: V2 (gating for the primary), V1, and how much the row was on."""
    on = np.asarray(on, dtype=bool) & inputs.live
    ret = portfolio_returns(sleeve_returns(on, inputs, leverage=leverage, cost=cost), inputs)
    prev = np.zeros_like(on)
    prev[:, 1:] = on[:, :-1]
    return {
        "v2": v2_stats(ret, hurdle=hurdle, alpha=alpha, B=B, block_len=block_len, rng=rng_v2),
        "v1": v1_stats(ret, alpha=alpha, B=B, block_len=block_len, rng=rng_v1),
        "share_on": float(on.sum() / max(int(inputs.live.sum()), 1)),
        "switches": int(np.sum(on != prev)),
    }


def run_screen(
    inputs: ScreenInputs, states: dict[str, np.ndarray], *, leverage: dict[str, float], cost: np.ndarray,
    hurdle: float, alpha: float, B: int, block_len: int, rng: Callable[[str], np.random.Generator],
    stop_if_p_at_least: float,
) -> dict:
    """The primary's V2 ``p_screen`` and the go/stop decision; ``always_on`` and V1 descriptive.

    ``states[row]`` is ``(pairs, days)`` on/off; ``rng(purpose)`` gives the generator of a purpose: the gating
    stream is ``"screen"``, the others ``"screen/v1"``, ``"screen/always_on/v2"``, ``"screen/always_on/v1"``.
    """
    rows = {}
    for row in ROWS:
        tag = "screen" if row == "primary" else f"screen/{row}"
        rows[row] = screen_row(
            states[row], inputs, leverage=leverage[row], cost=cost, hurdle=hurdle, alpha=alpha, B=B,
            block_len=block_len, rng_v2=rng("screen" if row == "primary" else f"{tag}/v2"), rng_v1=rng(f"{tag}/v1"),
        )
    p = rows["primary"]["v2"]["p"]
    return {
        "n_days": int(len(inputs.day_ms)),
        "n_pairs": len(inputs.pairs),
        "mean_live_pairs": float(inputs.live.sum(axis=0).mean()),
        "hurdle_annual": hurdle,
        "alpha": alpha,
        "bootstrap_n": B,
        "block_len_days": block_len,
        "p_screen": p,
        "stop_if_p_at_least": stop_if_p_at_least,
        "decision": "stop" if p >= stop_if_p_at_least else "go",
        "rows": rows,
    }


def round_cost(prereg: CarryPrereg, pair: str) -> float:
    """Entering or exiting both legs, per unit of each leg's notional: both taker fees and two slippages."""
    c = prereg.costs
    return c.spot_taker + c.perp_taker + 2.0 * c.slippage_for(pair)


def screen_from_store(prereg: CarryPrereg, data_cfg: DataConfig) -> dict:
    """The screen on the stored in-sample pairs: every ``universe.um`` member through ``load_pairframe``.

    Refuses (ConfigError) unless ``data_cfg``'s ``insample_end`` and ``warmup_start`` equal the prereg's, every
    member is listed, and every pair's frame ends on ``insample_end`` (no in-sample delisting is handled here).
    Seeded from ``(stats.master_seed, prereg_hash, purpose)``.
    """
    p = prereg.periods
    for name in ("insample_end", "warmup_start"):
        if getattr(data_cfg, name) != getattr(p, name):
            raise ConfigError(f"data config {name} {getattr(data_cfg, name)} differs from the prereg's {getattr(p, name)}")
    pairs = prereg.universe["um"]["members"]
    missing = [pair for pair in pairs if pair not in data_cfg.listing]
    if missing:
        raise ConfigError(f"data config has no listing date for {missing}")
    frames = {pair: load_pairframe(data_cfg, pair, end=p.insample_end) for pair in pairs}
    end_ms = date_ms(p.insample_end)
    for pair, pf in frames.items():
        if int(pf.day_ms[-1]) != end_ms:
            raise ConfigError(f"{pair}: its frame ends {_iso_day(int(pf.day_ms[-1]))}, not {p.insample_end}")
    always = prereg.exploratory_row("always_on")
    inputs = screen_inputs(frames, start=p.insample_start, end=p.insample_end)
    states = {
        "primary": inputs.align({pair: compute_signals(pf, prereg.signal).on for pair, pf in frames.items()}),
        "always_on": inputs.align({pair: compute_signals(pf, always.signal).on for pair, pf in frames.items()}),
    }
    h = prereg.prereg_hash()
    result = run_screen(
        inputs, states, leverage={"primary": prereg.sleeve.perp_leverage, "always_on": always.sleeve.perp_leverage},
        cost=np.array([round_cost(prereg, pair) for pair in pairs]), hurdle=prereg.hurdle.annual,
        alpha=prereg.stats.alpha, B=prereg.screen.bootstrap_n, block_len=prereg.stats.block_len_days,
        rng=lambda purpose: rng_for(prereg.stats.master_seed, h, purpose),
        stop_if_p_at_least=prereg.screen.stop_if_p_at_least,
    )
    return {
        "prereg_hash": h, "data_config_hash": config_hash({k: v for k, v in to_dict(data_cfg).items() if k != "data_dir"}),
        "period": [p.insample_start, p.insample_end], "pairs": list(pairs),
        "pair_last_day": {pair: _iso_day(int(pf.day_ms[-1])) for pair, pf in frames.items()}, **result,
    }


def _iso_day(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms // 1000))


def screen_path(runs_dir: str | Path) -> Path:
    return Path(runs_dir) / "carry" / "screen.json"


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:+.2f}%"


def _row_md(name: str, row: dict) -> str:
    v2, v1 = row["v2"], row["v1"]
    sr = "n/a" if v1["sharpe"] is None else f"{v1['sharpe']:.2f}"
    p1 = "n/a" if v1["p"] is None else f"{v1['p']:.4f}"
    return (f"| {name} | {_pct(v2['ann_return'])} [{_pct(v2['ann_return_ci'][0])}, {_pct(v2['ann_return_ci'][1])}] "
            f"| {_pct(v2['ann_excess'])} [{_pct(v2['ann_excess_ci'][0])}, {_pct(v2['ann_excess_ci'][1])}] "
            f"| {v2['p']:.4f} | {v2['reading']} | {sr} | {p1} | {row['share_on']:.3f} | {row['switches']} |")


def _summary_md(result: dict) -> str:
    rows = result["rows"]
    return "\n".join([
        "# Funding carry — summary",
        "",
        f"**Decision: {result['decision']}.** The simplified net screen (carry spec Phase 2 §2.3) gave "
        f"p_screen = {result['p_screen']:.4f} (V2: annualised net return above the "
        f"{100 * result['hurdle_annual']:.0f}%/yr hurdle), at or above the pre-registered stop threshold "
        f"{result['stop_if_p_at_least']}. Phases 3–7 are not built.",
        "",
        f"Period (return days) {result['period'][0]} .. {result['period'][1]} ({result['n_days']} days); pairs "
        f"{' '.join(result['pairs'])}. Block bootstrap: B = {result['bootstrap_n']}, blocks of "
        f"{result['block_len_days']} days; 95% percentile CIs.",
        "",
        "| row | annualised net return [CI] | excess over hurdle [CI] | p (V2) | reading | Sharpe | p (V1) "
        "| share of live pair-days on | switches |",
        "|---|---|---|---|---|---|---|---|---|",
        _row_md("primary (gating)", rows["primary"]),
        _row_md("always_on (descriptive)", rows["always_on"]),
        "",
        f"Pre-registration hash {result['prereg_hash']}; computed {result['computed_on']} at "
        f"{result['git_commit']}. Simplified model: no fill delay, margin, rebalancing or liquidation.",
        "",
    ])


def write_screen(result: dict, runs_dir: str | Path) -> list[Path]:
    """Write ``runs/carry/screen.json`` once (and ``summary.md`` on stop); refuses to overwrite either.

    Everything is serialised and both paths are checked before the first file is opened, so a refusal or a
    failure leaves no half-written result.
    """
    text = json.dumps(result, indent=2, allow_nan=False) + "\n"
    summary = _summary_md(result) if result["decision"] == "stop" else None
    path = screen_path(runs_dir)
    out = path.parent / "summary.md"
    for p in (path, out) if summary is not None else (path,):
        if p.exists():
            raise FileExistsError(f"{p} exists; the screen runs once")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        f.write(text)
    written = [path]
    if summary is not None:
        with open(out, "x", encoding="utf-8", newline="\n") as f:
            f.write(summary)
        written.append(out)
    return written
