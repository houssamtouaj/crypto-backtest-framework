"""Command-line entry point.

Phase 0 registered every subcommand as a stub. Phase 1 implements ``data
fetch`` and ``data validate``; Phase 6 implements ``prereg``, ``grid``,
``run``, ``baselines``, ``stats`` and ``holdout``; ``report`` prints "not
implemented" and exits with status 2 until Phase 7. Exit codes: 0 success,
1 a download, checksum, validation or run failure (or a refused holdout or
family pass), 2 a usage or config error.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from datetime import date

from perpbt.config import ConfigError, DataConfig, canonical_json, load_yaml, to_dict
from perpbt.data.bulk import ChecksumError, DownloadError
from perpbt.data.fetch import TIMEFRAMES, run_fetch
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

# (name, help text, phase that implements it)
_TOP_LEVEL: tuple[tuple[str, str, int], ...] = (
    ("report", "write per-variant reports and the summary", 7),
)


def _not_implemented(name: str, phase: int) -> Handler:
    def handler(args: argparse.Namespace) -> int:
        print(f"perpbt {name}: not implemented (Phase {phase})", file=sys.stderr)
        return 2

    return handler


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
        report = run_validate(cfg, pairs)
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
                                       alpha=prereg.stats.alpha)
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
    return 0


def _add_exp_args(p: argparse.ArgumentParser, *, pool: bool = False) -> None:
    p.add_argument("--prereg", default=DEFAULT_PREREG, help="pre-registration YAML (default: %(default)s)")
    p.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="market data directory (default: %(default)s)")
    p.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR, help="run outputs and the registry (default: %(default)s)")
    if pool:
        p.add_argument("--workers", type=int, default=8, help="worker processes (default: %(default)s)")
        p.add_argument("--force", action="store_true", help="rerun variants that are already done")


def _add_data_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", default=DEFAULT_DATA_CONFIG, help="DataConfig YAML (default: %(default)s)")
    p.add_argument(
        "--pairs", nargs="+", metavar="PAIR", help="pairs to process (default: every pair listed in the config)"
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

    for name, help_text, phase in _TOP_LEVEL:
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(handler=_not_implemented(name, phase))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Handler = args.handler
    return handler(args)
