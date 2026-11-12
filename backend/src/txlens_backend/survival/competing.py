"""Cumulative incidence with competing risks (Aalen-Johansen), and why 1 - KM overstates it.

Graft loss and death with a functioning graft compete: a patient who died can no longer lose
the graft. Kaplan-Meier treats such deaths as censored, as if the patient could still lose the
graft later, so 1 - KM estimates the risk in a world without death and overstates the real
cumulative incidence. Aalen-Johansen adds graft-loss risk only while the patient is alive with
a functioning graft: CIF(t) = sum over event times of S(t-) * d_loss / n_at_risk.

Written here in numpy rather than with lifelines' AalenJohansenFitter, which jitters tied
event times (our times are whole days, so ties are common) and would no longer match R.
Standard errors are the infinitesimal jackknife, the method R's survfit() uses for multi-state
curves: the variance is the sum over patients of their squared influence on the estimate.
"""

import numpy as np
import pandas as pd

from txlens_backend.survival.km import Z95, Curve


def aalen_johansen(time: pd.Series, status: pd.Series, cause: int, group: str = "all") -> Curve:
    """Cumulative incidence of `cause` (status codes: 0 censored, 1.. causes) at every distinct
    follow-up time."""
    t, s = time.to_numpy(float), status.to_numpy(int)
    times = np.unique(t)
    at_risk = t[:, None] >= times[None, :]  # Y_ij
    leaves = t[:, None] == times[None, :]
    any_event = leaves & (s[:, None] != 0)
    this_cause = leaves & (s[:, None] == cause)
    n = at_risk.sum(axis=0).astype(float)
    d_all, d_cause = any_event.sum(axis=0), this_cause.sum(axis=0)
    hazard_all, hazard_cause = d_all / n, d_cause / n
    infl_all = (any_event - at_risk * hazard_all) / n  # influence on each hazard increment
    infl_cause = (this_cause - at_risk * hazard_cause) / n
    cif, se = np.empty(len(times)), np.empty(len(times))
    surv_before, cif_now = 1.0, 0.0
    d_surv, d_cif = np.zeros(len(t)), np.zeros(len(t))
    for j in range(len(times)):
        d_cif = d_cif + d_surv * hazard_cause[j] + surv_before * infl_cause[:, j]
        cif_now += surv_before * hazard_cause[j]
        d_surv = d_surv * (1 - hazard_all[j]) - surv_before * infl_all[:, j]
        surv_before *= 1 - hazard_all[j]
        cif[j], se[j] = cif_now, float(np.sqrt((d_cif**2).sum()))
    censored = leaves & (s[:, None] == 0)
    lower, upper = log_interval(cif, se)
    return Curve(
        group,
        times,
        n.astype(int),
        d_cause.astype(int),
        censored.sum(axis=0).astype(int),
        cif,
        se,
        lower,
        upper,
    )


def log_interval(estimate: np.ndarray, se: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """R's default "log" interval for a probability with standard error `se`: p * exp(+-z se/p),
    capped at 1 (undefined while p is 0)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        width = Z95 * se / estimate
        lower = np.where(estimate > 0, estimate * np.exp(-width), np.nan)
        upper = np.where(estimate > 0, np.minimum(1.0, estimate * np.exp(width)), np.nan)
    return lower, upper
