"""Random-timing baselines A and B (spec §5.3, §5.4) and their p-values.

Both baselines enter long at the open of a random in-window 15m candle
``e`` (maker fee on entry, to isolate timing), with
``stop = open[e] − stop_dist_atr × ATR14[e−1]``,
``target = open[e] + r_target × (open[e] − stop)``, the variant's hold
rule, the same pierce, the real exits and actual funding.

- **A (timing):** for each real trade, a slot among the in-window candles
  of its own session, with its own ATR multiple. ``precompute_table_a``
  evaluates every (trade, slot) once; a run draws one slot per trade.
- **B (day selection plus timing):** for each real trade, a session drawn
  from the variant's eligible sessions of the period, a slot in it, and an
  ATR multiple drawn from the real trades' pool; one batched ``evaluate``
  per chunk of runs.

Both store per run the mean of every outcome component, so a run's mean
net R under any cost setting is ``repricing.reprice`` of that row.
Baseline trades that reach the data end are left out of their run's mean,
as the real ``data_end`` trades are left out of the observed mean.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from perpbt.config import ExecConfig, StrategyParams
from perpbt.data.sessions import SessionCalendar
from perpbt.data.store import Candles, Funding
from perpbt.execution.fills import CANDLE_15M_MS
from perpbt.indicators.atr import atr as atr_of
from perpbt.stats.evaluator import COMPONENTS, Outcome, TradeSpec, deadline_index, evaluate
from perpbt.stats.repricing import reprice

ATR_PERIOD = 14
_CELLS = 4_000_000  # gathered floats per chunk of baseline-A runs


@dataclass(frozen=True, eq=False)
class Setup:
    """The market and rules a variant's baselines are drawn on; build it with ``make_setup``."""

    candles15: Candles
    candles1m: Candles | None
    funding: Funding
    calendar: SessionCalendar
    exec_cfg: ExecConfig
    params: StrategyParams
    period_start_ms: int
    period_end_ms: int
    first_idx: int
    last_idx: int
    listing_ms: int | None
    atr: np.ndarray
    valid: np.ndarray  # candles that can be a baseline entry


def make_setup(
    candles15: Candles, candles1m: Candles | None, funding: Funding, calendar: SessionCalendar,
    exec_cfg: ExecConfig, params: StrategyParams, period_start_ms: int, period_end_ms: int,
    *, listing_ms: int | None = None,
) -> Setup:
    """A slot is an in-window candle ``e`` of the period, at or after the listing, with ``ATR14[e−1] > 0``."""
    first = candles15.index_at(period_start_ms)
    last = candles15.index_at(period_end_ms) - 1
    if last < first:
        raise ValueError(f"make_setup: no 15m candle in [{period_start_ms}, {period_end_ms})")
    atr = atr_of(candles15, ATR_PERIOD)
    n = len(candles15)
    idx = np.arange(n)
    prev = np.concatenate(([np.nan], atr[:-1]))
    with np.errstate(invalid="ignore"):
        valid = calendar.in_window & (idx >= first) & (idx <= last) & (prev > 0)
    if listing_ms is not None:
        valid &= candles15.ts >= listing_ms
    return Setup(candles15, candles1m, funding, calendar, exec_cfg, params, int(period_start_ms),
                 int(period_end_ms), first, last, listing_ms, atr, valid)


def _deadlines(setup: Setup, e: np.ndarray) -> np.ndarray:
    hold = setup.params.hold_rule
    ts = setup.candles15.ts
    if hold.kind == "none":
        return np.full(len(e), -1, dtype=np.int64)
    if hold.kind == "session_end":
        return deadline_index(ts, setup.calendar.end_ms[e])
    return deadline_index(ts, ts[e] + CANDLE_15M_MS + round(hold.hours * 3_600_000))


def slot_spec(setup: Setup, e: np.ndarray, mult: np.ndarray) -> TradeSpec:
    """Market-at-the-open entries on candles ``e`` with stops ``mult × ATR14[e−1]`` below (spec §5.3)."""
    e = np.asarray(e, dtype=np.int64)
    o = setup.candles15.o[e]
    stop = o - np.asarray(mult, dtype=np.float64) * setup.atr[e - 1]
    p = setup.params
    return TradeSpec(
        entry_idx=e, entry_kind="market_open", entry_price=o, stop=stop, target=o + p.r_target * (o - stop),
        deadline_idx=_deadlines(setup, e), pierce_abs=p.pierce * o, entry_role="maker",
    )


def _evaluate(setup: Setup, spec: TradeSpec) -> Outcome:
    return evaluate(spec, setup.candles15, setup.candles1m, setup.funding, setup.exec_cfg, last_idx=setup.last_idx)


def r_subset(trades: pd.DataFrame) -> pd.DataFrame:
    """The trades every statistic uses: ``exit_reason != data_end``."""
    return trades[trades["exit_reason"] != "data_end"].reset_index(drop=True)


def observed_components(trades: pd.DataFrame) -> pd.DataFrame:
    """The component columns of real trades (their entries are maker: ``c_taker_entry = 0``)."""
    out = pd.DataFrame({k: trades[k].to_numpy(dtype=np.float64) for k in COMPONENTS if k != "c_taker_entry"})
    out.insert(3, "c_taker_entry", 0.0)
    return out[list(COMPONENTS)]


def p_value(observed: float, run_values: np.ndarray) -> tuple[float | None, float | None]:
    """One-sided ``p = (1 + #{runs ≥ observed}) / (M + 1)`` and ``z = (observed − mean) / std`` over finite runs."""
    v = np.asarray(run_values, dtype=np.float64)
    v = v[np.isfinite(v)]
    if len(v) == 0 or not np.isfinite(observed):
        return None, None
    p = (1.0 + float(np.sum(v >= observed))) / (len(v) + 1.0)
    sd = v.std(ddof=1) if len(v) > 1 else 0.0
    z = float((observed - v.mean()) / sd) if sd > 0 else None
    return p, z


def run_means(runs: pd.DataFrame, fee_maker: float, fee_taker: float, slippage: float) -> np.ndarray:
    """Each run's mean net R under the given rates (linear in the stored component means)."""
    return reprice(runs, fee_maker, fee_taker, slippage)


# --- baseline A ---------------------------------------------------------------------------------

@dataclass(eq=False)
class TableA:
    """Outcome components of every (trade, slot): ``comps[t, s, :]`` in ``COMPONENTS`` order, NaN-padded.

    Slots are compacted (the first ``n_slots[t]`` are valid); ``entry_idx``
    is the slot's entry candle (−1 padding). ``n_excluded`` counts real
    R-subset trades with no usable slot (NaN ATR multiple, or every slot
    reaching the data end).
    """

    trade_ids: np.ndarray
    n_slots: np.ndarray
    entry_idx: np.ndarray
    comps: np.ndarray
    n_excluded: int = 0

    def __len__(self) -> int:
        return len(self.trade_ids)

    def to_frame(self) -> pd.DataFrame:
        """Long form, one row per valid (trade, slot): ``trade_id, slot, entry_idx`` and the components."""
        t, s = np.nonzero(np.arange(self.comps.shape[1])[None, :] < self.n_slots[:, None])
        out = pd.DataFrame({"trade_id": self.trade_ids[t], "slot": s.astype(np.int64), "entry_idx": self.entry_idx[t, s]})
        for k, name in enumerate(COMPONENTS):
            out[name] = self.comps[t, s, k]
        return out


def table_from_outcome(owner: np.ndarray, entry_idx: np.ndarray, outcome: Outcome, trade_ids: np.ndarray,
                       *, n_excluded: int = 0) -> TableA:
    """Table A from evaluated slots: ``owner[k]`` is the position (into ``trade_ids``) of slot ``k``'s trade.

    Slots must be grouped by owner in ascending order; slots that reach the
    data end are dropped, and so are trades left with none.
    """
    owner = np.asarray(owner, dtype=np.int64)
    if len(owner) > 1 and np.any(np.diff(owner) < 0):
        raise ValueError("table_from_outcome: slots must be grouped by owner in ascending order")
    keep = outcome.exit_reason != "data_end"
    m = len(trade_ids)
    counts = np.bincount(owner[keep], minlength=m)
    has = counts > 0
    pos_of = np.cumsum(has) - 1  # owner position -> table row
    width = int(counts.max()) if m and counts.max() > 0 else 0
    rows = int(has.sum())
    comps = np.full((rows, width, len(COMPONENTS)), np.nan)
    eidx = np.full((rows, width), -1, dtype=np.int64)
    ko = owner[keep]
    starts = np.concatenate(([0], np.cumsum(counts)))[:-1]
    slot = np.arange(len(ko)) - starts[ko]
    r = pos_of[ko]
    data = np.stack([getattr(outcome, k)[keep] for k in COMPONENTS], axis=1) if len(ko) else np.zeros((0, 7))
    comps[r, slot] = data
    eidx[r, slot] = np.asarray(entry_idx, dtype=np.int64)[keep]
    return TableA(np.asarray(trade_ids)[has], counts[has], eidx, comps, n_excluded + int((~has).sum()))


def slot_specs_a(trades_r: pd.DataFrame, setup: Setup) -> tuple[TradeSpec, np.ndarray, np.ndarray, int]:
    """``(spec, owner, trade_ids, n_excluded)``: every slot of every usable real trade, grouped by trade."""
    mult = trades_r["stop_dist_atr"].to_numpy(dtype=np.float64)
    usable = np.isfinite(mult) & (mult > 0)
    cand = np.flatnonzero(setup.valid)
    sid = setup.calendar.session_id[cand]
    uniq, first, count = np.unique(sid, return_index=True, return_counts=True)
    trade_sid = trades_r["session_id"].to_numpy(dtype=np.int64)
    k = np.clip(np.searchsorted(uniq, trade_sid), 0, max(len(uniq) - 1, 0))
    found = usable & (len(uniq) > 0) & (uniq[k] == trade_sid if len(uniq) else False)
    rows = np.flatnonzero(found)
    n = count[k[rows]] if len(rows) else np.zeros(0, dtype=np.int64)
    owner = np.repeat(np.arange(len(rows)), n)
    offs = np.arange(len(owner)) - np.repeat(np.cumsum(n) - n, n)
    e = cand[np.repeat(first[k[rows]], n) + offs]
    spec = slot_spec(setup, e, np.repeat(mult[rows], n))
    return spec, owner, trades_r["trade_id"].to_numpy()[rows], int(len(trades_r) - len(rows))


def precompute_table_a(trades: pd.DataFrame, setup: Setup) -> TableA:
    """Spec §5.3: outcome components of every (R-subset trade, slot), from one batched ``evaluate``."""
    spec, owner, ids, excluded = slot_specs_a(r_subset(trades), setup)
    return table_from_outcome(owner, spec.entry_idx, _evaluate(setup, spec), ids, n_excluded=excluded)


def runs_a(table: TableA, M: int, rng: np.random.Generator) -> pd.DataFrame:
    """``M`` runs: one uniform slot per trade; per run the mean of every component (and ``n``)."""
    m = len(table)
    if m == 0:
        return _runs_frame(np.full((M, len(COMPONENTS)), np.nan), np.zeros(M, dtype=np.int64))
    out = []
    rows_per = max(1, _CELLS // (m * len(COMPONENTS)))
    t = np.arange(m)
    done = 0
    while done < M:
        rows = min(rows_per, M - done)
        slot = np.minimum(np.floor(rng.random((rows, m)) * table.n_slots).astype(np.int64), table.n_slots - 1)
        out.append(table.comps[t, slot].mean(axis=1))
        done += rows
    return _runs_frame(np.concatenate(out), np.full(M, m, dtype=np.int64))


def _runs_frame(means: np.ndarray, n: np.ndarray) -> pd.DataFrame:
    out = pd.DataFrame(means, columns=list(COMPONENTS))
    out.insert(0, "run", np.arange(len(out), dtype=np.int64))
    out["n"] = n
    return out


# --- baseline B ---------------------------------------------------------------------------------

@dataclass(frozen=True, eq=False)
class BPlan:
    """What a baseline-B run draws from: eligible sessions (as runs of ``slots``) and the ATR-multiple pool."""

    first: np.ndarray  # per session, the offset of its first slot in ``slots``
    count: np.ndarray  # per session, its number of slots
    slots: np.ndarray  # valid entry candles of eligible sessions, grouped by session
    pool: np.ndarray  # stop_dist_atr of the real R-subset trades (finite, > 0)
    m: int  # trades per run (the real R-subset size)


def b_plan(trades: pd.DataFrame, setup: Setup) -> BPlan:
    """Sessions of ``calendar.eligible_days`` (weekday rule, from the listing) with at least one slot."""
    tr = r_subset(trades)
    mult = tr["stop_dist_atr"].to_numpy(dtype=np.float64)
    pool = mult[np.isfinite(mult) & (mult > 0)]
    start = setup.period_start_ms if setup.listing_ms is None else max(setup.period_start_ms, setup.listing_ms)
    opens = setup.calendar.eligible_days(start, setup.period_end_ms)
    cand = np.flatnonzero(setup.valid)
    cand = cand[np.isin(setup.calendar.open_ms[cand], opens)]
    _, first, count = np.unique(setup.calendar.session_id[cand], return_index=True, return_counts=True)
    return BPlan(first, count, cand, pool, len(tr))


def b_spec(setup: Setup, plan: BPlan, rng: np.random.Generator) -> TradeSpec:
    """One baseline-B run: per trade a session, a slot in it and an ATR multiple, all uniform (spec §5.4)."""
    u = rng.random((3, plan.m))
    k = np.minimum(np.floor(u[0] * len(plan.first)).astype(np.int64), len(plan.first) - 1)
    within = np.minimum(np.floor(u[1] * plan.count[k]).astype(np.int64), plan.count[k] - 1)
    j = np.minimum(np.floor(u[2] * len(plan.pool)).astype(np.int64), len(plan.pool) - 1)
    return slot_spec(setup, plan.slots[plan.first[k] + within], plan.pool[j])


def runs_b(trades: pd.DataFrame, setup: Setup, M: int, rng: np.random.Generator, *,
           chunk_trades: int = 200_000) -> pd.DataFrame:
    """``M`` baseline-B runs; per run the mean of every component over its completed trades (and ``n``).

    Runs are drawn in order from ``rng`` and evaluated in chunks of about
    ``chunk_trades`` trades, so the result does not depend on the chunk size.
    """
    plan = b_plan(trades, setup)
    means = np.full((M, len(COMPONENTS)), np.nan)
    n = np.zeros(M, dtype=np.int64)
    if plan.m == 0 or len(plan.pool) == 0 or len(plan.first) == 0:
        return _runs_frame(means, n)
    per = max(1, chunk_trades // plan.m)
    for a in range(0, M, per):
        b = min(M, a + per)
        out = _evaluate(setup, TradeSpec.concat([b_spec(setup, plan, rng) for _ in range(a, b)]))
        keep = (out.exit_reason != "data_end").reshape(b - a, plan.m)
        data = np.stack([getattr(out, k) for k in COMPONENTS], axis=1).reshape(b - a, plan.m, len(COMPONENTS))
        cnt = keep.sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            means[a:b] = np.where(keep[:, :, None], data, 0.0).sum(axis=1) / cnt[:, None]
        means[a:b][cnt == 0] = np.nan
        n[a:b] = cnt
    return _runs_frame(means, n)
