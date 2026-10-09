# Phase 8 — Runs

Read with `00-overview.md`. This phase produces no code. It is the
protocol for running the pre-registered experiment once Phases 0–7 are
green. The order matters: nothing in a later step may inform an earlier
one, and the holdout is touched exactly once.

**Branch:** `phase/8-runs`, created from `dev` after Phase 7 is merged.
Every commit of this phase (manifests, the freeze, results, the summary)
lands on this branch. It is merged into `dev` at step 8, and `dev` is then
merged into `main`. This is the only time `main` moves (overview §8.1).

## 8.1 Preconditions

- All non-slow tests green on `dev`; slow tests green at least once on
  real data.
- Phases 0–7 merged into `dev`; `phase/8-runs` created from that `dev`.
- `git status` clean; the code that will run is committed.
- Disk: at least 3 GB free on D: (1.2 GB data, transient zips, run
  outputs).

## 8.2 Protocol

1. **Fetch and validate data.**
   `perpbt data fetch --ccxt-head --ccxt-tail` then `perpbt data validate`.
   Review the gap and 1m→15m consistency counts in every manifest. Commit
   the manifests. Set `data_download_date` and `holdout.end` in
   `prereg.yaml`.
2. **Freeze.** `perpbt prereg validate`, then `perpbt prereg freeze`.
   Commit `prereg.yaml` and `prereg.lock` together with the message
   "Freeze pre-registration". Tag the commit `prereg-v1`. From here on,
   any edit to `perpbt/` is a code change the holdout will report.
3. **In-sample primary.** `perpbt run --primary` (nine cells), then
   `perpbt baselines --primary --runs 5000`, then `perpbt stats holm`.
   Generate the nine reports.
4. **Grid.** `perpbt grid --run --workers 8`, then
   `perpbt baselines --grid --runs 500`, then `perpbt stats dsr` (the DSR
   needs every grid cell). Generate the grid reports and the heatmaps.
5. **Review gate.** Read the nine primary reports and the heatmaps. Fix
   bugs if any are found, commit them, and rerun steps 3–4 (the registry
   keeps every attempt and the summary will show the code-version
   history). Do **not** change the primary parameters or the verdict
   rule; if the review shows the pre-registered primary was mis-specified,
   the honest path is to report the pre-registered result as is and add a
   clearly labelled post-hoc section.
6. **Holdout, once.** `perpbt holdout` (with `--allow-code-change --reason`
   only if step 5 changed `perpbt/`). Nine cells, baselines at 5,000
   runs, Holm over the holdout family.
7. **Summary.** `perpbt report --summary`. Commit `runs/summary.md`, the
   nine primary `report.md` files, `runs/registry.jsonl`, and
   `runs/results.parquet` (the Parquet trade tables stay git-ignored;
   they are reproducible from the code, data manifests, and seeds).
8. **Merge.** Merge `phase/8-runs` into `dev` with `--no-ff` and delete
   the branch. Then open a pull request from `dev` to `main` containing
   the code, the specs, the manifests, the lock, and the committed
   results.

## 8.3 What must not happen

- No `allow_holdout=True` outside `experiments/holdout.py`, and no
  `read_frame` outside `perpbt/data/` and `tests/`; the grep for both is part of the
  review at step 5.
- No edit to `prereg.yaml` after the freeze except `data_download_date`
  and `holdout.end`.
- No second holdout run without a `DONE` deletion recorded in the
  summary.
- No selection of the "primary" among the 324 grid cells after seeing
  results. The grid is for robustness and the DSR, not for picking.

## 8.4 Deliverables

- `runs/summary.md` with the verdict table and the plain-language answer.
- Nine primary reports and 324 grid reports (grid reports git-ignored,
  regenerable).
- `runs/results.parquet` and `runs/registry.jsonl`.
- `configs/prereg.lock` and the `prereg-v1` tag.

## 8.5 Expected wall-clock (from the overview budgets)

| Step | Budget |
|---|---|
| 1 fetch and validate | 10–20 min |
| 3 primary runs + baselines | 30–90 min |
| 4 grid + baselines | 1–2 h |
| 6 holdout | 10–30 min |
| 7 reports | 5–10 min |

If step 4 exceeds two hours, apply the signal-stream cache described in
the overview before continuing; it does not change any result.
