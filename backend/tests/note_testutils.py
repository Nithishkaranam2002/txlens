"""Seed one condition read from clinic notes (shared by the API tests and the e2e seed)."""

from dataclasses import dataclass

from sqlalchemy import Engine, text

from txlens_backend.db import tenant_transaction
from txlens_backend.notes.nlp import Mention
from txlens_backend.notes.results import save_note

SURROGATE = "Hollis"
DEID_TEXT = f"VISIT NOTE\n\nHPI: {SURROGATE} reports graft rejection today.\n"
CLEANUP = (
    "DELETE FROM cdm.condition_occurrence WHERE condition_occurrence_id IN "
    "(SELECT cdm_id FROM app.note_cdm_row WHERE note_source_value = ANY(:s))",
    "DELETE FROM cdm.observation WHERE observation_id IN "
    "(SELECT cdm_id FROM app.note_cdm_row WHERE note_source_value = ANY(:s))",
    "DELETE FROM cdm.note_nlp WHERE note_nlp_id IN "
    "(SELECT mention_id FROM app.note_mention WHERE note_source_value = ANY(:s))",
    "DELETE FROM app.note_processed WHERE note_source_value = ANY(:s)",
    "DELETE FROM app.note_phi_span WHERE note_id IN "
    "(SELECT note_id FROM cdm.note WHERE note_source_value = ANY(:s))",
)


@dataclass(frozen=True)
class NoteCondition:
    """One concept read from two notes of one patient, and what to restore afterwards."""

    person_id: int
    given_name: str
    concept_id: int
    concept_name: str
    note_ids: tuple[int, int]
    sources: tuple[str, str]
    original_texts: tuple[str, str]


def rejection_mention(note_text: str, concept_id: int) -> Mention:
    start = note_text.index("rejection")
    sentence = note_text.index("HPI:")
    return Mention(start, start + 9, "rejection", "condition", concept_id, False, "current",
                   "patient", "history_of_present_illness", sentence, note_text.index("\n", start),
                   "curated", 1.0)  # fmt: skip


def seed_note_condition(owner: Engine, app: Engine, tenant: str = "center_a") -> NoteCondition:
    """Two notes of one patient now say "<surrogate> reports graft rejection today": the
    surrogate stands for the patient's given name (app.note_phi_span), and the NLP results
    are saved through the real code path."""
    with owner.begin() as conn:
        person_id, given = conn.execute(text(
            "SELECT i.person_id, i.given_name FROM app.patient_identity i WHERE i.tenant_id = :t "
            "AND (SELECT count(*) FROM cdm.note n WHERE n.person_id = i.person_id) >= 2 "
            "ORDER BY i.person_id DESC LIMIT 1"), {"t": tenant}).one()  # fmt: skip
        notes = conn.execute(text(
            "SELECT note_id, note_source_value, note_text FROM cdm.note WHERE person_id = :p "
            "ORDER BY note_date, note_id LIMIT 2"), {"p": person_id}).all()  # fmt: skip
        concept_id, concept_name = conn.execute(text(
            "SELECT concept_id, concept_name FROM vocab.concept WHERE domain_id = 'Condition' "
            "AND standard_concept = 'S' AND concept_id NOT IN (SELECT condition_concept_id "
            "FROM cdm.condition_occurrence WHERE person_id = :p) ORDER BY concept_id LIMIT 1"),
            {"p": person_id}).one()  # fmt: skip
        start = DEID_TEXT.index(SURROGATE)
        for note_id, _, _ in notes:
            conn.execute(text("UPDATE cdm.note SET note_text = :t WHERE note_id = :n"),
                         {"t": DEID_TEXT, "n": note_id})  # fmt: skip
            conn.execute(text(
                "INSERT INTO app.note_phi_span (tenant_id, note_id, span_no, deid_start, "
                "deid_end, orig_start, orig_end, kind, original) VALUES (:t, :n, 0, :s, :e, :s, "
                ":o, 'name', :g)"),
                {"t": tenant, "n": note_id, "s": start, "e": start + len(SURROGATE),
                 "o": start + len(given), "g": given})  # fmt: skip
    with tenant_transaction(app, tenant) as conn:
        for _, source, _ in notes:
            save_note(conn, source, DEID_TEXT, [rejection_mention(DEID_TEXT, concept_id)],
                      "txlens-test", 0.70)  # fmt: skip
    return NoteCondition(
        person_id, given, concept_id, concept_name, (notes[0][0], notes[1][0]),
        (notes[0][1], notes[1][1]), (notes[0][2], notes[1][2]),
    )  # fmt: skip


def remove_note_condition(owner: Engine, seeded: NoteCondition) -> None:
    """Undo `seed_note_condition`."""
    with owner.begin() as conn:
        for statement in CLEANUP:
            conn.execute(text(statement), {"s": list(seeded.sources)})
        for note_id, original in zip(seeded.note_ids, seeded.original_texts, strict=True):
            conn.execute(text("UPDATE cdm.note SET note_text = :t WHERE note_id = :n"),
                         {"t": original, "n": note_id})  # fmt: skip
