"""Cohort rules -> SQL, attrition counts, and saved cohorts.

A cohort is every patient of the caller's center who passes all rules. The SQL is assembled
only from fixed fragments chosen by validated enums; every user value is a bind parameter.
Counts are step by step ("attrition"): how many patients remain after each rule, in order.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.expression import bindparam

from txlens_backend.clinical_codes import DSA_LOCAL, LABS
from txlens_backend.services.facts import FACTS_CTE, FACTS_PARAMS
from txlens_backend.services.patients import DonorType, Organ

LabKey = Literal["dd_cfdna", "creatinine", "egfr", "tacrolimus_trough", "dsa_mfi"]
OPERATORS = {">=": ">=", ">": ">", "<=": "<=", "<": "<"}  # validated op -> SQL operator
LAB_CODES = dict(LABS) | {"dsa_mfi": DSA_LOCAL}


class LabRule(BaseModel):
    """At least one result of `lab` meeting `op value` within a window after transplant."""

    model_config = ConfigDict(extra="forbid")

    lab: LabKey
    op: Literal[">=", ">", "<=", "<"]
    value: float
    days_from_transplant: tuple[int, int] = Field(
        default=(0, 365), description="Inclusive window, in days after the transplant date."
    )

    @model_validator(mode="after")
    def window_is_ordered(self) -> "LabRule":
        """The window's start is not after its end."""
        start, end = self.days_from_transplant
        if start > end:
            raise ValueError("days_from_transplant: start must not be after end")
        return self


class DateRange(BaseModel):
    """Inclusive date range; either end may be open."""

    model_config = ConfigDict(extra="forbid")

    start: date | None = None
    end: date | None = None


class CohortRules(BaseModel):
    """All rules must hold. Empty rules select every patient of the center."""

    model_config = ConfigDict(extra="forbid")

    organ: Organ | None = None
    donor_type: DonorType | None = None
    age_min: int | None = Field(default=None, ge=0, le=120, description="Age at transplant")
    age_max: int | None = Field(default=None, ge=0, le=120, description="Age at transplant")
    condition_concept_ids: list[int] = Field(
        default_factory=list, max_length=50,
        description="Has any of these conditions, or any of their descendants.",
    )  # fmt: skip
    lab_rules: list[LabRule] = Field(default_factory=list, max_length=10)
    transplant_date: DateRange | None = None


class Step(BaseModel):
    """Patients remaining after one rule (a string like "<11" when suppressed)."""

    rule: str
    count: int | str


class Preview(BaseModel):
    """Cohort size and attrition."""

    count: int | str
    steps: list[Step]


class Cohort(BaseModel):
    """A saved cohort."""

    cohort_id: str
    name: str
    rules: CohortRules
    created_by: str
    created_at: datetime
    member_count: int | str


@dataclass
class Query:
    """SQL rule fragments (in order, with labels) and their bind parameters."""

    steps: list[tuple[str, str]]
    params: dict[str, object]


def horizon_days(rules: CohortRules) -> int:
    """The last day after transplant whose results the rules look at (0 if only baseline facts):
    a survival analysis comparing this cohort must not start before it."""
    return max([0, *(lab.days_from_transplant[1] for lab in rules.lab_rules)])


def compile_rules(rules: CohortRules, as_of_day: int | None = None) -> Query:
    """One SQL condition per rule, over the `facts` row `f`. With `as_of_day`, a condition
    counts only if recorded by that day after transplant (what was known then)."""
    q = Query([], dict(FACTS_PARAMS))
    if rules.organ:
        q.steps.append((f"organ = {rules.organ}", "f.organ = :organ"))
        q.params["organ"] = rules.organ
    if rules.donor_type:
        q.steps.append((f"donor type = {rules.donor_type}", "f.donor_type = :donor_type"))
        q.params["donor_type"] = rules.donor_type
    age = "(extract(year FROM f.transplant_date) - f.year_of_birth)"
    if rules.age_min is not None:
        q.steps.append((f"age at transplant >= {rules.age_min}", f"{age} >= :age_min"))
        q.params["age_min"] = rules.age_min
    if rules.age_max is not None:
        q.steps.append((f"age at transplant <= {rules.age_max}", f"{age} <= :age_max"))
        q.params["age_max"] = rules.age_max
    if rules.transplant_date and rules.transplant_date.start:
        q.steps.append(("transplant on/after start", "f.transplant_date >= :tx_start"))
        q.params["tx_start"] = rules.transplant_date.start
    if rules.transplant_date and rules.transplant_date.end:
        q.steps.append(("transplant on/before end", "f.transplant_date <= :tx_end"))
        q.params["tx_end"] = rules.transplant_date.end
    if rules.condition_concept_ids:
        q.steps.append((
            f"condition in {rules.condition_concept_ids} (or descendants)",
            "EXISTS (SELECT 1 FROM cdm.condition_occurrence co "
            "JOIN vocab.concept_ancestor ca ON ca.descendant_concept_id = co.condition_concept_id "
            "WHERE co.person_id = f.person_id AND ca.ancestor_concept_id = ANY(:conditions)"
            + ("" if as_of_day is None
               else " AND co.condition_start_date <= f.transplant_date + :as_of_day") + ")",
        ))  # fmt: skip
        q.params["conditions"] = rules.condition_concept_ids
        if as_of_day is not None:
            q.params["as_of_day"] = as_of_day
    for n, lab in enumerate(rules.lab_rules):
        q.steps.append((lab_label(lab), lab_condition(n, lab)))
        vocabulary, code = LAB_CODES[lab.lab]
        q.params |= {f"lab{n}_vocab": vocabulary, f"lab{n}_code": code, f"lab{n}_value": lab.value,
                     f"lab{n}_from": lab.days_from_transplant[0],
                     f"lab{n}_to": lab.days_from_transplant[1]}  # fmt: skip
    return q


def lab_label(lab: LabRule) -> str:
    """'dd_cfdna >= 1.0 on days 0-365 after transplant'."""
    start, end = lab.days_from_transplant
    return f"{lab.lab} {lab.op} {lab.value:g} on days {start}-{end} after transplant"


def lab_condition(n: int, lab: LabRule) -> str:
    """EXISTS a qualifying result. DSA has no standard concept, so it matches the source one."""
    column = "measurement_source_concept_id" if lab.lab == "dsa_mfi" else "measurement_concept_id"
    return (
        "EXISTS (SELECT 1 FROM cdm.measurement m "
        f"JOIN vocab.concept c ON c.concept_id = m.{column} "
        f"WHERE m.person_id = f.person_id AND c.vocabulary_id = :lab{n}_vocab "
        f"AND c.concept_code = :lab{n}_code AND m.value_as_number {OPERATORS[lab.op]} "
        f":lab{n}_value AND m.measurement_date BETWEEN f.transplant_date + :lab{n}_from "
        f"AND f.transplant_date + :lab{n}_to)"
    )


def all_of(conditions: Sequence[str]) -> str:
    """Conditions joined with AND (TRUE when there are none)."""
    return " AND ".join(f"({c})" for c in conditions) or "TRUE"


def preview(conn: Connection, rules: CohortRules) -> tuple[int, list[tuple[str, int]]]:
    """(final count, [(rule label, patients remaining after it)])."""
    q = compile_rules(rules)
    counts = [
        f"count(*) FILTER (WHERE {all_of([c for _, c in q.steps[: i + 1]])})"
        for i in range(len(q.steps))
    ]
    row = conn.execute(
        text(f"WITH {FACTS_CTE} SELECT {', '.join(['count(*)', *counts])} FROM facts f"),
        q.params,
    ).one()
    steps = [("all patients", int(row[0]))]
    steps += [(label, int(row[i + 1])) for i, (label, _) in enumerate(q.steps)]
    return steps[-1][1], steps


def member_ids(conn: Connection, rules: CohortRules, as_of_day: int | None = None) -> list[int]:
    """person_id of every patient passing all rules (with what was known by `as_of_day`)."""
    q = compile_rules(rules, as_of_day)
    sql = (
        f"WITH {FACTS_CTE} SELECT f.person_id FROM facts f WHERE {all_of([c for _, c in q.steps])}"
    )
    return [int(p) for p in conn.execute(text(sql + " ORDER BY f.person_id"), q.params).scalars()]


def save(conn: Connection, name: str, rules: CohortRules, user_id: str) -> str:
    """Store the cohort and a snapshot of its members; returns the cohort id."""
    members = member_ids(conn, rules)
    cohort_id = conn.execute(
        text(
            "INSERT INTO app.cohort (name, rules, created_by, member_count) "
            "VALUES (:name, :rules, :user, :n) RETURNING cohort_id"
        ).bindparams(bindparam("rules", type_=JSONB)),
        {"name": name, "rules": rules.model_dump(mode="json"), "user": user_id, "n": len(members)},
    ).scalar_one()
    if members:
        conn.execute(
            text("INSERT INTO app.cohort_member (cohort_id, person_id) VALUES (:c, :p)"),
            [{"c": cohort_id, "p": p} for p in members],
        )
    return str(cohort_id)


COHORT_COLUMNS = "cohort_id::text, name, rules, created_by::text, created_at, member_count"


def get(conn: Connection, cohort_id: str) -> Cohort | None:
    """One cohort of this center, or None (also for ids that are not UUIDs)."""
    row = conn.execute(
        text(f"SELECT {COHORT_COLUMNS} FROM app.cohort WHERE cohort_id::text = :c"),
        {"c": cohort_id.lower()},
    ).one_or_none()
    return None if row is None else Cohort(**row._mapping)


def list_cohorts(conn: Connection, limit: int, offset: int) -> tuple[list[Cohort], int]:
    """Newest first."""
    total = int(conn.execute(text("SELECT count(*) FROM app.cohort")).scalar_one())
    rows = conn.execute(
        text(
            f"SELECT {COHORT_COLUMNS} FROM app.cohort "
            "ORDER BY created_at DESC, cohort_id LIMIT :l OFFSET :o"
        ),
        {"l": limit, "o": offset},
    ).all()
    return [Cohort(**r._mapping) for r in rows], total
