"""Number formatting and Markdown tables for the reports (spec §7.2, §7.3).

One format per kind of number, so a table can be parsed back and checked
against its source: integers plain, R values and ratios with fixed
decimals, p-values with three significant digits, fractions shown as
percent with a ``%`` suffix, missing values as ``n/a``. Tables use the
ASCII minus; ``parse_tables`` reads a document's tables back as lists of
rows (header first).
"""
from __future__ import annotations

import math
import re
from collections.abc import Sequence

NA = "n/a"


def _missing(x) -> bool:
    if x is None:
        return True
    try:
        return not math.isfinite(float(x))
    except (TypeError, ValueError):
        return True


def fint(x) -> str:
    """An integer count."""
    return NA if _missing(x) else str(int(x))


def fnum(x, digits: int = 3) -> str:
    """A real number with ``digits`` decimals (R values, Sharpe ratios, multiples)."""
    if _missing(x):
        return NA
    s = f"{float(x):.{digits}f}"
    return s[1:] if s.startswith("-") and float(s) == 0.0 else s  # no "-0.000"


def fp(x) -> str:
    """A p-value with three significant digits."""
    if _missing(x):
        return NA
    return f"{float(x):.3g}"


def fpct(x, digits: int = 1) -> str:
    """A fraction shown as percent: ``0.123`` → ``12.3%``."""
    return NA if _missing(x) else f"{fnum(100.0 * float(x), digits)}%"


def fci(lo, hi, digits: int = 3) -> str:
    """A confidence interval ``lo to hi``."""
    return f"{fnum(lo, digits)} to {fnum(hi, digits)}"


def fbool(x) -> str:
    return NA if x is None else ("yes" if x else "no")


def _cell(x) -> str:
    return str(x).replace("|", "\\|").replace("\n", " ")


def table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    """A Markdown table; every value is rendered with ``str`` (format numbers first)."""
    out = ["| " + " | ".join(_cell(h) for h in headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return "\n".join(out)


def parse_tables(text: str) -> list[list[list[str]]]:
    """Every Markdown table in ``text``: a list of rows (header first, separator dropped) of stripped cells."""
    tables, cur = [], []
    for line in text.splitlines() + [""]:
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s[1:-1])]
            if not all(re.fullmatch(r"-+", c) for c in cells):
                cur.append(cells)
        elif cur:
            tables.append(cur)
            cur = []
    return tables


NUMBER = re.compile(r"[-−]?\d+(?:\.\d+)?(?:e[-+]?\d+)?%?")


def numbers_in(cell: str) -> list[tuple[float, float]]:
    """The numbers of a value cell as ``(value, tolerance)``; ``%`` values are divided by 100.

    Only cells that consist of numbers (a value, ``lo to hi``, ``n/a``) are
    read; text cells give nothing. The tolerance is half a unit of the last
    printed digit.
    """
    if not re.fullmatch(rf"(?:{NUMBER.pattern}|{NA})(?: to (?:{NUMBER.pattern}|{NA}))?", cell):
        return []
    out = []
    for tok in NUMBER.findall(cell):
        pct = tok.endswith("%")
        t = tok.rstrip("%").replace("−", "-")
        mant = t.split("e")[0]
        dec = len(mant.split(".")[1]) if "." in mant else 0
        exp = int(t.split("e")[1]) if "e" in t else 0
        tol = 0.5 * 10.0 ** (exp - dec)
        v = float(t)
        out.append((v / 100.0, tol / 100.0) if pct else (v, tol))
    return out
