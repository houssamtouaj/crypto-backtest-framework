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
   ``ΔE = funding − fees − slippage + basis_pnl``; ``daily_pair`` carries each pair's share of the four.
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
PAIR_COLUMNS = (  # fees, slippage and basis_pnl per pair sum to the daily columns (carry Phase 4 §4.4)
    "date", "pair", "state", "spot_qty", "perp_qty", "perp_wallet", "sleeve_value", "funding", "fees", "slippage",
    "basis_pnl",
)
FILL_COLUMNS = ("date", "pair", "leg", "qty", "price", "reason", "fee", "slippage")
EVENT_COLUMNS = ("date", "pair", "kind", "leg", "detail", "value")
_MIN_MS = 60_000
CAP_TOL = 1e-12  # n × (E / n) can exceed E by an ulp; that is not a capped entry


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
    last = max(int(pf.day_ms[-1]) for pf in frames.values())
    if last < date_ms(cfg.end):
        raise ValueError(f"no frame reaches the period end {cfg.end} (the latest ends {_iso(last)}): the frames were "
                         "loaded through an earlier end, and every pair would read as delisted")
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
        capped = total > cash * (1.0 + CAP_TOL)
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
                                             fund[i], fee[i], slp[i], pnl[i])):
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


# --- the batched form ------------------------------------------------------------------------------------------

@dataclass(frozen=True, eq=False)
class _Aligned:
    """Every pair on the return days, ``(pairs, days)``; NaN / False / -1 where the pair is not live.

    ``mark_*`` are the decision day's closes, ``c_*`` / ``h_perp`` the return day's; ``ps`` / ``pp`` and
    ``ms_*`` the late-fill prices and times; ``ev_*`` the return day's funding events padded to ``K`` per day
    (``ev_px`` already falls back to ``funding_px_prev``, ``ev_fb`` marks where it did).
    """

    live: np.ndarray
    jd: np.ndarray
    ps: np.ndarray
    pp: np.ndarray
    ms_s: np.ndarray
    ms_p: np.ndarray
    mark_s: np.ndarray
    mark_p: np.ndarray
    c_spot: np.ndarray
    c_perp: np.ndarray
    h_perp: np.ndarray
    delist: np.ndarray
    ev_ts: np.ndarray
    ev_rate: np.ndarray
    ev_px: np.ndarray
    ev_fb: np.ndarray


def _align(frames: dict[str, PairFrame], cfg: ExecSpec, days: np.ndarray) -> _Aligned:
    pairs = tuple(frames)
    n, T = len(pairs), len(days)
    h = cfg.rebalance.fill_offset_min
    live, jd = np.zeros((n, T), dtype=bool), np.full((n, T), -1, dtype=np.int64)
    ps, pp, ms_s, ms_p, mark_s, mark_p, c_spot, c_perp, h_perp = (np.full((n, T), np.nan) for _ in range(9))
    delist = np.zeros((n, T), dtype=bool)
    per_day = []
    for i, pair in enumerate(pairs):
        pf = frames[pair]
        j = (days - DAY_MS - pf.day_ms[0]) // DAY_MS
        ok = (j >= 0) & (j + 1 < len(pf))
        jj = j[ok]
        jt = jj + 1
        live[i], jd[i, ok] = ok, jj
        ps[i, ok], pp[i, ok] = pf.fill_px_late["spot"][h][jt], pf.fill_px_late["perp"][h][jt]
        ms_s[i, ok], ms_p[i, ok] = pf.fill_late_ms["spot"][h][jt], pf.fill_late_ms["perp"][h][jt]
        mark_s[i, ok], mark_p[i, ok] = pf.spot.c[jj], pf.perp.c[jj]
        c_spot[i, ok], c_perp[i, ok], h_perp[i, ok] = pf.spot.c[jt], pf.perp.c[jt], pf.perp.h[jt]
        delist[i, ok] = (jt == len(pf) - 1) & (days[ok] < days[-1])
        per_day.append(pf.perp.funding_ptr[jt + 1] - pf.perp.funding_ptr[jt])
    K = max([1] + [int(c.max()) for c in per_day if len(c)])
    ev_ts = np.full((n, T, K), np.inf)
    ev_rate, ev_px = np.zeros((n, T, K)), np.zeros((n, T, K))
    ev_fb = np.zeros((n, T, K), dtype=bool)
    for i, pair in enumerate(pairs):
        pf = frames[pair]
        cols = np.flatnonzero(live[i])
        start = pf.perp.funding_ptr[jd[i, cols] + 1]
        for k in range(K):
            sel = per_day[i] > k
            idx = start[sel] + k
            raw = pf.perp.funding_px[idx]
            fb = np.isnan(raw)
            ev_ts[i, cols[sel], k] = pf.perp.funding_ts[idx]
            ev_rate[i, cols[sel], k] = pf.perp.funding_rate[idx]
            ev_px[i, cols[sel], k] = np.where(fb, pf.funding_px_prev[idx], raw)
            ev_fb[i, cols[sel], k] = fb
    return _Aligned(live, jd, ps, pp, ms_s, ms_p, mark_s, mark_p, c_spot, c_perp, h_perp, delist, ev_ts, ev_rate,
                    ev_px, ev_fb)


def _held_amounts(held: np.ndarray, rate: np.ndarray, q: np.ndarray, px: np.ndarray) -> np.ndarray:
    """``rate × q × px`` per event where a quantity is held; refuses an unpriced event with a holding, as run_path."""
    amt = np.where(held, rate[None] * q[:, :, None] * px[None], 0.0)
    if np.isnan(amt).any():
        raise AssertionError("a funding event has no price while a quantity is held")
    return amt


def run_batch(frames: dict[str, PairFrame], state_batch: dict[str, np.ndarray], cfg: ExecSpec) -> BatchResult:
    """``P`` on/off paths at once (``state_batch[pair]`` bool ``(P, len(frame))``); equals ``run_path`` per path.

    The same steps as ``run_path`` on ``(paths, pairs)`` arrays with masks; sums over pairs and funding events
    run in the same order, so the two evaluate the same floating-point expressions.
    """
    P = _check(frames, state_batch, cfg, ndim=2)
    pairs = tuple(frames)
    n = len(pairs)
    h = cfg.rebalance.fill_offset_min
    x, lev, fs, fp, slip, mmr = _pair_costs(cfg, pairs)
    slip, mmr = np.array(slip), np.array(mmr)
    liq_fee, below = cfg.margin.liquidation_fee, cfg.margin.rebalance_below
    days = _return_days(cfg)
    T = len(days)
    A = _align(frames, cfg, days)
    on_all = np.zeros((P, n, T), dtype=bool)
    for i, pair in enumerate(pairs):
        cols = np.flatnonzero(A.live[i])
        on_all[:, i, cols] = np.asarray(state_batch[pair])[:, A.jd[i, cols]]
    K = A.ev_ts.shape[2]

    def zeros():
        return np.zeros((P, n))

    qs, q, w, p0 = zeros(), zeros(), zeros(), zeros()
    blocked, pend = np.zeros((P, n), dtype=bool), np.zeros((P, n), dtype=bool)
    cash = np.full(P, float(cfg.equity0))
    equity = cash.copy()
    out = {c: np.zeros((P, T)) for c in ("equity", "ret", "cash", "funding", "fees", "slippage", "basis_pnl")}
    n_on = np.zeros((P, T), dtype=np.int64)
    counts = {c: np.zeros(P, dtype=np.int64) for c in COUNTS}
    n_live_all = A.live.sum(axis=0).astype(np.int64)

    with np.errstate(divide="ignore", invalid="ignore"):  # unmasked lanes (flat pairs, missing fills) give inf/NaN
        for k in range(T):
            lv = A.live[:, k]
            n_live = int(n_live_all[k])
            e_d = equity
            target = e_d / n_live if n_live else np.zeros(P)
            late_at = int(days[k]) + h * _MIN_MS
            ps, pp = A.ps[:, k], A.pp[:, k]
            mark_s = np.broadcast_to(A.mark_s[:, k], (P, n)).copy()
            mark_p = np.broadcast_to(A.mark_p[:, k], (P, n)).copy()

            # 2. the decision against the holding
            on = on_all[:, :, k]
            blocked = blocked & (on | ~lv)
            want = on & ~blocked
            orphan = (qs > 0) & (q == 0)
            hedged = q > 0
            flat = ~orphan & ~hedged
            s_ok = ~np.isnan(ps)
            b_ok = s_ok & ~np.isnan(pp)
            sell_w, exit_w, reb_w, ent_w = orphan & lv, hedged & ~want, hedged & want & pend, flat & want
            sell, ex, rebp, ent = sell_w & s_ok, exit_w & b_ok, reb_w & b_ok, ent_w & b_ok
            counts["postponed"] += ((sell_w & ~s_ok) | ((exit_w | reb_w | ent_w) & ~b_ok)).sum(axis=1)
            perp_trade = ex | rebp | ent

            # 1. funding before the perp leg's fill, on the old quantity
            ts, rate, px, fb = A.ev_ts[:, k], A.ev_rate[:, k], A.ev_px[:, k], A.ev_fb[:, k]
            pre = perp_trade[:, :, None] & (ts < A.ms_p[:, k][:, None])[None]
            held = (q != 0)[:, :, None]
            amt = _held_amounts(held, rate, q, px)
            f_pre = zeros()
            for e in range(K):
                f_pre = f_pre + np.where(pre[:, :, e], amt[:, :, e], 0.0)
            counts["funding_px_fallbacks"] += (pre & fb[None] & held).sum(axis=(1, 2))
            w = w + f_pre

            # 3a. exits and post-liquidation spot sales
            a_m = sell | ex
            qp_c, pp_c = np.where(ex, q, 0.0), np.where(ex, pp, 0.0)
            pnl = np.where(a_m, qs * (ps - mark_s), 0.0)
            mark_s = np.where(a_m, ps, mark_s)
            pnl = pnl - np.where(ex, qp_c * (pp - mark_p), 0.0)
            mark_p = np.where(ex, pp, mark_p)
            got, fe, sl = _close(qs, qp_c, w, p0, ps, pp_c, fs, fp, slip)
            for i in range(n):
                cash = cash + np.where(a_m[:, i], got[:, i], 0.0)
            fee, slp = np.where(a_m, fe, 0.0), np.where(a_m, sl, 0.0)
            late_s, late_p = A.ms_s[:, k] > late_at, A.ms_p[:, k] > late_at
            counts["late_fills"] += (a_m & late_s).sum(axis=1) + (ex & late_p).sum(axis=1)
            counts["exits"] += ex.sum(axis=1)
            qs, q, w = np.where(a_m, 0.0, qs), np.where(a_m, 0.0, q), np.where(a_m, 0.0, w)

            # 3b. rebalances
            pl = _liq_price(q, w, p0, mmr)
            dq = _rebalance_size(q, w, p0, ps, pp, lev, fs, fp, slip)
            reb = rebp & (pp < pl) & (dq > 0)
            pnl = pnl + np.where(reb, qs * (ps - mark_s), 0.0)
            pnl = pnl - np.where(reb, q * (pp - mark_p), 0.0)
            mark_s, mark_p = np.where(reb, ps, mark_s), np.where(reb, pp, mark_p)
            w_new, fe, sl = _rebalance(dq, w, p0, ps, pp, fs, fp, slip)
            w, q, qs = np.where(reb, w_new, w), np.where(reb, q - dq, q), np.where(reb, qs - dq, qs)
            fee, slp = fee + np.where(reb, fe, 0.0), slp + np.where(reb, sl, 0.0)
            counts["rebalances"] += reb.sum(axis=1)
            counts["late_fills"] += (reb & late_s).sum(axis=1) + (reb & late_p).sum(axis=1)

            # 3c. entries
            total = np.zeros(P)
            for i in range(n):
                total = total + np.where(ent[:, i], target, 0.0)
            capped = total > cash * (1.0 + CAP_TOL)
            lam = np.where(capped, cash / np.where(capped, total, 1.0), 1.0)
            spend = np.where(capped[:, None], lam[:, None] * target[:, None], target[:, None])
            counts["capped_entries"] += (ent & capped[:, None]).sum(axis=1)
            traded = ent & (spend > 0)
            a = _sleeve_amount(spend, ps, pp, x, lev, fs, fp, slip)
            qq, ww, fe, sl = _entry(a, ps, pp, x, lev, fs, fp, slip)
            for i in range(n):
                t_i = traded[:, i]
                cash = cash - np.where(t_i, a[:, i], 0.0) - np.where(t_i, fe[:, i], 0.0) - np.where(t_i, sl[:, i], 0.0)
            cash = np.where(ent.any(axis=1), np.maximum(cash, 0.0), cash)
            q, qs = np.where(traded, qq, q), np.where(traded, qq, qs)
            w, p0 = np.where(traded, ww, w), np.where(traded, pp, p0)
            mark_s, mark_p = np.where(traded, ps, mark_s), np.where(traded, pp, mark_p)
            fee, slp = fee + np.where(traded, fe, 0.0), slp + np.where(traded, sl, 0.0)
            counts["entries"] += traded.sum(axis=1)
            counts["late_fills"] += (traded & late_s).sum(axis=1) + (traded & late_p).sum(axis=1)

            # 1. funding at or after the perp leg's fill, on the new quantity
            held = (q != 0)[:, :, None]
            amt = _held_amounts(held, rate, q, px)
            f_post = zeros()
            for e in range(K):
                f_post = f_post + np.where(pre[:, :, e], 0.0, amt[:, :, e])
            counts["funding_px_fallbacks"] += (~pre & fb[None] & held).sum(axis=(1, 2))
            w = w + f_post
            fund = f_pre + f_post

            # 4. margin
            hedged = q > 0
            c_s, c_p = A.c_spot[:, k], A.c_perp[:, k]
            liq = hedged & _liquidates(q, w, p0, A.h_perp[:, k], mmr)
            pl = _liq_price(q, w, p0, mmr)
            fe = liq_fee * q * pl
            pnl = pnl - np.where(liq, q * (pl - mark_p), 0.0)
            mark_p = np.where(liq, pl, mark_p)
            w = np.where(liq, w + q * (p0 - pl) - fe, w)
            fee = fee + np.where(liq, fe, 0.0)
            pend = hedged & ~liq & _needs_rebalance(q, w, p0, c_p, lev, below)
            q = np.where(liq, 0.0, q)
            blocked = blocked | liq
            counts["liquidations"] += liq.sum(axis=1)

            # 6. mark at the close
            pnl = pnl + np.where(lv, qs * (c_s - mark_s), 0.0)
            pnl = pnl - np.where(lv, q * (c_p - mark_p), 0.0)

            # 5. delisting
            dl = A.delist[:, k]
            if dl.any():
                got, fe, sl = _close(qs, q, w, p0, c_s, c_p, fs, fp, slip)
                for i in np.flatnonzero(dl):
                    cash = cash + got[:, i]
                fee, slp = fee + np.where(dl, fe, 0.0), slp + np.where(dl, sl, 0.0)
                qs, q, w = np.where(dl, 0.0, qs), np.where(dl, 0.0, q), np.where(dl, 0.0, w)
                blocked, pend = blocked & ~dl, pend & ~dl
                counts["delists"] += int(dl.sum())

            val = _value(qs, q, w, p0, c_s, c_p)
            equity = cash
            for i in np.flatnonzero(lv):
                equity = equity + val[:, i]
            tot = {c: np.zeros(P) for c in ("funding", "fees", "slippage", "basis_pnl")}
            for i in range(n):
                tot["funding"] = tot["funding"] + fund[:, i]
                tot["fees"] = tot["fees"] + fee[:, i]
                tot["slippage"] = tot["slippage"] + slp[:, i]
                tot["basis_pnl"] = tot["basis_pnl"] + pnl[:, i]
            out["equity"][:, k], out["ret"][:, k], out["cash"][:, k] = equity, equity / e_d - 1.0, cash
            for c, v in tot.items():
                out[c][:, k] = v
            n_on[:, k] = (q > 0).sum(axis=1)

    return BatchResult(
        day_ms=days, n_live=n_live_all, equity=out["equity"], ret=out["ret"], cash=out["cash"],
        funding=out["funding"], fees=out["fees"], slippage=out["slippage"], basis_pnl=out["basis_pnl"], n_on=n_on,
        counts=counts,
    )
