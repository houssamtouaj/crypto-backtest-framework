"""Validation of an ETF fetch (ETF spec Phase 0 §0.4): rules 1-7 on in-memory frames.

``validate(data, cfg)`` returns one ``RuleResult`` per rule; a fetch passes
only when every rule has no failures, and each failure names the ticker and
date. ``validate_store`` re-runs the rules on the stored snapshot (it reads
the holdout, so it is one of the three ETF modules allowed
``allow_holdout=True``).

The cross-check (rule 6) is against Nasdaq (Phase 0 §0.9): Stooq serves a
bot check. Nasdaq serves about ten years of daily prices and, for
Nasdaq-listed symbols only, a dividend history; where it has none, rules 4
and 5 are the only dividend checks and the rule's info says so. The rules
run on the data after the reviewed corrections (``etf/corrections.py``);
rule 6 lists the accepted differences instead of failing on them.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from perpbt.data.store import date_ms
from perpbt.etf.config import EtfDataConfig
from perpbt.etf.corrections import file_sha256, load_corrections
from perpbt.etf.store import EtfData, EtfStore, StoreRefused, day_iso

# NYSE full-day closures (holidays and special closures: 2007-01-02 President Ford, 2012-10-29/30 Hurricane
# Sandy, 2018-12-05 President G. H. W. Bush, 2025-01-09 President Carter). Checked against the weekdays the
# Yahoo SPY history lacks, 2004-11-18..2026-10-23: equal.
_CLOSED_BY_YEAR = {
    2004: ("01-01", "01-19", "02-16", "04-09", "05-31", "06-11", "07-05", "09-06", "11-25", "12-24"),
    2005: ("01-17", "02-21", "03-25", "05-30", "07-04", "09-05", "11-24", "12-26"),
    2006: ("01-02", "01-16", "02-20", "04-14", "05-29", "07-04", "09-04", "11-23", "12-25"),
    2007: ("01-01", "01-02", "01-15", "02-19", "04-06", "05-28", "07-04", "09-03", "11-22", "12-25"),
    2008: ("01-01", "01-21", "02-18", "03-21", "05-26", "07-04", "09-01", "11-27", "12-25"),
    2009: ("01-01", "01-19", "02-16", "04-10", "05-25", "07-03", "09-07", "11-26", "12-25"),
    2010: ("01-01", "01-18", "02-15", "04-02", "05-31", "07-05", "09-06", "11-25", "12-24"),
    2011: ("01-17", "02-21", "04-22", "05-30", "07-04", "09-05", "11-24", "12-26"),
    2012: ("01-02", "01-16", "02-20", "04-06", "05-28", "07-04", "09-03", "10-29", "10-30", "11-22", "12-25"),
    2013: ("01-01", "01-21", "02-18", "03-29", "05-27", "07-04", "09-02", "11-28", "12-25"),
    2014: ("01-01", "01-20", "02-17", "04-18", "05-26", "07-04", "09-01", "11-27", "12-25"),
    2015: ("01-01", "01-19", "02-16", "04-03", "05-25", "07-03", "09-07", "11-26", "12-25"),
    2016: ("01-01", "01-18", "02-15", "03-25", "05-30", "07-04", "09-05", "11-24", "12-26"),
    2017: ("01-02", "01-16", "02-20", "04-14", "05-29", "07-04", "09-04", "11-23", "12-25"),
    2018: ("01-01", "01-15", "02-19", "03-30", "05-28", "07-04", "09-03", "11-22", "12-05", "12-25"),
    2019: ("01-01", "01-21", "02-18", "04-19", "05-27", "07-04", "09-02", "11-28", "12-25"),
    2020: ("01-01", "01-20", "02-17", "04-10", "05-25", "07-03", "09-07", "11-26", "12-25"),
    2021: ("01-01", "01-18", "02-15", "04-02", "05-31", "07-05", "09-06", "11-25", "12-24"),
    2022: ("01-17", "02-21", "04-15", "05-30", "06-20", "07-04", "09-05", "11-24", "12-26"),
    2023: ("01-02", "01-16", "02-20", "04-07", "05-29", "06-19", "07-04", "09-04", "11-23", "12-25"),
    2024: ("01-01", "01-15", "02-19", "03-29", "05-27", "06-19", "07-04", "09-02", "11-28", "12-25"),
    2025: ("01-01", "01-09", "01-20", "02-17", "04-18", "05-26", "06-19", "07-04", "09-01", "11-27", "12-25"),
    2026: ("01-01", "01-19", "02-16", "04-03", "05-25", "06-19", "07-03", "09-07", "11-26", "12-25"),
    2027: ("01-01", "01-18", "02-15", "03-26", "05-31", "06-18", "07-05", "09-06", "11-25", "12-24"),
}
NYSE_CLOSED = frozenset(f"{y}-{md}" for y, mds in _CLOSED_BY_YEAR.items() for md in mds)
CLOSED_LIST_END = f"{max(_CLOSED_BY_YEAR)}-12-31"

# ticker -> the first calendar year checked for 4 +- 1 dividends; QQQ paid annually until 2004 and twice in 2005
QUARTERLY_FROM = {"SPY": 2005, "QQQ": 2006, "IWM": 2005}
NON_PAYERS = ("GLD",)
DIVIDENDS_PER_YEAR = (3, 5)  # 4 +- 1 (special distributions happen)
MAX_MOVE = 0.25  # day-over-day close move without a split
PRICE_NOISE = 1e-6  # relative slack on low <= open/close <= high: Yahoo's prices carry float32 noise
ADJ_TOL = 0.0005  # rule 5: |total return - Adj Close return| on ADJ_SHARE of the days ...
ADJ_SHARE = 0.995
ADJ_TOL_MAX = 0.005  # ... and on every day
XCHECK_REPORT = 0.001  # rule 6: the share of common days over this is reported
XCHECK_MIN_COVERAGE = 0.99  # rule 6: the cross-check source has at least this share of Yahoo's days in its span
DIVIDEND_MATCH_REL = 0.005  # rule 6: dividend amounts agree within this (relative) ...
DIVIDEND_MATCH_ABS = 1e-4  # ... or this (per share)
RATE_RANGE = (-1.0, 10.0)  # DTB3 printed -0.01..-0.05 in 2015 and March 2020
RATE_MAX_GAP_BDAYS = 5
BASES = ("raw", "adjusted")

__all__ = [
    "BASES", "NYSE_CLOSED", "RuleResult", "detect_basis", "format_table", "total_return", "validate",
    "validate_store",
]


@dataclass(frozen=True)
class RuleResult:
    rule: str
    failures: tuple[str, ...]
    info: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failures

    def as_dict(self) -> dict:
        return {"rule": self.rule, "ok": self.ok, "failures": list(self.failures), "info": self.info}


def total_return(o: np.ndarray, c: np.ndarray, div: np.ndarray) -> np.ndarray:
    """Close-to-close total return, Phase 1 §1.2: ``(C_d + D_d C_d / O_d) / C_{d-1} - 1``; the first is NaN."""
    o, c, div = (np.asarray(x, dtype=np.float64) for x in (o, c, div))
    out = np.full(c.shape, np.nan)
    out[1:] = (c[1:] + div[1:] * c[1:] / o[1:]) / c[:-1] - 1.0
    return out


def _joined(data: EtfData, ticker: str) -> pd.DataFrame:
    """Prices with the actions (0 where none) of one ticker."""
    out = data.prices[ticker].merge(data.actions[ticker], on="day_ms", how="left")
    cols = ["dividend", "split", "capital_gain"]
    out[cols] = out[cols].fillna(0.0)
    return out


def _weekdays(lo: date, hi: date) -> list[date]:
    return [lo + timedelta(k) for k in range((hi - lo).days + 1) if (lo + timedelta(k)).weekday() < 5]


def _d(ms: int) -> date:
    return date.fromisoformat(day_iso(ms))


# --- rules -----------------------------------------------------------------------------------------------------

def _rule_calendar(data: EtfData, cfg: EtfDataConfig) -> RuleResult:
    fails: list[str] = []
    start = date_ms(cfg.warmup_start)
    days = {t: set(data.prices[t]["day_ms"].tolist()) for t in data.tickers}
    for t in data.tickers:
        first = data.prices[t]["day_ms"].iloc[0] if len(data.prices[t]) else None
        if first != start:
            fails.append(f"{t}: first date {None if first is None else day_iso(first)}, expected {cfg.warmup_start}")
    union = set().union(*days.values()) if days else set()
    for t in data.tickers:
        fails += [f"{t} {day_iso(d)}: missing (another ticker has prices)" for d in sorted(union - days[t])]
    info = {}
    if union:
        lo, hi = _d(min(union)), _d(max(union))
        if hi.isoformat() > CLOSED_LIST_END:
            fails.append(f"data end {hi}: the NYSE closure list ends {CLOSED_LIST_END}; extend it")
        traded = {_d(d) for d in union}
        for d in _weekdays(lo, hi):
            if d not in traded and d.isoformat() not in NYSE_CLOSED:
                fails.append(f"{d}: a weekday without prices that is not a listed NYSE closure")
        for d in sorted(traded):
            if d.weekday() >= 5:
                fails.append(f"{d}: prices on a weekend")
            elif d.isoformat() in NYSE_CLOSED:
                fails.append(f"{d}: prices on a listed NYSE closure")
        info = {"first": lo.isoformat(), "last": hi.isoformat(),
                "rows": {t: len(data.prices[t]) for t in data.tickers}}
    return RuleResult("1 calendar", tuple(fails), info)


def _rule_prices(data: EtfData) -> RuleResult:
    fails: list[str] = []
    for t in data.tickers:
        f = _joined(data, t)
        px = f[["open", "high", "low", "close", "adj_close"]].to_numpy()
        bad = ~(np.isfinite(px) & (px > 0)).all(axis=1)
        fails += [f"{t} {day_iso(d)}: a price is not positive and finite" for d in f["day_ms"][bad]]
        o, h, lo, c = (f[k].to_numpy() for k in ("open", "high", "low", "close"))
        with np.errstate(invalid="ignore"):
            band = (lo > np.minimum(o, c) * (1 + PRICE_NOISE)) | (h < np.maximum(o, c) * (1 - PRICE_NOISE))
            move = np.zeros(len(c), dtype=bool)
            move[1:] = (np.abs(c[1:] / c[:-1] - 1) > MAX_MOVE) & (f["split"].to_numpy()[1:] == 0)
        fails += [f"{t} {day_iso(d)}: low/high do not bracket open and close" for d in f["day_ms"][band & ~bad]]
        fails += [f"{t} {day_iso(d)}: close moved more than {MAX_MOVE:.0%} without a split" for d in f["day_ms"][move]]
    return RuleResult("2 prices", tuple(fails))


def _rule_splits(data: EtfData) -> RuleResult:
    fails: list[str] = []
    splits = {}
    for t in data.tickers:
        f = _joined(data, t)
        c, s = f["close"].to_numpy(), f["split"].to_numpy()
        for k in np.flatnonzero(s != 0):
            d = day_iso(f["day_ms"].iloc[k])
            splits.setdefault(t, []).append({"date": d, "ratio": float(s[k])})
            if not (math.isfinite(s[k]) and s[k] > 0):
                fails.append(f"{t} {d}: split ratio {s[k]!r} is not positive")
            if k == 0 or not abs(c[k] / c[k - 1] - 1) < MAX_MOVE:
                fails.append(f"{t} {d}: the split-adjusted close is not continuous across the split")
    return RuleResult("3 splits", tuple(fails), {"splits": splits})


def _rule_dividends(data: EtfData) -> RuleResult:
    fails: list[str] = []
    counts: dict[str, dict[str, int]] = {}
    for t in data.tickers:
        a = data.actions[t]
        day, div, cg = a["day_ms"].to_numpy(), a["dividend"].to_numpy(), a["capital_gain"].to_numpy()
        for k in np.flatnonzero(~np.isfinite(div) | (div < 0)):
            fails.append(f"{t} {day_iso(day[k])}: dividend {div[k]!r} is not a non-negative number")
        for k in np.flatnonzero(cg != 0):
            fails.append(f"{t} {day_iso(day[k])}: capital gain distribution {cg[k]!r} (the total return omits it)")
        paid = day[div > 0]
        if t in NON_PAYERS:
            fails += [f"{t} {day_iso(d)}: a dividend, but {t} pays none" for d in paid]
            continue
        if t not in QUARTERLY_FROM:
            fails.append(f"{t}: no dividend expectation for this ticker")
            continue
        years = pd.Series([_d(d).year for d in paid], dtype="int64").value_counts()
        last_full = _d(data.prices[t]["day_ms"].iloc[-1]).year - 1
        counts[t] = {str(y): int(years.get(y, 0)) for y in range(QUARTERLY_FROM[t], last_full + 1)}
        lo, hi = DIVIDENDS_PER_YEAR
        fails += [f"{t} {y}: {n} dividends, expected {lo}..{hi}" for y, n in counts[t].items() if not lo <= n <= hi]
    return RuleResult("4 dividends", tuple(fails), {"per_year": counts})


def _rule_adj_close(data: EtfData) -> RuleResult:
    fails: list[str] = []
    info = {}
    for t in data.tickers:
        f = _joined(data, t)
        r = total_return(f["open"], f["close"], f["dividend"])
        adj = f["adj_close"].to_numpy()
        a = np.full(len(adj), np.nan)
        a[1:] = adj[1:] / adj[:-1] - 1
        d = np.abs(r - a)[1:]
        if not len(d):
            continue
        share = float(np.mean(d <= ADJ_TOL))
        info[t] = {"share_within": share, "max": float(np.nanmax(d))}
        if share < ADJ_SHARE:
            fails.append(f"{t}: total return and Adj Close return agree within {ADJ_TOL} on {share:.2%} of days, "
                         f"expected >= {ADJ_SHARE:.1%}")
        for k in np.flatnonzero(~(d <= ADJ_TOL_MAX)):
            fails.append(f"{t} {day_iso(f['day_ms'].iloc[k + 1])}: total return {r[k + 1]:.5f} vs Adj Close "
                         f"return {a[k + 1]:.5f}")
    return RuleResult("5 adj close", tuple(fails), info)


def detect_basis(prices: pd.DataFrame, xprices: pd.DataFrame) -> str:
    """``raw`` when the cross-check closes sit nearer Yahoo's ``close`` than its ``adj_close``, else ``adjusted``."""
    m = prices.merge(xprices, on="day_ms", suffixes=("", "_x"))
    if not len(m):
        raise ValueError("detect_basis: no common days")
    to_close = np.nanmedian(np.abs(m["close_x"] / m["close"] - 1))
    to_adj = np.nanmedian(np.abs(m["close_x"] / m["adj_close"] - 1))
    return "raw" if to_close <= to_adj else "adjusted"


def _rule_cross_check(data: EtfData, cfg: EtfDataConfig, basis: Mapping[str, str],
                      accepted: AbstractSet[tuple[str, str, str]]) -> RuleResult:
    fails: list[str] = []
    info: dict[str, dict] = {}
    tol = cfg.cross_check.close_tol
    seen: list[tuple[str, str, str]] = []

    def differ(t: str, day: int, check: str, msg: str) -> None:
        """One difference: accepted (listed in the info) or a failure."""
        key = (t, day_iso(day), check)
        if key in accepted:
            seen.append(key)
        else:
            fails.append(f"{t} {day_iso(day)}: {msg}")

    for t in data.tickers:
        f = _joined(data, t)
        x = data.xprices[t]
        lo, hi = f["day_ms"].iloc[0], f["day_ms"].iloc[-1]
        x = x[(x["day_ms"] >= lo) & (x["day_ms"] <= hi)]
        if not len(x):
            fails.append(f"{t}: no cross-check prices inside the Yahoo span")
            continue
        yahoo_days = set(f["day_ms"].tolist())
        fails += [f"{t} {day_iso(d)}: a cross-check day Yahoo does not have" for d in x["day_ms"]
                  if d not in yahoo_days]
        span = f[(f["day_ms"] >= x["day_ms"].iloc[0]) & (f["day_ms"] <= x["day_ms"].iloc[-1])]
        coverage = float(span["day_ms"].isin(x["day_ms"]).mean())
        if coverage < XCHECK_MIN_COVERAGE:
            fails.append(f"{t}: the cross-check has {coverage:.2%} of Yahoo's days in its span, "
                         f"expected >= {XCHECK_MIN_COVERAGE:.0%}")
        b = basis[t]
        f = f.assign(ret=total_return(f["open"], f["close"], f["dividend"]), pos=np.arange(len(f)))
        m = f.merge(x, on="day_ms", suffixes=("", "_x"))
        if b == "raw":
            diff = np.abs(m["close_x"] / m["close"] - 1).to_numpy()
            what = "close"
            odiff = np.abs(m["open_x"] / m["open"] - 1).to_numpy()
            for k in np.flatnonzero(np.isfinite(odiff) & (odiff > tol)):
                differ(t, m["day_ms"].iloc[k], "open", f"open differs by {odiff[k]:.4%} (raw basis)")
            open_info = {"open_days": int(np.isfinite(odiff).sum()), "open_max": float(np.nanmax(odiff))
                         if np.isfinite(odiff).any() else None}
        else:
            consecutive = np.zeros(len(m), dtype=bool)
            consecutive[1:] = np.diff(m["pos"].to_numpy()) == 1
            xr = np.full(len(m), np.nan)
            xr[1:] = m["close_x"].to_numpy()[1:] / m["close_x"].to_numpy()[:-1] - 1
            diff = np.where(consecutive, np.abs(xr - m["ret"].to_numpy()), np.nan)
            what = "daily return"
            open_info = {}
        for k in np.flatnonzero(np.isfinite(diff) & (diff > tol)):
            differ(t, m["day_ms"].iloc[k], "close", f"{what} differs by {diff[k]:.4%} ({b} basis)")
        nonfinite = int((~np.isfinite(m["close_x"].to_numpy())).sum())
        if nonfinite:
            fails.append(f"{t}: {nonfinite} cross-check closes are not finite")
        ok = diff[np.isfinite(diff)]
        info[t] = {"basis": b, "first": day_iso(x["day_ms"].iloc[0]), "last": day_iso(x["day_ms"].iloc[-1]),
                   "common_days": int(len(ok)), "coverage": coverage,
                   "share_over_0.1pct": float(np.mean(ok > XCHECK_REPORT)) if len(ok) else None,
                   "max": float(ok.max()) if len(ok) else None, **open_info}
        xd = data.xdividends[t]
        if xd is None:
            info[t]["dividends"] = "no independent source (Nasdaq serves none for this symbol)"
            continue
        ydiv = f.loc[f["dividend"] > 0, ["day_ms", "dividend"]]
        xd = xd[(xd["day_ms"] >= lo) & (xd["day_ms"] <= hi)]
        if not len(xd):
            info[t]["dividends"] = "no independent dividends inside the Yahoo span"
            continue
        ywin = ydiv[(ydiv["day_ms"] >= xd["day_ms"].iloc[0]) & (ydiv["day_ms"] <= xd["day_ms"].iloc[-1])]
        yd = dict(zip(ywin["day_ms"].tolist(), ywin["dividend"].tolist()))
        matched = 0
        for d, amount in zip(xd["day_ms"].tolist(), xd["dividend"].tolist()):
            if d not in yd:
                differ(t, d, "dividend", f"the cross-check has a {amount} dividend, Yahoo has none")
            elif not abs(yd[d] - amount) <= max(DIVIDEND_MATCH_ABS, DIVIDEND_MATCH_REL * amount):
                differ(t, d, "dividend", f"dividend {yd[d]} (Yahoo) vs {amount} (cross-check)")
            else:
                matched += 1
        xdays = set(xd["day_ms"].tolist())
        for d in yd:
            if d not in xdays:
                differ(t, d, "dividend", "Yahoo has a dividend the cross-check lacks")
        info[t]["dividends"] = {"first": day_iso(xd["day_ms"].iloc[0]), "last": day_iso(xd["day_ms"].iloc[-1]),
                                "matched": matched, "cross_check": int(len(xd)), "yahoo": int(len(yd))}
    fails += [f"{t} {d}: accepted {check} difference no longer occurs; review the corrections file"
              for t, d, check in sorted(set(accepted) - set(seen))]
    info["accepted"] = [f"{t} {d} {check}" for t, d, check in sorted(set(seen))]
    return RuleResult("6 cross-check", tuple(fails), info)


def _rule_rates(data: EtfData, cfg: EtfDataConfig) -> RuleResult:
    fails: list[str] = []
    r = data.rates
    if not len(r):
        return RuleResult("7 rates", ("no rates",))
    days = sorted(set().union(*(set(data.prices[t]["day_ms"].tolist()) for t in data.tickers)))
    first, last = r["day_ms"].iloc[0], r["day_ms"].iloc[-1]
    if first > date_ms(cfg.warmup_start):
        fails.append(f"rates start {day_iso(first)}, after warmup_start {cfg.warmup_start}")
    need = days[-2] if len(days) >= 2 else None  # the last cash day uses the rate of the trading day before it
    if need is not None and last < need:
        fails.append(f"rates end {day_iso(last)}, before {day_iso(need)} (the trading day before the last price day)")
    v = r["rate_pct"].to_numpy()
    lo, hi = RATE_RANGE
    fails += [f"{day_iso(d)}: rate {x!r} outside [{lo}, {hi}]" for d, x in zip(r["day_ms"], v)
              if not (math.isfinite(x) and lo <= x <= hi)]
    ds = [_d(d) for d in r["day_ms"]]
    for a, b in zip(ds, ds[1:]):
        gap = len(_weekdays(a + timedelta(1), b - timedelta(1)))
        if gap > RATE_MAX_GAP_BDAYS:
            fails.append(f"{a}..{b}: {gap} business days without a rate")
    return RuleResult("7 rates", tuple(fails), {"first": day_iso(first), "last": day_iso(last), "rows": int(len(r))})


# --- entry points ----------------------------------------------------------------------------------------------

def validate(data: EtfData, cfg: EtfDataConfig, *, basis: Mapping[str, str] | None = None,
             accepted: AbstractSet[tuple[str, str, str]] = frozenset()
             ) -> tuple[list[RuleResult], dict[str, str]]:
    """Rules 1-7 and the cross-check basis used (detected per ticker unless ``basis`` gives the recorded one).

    ``accepted`` holds the reviewed ``(ticker, ISO date, check)`` cross-check differences (``Corrections``).
    """
    if list(data.tickers) != list(cfg.tickers):
        raise ValueError(f"validate: data tickers {list(data.tickers)} differ from the config's {list(cfg.tickers)}")
    if basis is None:
        basis = {t: detect_basis(data.prices[t], data.xprices[t]) for t in data.tickers}
    if sorted(basis) != sorted(data.tickers) or any(b not in BASES for b in basis.values()):
        raise ValueError(f"validate: bad cross-check basis {dict(basis)}")
    results = [_rule_calendar(data, cfg), _rule_prices(data), _rule_splits(data), _rule_dividends(data),
               _rule_adj_close(data), _rule_cross_check(data, cfg, basis, accepted), _rule_rates(data, cfg)]
    return results, dict(basis)


def validate_store(store: EtfStore, cfg: EtfDataConfig) -> tuple[list[RuleResult], dict[str, str]]:
    """The rules on the stored snapshot (corrections already applied), with the recorded basis and acceptances.

    Refuses (StoreRefused) when the corrections file differs from the one the fetch applied.
    """
    manifest = store.manifest()
    if manifest is None:
        raise StoreRefused(f"no manifest under {store.root}; run `perpbt etf fetch` first")
    if file_sha256(cfg.corrections) != manifest.get("corrections_sha256"):
        raise StoreRefused(f"{cfg.corrections} changed since the fetch; re-fetch to apply it")
    accepted = load_corrections(cfg.corrections).accepted_keys()
    return validate(store.load_all(allow_holdout=True), cfg, basis=manifest.get("cross_check_basis"),
                    accepted=accepted)


def format_table(results: list[RuleResult], *, max_failures: int = 5) -> str:
    """A plain table: rule, ok, failure count, then the first failures of each failing rule."""
    lines = [f"{'rule':<15} {'ok':<4} failures"]
    for r in results:
        lines.append(f"{r.rule:<15} {'yes' if r.ok else 'NO':<4} {len(r.failures)}")
        lines += [f"    {msg}" for msg in r.failures[:max_failures]]
        if len(r.failures) > max_failures:
            lines.append(f"    ... and {len(r.failures) - max_failures} more")
    return "\n".join(lines)
