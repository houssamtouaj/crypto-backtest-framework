"""Batched evaluator of pre-specified long trades (spec §5.2).

Given trades that are already entered (an entry candle, an entry price,
stop, target, deadline, pierce), it computes how each one exits under the
simulator's rules (Phase 4 §4.3–§4.5, §4.9) with numpy, and returns the
outcome as components (gross R, cost coefficients in R, funding in R), so
net R under any cost setting is ``repricing.reprice``. It never generates
signals; it is proven equal to the simulator by feeding it the
simulator's own trades (test 5.3).

Algorithm. Step k = 0 is the entry candle: the bracket is already filled,
so the stop may exit (reference ``min(stop, open)``) and the target may
not. When that candle touched two or more of {entry, stop, target} and 1m
candles are in use, its minutes are walked from the fill minute with
``fills.walk_minutes`` (the entry level is set marketable, so the walk's
fill minute is the given one; stop allowed and target not on it, both
after it): the post-fill part of the simulator's own walk. Steps
k = 1, 2, … gather ``entry_idx + k`` for the open trades and test stop and
target (with pierce) in one vectorized pass; a candle that touched both
goes to ``fills.resolve_candle`` (the 1m resolver, shared code) when 1m is
in use, else it is a stop. A trade with no stop or target exit on its
deadline candle exits at that close (``time``), and one still open after
the last candle's checks exits at its close (``data_end``). When fewer
than ``min_batch`` trades remain, or after ``max_steps`` steps, the rest
are finished one by one with ``argmax`` scans over windows of candles.
"""
from __future__ import annotations

from dataclasses import dataclass, fields

import numpy as np

from perpbt.config import ExecConfig
from perpbt.data.store import Candles, Funding
from perpbt.execution.fills import (
    CANDLE_15M_MS,
    MINUTE_MS,
    MISSING,
    MinuteIndex,
    Levels,
    levels,
    resolve_candle,
    walk_minutes,
)
from perpbt.stats.repricing import reprice

COMPONENTS = ("gross_r", "c_maker_entry", "c_maker_exit", "c_taker_entry", "c_taker_exit", "c_slip", "funding_r")
ENTRY_KINDS = ("limit", "market_open")
ROLES = ("maker", "taker")
EXIT_REASONS = ("stop", "target", "time", "data_end")
_SCAN_WINDOW = 1_024


def _arr(x, dtype, m: int | None, name: str) -> np.ndarray:
    a = np.asarray(x, dtype=dtype)
    if a.ndim == 0:
        if m is None:
            raise ValueError(f"TradeSpec.{name}: a scalar needs a length from entry_idx")
        a = np.full(m, a[()], dtype=dtype)
    if a.ndim != 1 or (m is not None and len(a) != m):
        raise ValueError(f"TradeSpec.{name}: expected {m} values, got shape {a.shape}")
    return a


@dataclass
class TradeSpec:
    """Arrays of length ``m`` (a scalar string or number is broadcast).

    ``entry_kind``: ``limit`` fills at ``entry_price`` on ``entry_idx``;
    ``market_open`` fills at ``open[entry_idx]`` (``entry_price`` ignored).
    ``deadline_idx``: the candle at whose close the time exit happens, −1
    for none. ``entry_role``: the fee role of the entry. ``entry_minute``:
    the 1m candle (0–14) of the fill inside the entry candle, used when the
    entry candle is resolved by 1m (default 0; ``market_open`` is always 0).
    ``stop_dist``: the R unit (default ``entry price − stop``).
    """

    entry_idx: np.ndarray
    entry_kind: np.ndarray
    entry_price: np.ndarray
    stop: np.ndarray
    target: np.ndarray
    deadline_idx: np.ndarray
    pierce_abs: np.ndarray
    entry_role: np.ndarray
    entry_minute: np.ndarray | None = None
    stop_dist: np.ndarray | None = None

    def __post_init__(self) -> None:
        self.entry_idx = _arr(self.entry_idx, np.int64, None, "entry_idx")
        m = len(self.entry_idx)
        self.entry_kind = _arr(self.entry_kind, object, m, "entry_kind")
        self.entry_role = _arr(self.entry_role, object, m, "entry_role")
        for name in ("entry_price", "stop", "target", "pierce_abs"):
            setattr(self, name, _arr(getattr(self, name), np.float64, m, name))
        self.deadline_idx = _arr(self.deadline_idx, np.int64, m, "deadline_idx")
        self.entry_minute = _arr(0 if self.entry_minute is None else self.entry_minute, np.int64, m, "entry_minute")
        if self.stop_dist is not None:
            self.stop_dist = _arr(self.stop_dist, np.float64, m, "stop_dist")

    def __len__(self) -> int:
        return len(self.entry_idx)

    @staticmethod
    def concat(specs: list[TradeSpec]) -> TradeSpec:
        """One spec holding the trades of ``specs`` in order (``stop_dist`` kept only if every part has it)."""
        names = [f.name for f in fields(TradeSpec)]
        out = {}
        for n in names:
            parts = [getattr(s, n) for s in specs]
            if n == "stop_dist" and any(p is None for p in parts):
                out[n] = None
            else:
                out[n] = np.concatenate(parts) if parts else np.zeros(0)
        return TradeSpec(**out)


@dataclass
class Outcome:
    """Arrays of length ``m``. ``exit_minute`` is the 1m candle of a 1m-resolved exit, −1 otherwise."""

    exit_idx: np.ndarray
    exit_minute: np.ndarray
    exit_ref: np.ndarray
    exit_reason: np.ndarray
    exit_role: np.ndarray
    funding_r: np.ndarray
    gross_r: np.ndarray
    c_maker_entry: np.ndarray
    c_maker_exit: np.ndarray
    c_taker_entry: np.ndarray
    c_taker_exit: np.ndarray
    c_slip: np.ndarray

    def __len__(self) -> int:
        return len(self.exit_idx)

    def components(self) -> dict[str, np.ndarray]:
        return {k: getattr(self, k) for k in COMPONENTS}


def net_r(outcome: Outcome, fee_maker: float, fee_taker: float, slippage: float) -> np.ndarray:
    """Net R per trade under the given rates (``repricing.reprice`` of the components)."""
    return reprice(outcome.components(), fee_maker, fee_taker, slippage)


def deadline_index(ts: np.ndarray, deadline_ms: np.ndarray) -> np.ndarray:
    """Index of the first candle whose close is at or after each deadline (the simulator's time-exit candle)."""
    return np.searchsorted(np.asarray(ts, dtype=np.int64) + CANDLE_15M_MS, np.asarray(deadline_ms, dtype=np.int64))


def specs_from_trades(trades, candles15: Candles) -> TradeSpec:
    """The simulator's trades as ``limit`` specs: fill candle and price, stop, target, deadline, pierce, R unit."""
    e = trades["entry_idx"].to_numpy(dtype=np.int64)
    entry_ms = trades["entry_ms"].to_numpy(dtype=np.int64)
    dl = trades["deadline_ms"]
    has = dl.notna().to_numpy()
    deadline = np.full(len(e), -1, dtype=np.int64)
    if has.any():
        deadline[has] = deadline_index(candles15.ts, dl[has].to_numpy(dtype=np.int64))
    return TradeSpec(
        entry_idx=e, entry_kind="limit", entry_price=trades["entry_price"].to_numpy(dtype=np.float64),
        stop=trades["stop_price"].to_numpy(dtype=np.float64), target=trades["target_price"].to_numpy(dtype=np.float64),
        deadline_idx=deadline, pierce_abs=trades["pierce_abs"].to_numpy(dtype=np.float64), entry_role="maker",
        entry_minute=(entry_ms - candles15.ts[e]) // MINUTE_MS if len(e) else np.zeros(0, dtype=np.int64),
        stop_dist=trades["stop_dist"].to_numpy(dtype=np.float64),
    )


def evaluate(
    spec: TradeSpec,
    candles15: Candles,
    candles1m: Candles | None,
    funding: Funding,
    exec_cfg: ExecConfig,
    *,
    last_idx: int | None = None,
    min_batch: int = 32,
    max_steps: int = 512,
) -> Outcome:
    """Exit every trade of ``spec`` on ``candles15[: last_idx + 1]`` (spec §5.2; module docstring).

    With ``exec_cfg.use_1m`` the 1m candles resolve ambiguous candles (as
    the simulator, ``use_1m`` without 1m candles raises); without it they
    are ignored. ``min_batch`` and ``max_steps`` only choose between the
    vectorized and the per-trade path; the result is the same.
    """
    return _Eval(spec, candles15, candles1m, funding, exec_cfg, last_idx).run(min_batch, max_steps)


class _Eval:
    def __init__(self, spec: TradeSpec, cd: Candles, candles1m: Candles | None, funding: Funding, cfg: ExecConfig,
                 last_idx: int | None) -> None:
        if cd.tf != "15m":
            raise ValueError(f"evaluate: candles15 must be 15m candles, got {cd.tf!r}")
        if cfg.use_1m and candles1m is None:
            raise ValueError("evaluate: exec_cfg.use_1m is set but no 1m candles were given")
        self.minutes = MinuteIndex(candles1m) if cfg.use_1m else None
        n = len(cd)
        self.last = n - 1 if last_idx is None else int(last_idx)
        if not 0 <= self.last < n:
            raise ValueError(f"evaluate: last_idx {last_idx} is outside the {n} candles")
        self.ts, self.o, self.h, self.l, self.c = cd.ts, cd.o, cd.h, cd.l, cd.c
        m = len(spec)
        e = spec.entry_idx
        if m and (e.min() < 0 or e.max() > self.last):
            raise ValueError(f"evaluate: entry_idx must lie in [0, {self.last}]")
        bad = sorted(set(spec.entry_kind.tolist()) - set(ENTRY_KINDS)) + sorted(set(spec.entry_role.tolist()) - set(ROLES))
        if bad:
            raise ValueError(f"evaluate: unknown entry kind or role {bad}")
        market = spec.entry_kind == "market_open"
        self.e = e
        self.entry = np.where(market, self.o[e], spec.entry_price) if m else np.zeros(0)
        self.stop = spec.stop
        self.target = spec.target
        self.pierce = spec.pierce_abs
        self.target_at = spec.target + spec.pierce_abs  # the same float operation as fills.levels
        self.stop_dist = self.entry - self.stop if spec.stop_dist is None else spec.stop_dist
        values = np.concatenate([self.entry, self.stop, self.target, self.pierce, self.stop_dist])
        if not np.all(np.isfinite(values)):
            raise ValueError("evaluate: entry, stop, target, pierce and stop_dist must be finite")
        if m and not (np.all(self.stop_dist > 0) and np.all(self.stop < self.entry)):
            raise ValueError("evaluate: every stop must lie below its entry (positive stop_dist)")
        self.minute0 = np.where(market, 0, spec.entry_minute)
        if m and (self.minute0.min() < 0 or self.minute0.max() > 14):
            raise ValueError("evaluate: entry_minute must lie in 0..14")
        dl = spec.deadline_idx
        self.deadline = np.where(dl >= 0, np.maximum(dl, e), -1)
        self.role = spec.entry_role
        self.funding = funding
        self.exit_idx = np.full(m, -1, dtype=np.int64)
        self.exit_minute = np.full(m, -1, dtype=np.int64)
        self.exit_ref = np.full(m, np.nan)
        self.reason = np.full(m, None, dtype=object)

    # --- recording --------------------------------------------------------------------------

    def _set(self, rows, idx, ref, reason: str, minute=-1) -> None:
        self.exit_idx[rows] = idx
        self.exit_ref[rows] = ref
        self.reason[rows] = reason
        self.exit_minute[rows] = minute

    def _levels(self, p: int) -> Levels:
        return levels(float(self.entry[p]), float(self.stop[p]), float(self.target[p]), float(self.pierce[p]))

    def _time_or_end(self, rows: np.ndarray, idx: np.ndarray) -> None:
        """Deadline exit at ``close[idx]``, else data end on the last candle, for open ``rows`` evaluated at ``idx``."""
        open_ = self.exit_idx[rows] < 0
        rows, idx = rows[open_], idx[open_]
        timed = self.deadline[rows] == idx
        self._set(rows[timed], idx[timed], self.c[idx[timed]], "time")
        end = ~timed & (idx == self.last)
        self._set(rows[end], idx[end], self.c[idx[end]], "data_end")

    # --- one candle, one open trade (the scalar path) ----------------------------------------

    def _candle_one(self, p: int, j: int) -> bool:
        """Stop/target on candle ``j > entry`` for open trade ``p``; True if it exited."""
        stop_hit = self.l[j] <= self.stop[p]
        target_hit = self.h[j] >= self.target_at[p]
        if stop_hit and target_hit and self.minutes is not None:
            out = resolve_candle(False, float(self.o[j]), float(self.h[j]), float(self.l[j]), self._levels(p),
                                 self.minutes.window(int(self.ts[j])))
            if out.exit is None:
                return False  # the 1m data disagree with the 15m bar: the trade stays open
            self._set(p, j, out.exit_ref, out.exit, out.exit_minute)
            return True
        if stop_hit:
            self._set(p, j, min(self.stop[p], self.o[j]), "stop")
            return True
        if target_hit:
            self._set(p, j, self.target[p], "target")
            return True
        return False

    def _entry_walk(self, p: int) -> None:
        """Entry candle of ``p`` resolved by its minutes from the fill minute (module docstring)."""
        e = int(self.e[p])
        win = self.minutes.window(int(self.ts[e]))
        if win is MISSING:
            if self.l[e] <= self.stop[p]:
                self._set(p, e, min(self.stop[p], self.o[e]), "stop")
            return
        o1, h1, l1 = win
        m0 = int(self.minute0[p])
        lv = self._levels(p)
        marketable = Levels(lv.entry, lv.stop, lv.target, lv.pierce_abs, float("inf"), lv.target_at)
        out = walk_minutes(True, o1[m0:], h1[m0:], l1[m0:], marketable, first_look=True)
        if out.exit is not None:
            self._set(p, e, out.exit_ref, out.exit, m0 + out.exit_minute)

    # --- the passes ------------------------------------------------------------------------

    def _step0(self) -> None:
        rows = np.arange(len(self.e))
        e = self.e
        stop_hit = self.l[e] <= self.stop
        target_hit = self.h[e] >= self.target_at
        walk = (stop_hit | target_hit) if self.minutes is not None else np.zeros(len(e), dtype=bool)
        for p in rows[walk]:
            self._entry_walk(int(p))
        s = stop_hit & ~walk
        self._set(rows[s], e[s], np.minimum(self.stop[s], self.o[e[s]]), "stop")
        self._time_or_end(rows, e)

    def _step(self, rows: np.ndarray, idx: np.ndarray) -> None:
        stop_hit = self.l[idx] <= self.stop[rows]
        target_hit = self.h[idx] >= self.target_at[rows]
        both = stop_hit & target_hit
        if self.minutes is not None:
            for p, j in zip(rows[both].tolist(), idx[both].tolist(), strict=True):
                self._candle_one(p, j)
            stop_hit = stop_hit & ~both
        s = stop_hit
        self._set(rows[s], idx[s], np.minimum(self.stop[rows[s]], self.o[idx[s]]), "stop")
        t = target_hit & ~both & ~s if self.minutes is not None else target_hit & ~s
        self._set(rows[t], idx[t], self.target[rows[t]], "target")
        self._time_or_end(rows, idx)

    def _scan(self, p: int, a: int) -> None:
        """Finish trade ``p`` from candle ``a`` on with windowed ``argmax`` scans."""
        d = int(self.deadline[p])
        b = d if 0 <= d <= self.last else self.last
        j = a
        w = _SCAN_WINDOW
        while j <= b:
            hi = min(b + 1, j + w)
            hits = (self.l[j:hi] <= self.stop[p]) | (self.h[j:hi] >= self.target_at[p])
            if hits.any():
                q = j + int(np.argmax(hits))
                if self._candle_one(p, q):
                    return
                j = q + 1
            else:
                j = hi
                w *= 2
        self._time_or_end(np.array([p]), np.array([b]))

    def run(self, min_batch: int, max_steps: int) -> Outcome:
        m = len(self.e)
        if m:
            self._step0()
            pending = np.flatnonzero(self.exit_idx < 0)
            k = 1
            while len(pending) and len(pending) >= min_batch and k <= max_steps:
                self._step(pending, self.e[pending] + k)
                pending = pending[self.exit_idx[pending] < 0]
                k += 1
            for p in pending.tolist():
                self._scan(p, int(self.e[p]) + k)
        return self._outcome()

    # --- components ------------------------------------------------------------------------

    def _funding_r(self) -> np.ndarray:
        """Spec §4.5 per trade: events ``τ_entry + 15m ≤ f < τ_exit + 15m``, each at ``close[i − 1]``."""
        m = len(self.e)
        if m == 0 or len(self.funding) == 0:
            return np.zeros(m)
        f = self.funding.ts
        lo_ms = int(self.ts[0])
        hi_ms = int(self.ts[self.last]) + CANDLE_15M_MS
        window = (f >= lo_ms) & (f < hi_ms)
        off = f[window & (f % CANDLE_15M_MS != 0)]
        if len(off):
            raise ValueError(f"evaluate: funding events off the 15m grid are not supported, first at {off[0]} ms")
        i = np.searchsorted(self.ts + CANDLE_15M_MS, f, side="right")  # the candle whose step 1 charges f
        charged = window & (i >= 1)  # an event before the first close finds no open trade
        unit = np.where(charged, self.funding.rate * self.c[np.maximum(i - 1, 0)], 0.0)
        cum = np.concatenate(([0.0], np.cumsum(unit)))
        a = np.searchsorted(f, self.ts[self.e] + CANDLE_15M_MS, side="left")
        b = np.searchsorted(f, self.ts[self.exit_idx] + CANDLE_15M_MS, side="left")
        return np.where(b > a, (cum[b] - cum[a]), 0.0) / self.stop_dist

    def _outcome(self) -> Outcome:
        sd = self.stop_dist
        target = self.reason == "target"
        xr = self.exit_ref / sd if len(sd) else np.zeros(0)
        r_entry = self.entry / sd if len(sd) else np.zeros(0)
        maker = self.role == "maker"
        zero = np.zeros(len(sd))
        return Outcome(
            exit_idx=self.exit_idx, exit_minute=self.exit_minute, exit_ref=self.exit_ref,
            exit_reason=self.reason, exit_role=np.where(target, "maker", "taker").astype(object),
            funding_r=self._funding_r(),
            gross_r=(self.exit_ref - self.entry) / sd if len(sd) else zero,
            c_maker_entry=np.where(maker, r_entry, 0.0), c_maker_exit=np.where(target, xr, 0.0),
            c_taker_entry=np.where(maker, 0.0, r_entry), c_taker_exit=np.where(target, 0.0, xr),
            c_slip=np.where(target, 0.0, xr),
        )
