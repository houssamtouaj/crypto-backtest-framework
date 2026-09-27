"""Command-line entry point.

Phase 0 registered every subcommand as a stub. Phase 1 implements ``data
fetch`` and ``data validate``; the other subcommands print "not implemented"
and exit with status 2 until their phase lands. Exit codes: 0 success, 1 a
download, checksum or validation failure, 2 a usage or config error.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from datetime import date

from perpbt.config import ConfigError, DataConfig, load_yaml
from perpbt.data.bulk import ChecksumError, DownloadError
from perpbt.data.fetch import TIMEFRAMES, run_fetch
from perpbt.data.validate import run_validate

Handler = Callable[[argparse.Namespace], int]
DEFAULT_DATA_CONFIG = "configs/data.yaml"

# (name, help text, phase that implements it)
_TOP_LEVEL: tuple[tuple[str, str, int], ...] = (
    ("run", "run one variant, or the nine primary cells, end to end", 6),
    ("grid", "enumerate (--dry-run) or run the pre-registered grid", 6),
    ("baselines", "run random-timing baselines A and B", 6),
    ("report", "write per-variant reports and the summary", 7),
    ("holdout", "one-shot holdout run with the guard", 6),
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

    for name, help_text, phase in _TOP_LEVEL:
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(handler=_not_implemented(name, phase))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Handler = args.handler
    return handler(args)
