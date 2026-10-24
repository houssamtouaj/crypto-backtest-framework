# Carry Phase 7 — Runs (plan and progress log)

Spec: `docs/superpowers/specs/2026-10-23-funding-carry/phase-7-runs.md` (with `00-overview.md` §2 CD5/CD8/CD13, §3,
§4 "How clean the holdout is", §5; Phase 5 §5.9; Phase 6 §6.6). Branch `carry/7-runs` from dev 675dffd.

No code is written in this phase. Every step that cannot be undone stops for the user's go. No step is re-run with
changes after its result is seen; a technical failure stops the phase, and a fix needs the user's go, a registry
record (the output-neutral check of Phase 5 §5.9) and a rerun of that step only.

## Steps

- [x] 1. Fetch both legs' 15m tails (perps with `--ccxt-tail`, funding included; spot from the archive) for the ten
  members with `configs/carry/data.yaml`; `data validate` both markets; a manifest-only check that every member
  reaches 2026-10-20 in full or is a known delisting (`runner.KNOWN_DELISTINGS`); commit the manifests.
- [x] 2. Full test suite once (max timeout), then **ask the user**; `perpbt carry lock-code`; commit
  `configs/carry/prereg.yaml` and `prereg.lock`.
- [ ] 3. `perpbt carry run --primary`, then `--exploratory` (foreground, one at a time), then `perpbt carry report`;
  commit the registry, `results.parquet`, every `stats.json`, the reports, figures and `runs/carry/summary.md`.
- [ ] 4. Report the in-sample verdict to the user.
- [ ] 5. **Ask the user**; `perpbt carry holdout` (once); `perpbt carry report`; commit `runs/carry/holdout/`
  (stats.json, reports, figures, `DONE`), the registry, `results.parquet` and the summary.
- [ ] 6. Report the holdout verdict (with the "not fully unseen" caveat); spec §7.1 run notes; **ask** before the
  `--no-ff` merge into dev and before the dev → main pull request.
- [ ] 7. Forward reviews are later sessions (`perpbt carry forward --review 12` once 365 days exist, `--review 36`
  once 1,095 days exist), each on its own branch `carry/7-forward-<n>`.

## Progress log

Step 1: fetched and validated both legs (perps 28 s, spot 39 s); manifest-only check: 9 members full through
2026-10-20, EOSUSDT the known delisting (2025-05-21); spec §7.1.
Step 2 (before the lock): full suite 1489 passed, 2 known warnings (15 min 22 s; slower than Phase 6's 6 min 17 s,
same count).
Step 2: code locked on the user's go (code_version 7ce43816…, git ab95921; forward start 2026-10-25).
