"""The two-leg carry executor (carry spec Phase 3): per pair, long spot and short perp in equal quantity.

``run_path`` is the reference: a plain loop over the days and pairs of one on/off path. ``run_batch`` runs the
same rules over ``P`` paths at once, looping over days and vectorised over paths and pairs; the two agree path
by path (tests: to 1e-12 of the starting equity). Both take the on/off decisions as given (``state[pair][j]`` is
the decision at the close of the pair's PairFrame row ``j``, from ``carry.signals.compute_signals`` or a shifted
path) and never compute a signal.

The account (§3.2): starting equity ``equity0``; on decision day ``d`` the sleeve target is ``E_d / N_live``,
``N_live`` the pairs whose frame has ``d`` and ``d+1``. An entry spends the target, costs included: a sleeve amount ``A``
buys spot notional ``x·A`` (``x = L / (L + 1)``), shorts the same quantity of perp and puts ``x·A / L`` in the
perp wallet, and ``A`` plus both legs' costs is the target. Free cash never goes negative: when it cannot cover
every entry of the day at the target, the entries share it pro rata (``capped_entry``). On-sleeves are not resized except by the rebalance rule. The spot and perp wallets
are separate (no cross-collateral). Every pair starts flat; nothing is closed at the period end.

The day (§3.3, return day ``t = d + 1``):

1. Funding events of ``t`` are booked to the perp wallet, ``rate × q × px`` for the short (it receives positive
   rates), on the perp quantity held at the event: an event strictly before the perp leg's fill time is charged
   on the old quantity, one at or after it on the new one (the fill time is the candle close; the exchange
   closes the candle 1 ms earlier). ``px`` is ``perp.funding_px`` or, when that candle is missing,
   ``funding_px_prev`` (counted). An event with no quantity held is never priced.
2. Decision ``on[d]`` against the holding: enter, exit, or hold. A pair that was liquidated stays out until a
   decision is off.
3. Fills at ``fill_px_late[leg][h]`` of ``t`` (trend Phase 3 §3.6: the close of the first candle closing at or
   after ``t + h``; ``late_fill`` per leg when after). A trade whose legs lack a fill candle that day is
   postponed (counted) and re-decided the next day. The day's trades settle together in this order: exits and
   post-liquidation spot sales, rebalances, entries. Fees and slippage are cash costs on traded notional.
4. Margin. Liquidation when the perp wallet equity at the day high ``W + q(P0 − H)`` is at or below
   ``mmr × q × H``: the short is bought back at the liquidation price ``(W + q·P0) / (q(1 + mmr))`` with the
   liquidation fee on that notional (in ``fees``, charged in full), and the spot leg is sold at the next fill.
   Otherwise a rebalance is due when the wallet equity at the close is below ``rebalance_below × q·C / L``; at
   the next fill both legs are reduced by ``δ`` so that the wallet equity after the trade and its costs is
   ``q'·Pp / L`` (no trade when ``δ ≤ 0`` or the perp fill is at or above the liquidation price).
5. A pair whose frame ends before the period's last day is delisted: on its last day it is closed at the close
   of its last common candle with costs and leaves ``N_live`` from the next decision. A frame ending on the
   period's last day is the window's end, not a delisting.
6. Mark both legs at the day close; ``ret = E_t / E_d − 1``. ``basis_pnl`` is the legs' price P&L, so
   ``ΔE = funding − fees − slippage + basis_pnl``.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from perpbt.carry.config import CarryCostConfig, CarryRebalanceConfig, MarginConfig, SleeveConfig
from perpbt.carry.frames import PairFrame
from perpbt.carry.prereg import CarryPrereg
from perpbt.data.store import DAY_MS, date_ms

START_EQUITY = 10_000.0  # spec §3.2: a scale only; returns are fractions of equity
COUNTS = (
    "entries", "exits", "rebalances", "liquidations", "delists", "capped_entries", "postponed", "late_fills",
    "funding_px_fallbacks",
)
DAILY_COLUMNS = (
    "date", "day_ms", "equity", "ret", "cash", "funding", "fees", "slippage", "basis_pnl", "n_on", "n_live",
)
PAIR_COLUMNS = ("date", "pair", "state", "spot_qty", "perp_qty", "perp_wallet", "sleeve_value", "funding")
FILL_COLUMNS = ("date", "pair", "leg", "qty", "price", "reason", "fee", "slippage")
EVENT_COLUMNS = ("date", "pair", "kind", "leg", "detail", "value")
_MIN_MS = 60_000


@dataclass(frozen=True)
class ExecSpec:
    """The frozen prereg's ``sleeve``, ``margin``, ``costs`` and ``rebalance`` blocks and the return days."""

    sleeve: SleeveConfig
    margin: MarginConfig
    costs: CarryCostConfig
    rebalance: CarryRebalanceConfig
    start: str  # first return day (ISO); its decision is the day before
    end: str  # last return day
    equity0: float = START_EQUITY

    def __post_init__(self) -> None:
        if date_ms(self.end) < date_ms(self.start):
            raise ValueError(f"ExecSpec: end {self.end} is before start {self.start}")
        if not (math.isfinite(self.equity0) and self.equity0 > 0):
            raise ValueError(f"ExecSpec.equity0: must be finite and > 0, got {self.equity0!r}")


def exec_spec(prereg: CarryPrereg, *, sleeve: SleeveConfig | None = None, start: str | None = None,
              end: str | None = None) -> ExecSpec:
    """The ExecSpec of the prereg's blocks; ``sleeve`` for an exploratory row; the in-sample period by default."""
    p = prereg.periods
    return ExecSpec(
        sleeve=prereg.sleeve if sleeve is None else sleeve, margin=prereg.margin, costs=prereg.costs,
        rebalance=prereg.rebalance, start=p.insample_start if start is None else start,
        end=p.insample_end if end is None else end,
    )


@dataclass(frozen=True, eq=False)
class PathResult:
    """One path's tables (spec §3.1) and its event counts (``COUNTS``)."""

    daily: pd.DataFrame
    daily_pair: pd.DataFrame
    fills: pd.DataFrame
    events: pd.DataFrame
    counts: dict[str, int]


@dataclass(frozen=True, eq=False)
class BatchResult:
    """``(paths, days)`` arrays of ``run_batch``, the daily columns of ``PathResult.daily``; counts ``(paths,)``."""

    day_ms: np.ndarray
    n_live: np.ndarray
    equity: np.ndarray
    ret: np.ndarray
    cash: np.ndarray
    funding: np.ndarray
    fees: np.ndarray
    slippage: np.ndarray
    basis_pnl: np.ndarray
    n_on: np.ndarray
    counts: dict[str, np.ndarray]


# --- money kernels: floats or arrays; run_path and run_batch evaluate the same expressions --------------------

def _entry(a, ps, pp, x, lev, fs, fp, slip):
    """Sleeve amount ``a`` entered at ``ps`` / ``pp``: quantity, perp wallet, fees, slippage."""
    ns = x * a
    q = ns / ps
    npp = q * pp
    return q, ns / lev, ns * fs + npp * fp, ns * slip + npp * slip


def _sleeve_amount(spend, ps, pp, x, lev, fs, fp, slip):
    """The sleeve amount whose entry, costs included, spends ``spend``."""
    _, _, fee, slp = _entry(1.0, ps, pp, x, lev, fs, fp, slip)
    return spend / (1.0 + fee + slp)


def _close(qs, q, w, p0, ps, pp, fs, fp, slip):
    """Selling ``qs`` spot at ``ps`` and buying back ``q`` perp at ``pp``: cash released, fees, slippage."""
    ns = qs * ps
    npp = q * pp
    fee = ns * fs + npp * fp
    slp = ns * slip + npp * slip
    return ns + (w + q * (p0 - pp)) - fee - slp, fee, slp


def _rebalance_size(q, w, p0, ps, pp, lev, fs, fp, slip):
    """``δ`` such that the perp wallet equity after reducing both legs by ``δ`` and paying the costs is ``q'·pp/L``."""
    eq = w + q * (p0 - pp)
    return (q * pp / lev - eq) / (ps * (1.0 - fs - slip) - pp * (fp + slip) + pp / lev)


def _rebalance(dq, w, p0, ps, pp, fs, fp, slip):
    """Both legs reduced by ``dq``; the freed spot cash, net of both legs' costs, goes to the perp wallet."""
    ns = dq * ps
    npp = dq * pp
    fee = ns * fs + npp * fp
    slp = ns * slip + npp * slip
    return w + dq * (p0 - pp) + ns - fee - slp, fee, slp


def _liq_price(q, w, p0, mmr):
    """The perp price at which the wallet equity ``w + q(p0 − P)`` equals the maintenance margin ``mmr·q·P``."""
    return (w + q * p0) / (q * (1.0 + mmr))


def _liquidates(q, w, p0, high, mmr):
    return w + q * (p0 - high) <= mmr * q * high


def _needs_rebalance(q, w, p0, close, lev, below):
    return w + q * (p0 - close) < below * (q * close / lev)


def _value(qs, q, w, p0, cs, cp):
    """A sleeve marked at ``cs`` / ``cp``: the spot leg, the perp wallet and the short's unrealised P&L."""
    return qs * cs + w + q * (p0 - cp)


# --- inputs ----------------------------------------------------------------------------------------------------

def _iso(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms // 1000))


def _return_days(cfg: ExecSpec) -> np.ndarray:
    return np.arange(date_ms(cfg.start), date_ms(cfg.end) + DAY_MS, DAY_MS, dtype=np.int64)


def _check(frames: dict[str, PairFrame], state: dict[str, np.ndarray], cfg: ExecSpec, *, ndim: int) -> int:
    """Validate the inputs; the number of paths (1 for ``ndim == 1``)."""
    if not frames:
        raise ValueError("no frames")
    if set(state) != set(frames):
        raise ValueError(f"state pairs {sorted(state)} differ from the frames' {sorted(frames)}")
    h = cfg.rebalance.fill_offset_min
    paths = set()
    for pair, pf in frames.items():
        if not isinstance(pf, PairFrame) or pf.pair != pair:
            raise ValueError(f"frames[{pair!r}] is not the PairFrame of {pair}")
        if h not in pf.fill_px_late["spot"]:
            raise ValueError(f"{pair}: the PairFrame has no fill offset {h}")
        arr = np.asarray(state[pair])
        if arr.dtype != bool or arr.ndim != ndim or arr.shape[-1] != len(pf):
            raise ValueError(f"{pair}: state must be bool of shape {'(paths, ' if ndim == 2 else '('}{len(pf)}), "
                             f"got {arr.dtype} {arr.shape}")
        paths.add(1 if ndim == 1 else arr.shape[0])
    if len(paths) != 1 or min(paths) < 1:
        raise ValueError(f"every pair needs the same number of paths (>= 1), got {sorted(paths)}")
    return paths.pop()


def _pair_costs(cfg: ExecSpec, pairs) -> tuple[float, float, float, float, list[float], list[float]]:
    lev = cfg.sleeve.perp_leverage
    x = lev / (lev + 1.0)
    return (x, lev, cfg.costs.spot_taker, cfg.costs.perp_taker, [cfg.costs.slippage_for(p) for p in pairs],
            [cfg.margin.mmr_for(p) for p in pairs])


# --- the reference loop ----------------------------------------------------------------------------------------

def run_path(frames: dict[str, PairFrame], state: dict[str, np.ndarray], cfg: ExecSpec) -> PathResult:
    """One on/off path through the executor; ``state[pair]`` is bool per PairFrame row."""
    _check(frames, state, cfg, ndim=1)
    pairs = tuple(frames)
    n = len(pairs)
    h = cfg.rebalance.fill_offset_min
    x, lev, fs, fp, slip, mmr = _pair_costs(cfg, pairs)
    liq_fee, below = cfg.margin.liquidation_fee, cfg.margin.rebalance_below
    days = _return_days(cfg)
    end_ms = int(days[-1])
    F = []
    for pair in pairs:
        pf = frames[pair]
        F.append({
            "d0": int(pf.day_ms[0]), "last": int(pf.day_ms[-1]), "n": len(pf),
            "sc": pf.spot.c.tolist(), "pc": pf.perp.c.tolist(), "ph": pf.perp.h.tolist(),
            "ps": pf.fill_px_late["spot"][h].tolist(), "pp": pf.fill_px_late["perp"][h].tolist(),
            "ms_s": pf.fill_late_ms["spot"][h].tolist(), "ms_p": pf.fill_late_ms["perp"][h].tolist(),
            "ptr": pf.perp.funding_ptr.tolist(), "ev_ts": pf.perp.funding_ts.tolist(),
            "ev_rate": pf.perp.funding_rate.tolist(), "ev_px": pf.perp.funding_px.tolist(),
            "ev_prev": pf.funding_px_prev.tolist(), "on": np.asarray(state[pair]).tolist(),
        })

    qs, q, w, p0 = [0.0] * n, [0.0] * n, [0.0] * n, [0.0] * n
    blocked, pend = [False] * n, [False] * n
    cash = equity = float(cfg.equity0)
    counts = dict.fromkeys(COUNTS, 0)
    daily = {c: [] for c in DAILY_COLUMNS}
    prow = {c: [] for c in PAIR_COLUMNS}
    fills = {c: [] for c in FILL_COLUMNS}
    events = {c: [] for c in EVENT_COLUMNS}

    def event(date, pair, kind, leg="", detail="", value=float("nan")):
        for c, v in zip(EVENT_COLUMNS, (date, pair, kind, leg, detail, value)):
            events[c].append(v)

    def fill(date, pair, leg, qty, price, reason, fee, slp):
        for c, v in zip(FILL_COLUMNS, (date, pair, leg, qty, price, reason, fee, slp)):
            fills[c].append(v)

    for t in days.tolist():
        date = _iso(t)
        d = t - DAY_MS
        e_d = equity
        jd = [(d - f["d0"]) // DAY_MS if f["d0"] <= d and d + DAY_MS <= f["last"] else -1 for f in F]
        live = [j >= 0 for j in jd]
        n_live = sum(live)
        target = e_d / n_live if n_live else 0.0
        late_at = t + h * _MIN_MS
        act = [None] * n
        on_today = [False] * n
        fee, slp, fund, pnl = [0.0] * n, [0.0] * n, [0.0] * n, [0.0] * n
        mark_s, mark_p = [0.0] * n, [0.0] * n

        # 2. the decision against the holding, and whether the legs can fill today
        for i in range(n):
            if not live[i]:
                continue
            f, j = F[i], jd[i]
            mark_s[i], mark_p[i] = f["sc"][j], f["pc"][j]
            on = f["on"][j]
            on_today[i] = on
            if not on:
                blocked[i] = False
            want = on and not blocked[i]
            spot_ok = not math.isnan(f["ps"][j + 1])
            both = spot_ok and not math.isnan(f["pp"][j + 1])
            if qs[i] > 0 and q[i] == 0:
                plan, ok = "sell", spot_ok
            elif q[i] > 0:
                plan = "exit" if not want else ("rebalance" if pend[i] else None)
                ok = both
            else:
                plan, ok = ("enter" if want else None), both
            if plan is not None and not ok:
                counts["postponed"] += 1
                event(date, pairs[i], "postponed", detail=plan)
                plan = None
            act[i] = plan

        # 1. funding before the perp leg's fill, on the old quantity
        def amount(i, e, qty):
            if qty == 0.0:
                return 0.0
            f = F[i]
            px = f["ev_px"][e]
            if math.isnan(px):
                px = f["ev_prev"][e]
                if math.isnan(px):
                    raise AssertionError(f"{pairs[i]}: funding event {f['ev_ts'][e]} has no price, with {qty} held")
                counts["funding_px_fallbacks"] += 1
                event(date, pairs[i], "funding_px_fallback", leg="perp", value=px)
            return f["ev_rate"][e] * qty * px

        perp_trade = [a in ("exit", "rebalance", "enter") for a in act]
        for i in range(n):
            if not live[i]:
                continue
            f, jt = F[i], jd[i] + 1
            total = 0.0
            for e in range(f["ptr"][jt], f["ptr"][jt + 1]):
                if perp_trade[i] and f["ev_ts"][e] < f["ms_p"][jt]:
                    total = total + amount(i, e, q[i])
            w[i] = w[i] + total
            fund[i] = total

        def late(i, jt, legs):
            for leg in legs:
                at = F[i]["ms_s" if leg == "spot" else "ms_p"][jt]
                if at > late_at:
                    counts["late_fills"] += 1
                    event(date, pairs[i], "late_fill", leg=leg, value=at)

        # 3a. exits and post-liquidation spot sales
        for i in range(n):
            if act[i] not in ("exit", "sell"):
                continue
            f, jt = F[i], jd[i] + 1
            ps = f["ps"][jt]
            pp = f["pp"][jt] if act[i] == "exit" else 0.0
            qp = q[i] if act[i] == "exit" else 0.0
            pnl[i] = pnl[i] + qs[i] * (ps - mark_s[i])
            mark_s[i] = ps
            if act[i] == "exit":
                pnl[i] = pnl[i] - qp * (pp - mark_p[i])
                mark_p[i] = pp
            got, fe, sl = _close(qs[i], qp, w[i], p0[i], ps, pp, fs, fp, slip[i])
            reason = "exit" if act[i] == "exit" else "liquidation"
            fill(date, pairs[i], "spot", -qs[i], ps, reason, qs[i] * ps * fs, qs[i] * ps * slip[i])
            if act[i] == "exit":
                fill(date, pairs[i], "perp", qp, pp, reason, qp * pp * fp, qp * pp * slip[i])
                counts["exits"] += 1
            late(i, jt, ("spot", "perp") if act[i] == "exit" else ("spot",))
            cash = cash + got
            fee[i] = fee[i] + fe
            slp[i] = slp[i] + sl
            qs[i] = q[i] = w[i] = 0.0

        # 3b. rebalances
        for i in range(n):
            if act[i] != "rebalance":
                continue
            f, jt = F[i], jd[i] + 1
            ps, pp = f["ps"][jt], f["pp"][jt]
            if not pp < _liq_price(q[i], w[i], p0[i], mmr[i]):
                continue
            dq = _rebalance_size(q[i], w[i], p0[i], ps, pp, lev, fs, fp, slip[i])
            if not dq > 0:
                continue
            pnl[i] = pnl[i] + qs[i] * (ps - mark_s[i])
            pnl[i] = pnl[i] - q[i] * (pp - mark_p[i])
            mark_s[i], mark_p[i] = ps, pp
            w[i], fe, sl = _rebalance(dq, w[i], p0[i], ps, pp, fs, fp, slip[i])
            q[i] = q[i] - dq
            qs[i] = qs[i] - dq
            fee[i] = fee[i] + fe
            slp[i] = slp[i] + sl
            fill(date, pairs[i], "spot", -dq, ps, "rebalance", dq * ps * fs, dq * ps * slip[i])
            fill(date, pairs[i], "perp", dq, pp, "rebalance", dq * pp * fp, dq * pp * slip[i])
            counts["rebalances"] += 1
            event(date, pairs[i], "rebalance", value=dq)
            late(i, jt, ("spot", "perp"))

        # 3c. entries: each spends the target, costs included, sharing the free cash pro rata when it falls short
        entering = [i for i in range(n) if act[i] == "enter"]
        total = 0.0
        for i in entering:
            total = total + target
        capped = total > cash
        lam = cash / total if capped else 1.0
        for i in entering:
            f, jt = F[i], jd[i] + 1
            spend = lam * target if capped else target
            if capped:
                counts["capped_entries"] += 1
                event(date, pairs[i], "capped_entry", value=lam)
            if not spend > 0:
                continue
            ps, pp = f["ps"][jt], f["pp"][jt]
            a = _sleeve_amount(spend, ps, pp, x, lev, fs, fp, slip[i])
            qq, ww, fe, sl = _entry(a, ps, pp, x, lev, fs, fp, slip[i])
            cash = cash - a - fe - sl
            qs[i] = q[i] = qq
            w[i], p0[i] = ww, pp
            mark_s[i], mark_p[i] = ps, pp
            fee[i] = fee[i] + fe
            slp[i] = slp[i] + sl
            fill(date, pairs[i], "spot", qq, ps, "enter", x * a * fs, x * a * slip[i])
            fill(date, pairs[i], "perp", -qq, pp, "enter", qq * pp * fp, qq * pp * slip[i])
            counts["entries"] += 1
            late(i, jt, ("spot", "perp"))
        if entering:
            cash = max(cash, 0.0)  # drops a rounding residue below 0 when the entries spend all the free cash

        # 1. funding at or after the perp leg's fill (all of the day's when it does not trade), on the new quantity
        for i in range(n):
            if not live[i]:
                continue
            f, jt = F[i], jd[i] + 1
            total = 0.0
            for e in range(f["ptr"][jt], f["ptr"][jt + 1]):
                if not (perp_trade[i] and f["ev_ts"][e] < f["ms_p"][jt]):
                    total = total + amount(i, e, q[i])
            w[i] = w[i] + total
            fund[i] = fund[i] + total

        # 4. margin: liquidation at the day high, else the rebalance trigger at the close
        for i in range(n):
            if not (live[i] and q[i] > 0):
                pend[i] = False
                continue
            f, jt = F[i], jd[i] + 1
            if _liquidates(q[i], w[i], p0[i], f["ph"][jt], mmr[i]):
                pl = _liq_price(q[i], w[i], p0[i], mmr[i])
                fe = liq_fee * q[i] * pl
                pnl[i] = pnl[i] - q[i] * (pl - mark_p[i])
                mark_p[i] = pl
                w[i] = w[i] + q[i] * (p0[i] - pl) - fe
                fee[i] = fee[i] + fe
                fill(date, pairs[i], "perp", q[i], pl, "liquidation", fe, 0.0)
                counts["liquidations"] += 1
                event(date, pairs[i], "liquidation", leg="perp", value=pl)
                q[i] = 0.0
                blocked[i], pend[i] = True, False
            else:
                pend[i] = _needs_rebalance(q[i], w[i], p0[i], f["pc"][jt], lev, below)

        # 6. mark at the close
        for i in range(n):
            if not live[i]:
                continue
            jt = jd[i] + 1
            pnl[i] = pnl[i] + qs[i] * (F[i]["sc"][jt] - mark_s[i])
            pnl[i] = pnl[i] - q[i] * (F[i]["pc"][jt] - mark_p[i])

        # 5. delisting: closed at the last common candle's close
        for i in range(n):
            if not (live[i] and jd[i] + 1 == F[i]["n"] - 1 and t < end_ms):
                continue
            f, jt = F[i], jd[i] + 1
            cs, cp = f["sc"][jt], f["pc"][jt]
            got, fe, sl = _close(qs[i], q[i], w[i], p0[i], cs, cp, fs, fp, slip[i])
            if qs[i] > 0:
                fill(date, pairs[i], "spot", -qs[i], cs, "delist", qs[i] * cs * fs, qs[i] * cs * slip[i])
            if q[i] > 0:
                fill(date, pairs[i], "perp", q[i], cp, "delist", q[i] * cp * fp, q[i] * cp * slip[i])
            cash = cash + got
            fee[i] = fee[i] + fe
            slp[i] = slp[i] + sl
            qs[i] = q[i] = w[i] = 0.0
            blocked[i] = pend[i] = False
            counts["delists"] += 1
            event(date, pairs[i], "delisting")

        equity = cash
        for i in range(n):
            if not live[i]:
                continue
            jt = jd[i] + 1
            v = _value(qs[i], q[i], w[i], p0[i], F[i]["sc"][jt], F[i]["pc"][jt])
            equity = equity + v
            for c, val in zip(PAIR_COLUMNS, (date, pairs[i], on_today[i], qs[i], -q[i] if q[i] else 0.0, w[i], v,
                                             fund[i])):
                prow[c].append(val)
        tot = {"funding": 0.0, "fees": 0.0, "slippage": 0.0, "basis_pnl": 0.0}
        for i in range(n):
            tot["funding"] = tot["funding"] + fund[i]
            tot["fees"] = tot["fees"] + fee[i]
            tot["slippage"] = tot["slippage"] + slp[i]
            tot["basis_pnl"] = tot["basis_pnl"] + pnl[i]
        row = {"date": date, "day_ms": t, "equity": equity, "ret": equity / e_d - 1.0, "cash": cash,
               "n_on": sum(1 for i in range(n) if q[i] > 0), "n_live": n_live}
        row.update(tot)
        for c in DAILY_COLUMNS:
            daily[c].append(row[c])

    return PathResult(
        daily=_table(daily, DAILY_COLUMNS, ints=("day_ms", "n_on", "n_live")),
        daily_pair=_table(prow, PAIR_COLUMNS, bools=("state",)),
        fills=_table(fills, FILL_COLUMNS),
        events=_table(events, EVENT_COLUMNS),
        counts=counts,
    )


_TEXT = frozenset({"date", "pair", "leg", "reason", "kind", "detail"})


def _table(cols: dict[str, list], names: tuple[str, ...], *, ints=(), bools=()) -> pd.DataFrame:
    """A DataFrame with fixed dtypes, so an empty table has the same columns and types as a full one."""
    def dtype(c):
        return object if c in _TEXT else np.int64 if c in ints else bool if c in bools else np.float64
    return pd.DataFrame({c: np.asarray(cols[c], dtype=dtype(c)) for c in names})
