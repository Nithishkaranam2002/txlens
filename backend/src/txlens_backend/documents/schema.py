"""What the LLM must return for a biopsy report: each value with its evidence, or null.

Every field is a `Finding`: a value plus the line number and the exact words it was read
from. A value without evidence is rejected later (evidence.py); a finding the report does not
state must be null with no evidence. The model never chooses a diagnosis for heart biopsies:
the category follows from the ISHLT grades in Python (`heart_diagnosis`).
"""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Organ = Literal["kidney", "heart"]
Diagnosis = Literal["no_rejection", "borderline", "TCMR", "ABMR", "mixed"]
TcmrGrade = Literal["IA", "IB", "IIA", "IIB", "III"]
Adequacy = Literal["adequate", "marginal", "inadequate"]
AdditionalFinding = Literal["BK_nephropathy", "ATN", "CMV", "other"]
AcrGrade = Literal["0R", "1R", "2R", "3R"]
PamrGrade = Literal["pAMR 0", "pAMR 1 (H+)", "pAMR 1 (I+)", "pAMR 2", "pAMR 3"]
Score = Annotated[int, Field(ge=0, le=3)]
BANFF_SCORES = ("banff_i", "banff_t", "banff_v", "banff_g", "banff_ptc", "banff_ci", "banff_ct")
# The Banff lesion label printed before each score, e.g. "ptc2".
SCORE_LABELS = {f: f.removeprefix("banff_") for f in (*BANFF_SCORES, "banff_cv")} | {"c4d": "C4d"}


class Evidence(BaseModel):
    """Where a value was read: a line number from the document block and words copied from it."""

    model_config = ConfigDict(extra="forbid")

    line: int = Field(description="Line number (the number before ':' in the document block).")
    quote: str = Field(
        description="The shortest exact words from that line that state the value.",
        max_length=200,
    )


class Finding[T](BaseModel):
    """A value with its evidence, or null when the report does not state it."""

    model_config = ConfigDict(extra="forbid")

    value: T | None = Field(description="null when the report does not state this.")
    evidence: Evidence | None = Field(description="Required when value is not null.")


class KidneyBiopsy(BaseModel):
    """Findings of a kidney allograft biopsy report (Banff classification)."""

    model_config = ConfigDict(extra="forbid")

    biopsy_date: Finding[date] = Field(description="The procedure date (not the report date).")
    adequacy: Finding[Adequacy] = Field(description="Specimen adequacy, only if stated.")
    banff_i: Finding[Score]
    banff_t: Finding[Score]
    banff_v: Finding[Score]
    banff_g: Finding[Score]
    banff_ptc: Finding[Score]
    banff_ci: Finding[Score]
    banff_ct: Finding[Score]
    banff_cv: Finding[Score] = Field(description="Banff cv score, only if printed.")
    c4d: Finding[Score] = Field(description="C4d score (0-3) in peritubular capillaries.")
    diagnosis: Finding[Diagnosis] = Field(
        description="Rejection category stated in the DIAGNOSIS section. 'mixed' = both "
        "T-cell mediated and antibody-mediated rejection."
    )
    tcmr_grade: Finding[TcmrGrade] = Field(
        description="Banff grade of T-cell mediated rejection; null if there is none."
    )
    additional_finding: Finding[AdditionalFinding] = Field(
        description="Non-rejection finding stated in the diagnosis: BK (polyomavirus) "
        "nephropathy, ATN (acute tubular injury/necrosis), CMV, or other; null if none."
    )


class HeartBiopsy(BaseModel):
    """Findings of an endomyocardial biopsy report (ISHLT grading)."""

    model_config = ConfigDict(extra="forbid")

    biopsy_date: Finding[date] = Field(description="The procedure date (not the report date).")
    acr_grade: Finding[AcrGrade] = Field(description="ISHLT 2004 acute cellular rejection grade.")
    pamr_grade: Finding[PamrGrade] = Field(
        description="ISHLT 2013 pathologic antibody-mediated rejection grade."
    )


Extraction = KidneyBiopsy | HeartBiopsy
SCHEMAS: dict[Organ, type[KidneyBiopsy] | type[HeartBiopsy]] = {
    "kidney": KidneyBiopsy,
    "heart": HeartBiopsy,
}


def heart_diagnosis(acr: AcrGrade | None, pamr: PamrGrade | None) -> Diagnosis | None:
    """Rejection category from ISHLT grades (a fixed rule, not a model judgement).

    Cellular rejection needs grade 2R or 3R; 1R is reported as borderline (mild). Any pAMR
    grade above 0 is antibody-mediated rejection; both together are mixed.
    """
    if acr is None or pamr is None:
        return None
    cellular = acr in ("2R", "3R")
    antibody = pamr != "pAMR 0"
    if cellular and antibody:
        return "mixed"
    if antibody:
        return "ABMR"
    if cellular:
        return "TCMR"
    return "borderline" if acr == "1R" else "no_rejection"
