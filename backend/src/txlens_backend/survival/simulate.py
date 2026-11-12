"""A seeded, simulated analysis extract whose true hazard ratios are known.

The synthetic patients in data_generator/ draw graft loss and death as flat probabilities, so
donor type, age and DGF have no effect there and a Cox model rightly finds hazard ratios near
1. This extract has real effects, so the Python port and the R reference can both be checked
against the truth. It has the same columns as extract.py and the same awkward parts:

- Weibull graft-loss and death hazards (death competes with graft loss);
- times rounded up to whole days, so many tied event times (Efron vs Breslow matters);
- staggered entry, so follow-up ends at different times (administrative censoring);
- DSA known only at the day-30 test, and only for kidney grafts; DGF only for kidney grafts;
- a dd-cfDNA result on the surveillance schedule while the patient is followed, positive at
  random, so "dd-cfDNA >= 1% in year 1" has a true hazard ratio of exactly 1 (the
  immortal-time demonstration).
"""

import numpy as np
import pandas as pd

from txlens_backend.survival.extract import CENSORED, DEATH, GRAFT_LOSS

TRUE_HAZARD_RATIOS = {
    "age_per_decade": 1.1,
    "deceased_donor": 1.5,
    "dsa_positive": 2.5,
    "dgf": 1.8,
    "heart": 1.3,
}
SEED = 20261112
N_PATIENTS = 3000
SCHEDULE_DAYS = (30, 60, 90, 120, 180, 270, 365)  # dd-cfDNA surveillance in year 1
DD_CFDNA_POSITIVE_RATE = 0.10  # per test, unrelated to the outcome
GRAFT_SHAPE, GRAFT_3Y_CUM_HAZARD = 0.7, 0.08  # reference patient: 50, living donor kidney
DEATH_SHAPE, DEATH_3Y_CUM_HAZARD, DEATH_HR_PER_DECADE = 1.1, 0.06, 1.6
THREE_YEARS = 1095.0


def weibull_times(
    rng: np.random.Generator, shape: float, cum_hazard_3y: float, linear_predictor: np.ndarray
) -> np.ndarray:
    """Event times with cumulative hazard H(t) = H3 * (t / 3 years)^shape * exp(lp)."""
    u = rng.uniform(size=len(linear_predictor))
    return THREE_YEARS * (-np.log(u) / (cum_hazard_3y * np.exp(linear_predictor))) ** (1 / shape)


def simulate(n: int = N_PATIENTS, seed: int = SEED) -> pd.DataFrame:
    """The simulated extract (same columns as extract.extract)."""
    rng = np.random.default_rng(seed)
    age = np.clip(rng.normal(50, 13, n).round(), 18, 78)
    heart = rng.uniform(size=n) < 0.15
    deceased = heart | (rng.uniform(size=n) < 0.65)
    dgf = np.where(heart, np.nan, rng.uniform(size=n) < np.where(deceased, 0.25, 0.05))
    dsa = np.where(heart, np.nan, (rng.uniform(size=n) < 0.08).astype(float))
    log_hr = {k: np.log(v) for k, v in TRUE_HAZARD_RATIOS.items()}
    lp = (
        (age - 50) / 10 * log_hr["age_per_decade"]
        + deceased * log_hr["deceased_donor"]
        + heart * log_hr["heart"]
        + np.nan_to_num(dsa) * log_hr["dsa_positive"]
        + np.nan_to_num(dgf) * log_hr["dgf"]
    )
    graft = weibull_times(rng, GRAFT_SHAPE, GRAFT_3Y_CUM_HAZARD, lp)
    death = weibull_times(
        rng, DEATH_SHAPE, DEATH_3Y_CUM_HAZARD, (age - 50) / 10 * np.log(DEATH_HR_PER_DECADE)
    )
    follow_up = rng.uniform(90, 4 * 365, n)
    time = np.ceil(np.minimum.reduce([graft, death, follow_up]))
    status = np.select(
        [graft <= np.minimum(death, follow_up), death <= follow_up], [GRAFT_LOSS, DEATH], CENSORED
    )
    dsa = np.where(time > SCHEDULE_DAYS[0], dsa, np.nan)  # no month-1 test after an early event
    return pd.DataFrame(
        {
            "person_id": np.arange(1, n + 1),
            "time": time.astype(int),
            "status": status,
            "age": age.astype(int),
            "organ": np.where(heart, "heart", "kidney"),
            "donor_type": np.where(deceased, "deceased", "living"),
            "dgf": dgf,
            "dsa_positive": dsa,
            "dd_cfdna_high_day": first_positive_test(rng, time),
            "rejection_day": np.nan,
            "abmr_day": np.nan,
        }
    )


def first_positive_test(rng: np.random.Generator, time: np.ndarray) -> np.ndarray:
    """Day of the first positive scheduled dd-cfDNA test taken while followed (NaN if none)."""
    first = np.full(len(time), np.nan)
    for day in reversed(SCHEDULE_DAYS):
        positive = (time > day) & (rng.uniform(size=len(time)) < DD_CFDNA_POSITIVE_RATE)
        first = np.where(positive, day, first)
    return first


def misclassify(
    frame: pd.DataFrame, column: str, sensitivity: float, specificity: float, seed: int = SEED
) -> pd.DataFrame:
    """A copy with a 0/1 column misread at the given sensitivity and specificity (NaN kept), as
    if it came from an imperfect NLP."""
    rng = np.random.default_rng(seed + 1)
    value = frame[column].to_numpy(float)
    u = rng.uniform(size=len(value))
    read = np.where(value == 1, (u < sensitivity).astype(float), (u >= specificity).astype(float))
    out = frame.copy()
    out[column] = np.where(np.isnan(value), np.nan, read)
    return out
