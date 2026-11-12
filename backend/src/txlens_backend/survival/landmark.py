"""Groups known only during follow-up: landmark analysis, time-varying Cox, and the guard.

Immortal time bias: "dd-cfDNA >= 1% in year 1" can only be seen in a patient who was still
being tested, so a group defined that way cannot have lost its graft before its first high
result. Compared from transplant (time zero), that group gets that event-free time for free
and looks protected. Two honest designs:

- Landmark: start the clock at a landmark day (e.g. day 365) among patients still at risk then,
  and define the group only from what was known by that day.
- Time-varying covariate: a patient counts as unexposed until the day of the first high result
  and as exposed afterwards (Cox with start/stop rows).

Every grouping has a horizon: the day by which it is known. The analysis never starts before it.
"""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter, CoxTimeVaryingFitter

from txlens_backend.survival.extract import GRAFT_LOSS
from txlens_backend.survival.km import Z95

YEAR = 365
# DGF is diagnosed in the first week (dialysis needed within 7 days of transplant).
DGF_HORIZON_DAYS = 7
DSA_HORIZON_DAYS = 30  # the first DSA test is the month-1 one


def binary(series: pd.Series, yes: str, no: str) -> pd.Series:
    """1/0/NaN -> labels (NaN stays NaN: not classifiable, left out)."""
    return series.map({1.0: yes, 0.0: no})


def by_day(day: pd.Series, by: int, yes: str, no: str) -> pd.Series:
    """`yes` if the first-exposure day is on or before `by`, else `no`."""
    return pd.Series(np.where(day.notna() & (day <= by), yes, no), index=day.index)


def age_band(age: pd.Series) -> pd.Series:
    """Under 40, 40-59, 60 and over."""
    return pd.Series(np.select([age < 40, age < 60], ["<40", "40-59"], "60+"), index=age.index)


def rejection_year1(frame: pd.DataFrame) -> pd.Series:
    """ABMR (incl. mixed) in year 1, other biopsy-proven rejection in year 1, or none."""
    abmr = frame["abmr_day"].notna() & (frame["abmr_day"] <= YEAR)
    other = frame["rejection_day"].notna() & (frame["rejection_day"] <= YEAR)
    labels = np.select(
        [abmr, other], ["ABMR in year 1", "Other rejection in year 1"], "No rejection in year 1"
    )
    return pd.Series(labels, index=frame.index)


@dataclass(frozen=True)
class Grouping:
    """How to split patients, and the earliest day (horizon) at which the split is known."""

    key: str
    label: str
    horizon_days: int
    assign: Callable[[pd.DataFrame], pd.Series]


GROUPINGS = {
    g.key: g
    for g in [
        Grouping("none", "All patients", 0, lambda f: pd.Series("All patients", index=f.index)),
        Grouping("organ", "Organ", 0, lambda f: f["organ"].str.capitalize()),
        Grouping("donor_type", "Donor type", 0, lambda f: f["donor_type"].str.capitalize()),
        Grouping("age_band", "Age at transplant", 0, lambda f: age_band(f["age"])),
        Grouping(
            "dgf",
            "Delayed graft function",
            DGF_HORIZON_DAYS,
            lambda f: binary(f["dgf"], "DGF", "No DGF"),
        ),
        Grouping(
            "dsa_month1",
            "DSA at month-1 test",
            DSA_HORIZON_DAYS,
            lambda f: binary(f["dsa_positive"], "DSA positive", "DSA negative"),
        ),
        Grouping(
            "dd_cfdna_year1",
            "dd-cfDNA >= 1% in year 1",
            YEAR,
            lambda f: by_day(
                f["dd_cfdna_high_day"],
                YEAR,
                "dd-cfDNA >= 1% in year 1",
                "No dd-cfDNA >= 1% in year 1",
            ),
        ),
        Grouping("rejection_year1", "Biopsy-proven rejection in year 1", YEAR, rejection_year1),
    ]
}


def at_landmark(frame: pd.DataFrame, day: int) -> pd.DataFrame:
    """Patients still at risk after `day` (no event, still followed), with the clock restarted
    there. Day 0 keeps everyone."""
    if day <= 0:
        return frame.copy()
    kept = frame[frame["time"] > day].copy()
    kept["time"] = kept["time"] - day
    return kept


def start_stop(frame: pd.DataFrame, exposure_day: str, by: int) -> pd.DataFrame:
    """Start/stop rows: unexposed until the first exposure (if on or before `by` and before the
    end of follow-up), exposed from then on. An exposure on the event day counts as after it."""
    rows = []
    days, times = frame[exposure_day].to_numpy(float), frame["time"].to_numpy(float)
    events = (frame["status"] == GRAFT_LOSS).to_numpy(int)
    for i, (day, time, event) in enumerate(zip(days, times, events, strict=True)):
        if not np.isnan(day) and day <= by and day < time:
            if day > 0:
                rows.append((i, 0.0, float(day), 0, 0))
            rows.append((i, float(day), float(time), int(event), 1))
        else:
            rows.append((i, 0.0, float(time), int(event), 0))
    return pd.DataFrame(rows, columns=["id", "start", "stop", "event", "exposed"])


@dataclass(frozen=True)
class HazardRatio:
    """Exposed vs unexposed, with its 95% CI and the data it came from."""

    design: str
    hazard_ratio: float
    lower: float
    upper: float
    coef: float
    se: float
    n: int
    events: int


def hazard_ratio(design: str, coef: float, se: float, n: int, events: int) -> HazardRatio:
    """exp(coef) with a Wald 95% interval."""
    return HazardRatio(
        design,
        float(np.exp(coef)),
        float(np.exp(coef - Z95 * se)),
        float(np.exp(coef + Z95 * se)),
        coef,
        se,
        n,
        events,
    )


def naive_from_time_zero(frame: pd.DataFrame, exposure_day: str, by: int) -> HazardRatio:
    """The biased design: exposed = ever exposed by `by`, compared from transplant."""
    data = pd.DataFrame(
        {
            "time": frame["time"].astype(float),
            "event": (frame["status"] == GRAFT_LOSS).astype(int),
            "exposed": (frame[exposure_day].notna() & (frame[exposure_day] <= by)).astype(int),
        }
    )
    fit = CoxPHFitter().fit(data, "time", "event")
    return hazard_ratio(
        "naive (from transplant)",
        float(fit.params_["exposed"]),
        float(fit.standard_errors_["exposed"]),
        len(data),
        int(data["event"].sum()),
    )


def landmark_ratio(frame: pd.DataFrame, exposure_day: str, landmark: int) -> HazardRatio:
    """Exposed by the landmark vs not, among patients at risk at the landmark."""
    kept = at_landmark(frame, landmark)
    data = pd.DataFrame(
        {
            "time": kept["time"].astype(float),
            "event": (kept["status"] == GRAFT_LOSS).astype(int),
            "exposed": (kept[exposure_day].notna() & (kept[exposure_day] <= landmark)).astype(int),
        }
    )
    fit = CoxPHFitter().fit(data, "time", "event")
    return hazard_ratio(
        f"landmark at day {landmark}",
        float(fit.params_["exposed"]),
        float(fit.standard_errors_["exposed"]),
        len(data),
        int(data["event"].sum()),
    )


def time_varying_ratio(frame: pd.DataFrame, exposure_day: str, by: int) -> HazardRatio:
    """Exposure as a time-varying covariate from its first day (Cox with start/stop rows)."""
    rows = start_stop(frame, exposure_day, by)
    fit = CoxTimeVaryingFitter().fit(
        rows, id_col="id", event_col="event", start_col="start", stop_col="stop"
    )
    return hazard_ratio(
        "time-varying covariate",
        float(fit.params_["exposed"]),
        float(fit.standard_errors_["exposed"]),
        len(frame),
        int(rows["event"].sum()),
    )


def immortal_time_comparison(
    frame: pd.DataFrame, exposure_day: str, by: int = YEAR
) -> list[HazardRatio]:
    """The same exposure analysed three ways: naive (biased), landmark and time-varying."""
    return [
        naive_from_time_zero(frame, exposure_day, by),
        landmark_ratio(frame, exposure_day, by),
        time_varying_ratio(frame, exposure_day, by),
    ]
