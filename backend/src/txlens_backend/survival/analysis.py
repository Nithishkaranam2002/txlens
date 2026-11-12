"""One survival analysis, from an extract to the suppressed report the API and exports show.

Groups (and the population) are given as labels on the extract. The analysis starts at the
landmark (never before the grouping's horizon), fits Kaplan-Meier, the log-rank test,
Aalen-Johansen cumulative incidence and 1 - KM per group, and a Cox model on the population
from the covariates' own horizon. Only suppressed numbers leave this module.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from pydantic import BaseModel

from txlens_backend.deid import suppress
from txlens_backend.survival.competing import aalen_johansen
from txlens_backend.survival.cox import fit_cox
from txlens_backend.survival.extract import GRAFT_LOSS
from txlens_backend.survival.km import kaplan_meier, log_rank
from txlens_backend.survival.landmark import DGF_HORIZON_DAYS, DSA_HORIZON_DAYS, at_landmark
from txlens_backend.survival.methods_text import (
    MethodsFacts,
    in_sentence,
    methods_paragraph,
    software,
)
from txlens_backend.survival.suppression import Count, Rule, ShownCurve, read_off, shown_curve

GRID_DAYS = (0, 91, 183, 365, 730, 1095)  # risk table: 0, 3, 6, 12, 24, 36 months
INCIDENCE_DAYS = (365, 730, 1095)
COVARIATE_HORIZON_DAYS = {"dsa_positive": DSA_HORIZON_DAYS, "dgf": DGF_HORIZON_DAYS}


class RiskCell(BaseModel):
    """At a day since transplant: at risk then, and graft losses since the previous column."""

    day: int
    n_risk: Count
    events: Count


class RiskRow(BaseModel):
    group: str
    cells: list[RiskCell]


class LogRankOut(BaseModel):
    chi2: float
    df: int
    p_value: float
    observed: dict[str, Count]
    expected: dict[str, float]


class IncidenceAt(BaseModel):
    """Cumulative incidence of graft loss vs 1 - KM, read off the displayed curves."""

    group: str
    day: int
    cumulative_incidence: float | None
    one_minus_km: float | None


class TermOut(BaseModel):
    key: str
    label: str
    hazard_ratio: float
    lower: float
    upper: float
    p_value: float
    ph_p_value: float
    ph_violated: bool
    n_exposed: Count | None
    events_exposed: Count | None


class CoxOut(BaseModel):
    landmark_days: int
    n: Count
    events: Count
    terms: list[TermOut]


class Report(BaseModel):
    """Everything shown for one analysis. Counts are suppressed; synthetic data only."""

    synthetic_data: bool = True
    population: str
    grouping: str
    grouping_label: str
    landmark_days: int
    n: Count
    events: Count
    km: list[ShownCurve]
    risk_table: list[RiskRow]
    log_rank: LogRankOut | None
    incidence: list[ShownCurve]
    one_minus_km: list[ShownCurve]
    incidence_at: list[IncidenceAt]
    cox: CoxOut | None
    methods: str
    warnings: list[str]
    software: str


@dataclass(frozen=True)
class Spec:
    """What to analyse: population name, grouping, landmark and Cox covariates."""

    population: str
    grouping: str
    grouping_label: str
    landmark_days: int
    covariates: list[str]
    threshold: int


def risk_row(group: str, data: pd.DataFrame, landmark: int, threshold: int) -> RiskRow:
    """Number at risk at each grid day (from the landmark on) and graft losses per interval."""
    days = sorted({landmark, *(g for g in GRID_DAYS if g > landmark)})
    t, loss = data["time"].to_numpy(float) + landmark, (data["status"] == GRAFT_LOSS).to_numpy()
    cells, previous = [], -np.inf
    for day in days:
        events = int((loss & (t > previous) & (t <= day)).sum())
        cells.append(
            RiskCell(
                day=day,
                n_risk=suppress(int((t >= day).sum()), threshold),
                events=suppress(events, threshold),
            )
        )
        previous = day
    return RiskRow(group=group, cells=cells)


def cox_part(population: pd.DataFrame, spec: Spec, warnings: list[str]) -> CoxOut | None:
    """Cox on the population from the covariates' horizon (or the landmark if later)."""
    if not spec.covariates:
        return None
    start = max([spec.landmark_days, *(COVARIATE_HORIZON_DAYS.get(c, 0) for c in spec.covariates)])
    data = at_landmark(population, start)
    if int((data["status"] == GRAFT_LOSS).sum()) < spec.threshold:
        warnings.append(f"No Cox model: fewer than {spec.threshold} graft losses.")
        return None
    result = fit_cox(data, spec.covariates)
    if result.missing:
        warnings.append(
            f"Cox model: {suppress(result.excluded, spec.threshold)} patients without a value for "
            f"{' / '.join(result.missing)} were left out (complete cases)."
        )
    warnings.extend(result.warnings)
    s = spec.threshold
    terms = [
        TermOut(
            key=t.key,
            label=t.label,
            hazard_ratio=t.hazard_ratio,
            lower=t.lower,
            upper=t.upper,
            p_value=t.p_value,
            ph_p_value=t.ph_p_value,
            ph_violated=t.ph_violated,
            n_exposed=None if t.n_exposed is None else suppress(t.n_exposed, s),
            events_exposed=None if t.events_exposed is None else suppress(t.events_exposed, s),
        )
        for t in result.terms
    ]
    return CoxOut(
        landmark_days=start, n=suppress(result.n, s), events=suppress(result.events, s), terms=terms
    )


def analyse(frame: pd.DataFrame, groups: pd.Series, spec: Spec) -> Report:
    """The suppressed report. `groups` labels each row of `frame` (NaN: not classifiable)."""
    warnings: list[str] = []
    data = at_landmark(frame, spec.landmark_days)
    labels = groups.loc[data.index]
    if labels.isna().any():
        warnings.append(
            f"{suppress(int(labels.isna().sum()), spec.threshold)} patients could not "
            f"be grouped by {in_sentence(spec.grouping_label)} and were left out."
        )
    data, labels = data[labels.notna()], labels.dropna().astype(str)
    rule = Rule(spec.threshold, spec.landmark_days)
    km, cif, one_minus, risk, incidence_at = [], [], [], [], []
    for group in sorted(labels.unique()):
        part = data[labels == group]
        loss = (part["status"] == GRAFT_LOSS).astype(int)
        km_curve = kaplan_meier(part["time"], loss, group)
        km.append(shown_curve(km_curve, len(part), 1.0, rule))
        one_minus.append(shown_curve(km_curve, len(part), 0.0, rule, complement=True))
        cif.append(
            shown_curve(
                aalen_johansen(part["time"], part["status"], GRAFT_LOSS, group),
                len(part),
                0.0,
                rule,
            )
        )
        risk.append(risk_row(group, part, spec.landmark_days, spec.threshold))
        incidence_at += [
            IncidenceAt(
                group=group,
                day=day,
                cumulative_incidence=read_off(cif[-1].points, day),
                one_minus_km=read_off(one_minus[-1].points, day),
            )
            for day in INCIDENCE_DAYS
            if day > spec.landmark_days
        ]
    test = None
    if labels.nunique() > 1:
        lr = log_rank(data["time"], (data["status"] == GRAFT_LOSS).astype(int), labels)
        test = LogRankOut(
            chi2=lr.chi2,
            df=lr.df,
            p_value=lr.p_value,
            observed={g: suppress(v, spec.threshold) for g, v in lr.observed.items()},
            expected=lr.expected,
        )
    cox = cox_part(frame, spec, warnings)
    n = suppress(len(data), spec.threshold)
    events = suppress(int((data["status"] == GRAFT_LOSS).sum()), spec.threshold)
    terms = cox.terms if cox else []
    facts = MethodsFacts(
        spec.population,
        n,
        events,
        spec.grouping_label if labels.nunique() > 1 else None,
        spec.landmark_days,
        [t.label for t in terms],
        cox.n if cox else None,
        cox.events if cox else None,
        cox.landmark_days if cox else 0,
        [t.label for t in terms if t.ph_violated],
        spec.threshold,
    )
    return Report(
        population=spec.population,
        grouping=spec.grouping,
        grouping_label=spec.grouping_label,
        landmark_days=spec.landmark_days,
        n=n,
        events=events,
        km=km,
        risk_table=risk,
        log_rank=test,
        incidence=cif,
        one_minus_km=one_minus,
        incidence_at=incidence_at,
        cox=cox,
        methods=methods_paragraph(facts),
        warnings=warnings,
        software=software(),
    )
