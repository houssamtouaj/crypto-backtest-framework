# Phase 6 — Reporting

Read with `00-overview.md`. Delivers the figures, the per-run report and
the summary. `perpbt/report/carry/` is outside the carry code version (as
`report/` is for the first strategy), so report edits never count as code
changes for the holdout or forward runs.

**Branch:** `carry/6-reporting`, from `dev` after Phase 5 is merged.

## 6.1 Figures (per run)

- Equity curve of the primary, `always_on`, and the hurdle line (5%/yr
  compounding), log scale.
- Trailing annualised funding per pair with the entry/exit thresholds and
  the on periods shaded (one small panel per pair).
- Basis (perp/spot − 1) per pair, daily, with the on periods shaded.
- Return decomposition per year: funding, basis, fees, slippage,
  rebalances, liquidations (stacked bars).
- V2: the bootstrap distribution of the annualised excess return with the
  0 line and the CI; the random-timing distribution with the primary
  marked.

Figures follow the repo's existing helpers (`perpbt/report/figures.py`
style: PNG, fixed sizes, no interactive output).

## 6.2 Report (`runs/carry/<variant_id>/report.md`)

Headline: the verdict sentence (edge / positive below the hurdle / no
edge) with V1 and V2 p-values, the annualised net return and its CI, and
the three-way V2 reading. Then the diagnostics of Phase 4 §4.4 as tables,
the hurdle table (0 / 3 / 5 / 8%), the re-pricing table, and the stated
unmodelled risks (overview §5 item 5).

## 6.3 Summary (`runs/carry/summary.md`)

One page: the question, the rule, the periods, the verdict per period
(in-sample, holdout, forward when it exists), with links to the reports.
When the screen stopped the project, the summary is the screen's.

## 6.4 Tests

Reports render from a synthetic run; every number in the headline comes
from `stats.json`; byte-identical on rerun.

## 6.5 Exit criterion

Tests green; a synthetic run's report reviewed by eye.

## 6.6 Implementation notes (Phase 6 build, 2026-10-24)

Builder's calls taken while building this phase. Nothing was run on real data: no carry run, holdout, forward or
report command ran on the real `runs/`, and no return, Sharpe, p-value, P&L or funding total was computed on real
data. The calls are also in the docstrings of `perpbt/report/carry/{figures,report,summary}.py` and
`perpbt/carry/runner.py`.

- **Code location.** `perpbt/report/carry/` (`figures.py`, `report.py`, `summary.py`), outside `CARRY_CODE_PATHS`
  (a test checks it), so a report edit after the lock is never a carry code change. The report code loads no market
  data (an AST test): it reads run folders, the registry, `holdout/DONE`, `screen.json`, the prereg and its lock.
- **Funding and basis series** *(my call)*. The run folder had no trailing funding or basis, and the report cannot
  load holdout or forward data (only `carry/holdout.py` and `carry/forward.py` may pass `allow_holdout`). The runner
  now writes `signals.parquet` in every run folder and in the forward reviews: per pair and frame day of the period,
  `date, pair, fbar, on, basis` (`runner.signal_table`). It is a carry code change made before the lock (Phase 7
  locks), it changes no other file of a folder (`daily.parquet` and `stats.json` keep their bytes), and a rerun is
  byte-identical. It is not committed (the tables stay git-ignored).
- **Hedged shading** *(my call)*: the funding and basis panels shade the days the pair is hedged at the close
  (`daily_pair.perp_qty < 0`), the position actually held, not the decision.
- **V2 replicates** *(my call)*. `stats.json` keeps only V2's CI and p, so the bootstrap figure redraws the replicates
  at report time with the verdict's own seed (`<period>/<row>/v2`, the prereg's B and block length) on
  `daily.parquet`'s returns; it is drawn only when their p and CI equal `stats.json`'s (else a placeholder and a
  note). Saving the replicates in the run folder would have meant computing them twice in the runner (the screen's
  `v2_stats` stays as frozen).
- **always_on in the equity figure** *(my call)*: read from the `always_on` portfolio folder of the same period. The
  forward reviews run only the primary, so their figure shows the row and the hurdle; the report says so.
- **Hurdle line**: `E0 (1 + h)^(t/365)` from the first day's starting equity, V2's daily rate.
- **Decomposition figure** *(my call)*: funding, basis, −fees and −slippage are stacked (they sum to the net, which
  is marked); rebalance and liquidation costs are a second panel ("of which"), since they are a part of fees and
  slippage and stacking them would count them twice.
- **Which runs get a report** *(my call)*: every portfolio folder (the primary and the six exploratory rows of each
  period, and each forward review): 7 + 7 + 2 once everything has run. A per-pair folder gets none; its row's report
  has a "Per-pair rows" table from its `stats.json` (a pair not run alone, as EOSUSDT in the holdout, is marked).
  Exploratory reports have the full layout with a "Descriptive (… no say in the verdict): it would read …" headline;
  their filter-value, random-timing and DSR sections say "primary only".
- **Headline** *(my call)*: the verdict sentence (edge / positive below the hurdle / no edge) with V1 and V2
  pass/fail and p, the annualised net return and its CI, the excess and its CI with the three-way reading; then a
  table of the same `stats.json` values. Holdout reports put the overview §4 caveat right under it; in-sample and
  36-month reports state their period's role (the 36-month review is "the only fully unseen test").
- **12-month review layout**: Review (the six metrics against the in-sample per-year range, inside yes/no), Equity
  (point Sharpe, no CI), decomposition, funding and basis, margin events, risks, notes; no p-value anywhere (a test).
- **Margin events table**: liquidations first, then rebalances, at most 40 rows; the rest are in `stats.json` and
  `events.parquet` (the report says how many).
- **Re-pricing note**: the table says no cell equals the verdict's costs and quotes the prereg's per-pair slippage.
- **Summary**: the question, the rule (from the prereg), the screen line (`screen.json`), the code lock, a
  periods table (in-sample, holdout with its `DONE` status, the 12- and 36-month reviews) with links, the verdict
  sentences, the holdout caveat, the exploratory rows of the in-sample and the holdout, the unmodelled risks and
  registry notes (output-neutral rows, code-change reasons). When `screen.json` says stop, `summary.md` is the
  screen's and is left untouched.
- **`perpbt carry report [--prereg] [--runs-dir]`** writes every portfolio report and the summary from what exists;
  it refuses (exit 1) an unfrozen or changed prereg and any `stats.json` of another prereg hash. A rerun gives the
  same bytes (reports, PNGs, summary).
- **`.gitignore`** admits `runs/carry/[holdout/|forward/]*/report.md` and `…/figures/*.png` (every report written is
  one Phase 7 commits); the Parquet tables stay ignored.

**Exit criterion.** `tests/carry/reportenv.py` builds the synthetic world (lock-code, `carry run`, `carry holdout`,
both forward reviews; CCCUSDT delisted as EOSUSDT); the report tests check every headline and table number against
`stats.json`, the byte-identical rerun and the linked figures. Its reports were rendered and looked at by eye
(every figure of the in-sample primary, the holdout `always_on` funding panels with the delisted pair, the 12-month
review).

**Review fixes (fresh reviewer, 2026-10-24: "merge with fixes"; 0 Critical, 1 Important, 8 Minor; all fixed or
noted).**
- (Important) The summary's period and exploratory tables and the reports' "Per-pair rows" showed the net-return CI
  beside a V2 reading that refers to the excess CI, so a "not proven" row could print a CI that visibly excludes 0.
  They now show the excess over the hurdle, its CI ("95% CI (excess)") and the V2 reading together (overview §3);
  the per-pair table gained the V2 reading.
- (Minor) The 36-month report's per-pair section says the forward reviews run the portfolio primary only, instead
  of listing every pair as not run; a holdout per-pair variant listed in `DONE["failed"]` is marked "failed".
- (Minor) The equity prose names `always_on`'s own perp leverage (it is not `lev_1`'s).
- (Minor) Hedged shading covered k − 1 days of a k-day run (`fill_between` with `step="post"`); `figures.shade_mask`
  extends each run by one day.
- (Minor) The equity table joins the row and `always_on` by label, not by position.
- (Minor) An unknown `stats.json` schema refuses before any report is written.
- (Minor) Tests: `tests/carry/test_report_text.py` renders the headline sentence and table for every reading
  (edge, positive below the hurdle with not proven and with proven below, no edge both ways) and checks each headline
  row against its `stats.json` key; the headline numbers are checked to half a unit of the printed digit.
- (Minor) Wording: the 12-month review says the share on is not annualised; the hurdle note prints the hurdle as the
  table does.
- (Noted) Phase 7 will commit about 10-15 MB of PNGs (ten pairs make the per-pair panels larger), as the first
  strategy committed its figures.
