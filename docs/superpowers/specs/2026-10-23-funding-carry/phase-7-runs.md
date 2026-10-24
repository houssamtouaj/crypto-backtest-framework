# Phase 7 — Runs

Read with `00-overview.md`. The run protocol, in order. Every step that
cannot be undone stops for the user first.

**Branch:** `carry/7-runs`, from `dev` after Phase 6 is merged.

1. **Fetch the tail** of both legs through the last complete UTC day
   (perps and spot), validate, commit manifests. Nothing past 2023-12-31
   is loaded by analysis code yet.
2. **Code lock** (`perpbt carry lock-code`): ask the user first. Commit
   the lock; this sets the forward start.
3. **In-sample primary and exploratory runs** (`perpbt carry run
   --primary`, then `--exploratory`), foreground, one at a time; reports
   and summary; commit `stats.json`, reports and the summary.
4. **Report the in-sample verdict** to the user.
5. **Holdout** (`perpbt carry holdout`): ask the user first; it runs once.
   Commit `runs/carry/holdout/` outputs and `DONE`; update the summary.
6. **Report the holdout verdict** to the user. Ask before merging `dev`
   into `main` (pull request, as before).
7. **Forward reviews** later: `perpbt carry forward --review 12` once 365
   days of forward data exist (descriptive), `--review 36` once 1,095 days
   exist (confirmatory). Each is a separate session, branch
   `carry/7-forward-<n>`.

No step is re-run with changes after its result is seen. If a step fails
for a technical reason, fix the code, record it in the registry (the
output-neutral check of Phase 5 §5.3 applies), and rerun that step only.

## 7.1 Run notes (Phase 7, from 2026-10-24)

Recorded as each step ran. *(my call)* marks a call taken on the user's standing instruction to take the recommended
option; one-shot steps (the code lock, the holdout) and merges ran only on the user's go.

**Step 1 — data tails (2026-10-24, 17:45 UTC; last complete UTC day 2026-10-23).**
- `perpbt data fetch --config configs/carry/data.yaml --market um --tfs 15m --ccxt-tail` (28 s): the 2026-10-23 daily
  zip and a ccxt tail of 70 candles and 4 funding events per active perp (to 2026-10-24 17:15 / 16:00);
  EOSUSDT skipped as delisted (its stored series ends more than 7 days ago). Then `--market spot --tfs 15m` (39 s):
  the 2026-10-21..23 daily zips per pair (the spot store already reached 2026-10-20 23:45 from the trend holdout
  fetch); EOSUSDT's later files are not published.
- `perpbt data validate` both markets: perps 0 gaps, every funding interval 8 h, no bad intervals; spot the same
  exchange-wide gaps as at Phase 0 (15 from 2020), none from 2024 on.
- Manifest-only check (no data loaded, no returns): every member's perp and spot 15m reach 2026-10-20 23:45 and its
  funding reaches the day's last event (16:00), except EOSUSDT, whose common last day is 2025-05-21 (perp last
  candle 09:00, spot 2025-05-26 02:45) = `runner.KNOWN_DELISTINGS`. No other early end; nothing added to the list.
- Noted: EOSUSDT's funding archive continues to 2026-09-30 16:00, after its perp's last candle; the PairFrame cuts
  both legs at the last common candle and drops the perp's later funding events (Phase 1), so they enter no run.

**Step 2 — code lock (2026-10-24, on the user's go).** Full suite before it: 1489 passed (15 min 22 s).
`perpbt carry lock-code`: carry code version `7ce43816563f41f4f2aa7318d46e382e027e7a547652ac4d6b748454fa80f240`,
locked on 2026-10-24 at git ab95921; `periods.forward.start` = 2026-10-25; the prereg hash is unchanged
(`a44eee47…b242`, `carry prereg validate`). The 12-month review needs data through 2027-10-24, the 36-month one
through 2029-10-23.

**Step 3 — in-sample runs (2026-10-24, git 229ce16, code locked).** `perpbt carry run --primary`: 11 ok, 0 failed
(35 s); `--exploratory`: 66 ok, 0 failed (2 min 36 s); registry 77 `ok` rows, no failure, no output-neutral row.
`perpbt carry report`: 7 reports (the primary and the six exploratory rows) and `runs/carry/summary.md` (26 s,
4.3 MB of PNGs). Committed: the registry, `results.parquet`, 77 `stats.json`, the 7 reports with figures, the summary.

- **Verdict (in-sample): positive below the hurdle.** V1 passes (net Sharpe 4.27, 95% CI 2.49..7.27, p 0.0001);
  V2 fails (annualised net return 7.85%, CI 4.24..11.82%; excess over 5% 2.97%, CI −0.64..6.94%, p 0.0571): not
  proven. Hurdles (descriptive): passes at 0% and 3%, not at 5% or 8%. Filter value against `always_on`: Sharpe
  difference 1.26 (CI 0.39..2.61, p 0.0062); random timing: return rank 1 of 1,000 paths; DSR 0.999 (7 trials).
- Per year (net, sum of daily returns): 2020 H2 8.64%, 2021 14.96%, 2022 0.34%, 2023 3.56%; share on 60.0%; 81 entries.
- **Margin events:** 91 rebalances (0.35% of equity) and 8 liquidations (0.93%), all from 2020-09-03 to 2021-05-06
  (TRX twice, XRP twice, XLM, ETC, BCH, EOS). A liquidated sleeve stays out until the signal's next entry, so the
  primary sat out high-funding stretches (XLM 2020-11-24 to mid 2021): its 2021 share on is 75.4% against `lev_1`'s
  88.0%. That is why `lev_1` (1 liquidation) earns almost the same funding (29.85% against 30.95%) on 3/4 of the
  notional, at 1.08% vol and Sharpe 7.20. The unhedged spot leg between a liquidation (at the day high) and the next
  00:15 fill makes the largest days: TRX 2020-09-03/04 (−139 and −95 USDT; the worst day −1.34%), XLM 2020-11-24
  (+185 USDT; the best day +1.85%), XRP 2020-11-22, EOS 2021-05-07 (daily skew 4.06, kurtosis 159). This is CD7 as
  registered (spot sold at the next fill), checked from the in-sample run tables only.
- **Exploratory rows (descriptive):** `entry_5` would read edge (V2 p 0.0427); the other five positive below the
  hurdle (V2 p: `always_on` 0.407, `entry_20` 0.183, `lookback_3d` 0.200, `lookback_14d` 0.0645, `lev_1` 0.0572).
- **Reading note.** A V2 "proven above" (one-sided p < 0.05) can sit beside a two-sided 95% excess CI that still
  contains 0 (`entry_5`: −0.47..7.38%; the per-pair BTCUSDT row; four re-pricing cells): the one-sided 5% test
  matches a 90% two-sided interval. The labels follow overview §3 (passes first); nothing was changed.
- Per pair alone (descriptive): five pass both (BTC, ETH, LINK, LTC, EOS), four are positive below the hurdle, TRXUSDT
  has no edge (−2.91%/yr; liquidations).
