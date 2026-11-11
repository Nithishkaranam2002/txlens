"""Reading de-identified notes for NLP: text, the patient's transplanted organ, PHI spans."""

from dataclasses import dataclass

from sqlalchemy import Connection, text

from txlens_pipelines.deid_notes import Replacement

KIDNEY_TRANSPLANT = 4322471  # SNOMED 70536003 and its descendants (donor-specific)
HEART_TRANSPLANT = 4137127  # SNOMED 32413006


@dataclass(frozen=True)
class NoteRow:
    """One de-identified note and what the NLP needs to know about it."""

    note_id: int
    source_value: str
    person_id: int
    note_date: object
    visit_occurrence_id: int | None
    text: str
    organ: str | None


NOTES_SQL = text(
    """
    SELECT n.note_id, n.note_source_value, n.person_id, n.note_date, n.visit_occurrence_id,
           n.note_text,
           (SELECT CASE a.ancestor_concept_id WHEN :kidney THEN 'kidney' ELSE 'heart' END
              FROM cdm.procedure_occurrence p
              JOIN vocab.concept_ancestor a ON a.descendant_concept_id = p.procedure_concept_id
             WHERE p.person_id = n.person_id
               AND a.ancestor_concept_id IN (:kidney, :heart)
             ORDER BY p.procedure_date LIMIT 1) AS organ
    FROM cdm.note n
    WHERE n.note_source_value = ANY(:sources)
    ORDER BY n.note_source_value
    """
)
SPANS_SQL = text(
    "SELECT note_id, deid_start, deid_end, orig_start, orig_end, kind, original "
    "FROM app.note_phi_span WHERE note_id = ANY(:ids) ORDER BY note_id, span_no"
)


def read_notes(conn: Connection, source_values: list[str]) -> list[NoteRow]:
    """Notes of the current tenant by note_source_value (RLS keeps other tenants out)."""
    rows = conn.execute(
        NOTES_SQL,
        {"sources": source_values, "kidney": KIDNEY_TRANSPLANT, "heart": HEART_TRANSPLANT},
    )
    return [NoteRow(int(r[0]), r[1], int(r[2]), r[3], r[4], r[5], r[6]) for r in rows]


def phi_spans(conn: Connection, note_ids: list[int]) -> dict[int, list[Replacement]]:
    """The replacement list of each note (clinician path and evaluation only)."""
    spans: dict[int, list[Replacement]] = {}
    for note_id, *values in conn.execute(SPANS_SQL, {"ids": note_ids}):
        spans.setdefault(int(note_id), []).append(Replacement(*values))
    return spans
