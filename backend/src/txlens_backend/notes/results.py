"""Saving one note's NLP mentions and the OMOP rows they produce, then loading them into cdm.

Every mention goes to `app.note_mention` (and from there to cdm.note_nlp). A mention needs a
human look ("review") when it has no concept, came from an ambiguous term, or its confidence is
below the review threshold (LLM findings always are). A note gives one condition_occurrence per
concept, only from accepted condition mentions that are affirmed, current and about the patient,
with a standard concept. TxLens local concepts (borderline rejection) are non-standard: OMOP
keeps them out of `*_concept_id`, so they stay in note_nlp as source concepts.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, text

from txlens_backend.notes.nlp import Mention

STANDARD_SQL = text(
    "SELECT concept_id FROM vocab.concept WHERE concept_id = ANY(:ids) "
    "AND standard_concept = 'S' AND invalid_reason IS NULL"
)
PROCESSED_SQL = text(
    "SELECT note_source_value, text_sha256, nlp_system FROM app.note_processed "
    "WHERE note_source_value = ANY(:sources)"
)
# The note's cdm copies go first: once the app rows are gone, nothing points at them.
UNLOAD_SQL = (
    text(
        "DELETE FROM cdm.condition_occurrence WHERE condition_occurrence_id IN "
        "(SELECT cdm_id FROM app.note_cdm_row WHERE note_source_value = :n)"
    ),
    text(
        "DELETE FROM cdm.note_nlp WHERE note_nlp_id IN "
        "(SELECT mention_id FROM app.note_mention WHERE note_source_value = :n)"
    ),
    text("DELETE FROM app.note_processed WHERE note_source_value = :n"),  # cascades
)
SAVE_NOTE_SQL = text(
    "INSERT INTO app.note_processed (note_source_value, text_sha256, nlp_system, mentions) "
    "VALUES (:note, :sha, :system, :mentions)"
)
SAVE_MENTION_SQL = text(
    """
    INSERT INTO app.note_mention (
        note_source_value, start_offset, end_offset, lexical_variant, sentence_start,
        sentence_end, snippet, section, category, concept_id, source_concept_id, negated,
        temporality, experiencer, method, confidence, status)
    VALUES (:note, :start, :end, :lexical, :s_start, :s_end, :snippet, :section, :category,
            :concept, :source_concept, :negated, :temporality, :experiencer, :method,
            :confidence, :status)
    RETURNING mention_id
    """
)
SAVE_CDM_SQL = text(
    "INSERT INTO app.note_cdm_row (note_source_value, mention_id, cdm_table, concept_id, "
    "mentions) VALUES (:note, :mention, 'condition_occurrence', :concept, :mentions)"
)


@dataclass(frozen=True)
class Saved:
    """What one note produced."""

    mentions: int
    review: int
    conditions: int


def text_sha256(note_text: str) -> str:
    """The hash that tells whether a note changed since it was processed."""
    return hashlib.sha256(note_text.encode()).hexdigest()


def needs_review(mention: Mention, threshold: float) -> bool:
    """No concept, an ambiguous term, or a confidence below the threshold."""
    return (
        mention.concept_id == 0 or mention.method == "ambiguous" or (mention.confidence < threshold)
    )


def is_patient_condition(mention: Mention, standard: set[int], threshold: float) -> bool:
    """An accepted, affirmed, current condition of the patient with a standard concept."""
    return (
        mention.category == "condition"
        and not needs_review(mention, threshold)
        and not mention.negated
        and mention.temporality == "current"
        and mention.experiencer == "patient"
        and mention.concept_id in standard
    )


def standard_ids(conn: Connection, mentions: Sequence[Mention]) -> set[int]:
    """The mentions' concepts that are standard (the rest go in source_concept_id)."""
    ids = sorted({m.concept_id for m in mentions if m.concept_id})
    return {int(r[0]) for r in conn.execute(STANDARD_SQL, {"ids": ids})} if ids else set()


def unchanged(conn: Connection, notes: dict[str, str], system: str) -> set[str]:
    """Notes (source value -> text) already processed with this text and NLP system."""
    rows = conn.execute(PROCESSED_SQL, {"sources": list(notes)})
    return {r[0] for r in rows if r[1] == text_sha256(notes[r[0]]) and r[2] == system}


def save_note(
    conn: Connection,
    note_source_value: str,
    note_text: str,
    mentions: Sequence[Mention],
    system: str,
    threshold: float,
) -> Saved:
    """Replace the note's stored results and copy them into cdm."""
    for statement in UNLOAD_SQL:
        conn.execute(statement, {"n": note_source_value})
    conn.execute(
        SAVE_NOTE_SQL,
        {"note": note_source_value, "sha": text_sha256(note_text), "system": system,
         "mentions": len(mentions)},
    )  # fmt: skip
    standard = standard_ids(conn, mentions)
    first: dict[int, tuple[int, int]] = {}  # concept -> (first mention id, mentions)
    review = 0
    for m in mentions:
        flagged = needs_review(m, threshold)
        review += flagged
        mention_id = conn.execute(
            SAVE_MENTION_SQL,
            {"note": note_source_value, "start": m.start, "end": m.end, "lexical": m.text,
             "s_start": m.sentence_start, "s_end": m.sentence_end,
             "snippet": note_text[m.sentence_start : m.sentence_end].strip() or m.text,
             "section": m.section, "category": m.category,
             "concept": m.concept_id if m.concept_id in standard else 0,
             "source_concept": 0 if m.concept_id in standard else m.concept_id,
             "negated": m.negated, "temporality": m.temporality, "experiencer": m.experiencer,
             "method": m.method, "confidence": round(m.confidence, 3),
             "status": "review" if flagged else "accepted"},
        ).scalar_one()  # fmt: skip
        if is_patient_condition(m, standard, threshold):
            mention, count = first.get(m.concept_id, (int(mention_id), 0))
            first[m.concept_id] = (mention, count + 1)
    for concept, (mention, count) in first.items():
        conn.execute(
            SAVE_CDM_SQL,
            {"note": note_source_value, "mention": mention, "concept": concept, "mentions": count},
        )
    conn.execute(text("SELECT app.load_note_nlp_results(:n)"), {"n": note_source_value})
    return Saved(len(mentions), review, len(first))
