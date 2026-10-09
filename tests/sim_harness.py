"""Drive the simulator over hand-placed candles with a scripted strategy.

``ScriptedStrategy`` returns given intents at given candles and records
every ``SimEvent`` and ``AccountView`` it sees, so the scenario tests can
check execution without the order-block rule in the way.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from perpbt.config import ExecConfig, HoldRule, SessionSpec, StrategyParams
from perpbt.data.sessions import SessionCalendar
from perpbt.data.store import Candles, Funding
from perpbt.execution.simulator import SimResult, run
from perpbt.strategy.base import AccountView, Intent, MarketView, PlaceBracketLimit, SimEvent
from tests.strategy_harness import UTC
from tests.synthetic import STEP_15M_MS, T0_MS, candles_from_rows

ZERO_COST = ExecConfig(fee_maker=0.0, fee_taker=0.0, slippage=0.0, use_1m=False)
FLAT = (100.5, 100.8, 100.2, 100.5)  # touches none of entry 100, stop 99, target 102


class ScriptedStrategy:
    name = "scripted"
    warmup_bars = 0

    def __init__(self, script: dict[int, Sequence[Intent]], params: StrategyParams | None = None) -> None:
        self.params = params or StrategyParams()
        self.script = script
        self.events: list[SimEvent] = []
        self.accounts: dict[int, AccountView] = {}

    def on_candle(self, view: MarketView, account: AccountView) -> list[Intent]:
        self.accounts[view.i] = account
        return list(self.script.get(view.i, ()))

    def on_event(self, event: SimEvent) -> None:
        self.events.append(event)

    def skip_counts(self) -> dict[str, int]:
        return {}


def close_ms(i: int, start_ms: int = T0_MS) -> int:
    """Close time of candle ``i`` of a series starting at ``start_ms``."""
    return start_ms + (i + 1) * STEP_15M_MS


def bracket(price=100.0, stop=99.0, target=102.0, *, expires_ms=None, hold=None, tag=None) -> PlaceBracketLimit:
    return PlaceBracketLimit(
        side="long", price=price, stop=stop, target=target,
        expires_ms=close_ms(10_000) if expires_ms is None else expires_ms,
        hold_rule=hold or HoldRule("none"), tag=tag or {},
    )


def no_funding(pair: str = "TEST") -> Funding:
    return Funding(pair, np.zeros(0, dtype=np.int64), np.zeros(0), np.zeros(0, dtype=np.int8))


def funding_at(times_ms: Sequence[int], rates: Sequence[float], pair: str = "TEST") -> Funding:
    return Funding(pair, np.asarray(times_ms, dtype=np.int64), np.asarray(rates, dtype=np.float64),
                   np.full(len(times_ms), 8, dtype=np.int8))


def minute_candles(rows15: Sequence[tuple], overrides: dict[int, Sequence[tuple]] | None = None,
                   *, start_ms: int = T0_MS, drop: Sequence[int] = ()) -> Candles:
    """1m candles for 15m ``rows15``: each 15m candle becomes 15 minutes consistent with it.

    By default the minutes go open, down to the low (minute 1), up to the
    high (minute 2), back to the close (minute 3) and stay there, so the low
    comes before the high. ``overrides[j]`` gives the (o, h, l, c) rows of
    candle ``j``'s 15 minutes explicitly. ``drop`` lists minute indices
    (global) to remove.
    """
    overrides = overrides or {}
    rows = []
    for j, (o, h, l, c) in enumerate(rows15):  # noqa: E741
        if j in overrides:
            mins = list(overrides[j])
            assert len(mins) == 15, len(mins)
        else:
            mins = [(o, o, o, o), (o, o, l, l), (l, h, l, h), (h, h, c, c)] + [(c, c, c, c)] * 11
        rows.extend(mins)
    cd = candles_from_rows(rows, start_ms=start_ms, step_ms=60_000, tf="1m")
    if drop:
        keep = np.setdiff1d(np.arange(len(cd)), np.asarray(drop))
        cd = Candles(cd.pair, "1m", cd.ts[keep], cd.o[keep], cd.h[keep], cd.l[keep], cd.c[keep], cd.v[keep])
    return cd


def sim(
    rows: Sequence[tuple],
    script: dict[int, Sequence[Intent]] | None = None,
    *,
    strategy=None,
    cfg: ExecConfig = ZERO_COST,
    params: StrategyParams | None = None,
    funding: Funding | None = None,
    candles1m: Candles | None = None,
    spec: SessionSpec = UTC,
    start_ms: int = T0_MS,
    first: int = 0,
    last: int | None = None,
) -> tuple[SimResult, ScriptedStrategy]:
    """Simulate candles ``first .. last`` (default: all) of ``rows``; returns the result and the strategy."""
    cd = candles_from_rows(rows, start_ms=start_ms)
    strategy = strategy or ScriptedStrategy(script or {}, params)
    last = len(rows) - 1 if last is None else last
    res = run(
        cd, candles1m, funding or no_funding(), SessionCalendar(spec, cd.ts), strategy, cfg,
        int(cd.ts[first]), close_ms(last, start_ms), variant_id="v",
    )
    return res, strategy
