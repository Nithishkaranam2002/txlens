"""Deterministic checks on an extraction: required fields, dates and Banff/ISHLT consistency.

The model only reads the report; these rules decide whether what it read hangs together. A
failed rule never changes a value: it sends the document to a human with the reason.

Banff rules (simplified from the Banff 2019 classification):
- T-cell mediated rejection grade IA needs i >= 2 and t = 2; IB needs i >= 2 and t = 3;
  IIA, IIB and III need intimal arteritis v = 1, 2 and 3.
- Borderline means some tubulitis and inflammation (t >= 1, i >= 1) short of grade IA, v = 0.
- Antibody-mediated rejection needs microvascular inflammation (g + ptc >= 2) or C4d >= 2.
- "No rejection" contradicts TCMR-level scores, unless BK nephropathy explains them (BK virus
  causes the same tubulitis).
"""

from collections.abc import Mapping
from datetime import date
from typing import Any

from txlens_backend.documents.schema import BANFF_SCORES, Organ

DATE_WINDOW_DAYS = 7
GRADE_V = {"IIA": 1, "IIB": 2, "III": 3}
REQUIRED: dict[Organ, tuple[str, ...]] = {
    "kidney": ("biopsy_date", "diagnosis", *BANFF_SCORES, "c4d"),
    "heart": ("biopsy_date", "acr_grade", "pamr_grade"),
}


def missing(organ: Organ, values: Mapping[str, Any]) -> list[str]:
    """Required fields the report did not yield."""
    return [f"{field} is missing" for field in REQUIRED[organ] if values.get(field) is None]


def date_problems(biopsy_date: date | None, procedure_date: date | None) -> list[str]:
    """The biopsy date must lie within 7 days of the linked biopsy procedure."""
    if procedure_date is None:
        return ["not linked to a biopsy procedure"]
    if biopsy_date is None:
        return []
    gap = abs((biopsy_date - procedure_date).days)
    if gap > DATE_WINDOW_DAYS:
        return [f"biopsy date is {gap} days from the procedure on {procedure_date}"]
    return []


def meets_grade(grade: str, i: int, t: int, v: int) -> bool:
    """True if the lesion scores satisfy the stated Banff TCMR grade."""
    if grade == "IA":
        return i >= 2 and t == 2 and v == 0
    if grade == "IB":
        return i >= 2 and t == 3 and v == 0
    return v == GRADE_V[grade]


def tcmr_level(i: int, t: int, v: int) -> bool:
    """True if the scores reach T-cell mediated rejection (grade IA or above)."""
    return (i >= 2 and t >= 2) or v >= 1


def kidney_problems(values: Mapping[str, Any]) -> list[str]:
    """Banff consistency between the diagnosis, its grade and the lesion scores."""
    diagnosis, grade = values.get("diagnosis"), values.get("tcmr_grade")
    raw = {f: values.get(f) for f in (*BANFF_SCORES, "c4d")}
    if diagnosis is None or any(v is None for v in raw.values()):
        return []  # reported as missing instead
    scores = {f: int(v) for f, v in raw.items() if v is not None}
    i, t, v = scores["banff_i"], scores["banff_t"], scores["banff_v"]
    mvi = scores["banff_g"] + scores["banff_ptc"]
    cellular, antibody = diagnosis in ("TCMR", "mixed"), diagnosis in ("ABMR", "mixed")
    problems = []
    if cellular and grade is None:
        problems.append(f"{diagnosis} without a Banff TCMR grade")
    if not cellular and grade is not None:
        problems.append(f"TCMR grade {grade} given for diagnosis {diagnosis}")
    if cellular and grade is not None and not meets_grade(grade, i, t, v):
        problems.append(f"scores i{i} t{t} v{v} do not meet TCMR grade {grade}")
    if antibody and mvi < 2 and scores["c4d"] < 2:
        problems.append(f"{diagnosis} with g+ptc = {mvi} and C4d{scores['c4d']}")
    if diagnosis == "borderline" and (tcmr_level(i, t, v) or i < 1 or t < 1):
        problems.append(f"scores i{i} t{t} v{v} do not fit borderline")
    bk = values.get("additional_finding") == "BK_nephropathy"
    if diagnosis == "no_rejection" and tcmr_level(i, t, v) and not bk:
        problems.append(f"no rejection reported with TCMR-level scores i{i} t{t} v{v}")
    return problems


def validate(organ: Organ, values: Mapping[str, Any], procedure_date: date | None) -> list[str]:
    """Every problem with one extraction; empty means it passed."""
    problems = missing(organ, values)
    problems += date_problems(values.get("biopsy_date"), procedure_date)
    if organ == "kidney":
        problems += kidney_problems(values)
    return problems
