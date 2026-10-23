# Trend following — summary

**Decision: stop.** The gross signal screen (trend spec Phase 2 §2.3) gave p_screen = 0.1558, at or above the pre-registered stop threshold 0.1. Phases 3–7 are not built.

| | value |
|---|---|
| period (return days) | 2020-07-01 .. 2026-10-20 (2303 days) |
| pairs | BTCUSDT ETHUSDT BCHUSDT LINKUSDT ETCUSDT LTCUSDT XRPUSDT EOSUSDT TRXUSDT XLMUSDT |
| observed gross Sharpe | 1.079 |
| random-days paths | 1000: mean 0.844, std 0.233, 5/50/95% 0.458 / 0.869 / 1.201 |
| p_screen | 0.1558 |
| vol-targeted buy-and-hold gross Sharpe (descriptive) | 0.986 |

Pre-registration hash a011ea29f9331570e6e2f5a8b8f904a5a39c9aab0b15db1b55392f6d67488fba; computed 2026-10-23 at 93984c865aff28c7481c2eda0d116391b8c1a8da. Gross model: no fill delay, drift band, fees, slippage or funding.
