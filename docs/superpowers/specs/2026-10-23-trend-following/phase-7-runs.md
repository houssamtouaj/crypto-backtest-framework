# Phase 7 — Runs

Read with `00-overview.md`. No code. The protocol, in order; nothing in a
later step may inform an earlier one.

**Branch:** `trend/7-runs`, from `dev` after Phase 6 is merged. Merged into
`dev` after step 5, then `dev` into `main`. The forward reviews (steps 6–7)
happen later on their own short branches.

## 7.1 Already done before this phase

- Phase 0: universe resolved, data fetched.
- Phase 2: pre-registration frozen (`trend-prereg-v1`), screen passed.

## 7.2 Protocol

1. **Code lock.** `perpbt trend lock-code`; commit the lock with the
   message "Lock trend code version". The forward period starts the next
   UTC day.
2. **In-sample primary.** `perpbt trend run --primary` (portfolio and per
   pair, 5,000 random paths, re-pricing). Then
   `perpbt trend run --exploratory`.
3. **Review gate.** Read the primary report. Fix bugs only; if a fix
   changes `perpbt/` outside `report/`, rerun steps 2 and record the code
   change (the holdout will then need `--allow-code-change --reason`).
   Do not change the pre-registration, the universe, or the verdict rule.
   If the rule turns out mis-specified, report it as registered and add a
   labelled post-hoc section.
4. **Spot holdout, once.** `perpbt trend holdout`.
5. **Summary and merge.** `perpbt trend report`; commit
   `runs/trend/summary.md`, the primary in-sample and holdout reports with
   figures, `registry.jsonl`, `results.parquet`. Merge into `dev`, then
   `dev` into `main`.
6. **Forward review at 12 months** (forward start + 365 days):
   `perpbt trend forward --review 12`; commit its report and the summary
   row.
7. **Forward confirmation at 36 months** (forward start + 1,095 days):
   `perpbt trend forward --review 36`; commit; this row is the final
   verdict on the strategy.

## 7.3 What must not happen

- No edit to `configs/trend/prereg.yaml` after the freeze except
  `periods.forward.start` (set by `lock-code`).
- No second spot holdout without a recorded `DONE` deletion in the summary.
- No promotion of an exploratory variant to primary in this
  pre-registration.
- No pooling of forward and in-sample data in any test.
- No first-strategy command run against the modified tree (overview §11.9).

## 7.4 Deliverables

`runs/trend/summary.md`, the primary reports (in-sample, holdout, later
forward), `registry.jsonl`, `results.parquet`, `configs/trend/prereg.lock`
with the code version, and the tag `trend-prereg-v1`.
