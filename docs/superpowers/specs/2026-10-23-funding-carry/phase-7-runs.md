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
