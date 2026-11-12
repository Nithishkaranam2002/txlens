"""95% bootstrap confidence intervals, and the small JSON files the summary is built from.

A metric is computed from per-unit counts (a document, a note, a distinct text). The units
are resampled with replacement B times, the metric is recomputed on each resample, and the
2.5th and 97.5th percentiles bound the interval. Resampling whole units keeps the
dependence between fields of one document. The seed is fixed, so a re-run gives the same
interval.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

RESAMPLES = 1000
SEED = 7
Counts = Sequence[float]  # one unit's counts, e.g. (tp, fp, fn)


@dataclass(frozen=True)
class Estimate:
    """A metric and its 95% bootstrap interval."""

    value: float
    low: float
    high: float

    def text(self) -> str:
        """'94.2% (93.8-94.6)'."""
        return f"{self.value:.1%} ({self.low:.1%}-{self.high:.1%})"

    def as_dict(self) -> dict[str, float]:
        return {
            "value": round(self.value, 4),
            "low": round(self.low, 4),
            "high": round(self.high, 4),
        }


def ratio(num: int, den: int) -> Callable[[Sequence[float]], float]:
    """A metric that divides two summed columns (0 when the denominator is 0)."""
    return lambda totals: totals[num] / totals[den] if totals[den] else 0.0


def precision(totals: Sequence[float]) -> float:
    """From (tp, fp, fn, ...)."""
    return totals[0] / (totals[0] + totals[1]) if totals[0] + totals[1] else 0.0


def recall(totals: Sequence[float]) -> float:
    return totals[0] / (totals[0] + totals[2]) if totals[0] + totals[2] else 0.0


def f1(totals: Sequence[float]) -> float:
    p, r = precision(totals), recall(totals)
    return 2 * p * r / (p + r) if p + r else 0.0


def bootstrap(
    units: Sequence[Counts], metric: Callable[[Sequence[float]], float],
    resamples: int = RESAMPLES, seed: int = SEED,
) -> Estimate:  # fmt: skip
    """The metric on all units, with its 95% percentile interval over unit resamples.

    Each resample is a vector of how often each unit was drawn (multinomial), so the
    resampled column sums are one matrix product."""
    if not units:
        return Estimate(0.0, 0.0, 0.0)
    table = np.asarray(units, dtype=float)
    n = len(table)
    draws = np.random.default_rng(seed).multinomial(n, np.full(n, 1 / n), size=resamples)
    values = np.sort([metric(row) for row in (draws @ table).tolist()])
    low, high = np.percentile(values, [2.5, 97.5])
    return Estimate(metric(table.sum(axis=0).tolist()), float(low), float(high))


def write_headline(path: Path, data: dict[str, object]) -> None:
    """The numbers a report contributes to summary.md, as JSON next to the report."""
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
