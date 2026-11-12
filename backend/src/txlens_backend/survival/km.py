"""Kaplan-Meier curves with R's default confidence intervals, risk tables and the log-rank test.

The curve itself is lifelines' estimate. The standard error and confidence interval follow R's
`survfit()` defaults, not lifelines': R reports `std.err` as the Greenwood standard error of
log S(t) and builds a "log" interval, S * exp(+-z * se), capped at 1; lifelines' own interval is
the "log-log" one. Doing it here keeps the numbers identical to the R reference (r_parity/).
"""

from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
import pandas as pd
from lifelines import KaplanMeierFitter
from lifelines.statistics import multivariate_logrank_test

Z95 = NormalDist().inv_cdf(0.975)


@dataclass(frozen=True)
class Curve:
    """A step curve at every distinct follow-up time (as R's survfit), with counts behind it.

    `estimate` is S(t) for Kaplan-Meier and the cumulative incidence for competing risks;
    `se` is on the scale R reports (log S for Kaplan-Meier, the estimate for incidence).
    """

    group: str
    time: np.ndarray
    n_risk: np.ndarray
    n_event: np.ndarray
    n_censor: np.ndarray
    estimate: np.ndarray
    se: np.ndarray
    lower: np.ndarray
    upper: np.ndarray

    @property
    def events(self) -> int:
        """Events counted by this curve."""
        return int(self.n_event.sum())


def kaplan_meier(time: pd.Series, event: pd.Series, group: str = "all") -> Curve:
    """KM curve; `event` is 1 for the event of interest and 0 for censoring."""
    fit = KaplanMeierFitter().fit(time, event)
    table = fit.event_table[fit.event_table["removed"] > 0]  # lifelines adds an empty t = 0
    n, d = table["at_risk"].to_numpy(float), table["observed"].to_numpy(float)
    surv = fit.survival_function_.loc[table.index].iloc[:, 0].to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        se = np.sqrt(np.cumsum(np.where(d > 0, d / (n * (n - d)), 0.0)))
        lower = np.where(surv > 0, surv * np.exp(-Z95 * se), np.nan)
        upper = np.where(surv > 0, np.minimum(1.0, surv * np.exp(Z95 * se)), np.nan)
    return Curve(
        group,
        table.index.to_numpy(float),
        n.astype(int),
        d.astype(int),
        table["censored"].to_numpy(int),
        surv,
        se,
        lower,
        upper,
    )


def value_at(curve: Curve, values: np.ndarray, t: float, start: float = 1.0) -> float:
    """A step curve's value at time t (right-continuous; `start` before the first step)."""
    i = int(np.searchsorted(curve.time, t, side="right")) - 1
    return start if i < 0 else float(values[i])


@dataclass(frozen=True)
class RiskRow:
    """At one time: patients still at risk, and events and censorings up to it."""

    time: float
    n_risk: int
    cumulative_events: int
    cumulative_censored: int


def risk_table(time: pd.Series, event: pd.Series, grid: list[float]) -> list[RiskRow]:
    """Number at risk at each grid time (follow-up >= t), as R's summary(fit, times=)."""
    t, e = time.to_numpy(float), event.to_numpy(int)
    return [
        RiskRow(
            g,
            int((t >= g).sum()),
            int(((t <= g) & (e == 1)).sum()),
            int(((t <= g) & (e == 0)).sum()),
        )
        for g in grid
    ]


@dataclass(frozen=True)
class LogRank:
    """Log-rank test across groups (R survdiff, rho = 0)."""

    chi2: float
    df: int
    p_value: float
    observed: dict[str, int]
    expected: dict[str, float]


def log_rank(time: pd.Series, event: pd.Series, group: pd.Series) -> LogRank:
    """Chi-square test that the groups share one survival curve, with observed and expected
    events per group."""
    result = multivariate_logrank_test(time, group, event)
    observed, expected = expected_events(time, event, group)
    return LogRank(
        float(result.test_statistic),
        int(result.degrees_of_freedom),
        float(result.p_value),
        observed,
        expected,
    )


def expected_events(
    time: pd.Series, event: pd.Series, group: pd.Series
) -> tuple[dict[str, int], dict[str, float]]:
    """Observed events per group and those expected if all groups shared one hazard."""
    t, e, g = time.to_numpy(float), event.to_numpy(int), group.astype(str).to_numpy()
    labels = sorted(set(g))
    expected = dict.fromkeys(labels, 0.0)
    for s in np.unique(t[e == 1]):
        at_risk = t >= s
        deaths = int(((t == s) & (e == 1)).sum())
        for label in labels:
            expected[label] += deaths * (at_risk & (g == label)).sum() / at_risk.sum()
    observed = {label: int(((g == label) & (e == 1)).sum()) for label in labels}
    return observed, expected
