"""PairFrame: perp and spot DayFrames on one calendar, cut at the last common candle (carry spec Phase 1 §1.1)."""
import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from perpbt.carry.frames import CARRY_INSAMPLE_END, LEGS, build_pairframe, load_pairframe
from perpbt.config import DataConfig, load_yaml, to_dict
from perpbt.data.store import DAY_MS, CandleStore, Candles, Funding, FundingStore, HoldoutAccessError, date_ms
from perpbt.trend.daily import FILL_OFFSETS_MIN, load_dayframe
from tests.synthetic import STEP_15M_MS, T0_MS, random_walk

ROOT = Path(__file__).resolve().parents[2]
PER_DAY = 96
FIRST = "2020-01-01"  # = T0_MS
H_MS = 3_600_000
MIN_MS = 60_000


def keep(candles: Candles, mask: np.ndarray) -> Candles:
    return Candles(candles.pair, candles.tf, candles.ts[mask], candles.o[mask], candles.h[mask],
                   candles.l[mask], candles.c[mask], candles.v[mask])


def walk(days: int, *, seed: int, start_ms: int = T0_MS, pair: str = "TEST") -> Candles:
    return random_walk(days * PER_DAY, seed=seed, start_ms=start_ms, pair=pair)


def funding(ts, rates=None, interval_h=8, pair: str = "TEST") -> Funding:
    ts = np.asarray(ts, dtype=np.int64)
    rates = np.full(len(ts), 1e-4) if rates is None else np.asarray(rates, dtype=np.float64)
    return Funding(pair, ts, rates, np.broadcast_to(np.asarray(interval_h, dtype=np.int8), ts.shape).copy())


def every_8h(days: int, start_ms: int = T0_MS) -> np.ndarray:
    return start_ms + 8 * H_MS * np.arange(days * 3, dtype=np.int64)


def _frame(candles: Candles) -> pd.DataFrame:
    return pd.DataFrame({
        "open_ms": candles.ts, "open": candles.o, "high": candles.h, "low": candles.l, "close": candles.c,
        "volume": candles.v, "quote_volume": candles.v, "trades": 1, "taker_buy_volume": 0.0, "source": "bulk_monthly",
    })


def _write_pair(cfg: DataConfig, pair: str, start_ms: int, days: int, *, perp_seed=5, spot_seed=6) -> None:
    CandleStore(cfg).write(pair, "15m", _frame(walk(days, seed=perp_seed, start_ms=start_ms, pair=pair)))
    CandleStore(cfg, market="spot").write(pair, "15m", _frame(walk(days, seed=spot_seed, start_ms=start_ms, pair=pair)))
    FundingStore(cfg).write(pair, pd.DataFrame({
        "funding_ms": every_8h(days, start_ms), "rate": 1e-4, "interval_h": 8, "source": "bulk_monthly"}))


def _with(cfg: DataConfig, **kw) -> DataConfig:
    return DataConfig(**{**to_dict(cfg), **kw})


# --- the cut at the last common candle ------------------------------------------------------------


def test_legs_equal_load_dayframe(data_cfg):
    cfg = _with(data_cfg, warmup_start="2020-01-01", listing={"BTCUSDT": "2020-01-01"})
    _write_pair(cfg, "BTCUSDT", T0_MS, 6)
    pf = load_pairframe(cfg, "BTCUSDT", end="2020-01-05")
    for leg, market in (("perp", "um"), ("spot", "spot")):
        ref = load_dayframe(cfg, "BTCUSDT", end="2020-01-05", market=market)
        got = getattr(pf, leg)
        for name in ("day_ms", "o", "h", "l", "c", "filled", "funding", "ret", "funding_ts", "funding_rate",
                     "funding_px", "funding_ptr"):
            np.testing.assert_array_equal(getattr(got, name), getattr(ref, name), err_msg=f"{leg}.{name}")
        for h in FILL_OFFSETS_MIN:
            np.testing.assert_array_equal(got.fill_px[h], ref.fill_px[h])
        assert got.last_candle_ms == ref.last_candle_ms
    assert pf.last_common_ms == date_ms("2020-01-05") + DAY_MS - STEP_15M_MS
    np.testing.assert_array_equal(pf.day_ms, pf.perp.day_ms)
    assert pf.funding_interval_h.tolist() == [8] * len(pf.perp.funding_ts)


def test_spot_ends_first():
    perp, spot = walk(5, seed=1), walk(5, seed=2)
    spot = keep(spot, spot.ts < T0_MS + 2 * DAY_MS + 14 * H_MS)  # spot's last candle: day 2 13:45
    pf = build_pairframe(perp, funding(every_8h(5)), spot, first_day=FIRST)
    last = T0_MS + 2 * DAY_MS + 14 * H_MS - STEP_15M_MS
    assert pf.last_common_ms == last
    assert pf.perp.last_candle_ms == pf.spot.last_candle_ms == last
    assert len(pf) == 3 and pf.day_ms[-1] == T0_MS + 2 * DAY_MS
    assert pf.perp.funding_ts.max() == T0_MS + 2 * DAY_MS + 8 * H_MS  # 16:00 is after the cut
    day2 = (perp.ts >= T0_MS + 2 * DAY_MS) & (perp.ts <= last)
    assert pf.perp.c[-1] == perp.c[day2][-1] and pf.perp.h[-1] == perp.h[day2].max()


def test_perp_ends_first_mid_day_eos_shape():
    # EOSUSDT: the perp's last candle is 2025-05-21 09:00, the spot runs on for five days and the
    # archive keeps publishing flat funding rates after the delisting
    perp = keep(walk(4, seed=3), walk(4, seed=3).ts <= T0_MS + 3 * DAY_MS + 9 * H_MS)
    spot = walk(9, seed=4)
    fts = every_8h(9)
    pf = build_pairframe(perp, funding(fts), spot, first_day=FIRST)
    cut = T0_MS + 3 * DAY_MS + 9 * H_MS
    assert pf.last_common_ms == cut
    assert pf.perp.last_candle_ms == pf.spot.last_candle_ms == cut
    assert len(pf) == len(pf.perp) == len(pf.spot) == 4
    assert pf.day_ms[-1] == T0_MS + 3 * DAY_MS
    assert pf.perp.funding_ts.max() == T0_MS + 3 * DAY_MS + 8 * H_MS
    assert pf.perp.funding_ptr[-1] == len(pf.perp.funding_ts) == len(pf.funding_interval_h) == 11
    assert pf.spot.c[-1] == spot.c[int(np.searchsorted(spot.ts, cut))]


def test_funding_event_at_last_common_kept():
    perp = keep(walk(3, seed=3), walk(3, seed=3).ts <= T0_MS + 2 * DAY_MS + 8 * H_MS)
    pf = build_pairframe(perp, funding(every_8h(3)), walk(4, seed=4), first_day=FIRST)
    assert pf.last_common_ms == T0_MS + 2 * DAY_MS + 8 * H_MS == pf.perp.funding_ts[-1]
    assert len(pf.perp.funding_ts) == 8


def test_last_common_skips_one_leg_gap():
    perp = walk(3, seed=1)
    spot = walk(4, seed=2)
    last_perp = int(perp.ts[-1])
    spot = keep(spot, (spot.ts != last_perp) & (spot.ts != last_perp - STEP_15M_MS))
    pf = build_pairframe(perp, funding([]), spot, first_day=FIRST)
    assert pf.last_common_ms == last_perp - 2 * STEP_15M_MS
    assert pf.perp.last_candle_ms == pf.spot.last_candle_ms == pf.last_common_ms


def test_basis_filled_fill_ok():
    perp, spot = walk(5, seed=1), walk(5, seed=2)
    sday = (spot.ts - T0_MS) // DAY_MS
    spot = keep(spot, (sday != 2) & (spot.ts != T0_MS + 3 * DAY_MS))  # day 2 missing; day 3 has no 00:00 candle
    pf = build_pairframe(perp, funding([]), spot, first_day=FIRST)
    np.testing.assert_array_equal(pf.basis, pf.perp.c / pf.spot.c - 1.0)
    assert pf.filled.tolist() == [False, False, True, False, False]
    assert pf.fill_ok[15].tolist() == [True, True, False, False, True]
    assert pf.fill_ok[60].tolist() == [True, True, False, True, True]
    assert set(pf.fill_ok) == set(FILL_OFFSETS_MIN) and pf.fill_ok[15].dtype == bool


def test_fill_px_late():
    perp, spot = walk(4, seed=1), walk(4, seed=2)
    d1, d2 = T0_MS + DAY_MS, T0_MS + 2 * DAY_MS
    gone = ((spot.ts >= d1) & (spot.ts < d1 + 2 * H_MS)) | ((spot.ts >= d2 + 22 * H_MS) & (spot.ts < d2 + DAY_MS))
    spot2 = keep(spot, ~gone)  # spot day 1 starts at 02:00; spot day 2 ends with the 21:45 candle
    offsets = FILL_OFFSETS_MIN + (1440,)
    pf = build_pairframe(perp, funding([]), spot2, first_day=FIRST, fill_offsets_min=offsets)
    for h in offsets:  # on time: the fill_px candle, at the fill time d + h
        np.testing.assert_array_equal(pf.fill_px_late["perp"][h], pf.perp.fill_px[h])
        np.testing.assert_array_equal(pf.fill_late_ms["perp"][h], pf.day_ms + h * MIN_MS)
    first_d1 = int(np.searchsorted(spot.ts, d1 + 2 * H_MS))
    for h in (15, 60):  # late: the first candle of the day at or after d + h
        assert np.isnan(pf.spot.fill_px[h][1]) and not pf.fill_ok[h][1]
        assert pf.fill_px_late["spot"][h][1] == spot.c[first_d1]
        assert pf.fill_late_ms["spot"][h][1] == d1 + 2 * H_MS + STEP_15M_MS
    assert pf.fill_px_late["spot"][240][1] == pf.spot.fill_px[240][1] and pf.fill_ok[240][1]
    assert np.isnan(pf.spot.fill_px[1440][2])  # none left in the day: NaN, never the next day's candle
    assert np.isnan(pf.fill_px_late["spot"][1440][2]) and np.isnan(pf.fill_late_ms["spot"][1440][2])
    assert pf.fill_px_late["spot"][1440][3] == spot.c[-1]
    assert set(pf.fill_px_late) == set(pf.fill_late_ms) == set(LEGS)


def test_funding_px_prev():
    # the price of a funding event whose own candle is missing: the close of the last perp candle before it
    perp = walk(3, seed=1, start_ms=T0_MS + 8 * H_MS)  # first candle 08:00 on day 0 (the XLM listing shape)
    perp = keep(perp, perp.ts != T0_MS + DAY_MS + 8 * H_MS)  # day 1's 08:00 candle missing
    ts = np.array([T0_MS, T0_MS + 8 * H_MS, T0_MS + DAY_MS, T0_MS + DAY_MS + 8 * H_MS], dtype=np.int64)
    pf = build_pairframe(perp, funding(ts), walk(4, seed=2), first_day=FIRST)
    prev = pf.funding_px_prev
    assert np.isnan(prev[0]) and np.isnan(pf.perp.funding_px[0])  # before the first candle: no price at all
    i8 = int(np.searchsorted(perp.ts, T0_MS + 8 * H_MS))
    assert np.isnan(prev[1]) and pf.perp.funding_px[1] == perp.o[i8]  # the event at the first candle's open
    i24 = int(np.searchsorted(perp.ts, T0_MS + DAY_MS))
    assert prev[2] == perp.c[i24 - 1]
    assert np.isnan(pf.perp.funding_px[3])
    assert prev[3] == perp.c[int(np.searchsorted(perp.ts, T0_MS + DAY_MS + 8 * H_MS)) - 1]
    assert len(prev) == len(pf.perp.funding_ts)
    with pytest.raises(ValueError):
        prev[0] = 0.0


def test_arrays_read_only():
    pf = build_pairframe(walk(3, seed=1), funding(every_8h(3)), walk(3, seed=2), first_day=FIRST)
    for arr in (pf.day_ms, pf.basis, pf.filled, pf.fill_ok[15], pf.fill_px_late["spot"][15],
                pf.fill_late_ms["perp"][60], pf.funding_interval_h):
        with pytest.raises(ValueError):
            arr[0] = 0


def test_pair_mismatch_and_no_common_raise():
    with pytest.raises(ValueError, match="pair"):
        build_pairframe(walk(2, seed=1, pair="A"), funding([], pair="A"), walk(2, seed=2, pair="B"), first_day=FIRST)
    perp = walk(2, seed=1)
    spot = keep(walk(2, seed=2), np.zeros(2 * PER_DAY, dtype=bool))
    with pytest.raises(ValueError, match="common"):
        build_pairframe(perp, funding([]), spot, first_day=FIRST)


# --- the carry holdout guard (Phase 0 §0.2) -------------------------------------------------------


@pytest.mark.parametrize("config", ["carry/data.yaml", "trend/data.yaml"])
def test_load_refuses_past_carry_insample_under_both_configs(tmp_path, config):
    cfg = _with(load_yaml(ROOT / "configs" / config, DataConfig), data_dir=str(tmp_path))
    assert CARRY_INSAMPLE_END == "2023-12-31"
    for end in ("2024-01-01", "2026-10-20"):
        with pytest.raises(HoldoutAccessError, match="carry in-sample end 2023-12-31"):  # empty store: before any read
            load_pairframe(cfg, "BTCUSDT", end=end)
    with pytest.raises(FileNotFoundError):  # the last in-sample day passes the guard and reaches the store
        load_pairframe(cfg, "BTCUSDT", end=CARRY_INSAMPLE_END)


def test_load_allow_holdout_passes_through(tmp_path):
    cfg = _with(load_yaml(ROOT / "configs" / "trend" / "data.yaml", DataConfig), data_dir=str(tmp_path),
                listing={"BTCUSDT": "2023-12-30"}, warmup_start="2023-12-30")
    _write_pair(cfg, "BTCUSDT", date_ms("2023-12-30"), 4)
    with pytest.raises(HoldoutAccessError, match="carry in-sample end"):
        load_pairframe(cfg, "BTCUSDT", end="2024-01-02")
    pf = load_pairframe(cfg, "BTCUSDT", end="2024-01-02", allow_holdout=True)
    assert pf.day_ms[-1] == date_ms("2024-01-02") and len(pf) == 4


def _carry_modules():
    return [p for p in sorted((ROOT / "perpbt" / "carry").glob("*.py"))]


def test_allow_holdout_only_passed_through_in_carry():
    # only Phase 5's carry/holdout.py and carry/forward.py may pass True (spec Phase 0 §0.2); everywhere else
    # an allow_holdout= keyword is False or the caller's own allow_holdout parameter
    bad = []
    for path in _carry_modules():
        if path.name in ("holdout.py", "forward.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.keyword) and node.arg == "allow_holdout":
                v = node.value
                if not ((isinstance(v, ast.Name) and v.id == "allow_holdout")
                        or (isinstance(v, ast.Constant) and v.value is False)):
                    bad.append(f"{path.name}:{v.lineno}")
            elif isinstance(node, ast.keyword) and node.arg is None:  # **kwargs could smuggle the flag
                bad.append(f"{path.name}:{node.value.lineno} (**)")
    assert not bad, bad


def test_carry_never_reads_dayframe_ret():
    # DayFrame.ret subtracts funding for a long; the carry perp leg's return is its price return
    bad = [f"{p.name}:{n.lineno}" for p in _carry_modules() for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
           if isinstance(n, ast.Attribute) and n.attr == "ret"]
    assert not bad, bad


def test_only_guarded_modules_read_the_stores():
    # load_pairframe is the only perp/funding guard at the carry in-sample end; basis.py has a fixed window
    names = {"CandleStore", "FundingStore", "load_dayframe"}
    users = {p.name for p in _carry_modules() for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
             if isinstance(n, ast.Name) and n.id in names}
    assert users <= {"frames.py", "basis.py"}, users
