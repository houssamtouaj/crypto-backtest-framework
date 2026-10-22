"""Seeded generators for every random statistic (overview §6: seeds from ``(master_seed, variant_id, purpose)``)."""
from __future__ import annotations

import hashlib

import numpy as np


def rng_for(master_seed: int, variant_id: str, purpose: str) -> np.random.Generator:
    """A generator that depends only on ``(master_seed, variant_id, purpose)``.

    The three are hashed (SHA-256) into the 128-bit entropy of a
    ``SeedSequence``, so two purposes of one variant draw independent
    streams and a rerun draws the same one.
    """
    key = f"{int(master_seed)}|{variant_id}|{purpose}".encode()
    entropy = int.from_bytes(hashlib.sha256(key).digest()[:16], "big")
    return np.random.default_rng(np.random.SeedSequence(entropy))
