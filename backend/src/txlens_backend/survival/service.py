"""A survival analysis request against the caller's center: population, grouping, landmark.

The landmark guard: an analysis never starts before its groups are known. Each grouping has a
horizon (landmark.GROUPINGS); a saved cohort's horizon is the end of its latest lab-rule
window. Without a landmark the analysis starts at that horizon; a landmark before it is
refused. Cohort membership is the saved snapshot, kept only for patients who already met the
rules by the landmark (conditions recorded by then; lab windows end by then by construction).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, text

from txlens_backend.services import cohorts
from txlens_backend.survival.analysis import Report, Spec, analyse
from txlens_backend.survival.cox import COVARIATES
from txlens_backend.survival.extract import extract
from txlens_backend.survival.landmark import GROUPINGS

GroupingKey = Literal["none", "organ", "donor_type", "age_band", "dgf", "dsa_month1",
                      "dd_cfdna_year1", "rejection_year1", "cohort"]  # fmt: skip
CovariateKey = Literal["age_per_decade", "deceased_donor", "dsa_positive", "dgf", "heart"]
MAX_LANDMARK_DAYS = 3 * 365


class AnalysisRequest(BaseModel):
    """What to analyse. Graft loss (death-censored) is always the outcome."""

    model_config = ConfigDict(extra="forbid")

    cohort_id: str | None = Field(
        default=None,
        max_length=64,
        description="Population: a saved cohort (default: every patient).",
    )
    rules: cohorts.CohortRules | None = Field(
        default=None, description="Population: patients passing these rules (not saved)."
    )
    grouping: GroupingKey = "none"
    compare_cohort_id: str | None = Field(
        default=None,
        max_length=64,
        description="grouping = cohort: members of this cohort vs the rest.",
    )
    landmark_days: int | None = Field(
        default=None, ge=0, le=MAX_LANDMARK_DAYS,
        description="Start of follow-up, in days after transplant (default: when the groups "
        "are known).",
    )  # fmt: skip
    covariates: list[CovariateKey] = Field(
        default=["age_per_decade", "deceased_donor", "dsa_positive", "dgf", "heart"],
        max_length=len(COVARIATES),
    )

    @model_validator(mode="after")
    def cohort_grouping_names_a_cohort(self) -> "AnalysisRequest":
        """grouping = cohort needs compare_cohort_id, and only then."""
        if (self.grouping == "cohort") != (self.compare_cohort_id is not None):
            raise ValueError("compare_cohort_id is required with grouping = cohort, only then")
        if self.cohort_id is not None and self.rules is not None:
            raise ValueError("give the population as cohort_id or as rules, not both")
        return self


class Option(BaseModel):
    key: str
    label: str
    horizon_days: int


class Options(BaseModel):
    """Groupings (with the day each is known) and Cox covariates the API accepts."""

    groupings: list[Option]
    covariates: list[Option]


class CohortNotFoundError(LookupError):
    """A cohort id that is not a cohort of this center."""


class LandmarkTooEarlyError(ValueError):
    """The landmark would start follow-up before the groups are known."""


def options() -> Options:
    """What the UI can offer."""
    groupings = [Option(key=g.key, label=g.label, horizon_days=g.horizon_days)
                 for g in GROUPINGS.values()]  # fmt: skip
    groupings.append(Option(key="cohort", label="Saved cohort (in vs not)", horizon_days=0))
    return Options(
        groupings=groupings,
        covariates=[Option(key=k, label=v, horizon_days=0) for k, v in COVARIATES.items()],
    )


def saved_cohort(conn: Connection, cohort_id: str) -> cohorts.Cohort:
    """A cohort of this center (CohortNotFoundError otherwise)."""
    found = cohorts.get(conn, cohort_id)
    if found is None:
        raise CohortNotFoundError(cohort_id)
    return found


def members_by(conn: Connection, cohort: cohorts.Cohort, day: int) -> set[int]:
    """Saved members who already met the rules by `day` after transplant."""
    saved = conn.execute(
        text("SELECT person_id FROM app.cohort_member WHERE cohort_id::text = :c"),
        {"c": cohort.cohort_id},
    ).scalars()
    return set(int(p) for p in saved) & set(cohorts.member_ids(conn, cohort.rules, day))


def resolve_landmark(request: AnalysisRequest, horizon: int, what: str) -> int:
    """The requested landmark, or the horizon; never before the horizon."""
    if request.landmark_days is None:
        return horizon
    if request.landmark_days < horizon:
        raise LandmarkTooEarlyError(
            f"{what} is known only from day {horizon} after transplant; a landmark at day "
            f"{request.landmark_days} would compare groups defined by the future (immortal "
            f"time bias). Use a landmark of at least {horizon} days."
        )
    return request.landmark_days


def population_rules(conn: Connection, request: AnalysisRequest) -> cohorts.CohortRules | None:
    """The rules defining the population: given inline, or the saved cohort's (None: all)."""
    if request.cohort_id:
        return saved_cohort(conn, request.cohort_id).rules
    return request.rules


def run(conn: Connection, request: AnalysisRequest, threshold: int) -> Report:
    """The suppressed report for the caller's center (RLS applies to every query)."""
    saved = saved_cohort(conn, request.cohort_id) if request.cohort_id else None
    rules = saved.rules if saved else request.rules
    compare = (saved_cohort(conn, request.compare_cohort_id)
               if request.compare_cohort_id else None)  # fmt: skip
    grouping = None if compare else GROUPINGS[request.grouping]
    label = (
        f"Membership of cohort '{compare.name}'" if compare else GROUPINGS[request.grouping].label
    )
    horizons = [grouping.horizon_days if grouping else 0]
    horizons += [cohorts.horizon_days(r) for r in (rules, compare and compare.rules) if r]
    landmark = resolve_landmark(request, max(horizons), label)
    frame = extract(conn)
    if saved:
        frame = frame[frame["person_id"].isin(members_by(conn, saved, landmark))]
    elif rules:
        frame = frame[frame["person_id"].isin(cohorts.member_ids(conn, rules, landmark))]
    frame = frame.reset_index(drop=True)
    if grouping:
        groups = grouping.assign(frame)
    else:
        assert compare is not None
        inside = frame["person_id"].isin(members_by(conn, compare, landmark))
        groups = inside.map({True: f"In '{compare.name}'", False: f"Not in '{compare.name}'"})
    spec = Spec(
        population=f"saved cohort '{saved.name}'" if saved
        else "patients matching the given rules" if rules
        else "every transplant recipient of the center",
        grouping=request.grouping, grouping_label=label, landmark_days=landmark,
        covariates=list(request.covariates), threshold=threshold,
    )  # fmt: skip
    return analyse(frame, groups, spec)
