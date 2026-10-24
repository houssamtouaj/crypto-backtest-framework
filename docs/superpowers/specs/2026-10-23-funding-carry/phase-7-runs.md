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
