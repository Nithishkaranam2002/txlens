"""Cox proportional hazards for graft loss, with a Schoenfeld-residual check of the PH assumption.

lifelines handles tied event times with Efron's method, which is also R coxph()'s default, so
coefficients and standard errors match R (Breslow, statsmodels PHReg's default, would not).
The model is fitted on complete cases: a patient missing a chosen covariate (no month-1 DSA
test, or DGF for a heart graft, where it does not apply) is left out, and a covariate that is
then the same for everyone (e.g. organ once only kidneys remain) is dropped with a warning.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter
from lifelines.statistics import proportional_hazard_test

from txlens_backend.survival.extract import GRAFT_LOSS
from txlens_backend.survival.km import Z95

# Covariate key -> label. Every covariate is numeric: age in decades, the rest 0/1.
COVARIATES = {
    "age_per_decade": "Age at transplant (per 10 years)",
    "deceased_donor": "Deceased donor (vs living)",
    "dsa_positive": "DSA positive at month-1 test",
    "dgf": "Delayed graft function",
    "heart": "Heart graft (vs kidney)",
}
PH_ALPHA = 0.05
# Newton-Raphson until steps are this small: R coxph() stops at a 1e-9 relative change in the
# log-likelihood; lifelines' default (1e-7) leaves coefficients ~1e-5 from R's. A first step of
# half the Newton step: with lifelines' default (0.95) its time-varying fitter overshoots on
# our OMOP extract and diverges, where R (step halving) converges.
FIT_OPTIONS = {"precision": 1e-10, "step_size": 0.5}


def covariate_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """time, event (graft loss) and every covariate, from an analysis frame (NaN = missing)."""
    return pd.DataFrame(
        {
            "time": frame["time"].astype(float),
            "event": (frame["status"] == GRAFT_LOSS).astype(int),
            "age_per_decade": frame["age"] / 10.0,
            "deceased_donor": (frame["donor_type"] == "deceased").astype(float),
            "dsa_positive": frame["dsa_positive"].astype(float),
            "dgf": frame["dgf"].astype(float),
            "heart": (frame["organ"] == "heart").astype(float),
        }
    )


@dataclass(frozen=True)
class Term:
    """One covariate's hazard ratio, its 95% CI, and its proportional-hazards check."""

    key: str
    label: str
    coef: float
    se: float
    hazard_ratio: float
    lower: float
    upper: float
    p_value: float
    ph_p_value: float
    ph_violated: bool
    n_exposed: int | None  # binary covariates: patients and events with the covariate = 1
    events_exposed: int | None


@dataclass(frozen=True)
class CoxResult:
    """A fitted model on complete cases, with what was left out and why."""

    terms: list[Term]
    n: int
    events: int
    excluded: int  # patients left out for a missing covariate
    missing: list[str]  # labels of the covariates some patients had no value for
    warnings: list[str]


def model_data(
    frame: pd.DataFrame, covariates: list[str]
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Complete cases for the chosen covariates minus covariates constant in them, the labels of
    covariates with missing values, and warnings (with no counts in them)."""
    full = covariate_frame(frame)[["time", "event", *covariates]]
    data = full.dropna()
    missing = [COVARIATES[c] for c in covariates if full[c].isna().any()]
    warnings = []
    for c in covariates:
        if data[c].nunique() < 2:
            data = data.drop(columns=c)
            warnings.append(f"'{COVARIATES[c]}' is the same for every patient analysed; dropped.")
    return data, missing, warnings


def fit_cox(frame: pd.DataFrame, covariates: list[str]) -> CoxResult:
    """Fit graft loss ~ covariates (Efron ties) and test proportional hazards per covariate
    (Schoenfeld residuals against the Kaplan-Meier transform of time, as R cox.zph)."""
    data, missing, warnings = model_data(frame, covariates)
    kept = [c for c in covariates if c in data.columns]
    fit = CoxPHFitter().fit(data, "time", "event", fit_options=FIT_OPTIONS)
    ph = proportional_hazard_test(fit, data, time_transform="km").summary["p"]
    terms = []
    for c in kept:
        coef, se = float(fit.params_[c]), float(fit.standard_errors_[c])
        binary = set(data[c].unique()) <= {0.0, 1.0}
        exposed = data[data[c] == 1.0]
        if float(ph[c]) < PH_ALPHA:
            warnings.append(
                f"'{COVARIATES[c]}' may break the proportional-hazards assumption "
                f"(Schoenfeld test p = {float(ph[c]):.3f}); its hazard ratio is an "
                "average over follow-up."
            )
        terms.append(
            Term(
                c,
                COVARIATES[c],
                coef,
                se,
                float(np.exp(coef)),
                float(np.exp(coef - Z95 * se)),
                float(np.exp(coef + Z95 * se)),
                float(fit.summary.loc[c, "p"]),
                float(ph[c]),
                float(ph[c]) < PH_ALPHA,
                len(exposed) if binary else None,
                int(exposed["event"].sum()) if binary else None,
            )
        )
    excluded = len(frame) - len(data)
    return CoxResult(terms, len(data), int(data["event"].sum()), excluded, missing, warnings)
