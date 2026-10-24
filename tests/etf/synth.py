"""A clean synthetic ETF snapshot that passes every validation rule, and a matching config.

Days: the NYSE trading days from 2004-11-18 to 2007-01-31 (or a later end). Each ticker's close
follows its own seeded walk; the open sits near the previous close; high and
low bracket both; ``adj_close`` compounds the Phase 1 total return, so rule 5
agrees exactly. SPY and IWM pay in March, June, September and December from
2005, QQQ twice in 2005 and quarterly from 2006, GLD never. Rates are 2% on
every weekday from 2004-11-01. The cross-check repeats Yahoo's open and close
from 2006-01-03, and QQQ's dividends.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from perpbt.data.store import date_ms
from perpbt.etf.config import EtfDataConfig
from perpbt.etf.store import ACTION_COLUMNS, PRICE_COLUMNS, EtfData
from perpbt.etf.validate import NYSE_CLOSED, total_return

TICKERS = ("SPY", "QQQ", "IWM", "GLD")
START, END = date(2004, 11, 18), date(2007, 1, 31)
XCHECK_FROM = "2006-01-03"


def trading_days(lo: date = START, hi: date = END) -> list[str]:
    out = []
    d = lo
    while d <= hi:
        if d.weekday() < 5 and d.isoformat() not in NYSE_CLOSED:
            out.append(d.isoformat())
        d += timedelta(1)
    return out


def _ex_dates(days: list[str], ticker: str) -> list[str]:
    """The first trading day on or after the 15th of each paying month."""
    if ticker == "GLD":
        return []
    months = []
    for y in range(2005, int(days[-1][:4]) + 1):
        ms = (6, 12) if ticker == "QQQ" and y == 2005 else (3, 6, 9, 12)
        months += [(y, m) for m in ms]
    return [next(d for d in days if d >= f"{y}-{m:02d}-15") for y, m in months if days[-1] >= f"{y}-{m:02d}-15"]


def make_data(*, dividend_yield: float = 0.004, end: date = END) -> EtfData:
    """The clean snapshot from 2004-11-18 to ``end`` (2018-01-31 reaches the holdout, for the fetch tests)."""
    days = trading_days(START, end)
    day_ms = np.array([date_ms(d) for d in days], dtype=np.int64)
    prices, actions, xprices, xdividends = {}, {}, {}, {}
    for k, t in enumerate(TICKERS):
        rng = np.random.default_rng(100 + k)
        c = 100.0 * (1 + k) * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(days))))
        o = np.empty_like(c)
        o[0] = c[0]
        o[1:] = c[:-1] * (1 + rng.normal(0, 0.002, len(days) - 1))
        h = np.maximum(o, c) * 1.004
        lo = np.minimum(o, c) * 0.996
        div = np.zeros(len(days))
        ex = _ex_dates(days, t)
        for d in ex:
            i = days.index(d)
            div[i] = round(c[i - 1] * dividend_yield, 4)
        r = total_return(o, c, div)
        adj = np.empty_like(c)
        adj[0] = c[0] * 0.8
        adj[1:] = adj[0] * np.cumprod(1 + r[1:])
        prices[t] = pd.DataFrame({"day_ms": day_ms, "open": o, "high": h, "low": lo, "close": c,
                                  "volume": 1e6, "adj_close": adj}, columns=list(PRICE_COLUMNS))
        has = div > 0
        actions[t] = pd.DataFrame({"day_ms": day_ms[has], "dividend": div[has], "split": 0.0, "capital_gain": 0.0},
                                  columns=list(ACTION_COLUMNS))
        x = day_ms >= date_ms(XCHECK_FROM)
        xprices[t] = pd.DataFrame({"day_ms": day_ms[x], "open": o[x], "close": c[x]})
        xdividends[t] = (pd.DataFrame({"day_ms": day_ms[has], "dividend": div[has]}) if t == "QQQ" else None)
    rate_days = [d for d in (date(2004, 11, 1) + timedelta(i) for i in range((end - date(2004, 11, 1)).days + 1))
                 if d.weekday() < 5]
    rates = pd.DataFrame({"day_ms": [date_ms(d.isoformat()) for d in rate_days], "rate_pct": 2.0})
    return EtfData(prices=prices, actions=actions, rates=rates, xprices=xprices, xdividends=xdividends)


def make_config(**over) -> EtfDataConfig:
    base = dict(data_dir="data/etf", tickers=list(TICKERS), warmup_start="2004-11-18", insample_start="2005-08-01",
                insample_end="2017-12-31", holdout_start="2018-01-01", holdout_end=None, download_date=None,
                rates={"source": "fred", "series": "DTB3"},
                cross_check={"source": "nasdaq", "close_tol": 0.005, "dividend_tickers": ["QQQ"], "min_span_days": 300},
                corrections="configs/etf/corrections.yaml")
    base.update(over)
    return EtfDataConfig(**base)


def load_rows(days, c, o=None, div=None, split=None) -> pd.DataFrame:
    """``EtfStore.load``-shaped rows (prices with actions, 0 where none) for the frame tests (Phase 1)."""
    c = np.asarray(c, dtype=float)
    o = c.copy() if o is None else np.asarray(o, dtype=float)
    n = len(c)
    return pd.DataFrame({
        "day_ms": [date_ms(d) for d in days], "open": o, "high": np.maximum(o, c) * 1.01,
        "low": np.minimum(o, c) * 0.99, "close": c, "volume": 1e6, "adj_close": c,
        "dividend": np.zeros(n) if div is None else np.asarray(div, dtype=float),
        "split": np.zeros(n) if split is None else np.asarray(split, dtype=float), "capital_gain": 0.0,
    })


def walk_inputs(n: int = 300, seed: int = 7) -> tuple[list[str], pd.DataFrame, pd.DataFrame]:
    """``n`` trading days from 2004-11-18 of a seeded walk with a quarterly dividend, and daily rates around 2%."""
    days = trading_days(START, date(2008, 12, 31))[:n]
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    o = c * (1 + rng.normal(0, 0.002, n))
    div = np.where(np.arange(n) % 63 == 30, 0.4, 0.0)
    lo = date_ms(days[0]) - 14 * 86_400_000
    rate_ms = np.arange(lo, date_ms(days[-1]) + 86_400_000, 86_400_000)
    rates = pd.DataFrame({"day_ms": rate_ms, "rate_pct": 2 + rng.normal(0, 0.3, len(rate_ms))})
    return days, load_rows(days, c, o=o, div=div), rates


def replace(data: EtfData, **frames) -> EtfData:
    """``data`` with some of its mappings or frames replaced."""
    parts = {"prices": dict(data.prices), "actions": dict(data.actions), "rates": data.rates,
             "xprices": dict(data.xprices), "xdividends": dict(data.xdividends)}
    parts.update(frames)
    return EtfData(**parts)
