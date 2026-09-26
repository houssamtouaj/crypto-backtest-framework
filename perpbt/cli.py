"""Command-line entry point.

Phase 0 registers every subcommand from the overview as a stub that prints
"not implemented" and exits with status 2. Each later phase replaces the
handler of its own subparser and adds that subparser's arguments.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

Handler = Callable[[argparse.Namespace], int]

# (name, help text, phase that implements it)
_TOP_LEVEL: tuple[tuple[str, str, int], ...] = (
    ("run", "run one variant, or the nine primary cells, end to end", 6),
    ("grid", "enumerate (--dry-run) or run the pre-registered grid", 6),
    ("baselines", "run random-timing baselines A and B", 6),
    ("report", "write per-variant reports and the summary", 7),
    ("holdout", "one-shot holdout run with the guard", 6),
)
_DATA: tuple[tuple[str, str, int], ...] = (
    ("fetch", "download the bulk archive and the ccxt head/tail", 1),
    ("validate", "validate stored candles and funding", 1),
)


def _not_implemented(name: str, phase: int) -> Handler:
    def handler(args: argparse.Namespace) -> int:
        print(f"perpbt {name}: not implemented (Phase {phase})", file=sys.stderr)
        return 2

    return handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="perpbt",
        description="Backtest framework for Binance USDT-M perpetuals.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    data = sub.add_parser("data", help="fetch and validate market data")
    data_sub = data.add_subparsers(dest="data_command", metavar="SUBCOMMAND", required=True)
    for name, help_text, phase in _DATA:
        p = data_sub.add_parser(name, help=help_text)
        p.set_defaults(handler=_not_implemented(f"data {name}", phase))

    for name, help_text, phase in _TOP_LEVEL:
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(handler=_not_implemented(name, phase))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Handler = args.handler
    return handler(args)
