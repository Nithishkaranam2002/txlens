"""One queue item with its form fields and its evidence, shaped for the viewer's role.

- Biopsy document: every extracted field (model value and current value) and the document's
  evidence: page images and boxes for clinicians; for data stewards, redacted lines and
  cropped line images of fields outside the report header (crop.py).
- Note mention: the concept and its attributes, and the sentence with the mention's span
  marked: the original sentence for clinicians, the de-identified one for data stewards.
- Condition mapping gap / curated mapping row: the source text, the current or suggested
  concept and, for curated rows, every earlier review (who, which center, when, why).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from sqlalchemy import Connection, text

from txlens_backend.notes.store import phi_spans
from txlens_backend.review import crop, curated
from txlens_backend.review.queue import (
    PatientRef,
    ReviewItem,
    curated_item,
    decided,
    item_of,
    pending,
)
from txlens_backend.review.reasons import ACTIONS, REASON_CODES, Action, ItemType, ReasonCode
from txlens_backend.services import biopsies
from txlens_pipelines.deid_notes import reidentify

TEMPORALITIES = ["current", "historical", "hypothetical"]
EXPERIENCERS = ["patient", "family"]
BOOLEANS = ["true", "false"]


class ReviewField(BaseModel):
    """A form field: what the model said, what it is now, and the allowed values if closed."""

    name: str
    label: str
    model_value: str | None
    value: str | None
    confidence: float | None = None
    choices: list[str] | None = None
    concept: bool = False  # the value is a concept id (pick with the concept search)


class DocumentPane(BaseModel):
    kind: Literal["document"] = "document"
    evidence: biopsies.DocumentEvidence
    crop_urls: dict[str, str]  # data stewards: field -> redacted line image


class SentencePane(BaseModel):
    kind: Literal["sentence"] = "sentence"
    view: Literal["identified", "deidentified"]
    sentence: str
    highlight_start: int
    highlight_end: int
    section: str | None
    note_date: str | None


class ConceptRef(BaseModel):
    concept_id: int
    name: str | None


class HistoryEntry(BaseModel):
    """One earlier review of a curated row. Reviewer shown by role and center, never name."""

    action: Literal["approve", "correct", "revert"]
    old_concept: ConceptRef
    new_concept: ConceptRef
    reviewer_center: str
    reason_code: str | None
    note: str | None
    decided_at: datetime


class MappingPane(BaseModel):
    kind: Literal["mapping"] = "mapping"
    source_value: str
    source_vocabulary_id: str | None
    description: str | None
    concept: ConceptRef | None
    original_concept: ConceptRef | None  # curated rows: the AI assistant's concept
    suggestion_score: float | None
    reason: str | None
    rows: int | None
    label: str | None  # curated rows: "AI-curated, pending" or "clinician-reviewed"
    history: list[HistoryEntry]


Pane = Annotated[DocumentPane | SentencePane | MappingPane, Field(discriminator="kind")]


class ReviewDetail(BaseModel):
    item: ReviewItem
    fields: list[ReviewField]
    evidence: Pane
    actions: list[Action]
    reason_codes: list[ReasonCode]


@dataclass(frozen=True)
class Viewer:
    """Who is looking: identified (clinician) or not, the patient reference and date shift."""

    identified: bool
    patient: PatientRef
    shift: Callable[[int | None], timedelta]
    can_review_curated: bool


class NotFoundError(LookupError):
    """No such item in the caller's center."""


Builder = Callable[
    [Connection, str, Viewer], tuple[list[ReviewField], DocumentPane | SentencePane | MappingPane]
]


def concept(conn: Connection, concept_id: int | None) -> ConceptRef | None:
    if concept_id is None:
        return None
    name = conn.execute(
        text("SELECT concept_name FROM vocab.concept WHERE concept_id = :c"), {"c": concept_id}
    ).scalar_one_or_none()
    return ConceptRef(concept_id=concept_id, name=name)


def find_item(conn: Connection, item_type: ItemType, key: str, viewer: Viewer) -> ReviewItem:
    """The pending item, or its latest decision; NotFoundError if neither exists. Curated
    rows are shared by every center, so they are found whatever their state."""
    if item_type == "curated_mapping":
        found = curated_item(conn, key)
        if found is None:
            raise NotFoundError(key)
        return found
    for p in pending(conn, [item_type]):
        if p.key == key:
            return item_of(p, viewer.patient)
    for done in decided(conn, [item_type]):
        if done.item_key == key:
            return done
    raise NotFoundError(key)


def actions_for(item_type: ItemType, viewer: Viewer) -> list[Action]:
    if item_type == "curated_mapping" and not viewer.can_review_curated:
        return []
    return list(ACTIONS[item_type])


def detail(conn: Connection, item_type: ItemType, key: str, viewer: Viewer) -> ReviewDetail:
    """The item, its fields and its evidence."""
    item = find_item(conn, item_type, key, viewer)
    builders: dict[ItemType, Builder] = {
        "biopsy_document": document_detail, "note_mention": mention_detail,
        "condition_mapping": gap_detail, "curated_mapping": curated_detail,
    }  # fmt: skip
    fields, pane = builders[item_type](conn, key, viewer)
    return ReviewDetail(item=item, fields=fields, evidence=pane,
                        actions=actions_for(item_type, viewer),
                        reason_codes=list(REASON_CODES))  # fmt: skip


def document_detail(
    conn: Connection, key: str, viewer: Viewer
) -> tuple[list[ReviewField], DocumentPane]:
    row = biopsies.document_row(conn, key)
    if row is None:
        raise NotFoundError(key)
    evidence = biopsies.evidence(conn, row, not viewer.identified, viewer.shift(row.person_id))
    fields = [
        ReviewField(name=f.field, label=f.field.replace("_", " "), model_value=f.extracted_value,
                    value=f.value, confidence=f.confidence)
        for f in evidence.fields
    ]  # fmt: skip
    crops = {} if viewer.identified else crop.crop_urls(conn, row)
    return fields, DocumentPane(evidence=evidence, crop_urls=crops)


MENTION_SQL = text(
    """
    SELECT m.mention_id, m.start_offset, m.end_offset, m.sentence_start, m.sentence_end,
           m.section, m.concept_id, m.source_concept_id, m.negated, m.temporality,
           m.experiencer, m.confidence, n.note_id, n.note_date, n.person_id,
           substr(n.note_text, m.sentence_start + 1, m.sentence_end - m.sentence_start)
             AS sentence
    FROM app.note_mention m
    JOIN cdm.note n ON n.note_source_value = m.note_source_value
    WHERE m.note_source_value = :note AND m.start_offset = :start AND m.end_offset = :end
    """
)
# What the model said: the first decision on a mention keeps the values before any edit.
MODEL_VALUES_SQL = text(
    "SELECT changes -> 'model' FROM app.review_decision WHERE item_type = 'note_mention' "
    "AND item_key = :key ORDER BY decided_at LIMIT 1"
)


def mention_key(key: str) -> tuple[str, int, int]:
    """'N-000001-003:296-312' -> ('N-000001-003', 296, 312)."""
    try:
        note, span = key.rsplit(":", 1)
        start, end = (int(v) for v in span.split("-"))
    except ValueError as error:
        raise NotFoundError(key) from error
    return note, start, end


def mention_row(conn: Connection, key: str):  # type: ignore[no-untyped-def]
    note, start, end = mention_key(key)
    row = conn.execute(MENTION_SQL, {"note": note, "start": start, "end": end}).first()
    if row is None:
        raise NotFoundError(key)
    return row


def mention_values(row) -> dict[str, str | None]:  # type: ignore[no-untyped-def]
    """A mention's reviewable values as strings."""
    return {
        "concept_id": str(row.concept_id or row.source_concept_id),
        "negated": "true" if row.negated else "false",
        "temporality": row.temporality,
        "experiencer": row.experiencer,
    }


def mention_detail(
    conn: Connection, key: str, viewer: Viewer
) -> tuple[list[ReviewField], SentencePane]:
    row = mention_row(conn, key)
    now = mention_values(row)
    model = conn.execute(MODEL_VALUES_SQL, {"key": key}).scalar_one_or_none() or now
    choices = {"negated": BOOLEANS, "temporality": TEMPORALITIES, "experiencer": EXPERIENCERS}
    fields = [
        ReviewField(name=name, label=name.replace("_", " "), model_value=model.get(name),
                    value=value, confidence=float(row.confidence), choices=choices.get(name),
                    concept=name == "concept_id")
        for name, value in now.items()
    ]  # fmt: skip
    sentence: str = row.sentence or ""
    before = row.start_offset - row.sentence_start
    mention_len = row.end_offset - row.start_offset
    if viewer.identified:
        spans = phi_spans(conn, [row.note_id]).get(row.note_id, [])
        prefix = reidentify(sentence[:before], spans, row.sentence_start)
        span = reidentify(sentence[before : before + mention_len], spans, row.start_offset)
        rest = reidentify(sentence[before + mention_len :], spans, row.end_offset)
        sentence, start, end = prefix + span + rest, len(prefix), len(prefix) + len(span)
        note_date = row.note_date.isoformat()
    else:
        start, end = before, before + mention_len
        note_date = (row.note_date + viewer.shift(row.person_id)).isoformat()
    return fields, SentencePane(view="identified" if viewer.identified else "deidentified",
                                sentence=sentence, highlight_start=start, highlight_end=end,
                                section=row.section, note_date=note_date)  # fmt: skip


def gap_detail(conn: Connection, key: str, viewer: Viewer) -> tuple[list[ReviewField], MappingPane]:
    row = conn.execute(
        text("SELECT source_value, reason, rows, suggested_concept_id, suggestion_score, "
             "review_reason FROM app.mapping_gap WHERE source_value = :k"), {"k": key},
    ).first()  # fmt: skip
    if row is None:
        raise NotFoundError(key)
    suggested = concept(conn, row.suggested_concept_id)
    value = str(row.suggested_concept_id) if row.suggested_concept_id else None
    fields = [ReviewField(name="concept_id", label="concept", model_value=value, value=value,
                          confidence=row.suggestion_score, concept=True)]  # fmt: skip
    return fields, MappingPane(
        source_value=row.source_value, source_vocabulary_id=None, description=None,
        concept=suggested, original_concept=None, suggestion_score=row.suggestion_score,
        reason=row.review_reason or row.reason, rows=row.rows, label=None, history=[],
    )  # fmt: skip


def curated_key(key: str) -> tuple[str, str]:
    """'TXLENS_NOTE_ABBREV:kidney:tcmr' -> ('TXLENS_NOTE_ABBREV', 'kidney:tcmr')."""
    vocabulary, _, code = key.partition(":")
    if not code:
        raise NotFoundError(key)
    return vocabulary, code


HISTORY_SQL = text(
    "SELECT action, old_concept_id, new_concept_id, reviewer_tenant, reason_code, note, "
    "decided_at FROM app.curated_mapping_history "
    "WHERE source_vocabulary_id = :v AND source_code = :c ORDER BY history_id"
)


def curated_row(conn: Connection, key: str):  # type: ignore[no-untyped-def]
    vocabulary, code = curated_key(key)
    row = conn.execute(
        text("SELECT source_vocabulary_id, source_code, source_code_description, "
             "target_concept_id FROM vocab.source_to_concept_map "
             "WHERE source_vocabulary_id = :v AND source_code = :c AND invalid_reason IS NULL"),
        {"v": vocabulary, "c": code},
    ).first()  # fmt: skip
    if row is None:
        raise NotFoundError(key)
    return row


def history(conn: Connection, vocabulary: str, code: str) -> list[HistoryEntry]:
    """Every earlier review of one curated row, oldest first."""
    entries = []
    for h in conn.execute(HISTORY_SQL, {"v": vocabulary, "c": code}):
        old, new = concept(conn, h.old_concept_id), concept(conn, h.new_concept_id)
        assert old is not None and new is not None
        entries.append(HistoryEntry(action=h.action, old_concept=old, new_concept=new,
                                    reviewer_center=h.reviewer_tenant, reason_code=h.reason_code,
                                    note=h.note, decided_at=h.decided_at))  # fmt: skip
    return entries


def curated_detail(
    conn: Connection, key: str, viewer: Viewer
) -> tuple[list[ReviewField], MappingPane]:
    row = curated_row(conn, key)
    entries = history(conn, row.source_vocabulary_id, row.source_code)
    now = curated.state(row.target_concept_id, [
        curated.Version(e.action, e.old_concept.concept_id, e.new_concept.concept_id)
        for e in entries
    ])  # fmt: skip
    fields = [ReviewField(name="concept_id", label="concept",
                          model_value=str(row.target_concept_id), value=str(now.concept_id),
                          concept=True)]  # fmt: skip
    return fields, MappingPane(
        source_value=row.source_code, source_vocabulary_id=row.source_vocabulary_id,
        description=row.source_code_description, concept=concept(conn, now.concept_id),
        original_concept=concept(conn, row.target_concept_id), suggestion_score=None,
        reason=None, rows=None, label=now.label, history=entries,
    )  # fmt: skip
