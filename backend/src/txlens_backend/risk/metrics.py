"""Model metrics: discrimination, calibration, net benefit, alert burden, drift.

Rejection is rare (about 2% of visits), so AUPRC is reported next to AUROC, and confidence
intervals resample patients (a patient's visits are not independent).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

Array = NDArray[np.float64]
RESAMPLES = 1000
SEED = 7  # same seed as the other evaluation bootstraps
EPS = 1e-6


@dataclass(frozen=True)
class Interval:
    """A point estimate with its 95% patient-level bootstrap interval."""

    value: float
    low: float
    high: float

    def text(self, digits: int = 3) -> str:
        return f"{self.value:.{digits}f} ({self.low:.{digits}f} to {self.high:.{digits}f})"

    def as_dict(self) -> dict[str, float]:
        return {"value": self.value, "low": self.low, "high": self.high}


def auroc(y: Array, p: Array) -> float:
    return float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else float("nan")


def auprc(y: Array, p: Array) -> float:
    return float(average_precision_score(y, p)) if y.sum() > 0 else float("nan")


def brier(y: Array, p: Array) -> float:
    return float(brier_score_loss(y, p))


def calibration(y: Array, p: Array) -> tuple[float, float]:
    """(intercept, slope) of the logistic recalibration of y on logit(p); (0, 1) is perfect.

    The intercept is calibration-in-the-large with the slope fixed at 1."""
    logit = np.log(np.clip(p, EPS, 1 - EPS) / (1 - np.clip(p, EPS, 1 - EPS)))
    slope = LogisticRegression(C=1e6, max_iter=1000).fit(logit[:, None], y).coef_[0][0]
    # Intercept with offset: solve for a in logit(P) = a + logit(p) by Newton steps.
    a = 0.0
    for _ in range(50):
        q = 1 / (1 + np.exp(-(a + logit)))
        step = (y - q).sum() / max((q * (1 - q)).sum(), EPS)
        a += step
        if abs(step) < 1e-10:
            break
    return float(a), float(slope)


def net_benefit(y: Array, alert: NDArray[np.bool_], threshold: float) -> float:
    """Decision-curve net benefit of acting on `alert` at a risk threshold."""
    n = len(y)
    tp = float((alert & (y == 1)).sum())
    fp = float((alert & (y == 0)).sum())
    return tp / n - fp / n * threshold / (1 - threshold)


def threshold_for_rate(p: Array, rate: float) -> float:
    """The score cut-off that alerts on (about) `rate` of visits: the top `rate` fraction."""
    return float(np.quantile(p, 1 - rate)) if 0 < rate < 1 else float("inf")


def cluster_bootstrap(
    keys: Sequence[str],
    columns: Sequence[NDArray[Any]],
    metric: Callable[..., float],
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> Interval:
    """`metric(*columns)` with a 95% percentile interval from resampling patients."""
    groups: dict[str, list[int]] = {}
    for i, key in enumerate(keys):
        groups.setdefault(key, []).append(i)
    members = [np.array(v) for v in groups.values()]
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(resamples):
        pick = np.concatenate([members[i] for i in rng.integers(0, len(members), len(members))])
        value = metric(*(c[pick] for c in columns))
        if not np.isnan(value):
            values.append(value)
    point = metric(*columns)
    low, high = np.percentile(values, [2.5, 97.5]) if values else (np.nan, np.nan)
    return Interval(float(point), float(low), float(high))


@dataclass(frozen=True)
class Bins:
    """A reference distribution for PSI: inner quantile edges and the share of each bin (the
    last bin holds missing values). Stored with the model instead of the training rows."""

    edges: tuple[float, ...]
    shares: tuple[float, ...]

    def as_dict(self) -> dict[str, list[float]]:
        return {"edges": list(self.edges), "shares": list(self.shares)}


def bin_shares(edges: Array, values: Array) -> Array:
    """Share of `values` in each bin between `edges`, plus a last bin for missing values."""
    values = np.asarray(values, dtype=float)
    ok = values[~np.isnan(values)]
    counts = np.bincount(np.searchsorted(edges, ok, side="right"), minlength=len(edges) + 1)
    return np.append(counts, len(values) - len(ok)).astype(float) / max(len(values), 1)


def reference_bins(expected: Array, bins: int = 10) -> Bins:
    """Quantile bins of the reference (training) values."""
    expected = np.asarray(expected, dtype=float)
    ok = expected[~np.isnan(expected)]
    if len(ok) == 0:
        return Bins((), (0.0, 1.0))
    distinct = np.unique(ok)
    if len(distinct) <= bins:  # yes/no and other few-valued features: one bin per value
        edges = (distinct[:-1] + distinct[1:]) / 2
    else:
        edges = np.unique(np.quantile(ok, np.linspace(0, 1, bins + 1)))[1:-1]
    return Bins(
        tuple(float(e) for e in edges), tuple(float(s) for s in bin_shares(edges, expected))
    )


def psi_against(reference: Bins, actual: Array) -> float:
    """Population stability index of `actual` against a reference distribution.
    < 0.1 stable, 0.1-0.25 watch, > 0.25 shifted."""
    if len(actual) == 0:
        return float("nan")
    e = np.clip(np.array(reference.shares), 1e-4, None)
    a = np.clip(bin_shares(np.array(reference.edges), actual), 1e-4, None)
    return float(((a - e) * np.log(a / e)).sum())


def psi(expected: Array, actual: Array, bins: int = 10) -> float:
    """PSI of `actual` against `expected` (quantile bins of expected; missing is its own bin)."""
    return psi_against(reference_bins(expected, bins), actual)


def drift_band(value: float) -> str:
    """The usual PSI reading."""
    if np.isnan(value):
        return "n/a"
    return "stable" if value < 0.1 else "watch" if value <= 0.25 else "shifted"
