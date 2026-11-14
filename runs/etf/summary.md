# ETF trend following — summary

**Decision: stop.** The gross screen (ETF spec Phase 2 §2.3) gave p_screen = 0.5455, at or above the pre-registered stop threshold 0.1. Phases 3–7 are not built.

| | value |
|---|---|
| period (return days) | 2005-08-01 .. 2017-12-29 (3127 days) |
| ETFs | SPY QQQ IWM GLD (equal weight) |
| observed gross excess Sharpe | 0.663 |
| random-days paths | 1000: mean 0.680, std 0.110, 5/50/95% 0.513 / 0.678 / 0.844 |
| p_screen | 0.5455 |
| vol-targeted buy-and-hold gross excess Sharpe (descriptive) | 0.795 |
| 100% SPY excess Sharpe (descriptive) | 0.468 |

| ETF | strategy excess Sharpe | buy-and-hold excess Sharpe | mean w* |
|---|---|---|---|
| SPY | 0.535 | 0.627 | 0.611 |
| QQQ | 0.678 | 0.769 | 0.610 |
| IWM | 0.203 | 0.470 | 0.518 |
| GLD | 0.579 | 0.567 | 0.455 |

Pre-registration hash 46ba8b847fc966d3e5b8969b1de3ea869e06c20540388cde88415916693c319d; data manifest 308f5012c5a274b62ee2d5f29e4b299cd225be3fa0cf2b02ff60a73f88c60658; computed 2026-10-24 at 570c809094bb064e1e357c40f436286afd578fb8. Gross model: close-to-close total return, idle cash at DTB3, no fill delay, drift band or costs; Sharpe on the excess over T-bills at √252. This is the fourth strategy tested in this repo.
