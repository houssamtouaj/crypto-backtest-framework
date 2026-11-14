# Phase 7 — Runs

Read with `00-overview.md`. The run protocol, in order. Every step is
recorded in the phase plan's progress log with commit hashes.

**Branch:** `etf/7-runs`, from `dev` after Phase 6 is merged.

## 7.1 Protocol

1. **Data check.** `perpbt etf validate` passes; the manifest hash equals
   the prereg's `data.manifest_sha256`. No re-fetch.
2. **Code lock.** Clean tree; `perpbt etf lock-code`. Commit the lock.
   The forward period starts the next day.
3. **In-sample runs.** `perpbt etf run` (primary), `perpbt etf run
   --exploratory`. Commit `stats.json`, reports, figures, registry,
   results.
4. **Reports and summary** for the in-sample period. Commit.
5. **Stop: report the in-sample verdict to the user** and ask for the go
   for the holdout.
6. **Holdout, once.** `perpbt etf holdout`. Commit `DONE`, its
   `stats.json`, reports, figures, registry, results, summary.
7. **Stop: report the holdout verdict to the user**; merge `etf/7-runs`
   into `dev` after their go; then `dev` into `main` (PR) after their go.

## 7.2 Forward reviews

Each is its own session on branch `etf/7-forward-<n>`:

- **12 months** after `forward.start`: `perpbt etf forward-fetch`, then
  `perpbt etf forward --review 12`. Descriptive only.
- **36 months**: the same with `--review 36`; T1, T2 and the verdict.

## 7.3 What is never done

- Re-running the screen or the holdout with any change.
- Editing `prereg.yaml` other than `periods.forward.start`.
- Editing any file in `CARRY_CODE_PATHS` or `ETF_CODE_PATHS` after the
  code lock without `--allow-code-change --reason` and a registry note.
- Re-fetching the frozen snapshot.

## 7.4 Exit criterion

`runs/etf/summary.md` committed with the in-sample and holdout verdicts;
the forward rows marked "not run yet" with their earliest dates.
