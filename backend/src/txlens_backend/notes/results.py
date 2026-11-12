"""Saving one note's NLP mentions and the OMOP rows they produce, then loading them into cdm.

Every mention goes to `app.note_mention` (and from there to cdm.note_nlp). A mention needs a
human look ("review") when it has no concept, came from an ambiguous term, its confidence is
below the review threshold (LLM findings always are), or a rule gave a review reason. A
reviewer's decision (app.review_decision, keyed by note and span) is re-applied whenever the
note is processed again, so re-running the NLP never undoes a person's work. A note gives one
condition_occurrence per concept, only from accepted condition mentions that are affirmed,
current and about the patient, with a standard concept. TxLens local concepts (borderline
rejection) are non-standard: OMOP keeps them out of `*_concept_id`, so they stay in note_nlp
as source concepts.
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
        temporality, experiencer, method, confidence, status, review_reason)
    VALUES (:note, :start, :end, :lexical, :s_start, :s_end, :snippet, :section, :category,
            :concept, :source_concept, :negated, :temporality, :experiencer, :method,
            :confidence, :status, :review_reason)
    RETURNING mention_id
    """
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
    """No concept, an ambiguous term, a confidence below the threshold, or a rule's review
    reason (a rejection called current in a sentence that looks like a past episode)."""
    return (
        mention.concept_id == 0
        or mention.method == "ambiguous"
        or mention.confidence < threshold
        or mention.review_reason is not None
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
    """Replace the note's stored results, re-apply earlier review decisions on the same spans,
    and copy them into cdm."""
    for statement in UNLOAD_SQL:
        conn.execute(statement, {"n": note_source_value})
    conn.execute(
        SAVE_NOTE_SQL,
        {"note": note_source_value, "sha": text_sha256(note_text), "system": system,
         "mentions": len(mentions)},
    )  # fmt: skip
    standard = standard_ids(conn, mentions)
    for m in mentions:
        conn.execute(
            SAVE_MENTION_SQL,
            {"note": note_source_value, "start": m.start, "end": m.end, "lexical": m.text,
             "s_start": m.sentence_start, "s_end": m.sentence_end,
             "snippet": note_text[m.sentence_start : m.sentence_end].strip() or m.text,
             "section": m.section, "category": m.category,
             "concept": m.concept_id if m.concept_id in standard else 0,
             "source_concept": 0 if m.concept_id in standard else m.concept_id,
             "negated": m.negated, "temporality": m.temporality, "experiencer": m.experiencer,
             "method": m.method, "confidence": round(m.confidence, 3),
             "status": "review" if needs_review(m, threshold) else "accepted",
             "review_reason": m.review_reason},
        )  # fmt: skip
    reapply_reviews(conn, note_source_value)
    review = conn.execute(REVIEW_COUNT_SQL, {"n": note_source_value}).scalar_one()
    return Saved(len(mentions), int(review), rebuild_conditions(conn, note_source_value))


# Accepted condition mentions that are affirmed, current, about the patient and have a
# standard concept: one condition_occurrence per note and concept, from its first mention.
CONDITIONS_SQL = text(
    """
    INSERT INTO app.note_cdm_row (note_source_value, mention_id, cdm_table, concept_id, mentions)
    SELECT :n, min(mention_id), 'condition_occurrence', concept_id, count(*)
    FROM app.note_mention
    WHERE note_source_value = :n AND status = 'accepted' AND category = 'condition'
      AND NOT negated AND temporality = 'current' AND experiencer = 'patient' AND concept_id <> 0
    GROUP BY concept_id
    """
)
REVIEW_COUNT_SQL = text(
    "SELECT count(*) FROM app.note_mention WHERE note_source_value = :n AND status = 'review'"
)


def rebuild_conditions(conn: Connection, note_source_value: str) -> int:
    """Recompute the note's conditions from its accepted mentions and copy them into cdm;
    returns how many conditions it gives."""
    conn.execute(UNLOAD_SQL[0], {"n": note_source_value})
    conn.execute(
        text("DELETE FROM app.note_cdm_row WHERE note_source_value = :n"), {"n": note_source_value}
    )
    conditions = conn.execute(CONDITIONS_SQL, {"n": note_source_value}).rowcount
    conn.execute(text("SELECT app.load_note_nlp_results(:n)"), {"n": note_source_value})
    return int(conditions)


# The latest reviewer decision on each span of the note (keys are "<note>:<start>-<end>").
DECISIONS_SQL = text(
    """
    SELECT DISTINCT ON (item_key) item_key, action, changes, reviewer_id, decided_at
    FROM app.review_decision
    WHERE item_type = 'note_mention' AND starts_with(item_key, :n || ':')
    ORDER BY item_key, decided_at DESC
    """
)
APPLY_SQL = text(
    """
    UPDATE app.note_mention SET
        status = :status, reviewed_by = :reviewer, reviewed_at = :at,
        concept_id = coalesce(:concept, concept_id),
        source_concept_id = coalesce(:source_concept, source_concept_id),
        negated = coalesce(:negated, negated),
        temporality = coalesce(:temporality, temporality),
        experiencer = coalesce(:experiencer, experiencer)
    WHERE note_source_value = :n AND start_offset = :start AND end_offset = :end
    """
)


def concept_columns(conn: Connection, concept_id: int) -> tuple[int, int]:
    """(concept_id, source_concept_id) columns for a reviewer's concept: standard concepts go
    in concept_id, others (TxLens local concepts) in source_concept_id."""
    standard = conn.execute(STANDARD_SQL, {"ids": [concept_id]}).first() is not None
    return (concept_id, 0) if standard else (0, concept_id)


def apply_review(
    conn: Connection, key: str, action: str, final: dict[str, str], reviewer_id: str,
    decided_at: object,
) -> int:  # fmt: skip
    """Set one mention's reviewed status and values; returns rows changed (0 if the span is
    no longer found, e.g. the note text changed)."""
    note, span = key.rsplit(":", 1)
    start, end = (int(v) for v in span.split("-"))
    concept = source_concept = None
    if action == "edit" and "concept_id" in final:
        concept, source_concept = concept_columns(conn, int(final["concept_id"]))
    edit = action == "edit"
    return int(
        conn.execute(
            APPLY_SQL,
            {"status": "rejected" if action == "reject" else "accepted", "reviewer": reviewer_id,
             "at": decided_at, "concept": concept, "source_concept": source_concept,
             "negated": final["negated"] == "true" if edit and "negated" in final else None,
             "temporality": final.get("temporality") if edit else None,
             "experiencer": final.get("experiencer") if edit else None,
             "n": note, "start": start, "end": end},
        ).rowcount
    )  # fmt: skip


def reapply_reviews(conn: Connection, note_source_value: str) -> None:
    """Re-running the NLP must not undo a person's decision: apply each span's latest one."""
    for d in conn.execute(DECISIONS_SQL, {"n": note_source_value}).all():
        apply_review(conn, d.item_key, d.action, (d.changes or {}).get("final", {}),
                     str(d.reviewer_id), d.decided_at)  # fmt: skip
