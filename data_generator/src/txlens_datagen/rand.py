"""Small typed helpers around numpy's random generator.

Each patient (and each raw file) gets its own random stream derived from the master seed and
fixed keys. Patient #17 is therefore identical whether 50 or 2000 patients are generated.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

import numpy as np

Rng = np.random.Generator

# Top-level stream keys, so different parts of the generator never share random numbers.
STREAM_PATIENT = 0
STREAM_FEED = 1
STREAM_PDF = 2


def rng_for(seed: int, *keys: int) -> Rng:
    """Return an independent generator for the given seed and key path."""
    return np.random.default_rng(np.random.SeedSequence(entropy=seed, spawn_key=keys))


def chance(rng: Rng, p: float) -> bool:
    """True with probability p."""
    return bool(rng.random() < p)


def uniform(rng: Rng, low: float, high: float) -> float:
    """A float drawn uniformly from [low, high)."""
    return float(rng.uniform(low, high))


def randint(rng: Rng, low: int, high: int) -> int:
    """An integer drawn uniformly from [low, high] (inclusive)."""
    return int(rng.integers(low, high + 1))


def pick[T](rng: Rng, options: Sequence[T]) -> T:
    """One item chosen uniformly."""
    return options[int(rng.integers(len(options)))]


def pick_weighted[T](rng: Rng, weights: Mapping[T, float]) -> T:
    """One key chosen with probability proportional to its weight."""
    keys = list(weights)
    probs = np.array([weights[k] for k in keys], dtype=float)
    return keys[int(rng.choice(len(keys), p=probs / probs.sum()))]


def lognormal_factor(rng: Rng, sigma: float) -> float:
    """A multiplicative noise factor with median 1."""
    return float(np.exp(rng.normal(0.0, sigma)))


def make_uuid(rng: Rng) -> str:
    """A reproducible random UUID string."""
    return str(uuid.UUID(bytes=rng.bytes(16), version=4))


def random_date(rng: Rng, start: date, end: date) -> date:
    """A date drawn uniformly from [start, end]."""
    return start + timedelta(days=randint(rng, 0, (end - start).days))
