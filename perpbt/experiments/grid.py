"""Grid enumeration from the pre-registration (spec §6.4, overview §9.1).

For each pair × session: every heatmap's cartesian product with the other
parameters at their primary values, then every single, deduplicated by
``config_hash`` (the primary sits in every heatmap). Each variant carries
the heatmaps and singles it belongs to (``heatmap_1``, ``single_2``, …)
and ``is_primary``. With the registered grid that is 36 variants per
pair × session and 324 in all.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

from perpbt.config import StrategyParams, VariantConfig, config_hash, to_dict
from perpbt.experiments.prereg import Prereg


@dataclass(frozen=True)
class GridVariant:
    cfg: VariantConfig
    members: tuple[str, ...]
    is_primary: bool

    @property
    def cell(self) -> tuple[str, str]:
        return self.cfg.pair, self.cfg.session.name

    def labels(self) -> dict:
        """The ``experiment`` block of ``stats.json`` (the runner adds ``code_version``)."""
        return {"is_primary": self.is_primary, "members": list(self.members)}


def cell_params(prereg: Prereg) -> list[tuple[StrategyParams, tuple[str, ...]]]:
    """The unique parameter sets of one pair × session with their memberships, in enumeration order."""
    base = to_dict(prereg.primary)
    found: dict[str, tuple[StrategyParams, list[str]]] = {}

    def add(override: dict, member: str) -> None:
        params = StrategyParams(**{**base, **override})
        key = config_hash(params)
        if key not in found:
            found[key] = (params, [])
        if member not in found[key][1]:
            found[key][1].append(member)

    for n, h in enumerate(prereg.grid.heatmaps, start=1):
        for combo in itertools.product(*h.values):
            add(dict(zip(h.axes, combo, strict=True)), f"heatmap_{n}")
    for n, s in enumerate(prereg.grid.singles, start=1):
        add(dict(s), f"single_{n}")
    return [(p, tuple(m)) for p, m in found.values()]


def enumerate_grid(prereg: Prereg) -> list[GridVariant]:
    """Every in-sample grid variant: pairs in file order × sessions in file order × ``cell_params``."""
    params = cell_params(prereg)
    out = []
    for pair in prereg.pairs:
        for session in prereg.sessions:
            for p, members in params:
                out.append(GridVariant(prereg.variant(pair, session, p), members, prereg.is_primary_params(p)))
    return out


def primary_variants(prereg: Prereg, *, holdout: bool = False) -> list[GridVariant]:
    """The nine primary cells (pairs × sessions at the primary parameters), in-sample or on holdout."""
    members = next((m for p, m in cell_params(prereg) if prereg.is_primary_params(p)), ("primary",))
    return [
        GridVariant(prereg.variant(pair, session, prereg.primary, holdout=holdout), members, True)
        for pair in prereg.pairs
        for session in prereg.sessions
    ]


def differing_fields(a: StrategyParams, b: StrategyParams) -> set[str]:
    da, db = to_dict(a), to_dict(b)
    return {k for k in da if da[k] != db[k]}
