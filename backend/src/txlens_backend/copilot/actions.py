"""What the copilot's planner may decide: one schema-validated action per step.

The model can only pick one of these (a discriminated union validated by Pydantic): text in
a question, a tool result or an abstract cannot add a tool or change what a tool may do.
Structured tools come first; SQL is for aggregate questions they cannot express.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from txlens_backend.services.cohorts import CohortRules
from txlens_backend.survival.service import CovariateKey

SurvivalGrouping = Literal["none", "organ", "donor_type", "age_band", "dgf", "dsa_month1",
                           "dd_cfdna_year1", "rejection_year1"]  # fmt: skip
RefusalReason = Literal["identifiers", "cross_tenant", "write", "clinical_advice",
                        "prompt_injection", "out_of_scope"]  # fmt: skip


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConceptSearch(Action):
    """Find SNOMED condition concept ids by name (for cohort rules or SQL)."""

    tool: Literal["concept_search"]
    query: str = Field(min_length=2, max_length=100)


class PreviewCohort(Action):
    """Count patients passing cohort rules, with attrition (preferred for 'how many')."""

    tool: Literal["preview_cohort"]
    rules: CohortRules


class RunSurvival(Action):
    """Graft survival (death-censored graft loss): Kaplan-Meier, log-rank, Cox."""

    tool: Literal["run_survival"]
    rules: CohortRules | None = Field(default=None, description="Population; none = everyone")
    grouping: SurvivalGrouping = "none"
    landmark_days: int | None = Field(default=None, ge=0, le=1095)
    covariates: list[CovariateKey] = Field(default_factory=list, max_length=5)


class SqlQuery(Action):
    """One aggregate SELECT over the copilot views (only when no structured tool fits)."""

    tool: Literal["sql_query"]
    sql: str = Field(min_length=10, max_length=4000)
    purpose: str = Field(max_length=200, description="What the query answers, in a few words")


class LiteratureSearch(Action):
    """Search open-access transplant abstracts (hybrid vector + full-text)."""

    tool: Literal["literature_search"]
    query: str = Field(min_length=3, max_length=300)


class Answer(Action):
    """Enough is known: write the answer from the tool results."""

    tool: Literal["answer"]


class Refuse(Action):
    """The question asks for something the copilot must not do."""

    tool: Literal["refuse"]
    reason: RefusalReason


ToolAction = ConceptSearch | PreviewCohort | RunSurvival | SqlQuery | LiteratureSearch
AnyAction = Annotated[ToolAction | Answer | Refuse, Field(discriminator="tool")]


class Step(BaseModel):
    """The planner's decision for the next step."""

    model_config = ConfigDict(extra="forbid")

    action: AnyAction


class Screening(BaseModel):
    """The classifier's reading of a question."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["data", "literature", "both", "refuse"]
    reason: RefusalReason | None = None
