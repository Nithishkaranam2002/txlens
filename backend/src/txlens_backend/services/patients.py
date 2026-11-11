"""Patient list and timeline, identified (clinicians) or de-identified (everyone else).

The identified path is the only code that reads `app.patient_identity`. The de-identified
path never joins it: ids become keyed pseudonyms, dates move by a per-patient shift, birth
years become age bands, and notes show only their date.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Connection, Row, text

from txlens_backend import deid
from txlens_backend.clinical_codes import BIOPSY, DSA_LOCAL, LABS, TRANSPLANT
from txlens_backend.services.facts import FACTS_CTE, FACTS_PARAMS, concept_keys, descendant_ids

Organ = Literal["kidney", "heart"]
DonorType = Literal["living", "deceased"]
Status = Literal["active", "graft_failure", "deceased"]
PSEUDONYM = deid.pseudonym_sql("f.tenant_id", "f.person_id")


class PatientBase(BaseModel):
    """What every role may see about a patient (dates are shifted for de-identified roles)."""

    sex: str | None
    organ: Organ | None
    donor_type: DonorType | None
    status: Status
    transplant_date: date | None
    death_date: date | None


class IdentifiedPatient(PatientBase):
    """Clinician view: real ids, identifiers and dates."""

    patient_id: int
    mrn: str | None
    given_name: str | None
    family_name: str | None
    birth_date: date | None


class DeidentifiedPatient(PatientBase):
    """Researcher / data steward view: pseudonym, age band, shifted dates."""

    patient_id: str
    age_band: str | None


class Lab(BaseModel):
    """One lab result (value in the standard unit)."""

    date: date
    time: datetime | None
    lab: str
    value: float | None
    unit: str | None


class Drug(BaseModel):
    """One drug exposure."""

    start_date: date
    end_date: date | None
    concept_id: int
    drug: str | None


class Condition(BaseModel):
    """One condition."""

    date: date
    concept_id: int
    condition: str | None


class Procedure(BaseModel):
    """A transplant, a biopsy, or another procedure."""

    date: date
    kind: Literal["transplant", "biopsy", "other"]
    concept_id: int
    procedure: str | None


class NoteMeta(BaseModel):
    """A clinical note's metadata; the text is never returned. Id and title: clinicians only."""

    date: date
    note_id: int | None = None
    title: str | None = None


class Timeline(BaseModel):
    """Everything known about one patient, oldest first."""

    patient: IdentifiedPatient | DeidentifiedPatient
    labs: list[Lab]
    drugs: list[Drug]
    conditions: list[Condition]
    procedures: list[Procedure]
    notes: list[NoteMeta]


@dataclass(frozen=True)
class Filters:
    """Patient list filters."""

    q: str | None = None
    organ: Organ | None = None
    donor_type: DonorType | None = None
    status: Status | None = None
    cohort_id: str | None = None  # only members of this saved cohort


@dataclass(frozen=True)
class Viewer:
    """How results must be shaped for the caller."""

    identified: bool
    deid_key: str

    def shift_days(self, tenant_id: str, person_id: int) -> int:
        """0 for clinicians; the patient's shift otherwise."""
        return 0 if self.identified else deid.date_shift_days(self.deid_key, tenant_id, person_id)


FILTERS = (
    "(CAST(:organ AS text) IS NULL OR f.organ = :organ) "
    "AND (CAST(:donor_type AS text) IS NULL OR f.donor_type = :donor_type) "
    "AND (CAST(:status AS text) IS NULL OR f.status = :status) "
    "AND (CAST(:cohort_id AS text) IS NULL OR f.person_id IN "
    "(SELECT person_id FROM app.cohort_member WHERE cohort_id::text = :cohort_id))"
)
IDENTIFIED_SEARCH = (
    "(CAST(:q AS text) IS NULL OR i.mrn = :q OR i.given_name ILIKE :like "
    "OR i.family_name ILIKE :like OR (i.given_name || ' ' || i.family_name) ILIKE :like)"
)


def identified_query(select: str, tail: str = "") -> str:
    """facts + identity, filtered by the list filters and the name/MRN search."""
    return (
        f"WITH {FACTS_CTE} SELECT {select} FROM facts f "
        "LEFT JOIN app.patient_identity i ON i.person_id = f.person_id "
        f"WHERE {FILTERS} AND {IDENTIFIED_SEARCH} {tail}"
    )


def deidentified_query(select: str, tail: str = "") -> str:
    """facts + pseudonym (no identity table), filtered; search matches the pseudonym only."""
    return (
        f"WITH {FACTS_CTE}, pseudo AS (SELECT f.*, {PSEUDONYM} AS pseudonym FROM facts f) "
        f"SELECT {select} FROM pseudo f "
        f"WHERE {FILTERS} AND (CAST(:q AS text) IS NULL OR f.pseudonym = :q) {tail}"
    )


def query_params(viewer: Viewer, filters: Filters) -> dict[str, object]:
    """Bind parameters for the facts CTE, the filters and the pseudonym key."""
    q = filters.q.strip() if filters.q else None
    return dict(FACTS_PARAMS) | {
        "organ": filters.organ, "donor_type": filters.donor_type, "status": filters.status,
        "cohort_id": filters.cohort_id, "q": q, "like": f"%{q or ''}%",
        "deid_key": viewer.deid_key,
    }  # fmt: skip


def list_patients(
    conn: Connection, viewer: Viewer, filters: Filters, limit: int, offset: int
) -> tuple[list[IdentifiedPatient] | list[DeidentifiedPatient], int]:
    """One page of patients and the total matching the filters."""
    params = query_params(viewer, filters) | {"limit": limit, "offset": offset}
    build = identified_query if viewer.identified else deidentified_query
    total = int(conn.execute(text(build("count(*)")), params).scalar_one())
    if viewer.identified:
        rows = conn.execute(
            text(build("f.*, i.mrn, i.given_name, i.family_name, i.birth_date",
                       "ORDER BY f.person_id LIMIT :limit OFFSET :offset")),
            params,
        ).all()  # fmt: skip
        return [identified(r) for r in rows], total
    rows = conn.execute(
        text(build("f.*", "ORDER BY f.pseudonym LIMIT :limit OFFSET :offset")), params
    ).all()
    return [deidentified(r, viewer) for r in rows], total


def identified(row: Row[tuple[object, ...]]) -> IdentifiedPatient:
    """Clinician view of a facts + identity row."""
    m = row._mapping
    return IdentifiedPatient(
        patient_id=m["person_id"], mrn=m["mrn"], given_name=m["given_name"],
        family_name=m["family_name"], birth_date=m["birth_date"], sex=m["sex"], organ=m["organ"],
        donor_type=m["donor_type"], status=m["status"], transplant_date=m["transplant_date"],
        death_date=m["death_date"],
    )  # fmt: skip


def deidentified(row: Row[tuple[object, ...]], viewer: Viewer) -> DeidentifiedPatient:
    """De-identified view of a facts + pseudonym row."""
    m = row._mapping
    days = viewer.shift_days(m["tenant_id"], m["person_id"])
    reference = m["transplant_date"] or date.today()
    return DeidentifiedPatient(
        patient_id=m["pseudonym"], age_band=deid.age_band(m["year_of_birth"], reference),
        sex=m["sex"], organ=m["organ"], donor_type=m["donor_type"], status=m["status"],
        transplant_date=deid.shift(m["transplant_date"], days),
        death_date=deid.shift(m["death_date"], days),
    )  # fmt: skip


def find_person(conn: Connection, viewer: Viewer, patient_id: str) -> int | None:
    """The person_id behind a path id (person_id or pseudonym), or None if not in this tenant."""
    params = query_params(viewer, Filters())
    if viewer.identified:
        if not patient_id.isdigit():
            return None
        found = conn.execute(
            text("SELECT person_id FROM cdm.person WHERE person_id = :p"), {"p": int(patient_id)}
        ).scalar_one_or_none()
    else:
        found = conn.execute(
            text(deidentified_query("f.person_id", "AND f.pseudonym = :pseudonym")),
            params | {"pseudonym": patient_id},
        ).scalar_one_or_none()
    return None if found is None else int(found)


def patient_row(conn: Connection, viewer: Viewer, person_id: int) -> Row[tuple[object, ...]]:
    """The facts row (with identity or pseudonym) of one person."""
    params = query_params(viewer, Filters()) | {"p": person_id}
    if viewer.identified:
        query = identified_query(
            "f.*, i.mrn, i.given_name, i.family_name, i.birth_date", "AND f.person_id = :p"
        )
    else:
        query = deidentified_query("f.*", "AND f.person_id = :p")
    return conn.execute(text(query), params).one()


def timeline(conn: Connection, viewer: Viewer, person_id: int) -> Timeline:
    """All events of one patient, shaped for the viewer."""
    row = patient_row(conn, viewer, person_id)
    m = row._mapping
    days = viewer.shift_days(m["tenant_id"], person_id)
    move = timedelta(days=days)
    patient: IdentifiedPatient | DeidentifiedPatient = (
        identified(row) if viewer.identified else deidentified(row, viewer)
    )
    return Timeline(
        patient=patient,
        labs=labs(conn, person_id, move),
        drugs=[
            Drug(
                start_date=r.drug_exposure_start_date + move,
                end_date=r.drug_exposure_end_date + move if r.drug_exposure_end_date else None,
                concept_id=r.drug_concept_id,
                drug=r.concept_name or r.drug_source_value,
            )
            for r in events(conn, "drug_exposure", "drug", "drug_exposure_start_date", person_id)
        ],
        conditions=[
            Condition(
                date=r.condition_start_date + move,
                concept_id=r.condition_concept_id,
                condition=r.concept_name or r.condition_source_value,
            )
            for r in events(
                conn, "condition_occurrence", "condition", "condition_start_date", person_id
            )
        ],
        procedures=procedures(conn, person_id, move),
        notes=notes(conn, viewer, person_id, move),
    )


def events(
    conn: Connection, table: str, domain: str, date_column: str, person_id: int
) -> list[Row[tuple[object, ...]]]:
    """Rows of one clinical table for a person, with the concept name, oldest first."""
    return list(
        conn.execute(
            text(
                f"SELECT e.*, c.concept_name FROM cdm.{table} e "
                f"LEFT JOIN vocab.concept c ON c.concept_id = e.{domain}_concept_id "
                f"WHERE e.person_id = :p ORDER BY e.{date_column}, e.{table}_id"
            ),
            {"p": person_id},
        ).all()
    )


def labs(conn: Connection, person_id: int, move: timedelta) -> list[Lab]:
    """Lab results named by our lab keys (dd_cfdna, creatinine, ...), else the source name."""
    keys = concept_keys(conn, LABS)
    dsa = concept_keys(conn, {"dsa_mfi": DSA_LOCAL})
    rows = conn.execute(
        text(
            "SELECT measurement_date, measurement_datetime, measurement_concept_id, "
            "measurement_source_concept_id, measurement_source_value, value_as_number, "
            "unit_source_value FROM cdm.measurement WHERE person_id = :p "
            "ORDER BY measurement_date, measurement_datetime, measurement_id"
        ),
        {"p": person_id},
    ).all()
    return [
        Lab(date=r.measurement_date + move,
            time=r.measurement_datetime + move if r.measurement_datetime else None,
            lab=keys.get(r.measurement_concept_id)
            or dsa.get(r.measurement_source_concept_id or 0)
            or r.measurement_source_value or "unknown",
            value=float(r.value_as_number) if r.value_as_number is not None else None,
            unit=r.unit_source_value)
        for r in rows
    ]  # fmt: skip


def procedures(conn: Connection, person_id: int, move: timedelta) -> list[Procedure]:
    """Procedures, each classified as transplant, biopsy or other."""
    biopsies = set(concept_keys(conn, BIOPSY))
    transplants = descendant_ids(conn, list(concept_keys(conn, TRANSPLANT)))
    rows = events(conn, "procedure_occurrence", "procedure", "procedure_date", person_id)

    def kind(concept_id: int) -> Literal["transplant", "biopsy", "other"]:
        if concept_id in biopsies:
            return "biopsy"
        return "transplant" if concept_id in transplants else "other"

    return [
        Procedure(date=r.procedure_date + move, kind=kind(r.procedure_concept_id),
                  concept_id=r.procedure_concept_id,
                  procedure=r.concept_name or r.procedure_source_value)
        for r in rows
    ]  # fmt: skip


def notes(conn: Connection, viewer: Viewer, person_id: int, move: timedelta) -> list[NoteMeta]:
    """Note dates; ids and titles for clinicians only. The text is never selected."""
    rows = conn.execute(
        text(
            "SELECT note_id, note_date, note_title FROM cdm.note "
            "WHERE person_id = :p ORDER BY note_date, note_id"
        ),
        {"p": person_id},
    ).all()
    if viewer.identified:
        return [NoteMeta(date=r.note_date, note_id=r.note_id, title=r.note_title) for r in rows]
    return [NoteMeta(date=r.note_date + move) for r in rows]
