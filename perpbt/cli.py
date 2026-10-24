"""Command-line entry point.

Phase 1 implements ``data fetch`` and ``data validate``; Phase 6
``prereg``, ``grid``, ``run``, ``baselines``, ``stats`` and ``holdout``;
Phase 7 ``report``; the trend strategy ``trend universe``, ``trend prereg`` and ``trend screen``; the carry strategy
``carry basis``, ``carry prereg`` and ``carry screen``. Exit codes: 0 success, 1 a download, checksum,
validation or run failure (or a refused holdout or family pass, or nothing
to report), 2 a usage or config error.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone
from pathlib import Path

from perpbt.carry.basis import basis_check
from perpbt.config import ConfigError, DataConfig, canonical_json, load_yaml, to_dict
from perpbt.data.bulk import MARKETS, ChecksumError, DownloadError
from perpbt.data.fetch import TIMEFRAMES, run_fetch
from perpbt.data.store import HoldoutAccessError
from perpbt.data.validate import run_validate
from perpbt.experiments import holdout as holdout_mod
from perpbt.experiments import runner
from perpbt.experiments.grid import differing_fields, enumerate_grid, primary_variants
from perpbt.experiments.prereg import Prereg, freeze, load_prereg, lock_path, read_lock
from perpbt.version import code_version

Handler = Callable[[argparse.Namespace], int]
DEFAULT_DATA_CONFIG = "configs/data.yaml"
DEFAULT_PREREG = "configs/prereg.yaml"
DEFAULT_DATA_DIR = "data"
DEFAULT_RUNS_DIR = "runs"
DEFAULT_UNIVERSE = "configs/trend/universe.json"
DEFAULT_TREND_PREREG = "configs/trend/prereg.yaml"
DEFAULT_TREND_DATA = "configs/trend/data.yaml"
DEFAULT_CARRY_DATA = "configs/carry/data.yaml"
DEFAULT_BASIS_OUT = "runs/carry/basis_check.json"
DEFAULT_CARRY_PREREG = "configs/carry/prereg.yaml"

def _fmt(value: object) -> str:
    if isinstance(value, dict):
        return " ".join(f"{k}={_fmt(v)}" for k, v in value.items())
    return str(value)


def _load_data_config(path: str, command: str) -> DataConfig | None:
    try:
        return load_yaml(path, DataConfig)
    except (OSError, ConfigError) as e:
        print(f"perpbt {command}: cannot load {path}: {e}", file=sys.stderr)
        return None


def _data_fetch(args: argparse.Namespace) -> int:
    cfg = _load_data_config(args.config, "data fetch")
    if cfg is None:
        return 2
    pairs = args.pairs or sorted(cfg.listing)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    try:
        summary = run_fetch(
            cfg,
            pairs=pairs,
            tfs=tuple(args.tfs),
            from_date=args.from_date,
            to_date=args.to_date,
            ccxt_head=args.ccxt_head,
            ccxt_tail=args.ccxt_tail,
            ccxt_gaps=args.ccxt_gaps,
            market=args.market,
        )
    except (DownloadError, ChecksumError, ValueError) as e:
        print(f"perpbt data fetch: {e}", file=sys.stderr)
        return 1
    for pair, entry in summary.items():
        for key, value in entry.items():
            print(f"{pair} {key}: {_fmt(value)}")
    return 0


def _data_validate(args: argparse.Namespace) -> int:
    cfg = _load_data_config(args.config, "data validate")
    if cfg is None:
        return 2
    pairs = args.pairs or sorted(cfg.listing)
    try:
        report = run_validate(cfg, pairs, market=args.market)
    except ValueError as e:
        print(f"perpbt data validate: {e}", file=sys.stderr)
        return 1
    for pair, entry in report.items():
        if not entry:
            print(f"{pair}: nothing stored")
            continue
        for key, value in entry.items():
            print(f"{pair} {key}: {_fmt(value)}")
    return 0


# --- experiments (Phase 6) -----------------------------------------------------------------------

def _load_prereg(args: argparse.Namespace, command: str) -> Prereg | None:
    try:
        return load_prereg(args.prereg)
    except (OSError, ConfigError) as e:
        print(f"perpbt {command}: cannot load {args.prereg}: {e}", file=sys.stderr)
        return None


def _log_to_stderr() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)


def _outcome(command: str, done: dict) -> int:
    print(f"perpbt {command}: " + ", ".join(f"{k} {len(v)}" for k, v in done.items()))
    return 1 if done.get("failed") or done.get("missing") else 0


def _prereg(args: argparse.Namespace) -> int:
    prereg = _load_prereg(args, f"prereg {args.prereg_command}")
    if prereg is None:
        return 2
    if args.prereg_command == "freeze":
        try:
            lock = freeze(args.prereg)
        except (FileExistsError, ConfigError) as e:
            print(f"perpbt prereg freeze: {e}", file=sys.stderr)
            return 1
        for k, v in lock.items():
            print(f"{k}: {v}")
        return 0
    grid = enumerate_grid(prereg)
    print(f"prereg_hash: {prereg.prereg_hash()}")
    print(f"pairs: {' '.join(prereg.pairs)}; sessions: {' '.join(prereg.sessions)}")
    print(f"grid: {len(grid)} variants, {sum(v.is_primary for v in grid)} primary")
    path = lock_path(args.prereg)
    if not path.exists():
        print("lock: not frozen")
        return 0
    lock = read_lock(path)
    same = lock["prereg_hash"] == prereg.prereg_hash()
    print(f"lock: frozen {lock['frozen_on']}; prereg hash {'matches' if same else 'DIFFERS'}; "
          f"code {'unchanged' if lock['code_version'] == code_version() else 'changed'} since the freeze")
    return 0 if same else 1


def _grid(args: argparse.Namespace) -> int:
    prereg = _load_prereg(args, "grid")
    if prereg is None:
        return 2
    grid = enumerate_grid(prereg)
    if args.dry_run:
        cv = code_version()
        for v in grid:
            plain = to_dict(v.cfg.params)
            diff = sorted(differing_fields(v.cfg.params, prereg.primary))
            role = "primary" if v.is_primary else ",".join(v.members)
            print(f"{v.cfg.pair} {v.cfg.session.name:6} {v.cfg.variant_id(cv)[:12]} {role:10} "
                  + " ".join(f"{k}={canonical_json(plain[k])}" for k in diff))
        cells = len(prereg.pairs) * len(prereg.sessions)
        print(f"{len(grid)} variants ({len(grid) // cells} per pair x session, {cells} cells)")
        return 0
    _log_to_stderr()
    done = runner.run_many(grid, prereg.data_config(args.data_dir), runs_dir=args.runs_dir, workers=args.workers,
                           force=args.force)
    return _outcome("grid", done)


def _run(args: argparse.Namespace) -> int:
    prereg = _load_prereg(args, "run")
    if prereg is None:
        return 2
    variants = [v for v in primary_variants(prereg)
                if args.pair in (None, v.cfg.pair) and args.session in (None, v.cfg.session.name)]
    if not variants:
        print("perpbt run: no primary cell matches --pair/--session", file=sys.stderr)
        return 2
    _log_to_stderr()
    done = runner.run_many(variants, prereg.data_config(args.data_dir), runs_dir=args.runs_dir,
                           workers=args.workers, force=args.force)
    return _outcome("run", done)


def _baselines(args: argparse.Namespace) -> int:
    prereg = _load_prereg(args, "baselines")
    if prereg is None:
        return 2
    variants = primary_variants(prereg) if args.primary else [v for v in enumerate_grid(prereg) if not v.is_primary]
    _log_to_stderr()
    done = runner.baselines_many(variants, prereg.data_config(args.data_dir), runs_dir=args.runs_dir,
                                 n_runs=args.runs, workers=args.workers, force=args.force)
    return _outcome("baselines", done)


def _stats(args: argparse.Namespace) -> int:
    prereg = _load_prereg(args, f"stats {args.stats_command}")
    if prereg is None:
        return 2
    cv = code_version()
    try:
        if args.stats_command == "holm":
            primary = primary_variants(prereg)
            blocks = runner.apply_holm(args.runs_dir, [v.cfg.variant_id(cv) for v in primary], family="insample",
                                       alpha=prereg.stats.alpha, baseline_runs=prereg.stats.baseline_runs_primary)
            for v, b in zip(primary, blocks, strict=True):
                print(f"{v.cfg.pair} {v.cfg.session.name:6} p_a_adj {b['p_a_adj']} p_b_adj {b['p_b_adj']} "
                      f"p_bh_adj {b['p_bh_adj']}")
        elif args.stats_command == "dsr":
            cells: dict[tuple[str, str], list[str]] = {}
            for v in enumerate_grid(prereg):
                cells.setdefault(v.cell, []).append(v.cfg.variant_id(cv))
            out = runner.apply_dsr(args.runs_dir, cells)
            for v in primary_variants(prereg):
                d = out[v.cfg.variant_id(cv)]
                print(f"{v.cfg.pair} {v.cfg.session.name:6} dsr_local {d['local']['dsr']} (N {d['local']['N']}) "
                      f"dsr_global {d['global']['dsr']} (N {d['global']['N']})")
        else:
            print(f"wrote {runner.rebuild_results(args.runs_dir)}")
    except runner.FamilyIncomplete as e:
        print(f"perpbt stats {args.stats_command}: {e}", file=sys.stderr)
        return 1
    return 0


def _holdout(args: argparse.Namespace) -> int:
    if args.reason and not args.allow_code_change:
        print("perpbt holdout: --reason is only used with --allow-code-change", file=sys.stderr)
        return 2
    _log_to_stderr()
    try:
        done = holdout_mod.run_holdout(args.prereg, data_dir=args.data_dir, runs_dir=args.runs_dir,
                                       allow_code_change=args.allow_code_change, reason=args.reason)
    except holdout_mod.HoldoutRefused as e:
        print(f"perpbt holdout: refused: {e}", file=sys.stderr)
        return 1
    except (OSError, ConfigError) as e:
        print(f"perpbt holdout: {e}", file=sys.stderr)
        return 2
    for k, v in done.items():
        print(f"{k}: {v}")
    return 0 if done["status"] == "ok" else 1


def _report(args: argparse.Namespace) -> int:
    # imported here so the other commands do not load matplotlib
    from perpbt.experiments.registry import read_results, rebuild_results
    from perpbt.report.report import write_reports
    from perpbt.report.summary import write_summary

    prereg = _load_prereg(args, "report")
    if prereg is None:
        return 2
    runs = Path(args.runs_dir)
    if not (runs / "results.parquet").exists():  # every run and family pass rebuilds it; never overwrite it here
        rebuild_results(runs)
    results = read_results(runs)
    if not results["is_primary"].fillna(False).astype(bool).any():
        print(f"perpbt report: no primary variant in {runs}/results.parquet; run `perpbt run --primary` first",
              file=sys.stderr)
        return 1
    paths = write_reports(runs, results, prereg, prereg.data_config(args.data_dir), grid=args.grid)
    summary = write_summary(runs, prereg, args.prereg, results)
    for p in paths:
        print(p.as_posix())
    print(f"perpbt report: {len(paths)} reports; summary {summary.as_posix()}")
    return 0


# --- trend strategy ------------------------------------------------------------------------------


def _trend_universe(args: argparse.Namespace) -> int:
    from perpbt.trend.universe import rank_universe, write_universe

    _log_to_stderr()
    try:
        block = rank_universe(args.market, today=datetime.now(timezone.utc).date())
    except (DownloadError, ChecksumError) as e:
        print(f"perpbt trend universe: {e}", file=sys.stderr)
        return 1
    write_universe(args.out, args.market, block)
    print(f"{args.market}: {len(block['ranked'])} candidates, {len(block['dropped'])} dropped; "
          f"members: {' '.join(block['members'])}")
    print(f"wrote {Path(args.out).as_posix()}")
    return 0


def _trend_prereg(args: argparse.Namespace) -> int:
    from perpbt.trend.prereg import check_universe, freeze, load_prereg

    try:
        prereg = load_prereg(args.prereg)
        check_universe(prereg, json.loads(Path(args.universe).read_text(encoding="utf-8")))
    except (ConfigError, OSError, ValueError) as e:
        print(f"perpbt trend prereg: {e}", file=sys.stderr)
        return 2
    if args.trend_prereg_command == "validate":
        print(f"{Path(args.prereg).as_posix()}: valid; prereg_hash {prereg.prereg_hash()}")
        return 0
    try:
        lock = freeze(args.prereg, universe_path=args.universe)
    except FileExistsError as e:
        print(f"perpbt trend prereg freeze: {e}", file=sys.stderr)
        return 1
    print(f"frozen on {lock['frozen_on']}: prereg_hash {lock['prereg_hash']} (git {lock['git_commit']})")
    return 0


def _trend_screen(args: argparse.Namespace) -> int:
    from perpbt.trend.prereg import check_frozen
    from perpbt.trend.screen import screen_from_store, screen_path, write_screen
    from perpbt.version import git_commit

    _log_to_stderr()
    try:
        prereg = check_frozen(args.prereg)
    except ConfigError as e:
        print(f"perpbt trend screen: refused: {e}", file=sys.stderr)
        return 1
    out = screen_path(args.runs_dir)
    if out.exists():
        print(f"perpbt trend screen: refused: {out.as_posix()} exists; the screen runs once", file=sys.stderr)
        return 1
    try:
        result = screen_from_store(prereg, load_yaml(args.data_config, DataConfig))
    except ConfigError as e:
        print(f"perpbt trend screen: {e}", file=sys.stderr)
        return 2
    result = {**result, "computed_on": datetime.now(timezone.utc).date().isoformat(), "git_commit": git_commit()}
    paths = write_screen(result, args.runs_dir)
    q = result["paths"]
    print(f"observed gross Sharpe {result['observed_sharpe']:.3f}; {q['n']} random-days paths: "
          f"5/50/95% {q['p05']:.3f} / {q['p50']:.3f} / {q['p95']:.3f}")
    print(f"p_screen {result['p_screen']:.4f} (stop if >= {result['stop_if_p_at_least']}): {result['decision']}")
    print(f"vol-targeted buy-and-hold gross Sharpe (descriptive) {result['bh_sharpe']:.3f}")
    for p in paths:
        print(f"wrote {p.as_posix()}")
    return 0


# --- carry ---------------------------------------------------------------------------------------


def _carry_basis(args: argparse.Namespace) -> int:
    cfg = _load_data_config(args.config, "carry basis")
    if cfg is None:
        return 2
    pairs = args.pairs or list(cfg.listing)
    try:
        result = basis_check(cfg, pairs)
    except FileNotFoundError as e:
        print(f"perpbt carry basis: {e}", file=sys.stderr)
        return 2
    except HoldoutAccessError as e:
        print(f"perpbt carry basis: {e}", file=sys.stderr)
        return 1
    try:
        text = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    except ValueError as e:  # a zero or non-finite close
        print(f"perpbt carry basis: non-finite basis statistic: {e}", file=sys.stderr)
        return 1
    print(f"window {result['window'][0]}..{result['window'][1]}; flag |median| > {result['limits']['median_abs']}"
          f" or max |basis| > {result['limits']['max_abs']}")
    for pair, st in result["pairs"].items():
        if st["common"] == 0:
            print(f"{pair}: no common slots  FLAGGED")
            continue
        flag = f"  FLAGGED ({', '.join(st['reasons'])})" if st["flagged"] else ""
        print(f"{pair}: coverage {st['coverage']:.4f} (perp {st['perp_rows']}, spot {st['spot_rows']}, "
              f"common {st['common']} of {st['slots']}); median {st['median']:+.5f}, p01 {st['p01']:+.5f}, "
              f"p99 {st['p99']:+.5f}, max |basis| {st['max_abs']:.4f} ({st['basis_at_max_abs']:+.4f}) "
              f"at {st['max_abs_at']}{flag}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {out.as_posix()}")
    return 0


def _carry_prereg(args: argparse.Namespace) -> int:
    from perpbt.carry.prereg import check_universe, freeze, load_prereg

    try:
        prereg = load_prereg(args.prereg)
        check_universe(prereg, json.loads(Path(args.universe).read_text(encoding="utf-8")))
    except (ConfigError, OSError, ValueError) as e:
        print(f"perpbt carry prereg: {e}", file=sys.stderr)
        return 2
    if args.carry_prereg_command == "validate":
        print(f"{Path(args.prereg).as_posix()}: valid; prereg_hash {prereg.prereg_hash()}")
        return 0
    try:
        lock = freeze(args.prereg, universe_path=args.universe)
    except FileExistsError as e:
        print(f"perpbt carry prereg freeze: {e}", file=sys.stderr)
        return 1
    print(f"frozen on {lock['frozen_on']}: prereg_hash {lock['prereg_hash']} (git {lock['git_commit']})")
    return 0


def _carry_screen(args: argparse.Namespace) -> int:
    from perpbt.carry.prereg import check_frozen
    from perpbt.carry.screen import screen_from_store, screen_path, write_screen
    from perpbt.version import git_commit

    _log_to_stderr()
    try:
        prereg = check_frozen(args.prereg)
    except ConfigError as e:
        print(f"perpbt carry screen: refused: {e}", file=sys.stderr)
        return 1
    out = screen_path(args.runs_dir)
    if out.exists():
        print(f"perpbt carry screen: refused: {out.as_posix()} exists; the screen runs once", file=sys.stderr)
        return 1
    try:
        result = screen_from_store(prereg, load_yaml(args.data_config, DataConfig))
    except (ConfigError, FileNotFoundError) as e:
        print(f"perpbt carry screen: {e}", file=sys.stderr)
        return 2
    except (HoldoutAccessError, ValueError, KeyError) as e:  # past the in-sample end; a non-finite statistic
        print(f"perpbt carry screen: refused: {e}", file=sys.stderr)
        return 1
    result = {**result, "computed_on": datetime.now(timezone.utc).date().isoformat(), "git_commit": git_commit()}
    try:
        paths = write_screen(result, args.runs_dir)
    except (FileExistsError, ValueError) as e:
        print(f"perpbt carry screen: refused: {e}", file=sys.stderr)
        return 1
    print(f"{result['n_days']} days ({result['period'][0]}..{result['period'][1]}), {result['n_pairs']} pairs; "
          f"hurdle {result['hurdle_annual']:.0%}/yr")
    for name, row in result["rows"].items():
        v2, v1 = row["v2"], row["v1"]
        sr = "n/a" if v1["sharpe"] is None else f"{v1['sharpe']:.3f}"
        p1 = "n/a" if v1["p"] is None else f"{v1['p']:.4f}"
        print(f"{name}: annualised net return {v2['ann_return']:+.4f} "
              f"[{v2['ann_return_ci'][0]:+.4f}, {v2['ann_return_ci'][1]:+.4f}]; excess {v2['ann_excess']:+.4f} "
              f"[{v2['ann_excess_ci'][0]:+.4f}, {v2['ann_excess_ci'][1]:+.4f}], p(V2) {v2['p']:.4f} "
              f"({v2['reading']}); Sharpe {sr}, p(V1) {p1}; share on {row['share_on']:.3f}, "
              f"switches {row['switches']}")
    print(f"p_screen {result['p_screen']:.4f} (stop if >= {result['stop_if_p_at_least']}): {result['decision']}")
    for p in paths:
        print(f"wrote {p.as_posix()}")
    return 0


def _carry_lock_code(args: argparse.Namespace) -> int:
    from perpbt.carry.prereg import LockRefused, lock_code

    try:
        lock = lock_code(args.prereg)
    except LockRefused as e:
        print(f"perpbt carry lock-code: refused: {e}", file=sys.stderr)
        return 1
    print(f"code locked on {lock['code_locked_on']}: code_version {lock['code_version']} "
          f"(git {lock['code_git_commit']}); periods.forward.start set in {Path(args.prereg).as_posix()}")
    return 0


def _carry_done(command: str, done: dict) -> int:
    print(f"perpbt carry {command}: " + ", ".join(f"{k} {len(v)}" for k, v in done.items()))
    for name in done.get("failed", []):
        print(f"  failed: {name} (traceback in the registry)", file=sys.stderr)
    return 1 if done.get("failed") else 0


def _carry_run(args: argparse.Namespace) -> int:
    from perpbt.carry.prereg import load_prereg
    from perpbt.carry.runner import CodeRefused, run_insample

    _log_to_stderr()
    cfg = _load_data_config(args.data_config, "carry run")
    if cfg is None:
        return 2
    rows = None
    try:
        if args.primary:
            rows = ["primary"]
        elif args.exploratory:
            rows = [e.name for e in load_prereg(args.prereg).exploratory]
        done = run_insample(args.prereg, cfg, args.runs_dir, rows=rows, allow_code_change=args.allow_code_change,
                            reason=args.reason)
    except (ConfigError, CodeRefused, HoldoutAccessError) as e:
        print(f"perpbt carry run: refused: {e}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as e:
        print(f"perpbt carry run: {e}", file=sys.stderr)
        return 2
    return _carry_done("run", done)


def _carry_holdout(args: argparse.Namespace) -> int:
    from perpbt.carry.holdout import HoldoutRefused, run_holdout

    _log_to_stderr()
    cfg = _load_data_config(args.data_config, "carry holdout")
    if cfg is None:
        return 2
    try:
        done = run_holdout(args.prereg, cfg, runs_dir=args.runs_dir, allow_code_change=args.allow_code_change,
                           reason=args.reason)
    except HoldoutRefused as e:
        print(f"perpbt carry holdout: refused: {e}", file=sys.stderr)
        return 1
    print(f"perpbt carry holdout: {done['status']}; {len(done['variant_ids'])} variants, failed "
          f"{len(done['failed'])}, skipped {len(done['skipped'])}; DONE written")
    for name in done["failed"]:
        print(f"  failed: {name} (traceback in the registry)", file=sys.stderr)
    return 0 if done["status"] == "ok" else 1


def _add_code_change_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--allow-code-change", action="store_true",
                   help="run although the carry code version differs from the lock (needs --reason)")
    p.add_argument("--reason", help="why the code changed; recorded in the registry")


def _add_exp_args(p: argparse.ArgumentParser, *, pool: bool = False) -> None:
    p.add_argument("--prereg", default=DEFAULT_PREREG, help="pre-registration YAML (default: %(default)s)")
    p.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="market data directory (default: %(default)s)")
    p.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR, help="run outputs and the registry (default: %(default)s)")
    if pool:
        p.add_argument("--workers", type=int, default=4,
                       help="worker processes, about 0.75 GB each at peak (default: %(default)s)")
        p.add_argument("--force", action="store_true", help="rerun variants that are already done")


def _add_data_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", default=DEFAULT_DATA_CONFIG, help="DataConfig YAML (default: %(default)s)")
    p.add_argument(
        "--pairs", nargs="+", metavar="PAIR", help="pairs to process (default: every pair listed in the config)"
    )
    p.add_argument(
        "--market", choices=MARKETS, default="um",
        help="um (USDT-M perpetuals) or spot (the trend holdout; no funding, no ccxt) (default: %(default)s)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="perpbt",
        description="Backtest framework for Binance USDT-M perpetuals.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    data = sub.add_parser("data", help="fetch and validate market data")
    data_sub = data.add_subparsers(dest="data_command", metavar="SUBCOMMAND", required=True)

    fetch = data_sub.add_parser("fetch", help="download the bulk archive and the ccxt head/tail")
    _add_data_args(fetch)
    fetch.add_argument(
        "--tfs", nargs="+", choices=TIMEFRAMES, default=list(TIMEFRAMES), metavar="TF",
        help="timeframes, 1m and/or 15m (default: both)",
    )
    fetch.add_argument(
        "--from", dest="from_date", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD",
        help="first day to fetch (default: the config's warmup_start)",
    )
    fetch.add_argument(
        "--to", dest="to_date", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD",
        help="last day to fetch (default: today, UTC)",
    )
    fetch.add_argument("--ccxt-head", action="store_true", help="backfill 15m candles before 2020-01 via ccxt (D14)")
    fetch.add_argument("--ccxt-tail", action="store_true", help="extend candles and funding to now via ccxt")
    fetch.add_argument("--ccxt-gaps", action="store_true", help="fill holes in the bulk archive via ccxt (once per gap)")
    fetch.set_defaults(handler=_data_fetch)

    validate = data_sub.add_parser("validate", help="validate stored candles and funding")
    _add_data_args(validate)
    validate.set_defaults(handler=_data_validate)

    prereg = sub.add_parser("prereg", help="validate or freeze the pre-registration")
    prereg.add_argument("prereg_command", choices=("validate", "freeze"))
    _add_exp_args(prereg)
    prereg.set_defaults(handler=_prereg)

    grid = sub.add_parser("grid", help="enumerate (--dry-run) or run the pre-registered grid")
    mode = grid.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print the variants and their count")
    mode.add_argument("--run", action="store_true", help="run every grid variant not already done")
    _add_exp_args(grid, pool=True)
    grid.set_defaults(handler=_grid)

    run = sub.add_parser("run", help="run the nine primary cells (or some of them) end to end")
    run.add_argument("--primary", action="store_true", required=True, help="the primary cells")
    run.add_argument("--pair", help="only this pair")
    run.add_argument("--session", help="only this session variant")
    _add_exp_args(run, pool=True)
    run.set_defaults(handler=_run)

    baselines = sub.add_parser("baselines", help="run random-timing baselines A and B on stored variants")
    which = baselines.add_mutually_exclusive_group(required=True)
    which.add_argument("--primary", action="store_true", help="the nine primary cells")
    which.add_argument("--grid", action="store_true", help="every non-primary grid variant")
    baselines.add_argument("--runs", type=int, default=None,
                           help="runs per baseline (default: the prereg budget for primary or grid cells)")
    _add_exp_args(baselines, pool=True)
    baselines.set_defaults(handler=_baselines)

    stats = sub.add_parser("stats", help="family statistics: holm (primary cells), dsr (grid), results table")
    stats.add_argument("stats_command", choices=("holm", "dsr", "results"))
    _add_exp_args(stats)
    stats.set_defaults(handler=_stats)

    holdout = sub.add_parser("holdout", help="one-shot holdout run of the primary cells, with the guard")
    holdout.add_argument("--allow-code-change", action="store_true", help="run although perpbt/ changed since freeze")
    holdout.add_argument("--reason", help="why the code changed (required with --allow-code-change)")
    _add_exp_args(holdout)
    holdout.set_defaults(handler=_holdout)

    report = sub.add_parser("report", help="write the per-variant reports and the summary")
    report.add_argument("--grid", action="store_true", help="also write a report for every in-sample grid variant")
    _add_exp_args(report)
    report.set_defaults(handler=_report)

    trend = sub.add_parser("trend", help="the daily trend-following strategy")
    trend_sub = trend.add_subparsers(dest="trend_command", metavar="SUBCOMMAND", required=True)
    universe = trend_sub.add_parser("universe", help="rank the point-in-time universe from the archive")
    universe.add_argument("--market", choices=MARKETS, required=True, help="um (perpetuals) or spot (the holdout)")
    universe.add_argument("--out", default=DEFAULT_UNIVERSE, help="universe file (default: %(default)s)")
    universe.set_defaults(handler=_trend_universe)
    tprereg = trend_sub.add_parser("prereg", help="validate or freeze the trend pre-registration")
    tprereg.add_argument("trend_prereg_command", choices=("validate", "freeze"))
    tprereg.add_argument("--prereg", default=DEFAULT_TREND_PREREG, help="trend pre-registration (default: %(default)s)")
    tprereg.add_argument("--universe", default=DEFAULT_UNIVERSE, help="universe file (default: %(default)s)")
    tprereg.set_defaults(handler=_trend_prereg)
    screen = trend_sub.add_parser("screen", help="the one-shot gross signal screen (go / stop), after the freeze")
    screen.add_argument("--prereg", default=DEFAULT_TREND_PREREG, help="trend pre-registration (default: %(default)s)")
    screen.add_argument("--data-config", default=DEFAULT_TREND_DATA, help="perp DataConfig (default: %(default)s)")
    screen.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR, help="run outputs (default: %(default)s)")
    screen.set_defaults(handler=_trend_screen)

    carry = sub.add_parser("carry", help="the funding-rate carry strategy")
    carry_sub = carry.add_subparsers(dest="carry_command", metavar="SUBCOMMAND", required=True)
    basis = carry_sub.add_parser("basis", help="perp/spot basis sanity check over 2020-07-01..2023-12-31 (no returns)")
    basis.add_argument("--config", default=DEFAULT_CARRY_DATA, help="DataConfig for both stores (default: %(default)s)")
    basis.add_argument("--pairs", nargs="+", metavar="PAIR", help="pairs (default: every pair listed in the config)")
    basis.add_argument("--out", default=DEFAULT_BASIS_OUT, help="JSON output (default: %(default)s)")
    basis.set_defaults(handler=_carry_basis)
    cprereg = carry_sub.add_parser("prereg", help="validate or freeze the carry pre-registration")
    cprereg.add_argument("carry_prereg_command", choices=("validate", "freeze"))
    cprereg.add_argument("--prereg", default=DEFAULT_CARRY_PREREG, help="carry pre-registration (default: %(default)s)")
    cprereg.add_argument("--universe", default=DEFAULT_UNIVERSE, help="universe file (default: %(default)s)")
    cprereg.set_defaults(handler=_carry_prereg)
    cscreen = carry_sub.add_parser("screen", help="the one-shot simplified net screen (go / stop), after the freeze")
    cscreen.add_argument("--prereg", default=DEFAULT_CARRY_PREREG, help="carry pre-registration (default: %(default)s)")
    cscreen.add_argument("--data-config", default=DEFAULT_CARRY_DATA,
                         help="DataConfig for both stores (default: %(default)s)")
    cscreen.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR, help="run outputs (default: %(default)s)")
    cscreen.set_defaults(handler=_carry_screen)
    clock = carry_sub.add_parser("lock-code", help="lock the carry code version once and set the forward start "
                                                   "(immediately before the in-sample primary run)")
    clock.add_argument("--prereg", default=DEFAULT_CARRY_PREREG, help="carry pre-registration (default: %(default)s)")
    clock.set_defaults(handler=_carry_lock_code)
    crun = carry_sub.add_parser("run", help="the in-sample variants (all rows, each for the portfolio and each pair)")
    which = crun.add_mutually_exclusive_group()
    which.add_argument("--primary", action="store_true", help="only the primary row")
    which.add_argument("--exploratory", action="store_true", help="only the six exploratory rows")
    crun.add_argument("--prereg", default=DEFAULT_CARRY_PREREG, help="carry pre-registration (default: %(default)s)")
    crun.add_argument("--data-config", default=DEFAULT_CARRY_DATA, help="DataConfig for both stores (default: %(default)s)")
    crun.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR, help="run outputs (default: %(default)s)")
    _add_code_change_args(crun)
    crun.set_defaults(handler=_carry_run)
    chold = carry_sub.add_parser("holdout", help="the one-shot holdout run (2024-01-01..2026-10-20); writes DONE")
    chold.add_argument("--prereg", default=DEFAULT_CARRY_PREREG, help="carry pre-registration (default: %(default)s)")
    chold.add_argument("--data-config", default=DEFAULT_CARRY_DATA,
                       help="DataConfig for both stores (default: %(default)s)")
    chold.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR, help="run outputs (default: %(default)s)")
    _add_code_change_args(chold)
    chold.set_defaults(handler=_carry_holdout)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Handler = args.handler
    return handler(args)
