"""ccxt head backfill (D14) and tail fetch (spec §1.1, §1.6).

The exchange object is injected so tests never touch the network;
``make_exchange`` builds the real ``ccxt.binanceusdm`` lazily because ccxt is
an optional extra. Callers pick ``end_ms`` on the grid at or before now so
the candle that is still open is never stored.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date
from typing import Any, Protocol

import numpy as np
import pandas as pd

from perpbt.config import DataConfig
from perpbt.data.bulk import ARCHIVE_START, FUNDING_FRAME_COLUMNS, KLINE_FRAME_COLUMNS, DownloadError, round_to_minute
from perpbt.data.store import _STEP_MS, date_ms

OHLCV_LIMIT = 1500  # binanceusdm maximum per fetch_ohlcv call
FUNDING_LIMIT = 1000
ATTEMPTS = 3  # retries of one page on a transient exchange error


def _ccxt_errors() -> tuple[type[BaseException], type[BaseException]]:
    """(transient, any) ccxt error classes; placeholders that never match when ccxt is absent."""
    try:
        import ccxt
    except ImportError:  # pragma: no cover - a fake exchange never raises ccxt errors

        class _Never(Exception):
            pass

        return _Never, _Never
    return ccxt.NetworkError, ccxt.BaseError


def _call(fn: Callable[..., Any], *args: Any, attempts: int = ATTEMPTS) -> Any:
    """Call one exchange method; retry transient errors with backoff, report the rest as DownloadError."""
    transient, any_error = _ccxt_errors()
    for attempt in range(1, attempts + 1):
        try:
            return fn(*args)
        except transient as e:
            if attempt == attempts:
                raise DownloadError(f"ccxt: {e}") from e
            time.sleep(2.0 * attempt)
        except any_error as e:
            raise DownloadError(f"ccxt: {e}") from e
    raise AssertionError("unreachable")


class Exchange(Protocol):
    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1m", since: int | None = None, limit: int | None = None
    ) -> list[list]: ...

    def fetch_funding_rate_history(
        self, symbol: str | None = None, since: int | None = None, limit: int | None = None
    ) -> list[dict]: ...


def make_exchange() -> Exchange:
    """The real ``ccxt.binanceusdm`` with rate limiting on; needs the ``ccxt`` extra."""
    try:
        import ccxt
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise RuntimeError("ccxt is not installed; run: pip install 'perpbt[ccxt]'") from e
    return ccxt.binanceusdm({"enableRateLimit": True})


def ccxt_symbol(pair: str) -> str:
    """``BTCUSDT`` → ``BTC/USDT:USDT``, ccxt's unified symbol for the USDT-margined perpetual."""
    if not pair.endswith("USDT") or len(pair) <= 4:
        raise ValueError(f"expected a USDT-margined pair like BTCUSDT, got {pair!r}")
    return f"{pair[:-4]}/USDT:USDT"


def fetch_ohlcv_range(
    exchange: Exchange, pair: str, tf: str, start_ms: int, end_ms: int, *, limit: int = OHLCV_LIMIT
) -> pd.DataFrame:
    """Candles with ``start_ms <= open_ms < end_ms`` as a storage frame, ``source='ccxt'``.

    Pages forward from ``start_ms`` by ``limit`` candles. ccxt gives no quote
    volume, trade count or taker volume, so those are NaN, 0 and NaN.
    """
    step = _STEP_MS[tf]
    symbol = ccxt_symbol(pair)
    rows: list[list] = []
    since = start_ms
    while since < end_ms:
        page = _call(exchange.fetch_ohlcv, symbol, tf, since, limit)
        if not page:
            break
        rows.extend(page)
        nxt = int(page[-1][0]) + step
        if nxt <= since:  # no progress: the exchange ignored ``since``
            break
        since = nxt
    arr = np.asarray([r[:6] for r in rows], dtype=np.float64).reshape(-1, 6)
    ts = arr[:, 0].astype(np.int64)
    keep = (ts >= start_ms) & (ts < end_ms)
    arr, ts = arr[keep], ts[keep]
    order = np.argsort(ts, kind="stable")
    arr, ts = arr[order], ts[order]
    ts, first = np.unique(ts, return_index=True)
    arr = arr[first]
    n = len(ts)
    frame = pd.DataFrame(
        {
            "open_ms": ts,
            "open": arr[:, 1],
            "high": arr[:, 2],
            "low": arr[:, 3],
            "close": arr[:, 4],
            "volume": arr[:, 5],
            "quote_volume": np.full(n, np.nan),
            "trades": np.zeros(n, dtype=np.int64),
            "taker_buy_volume": np.full(n, np.nan),
            "source": np.full(n, "ccxt", dtype=object),
        }
    )
    return frame[KLINE_FRAME_COLUMNS + ["source"]]


def fetch_funding_range(
    exchange: Exchange, pair: str, start_ms: int, end_ms: int, *, interval_h: int, limit: int = FUNDING_LIMIT
) -> pd.DataFrame:
    """Funding events with ``start_ms <= funding_ms < end_ms``, ``interval_h`` stamped on every row.

    ccxt's funding history carries no interval, so the caller passes the last
    interval seen in the bulk data for the pair.
    """
    symbol = ccxt_symbol(pair)
    events: list[tuple[int, float]] = []
    since = start_ms
    while since < end_ms:
        page = _call(exchange.fetch_funding_rate_history, symbol, since, limit)
        if not page:
            break
        events.extend((int(e["timestamp"]), float(e["fundingRate"])) for e in page)
        nxt = max(int(e["timestamp"]) for e in page) + 1
        if nxt <= since:
            break
        since = nxt
    raw = np.asarray([e[0] for e in events], dtype=np.int64)
    rate = np.asarray([e[1] for e in events], dtype=np.float64)
    ts = round_to_minute(raw)
    keep = (ts >= start_ms) & (ts < end_ms)
    ts, rate = ts[keep], rate[keep]
    order = np.argsort(ts, kind="stable")
    ts, rate = ts[order], rate[order]
    ts, first = np.unique(ts, return_index=True)
    rate = rate[first]
    n = len(ts)
    frame = pd.DataFrame(
        {
            "funding_ms": ts,
            "rate": rate,
            "interval_h": np.full(n, interval_h, dtype=np.int8),
            "source": np.full(n, "ccxt", dtype=object),
        }
    )
    return frame[FUNDING_FRAME_COLUMNS + ["source"]]


def head_range(data_cfg: DataConfig, pair: str, *, from_date: date | None = None) -> tuple[int, int] | None:
    """``[from_date, ARCHIVE_START)`` in ms for pairs listed before the archive starts, else None.

    ``from_date`` defaults to ``data_cfg.warmup_start``. Rows before a pair's
    listing simply do not come back from the exchange, so BTCUSDT and ETHUSDT
    get the same requested range (spec §1.1) and SOLUSDT gets nothing.
    """
    listing = date.fromisoformat(data_cfg.listing[pair])
    start = from_date or date.fromisoformat(data_cfg.warmup_start)
    if listing >= ARCHIVE_START or start >= ARCHIVE_START:
        return None
    return date_ms(start.isoformat()), date_ms(ARCHIVE_START.isoformat())
