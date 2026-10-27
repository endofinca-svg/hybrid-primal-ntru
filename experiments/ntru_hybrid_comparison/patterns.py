"""Deterministic public pattern choices, without compatibility filtering.

The historical rule reproduces the saved pattern-primal public permutation.
The sample rule preserves the comparison runner's original sequential RNG.
Neither rule reads a key, candidate, reference, or previous solve outcome.
"""

from hashlib import sha256
import random


PATTERN_RULES = ("random_sample_v1", "historical_sha256_shuffle_v1")


def select_pattern(n, k, seed, index, rule):
    """Return sorted original-coordinate positions for one public attempt.

    Indices count raw attempts. Duplicate sets remain duplicate attempts; this
    helper does not skip or reorder them. The caller records and charges them.
    """
    for name, value in (("n", n), ("k", k), ("seed", seed), ("index", index)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(name + " must be an integer")
    if n < 1 or not 0 <= k <= n or seed < 0 or index < 0:
        raise ValueError("require n>=1, 0<=k<=n, and nonnegative seed/index")
    if rule not in PATTERN_RULES:
        raise ValueError("unknown public pattern rule")
    if rule == "random_sample_v1":
        rng = random.Random(seed)
        for _ in range(index + 1):
            positions = rng.sample(range(n), k)
    else:
        material = f"pattern-primal-v1:{seed}:{index}"
        digest = sha256(material.encode("ascii")).digest()
        positions = list(range(n))
        random.Random(int.from_bytes(digest, "big")).shuffle(positions)
        positions = positions[:k]
    return sorted(positions)
