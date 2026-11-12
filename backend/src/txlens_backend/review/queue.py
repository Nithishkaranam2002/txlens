"""The unified review queue: what waits for a person, in priority order, per center.

Items are read from where they live (no copy to keep in sync):
- biopsy documents in review (`app.document`, including the QC sample);
- note mentions in review (`app.note_mention`);
- condition mapping gaps (`app.mapping_gap`) without a decision;
- AI-curated mapping rows (`vocab.source_to_concept_map`) with no clinician review yet.
Row-Level Security keeps the first three to the caller's center; curated rows are shared.
Decided items come from `app.review_decision`.
"""

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Connection, text

from txlens_backend.documents.service import QC_REASON
from txlens_backend.rejection import rejection_concept_ids
from txlens_backend.review.curated import histories, state
from txlens_backend.review.priority import TIER_LABELS, Signals, biopsy_signals, sort_key, tier
from txlens_backend.review.reasons import ITEM_TYPES, ItemType
from txlens_omop.mappings import CURATED_PENDING_REVIEW

Status = Literal["pending", "decided"]
# person_id -> how this viewer addresses the patient (person id or pseudonym)
PatientRef = Callable[[int | None], str | None]

BIOPSY_SQL = text(
    """
    SELECT d.document_id::text AS key, d.updated_at AS queued_at, d.organ, d.person_id,
           d.review_reasons, dx.value AS diagnosis, gr.value AS grade
    FROM app.document d
    LEFT JOIN app.extracted_field dx ON dx.document_id = d.document_id AND dx.field = 'diagnosis'
    LEFT JOIN app.extracted_field gr ON gr.document_id = d.document_id
         AND gr.field IN ('tcmr_grade', 'acr_grade')
    WHERE d.status = 'in_review'
    """
)
MENTION_SQL = text(
    """
    SELECT m.note_source_value || ':' || m.start_offset || '-' || m.end_offset AS key,
           p.processed_at AS queued_at, m.lexical_variant, m.concept_id, m.source_concept_id,
           m.review_reason, m.method, n.person_id
    FROM app.note_mention m
    JOIN app.note_processed p ON p.tenant_id = m.tenant_id
         AND p.note_source_value = m.note_source_value
    LEFT JOIN cdm.note n ON n.note_source_value = m.note_source_value
    WHERE m.status = 'review'
    """
)
GAP_SQL = text(
    """
    SELECT g.source_value AS key, g.loaded_at AS queued_at, g.reason, g.rows,
           g.suggested_concept_id, g.review_reason
    FROM app.mapping_gap g
    WHERE NOT EXISTS (SELECT 1 FROM app.review_decision r
                      WHERE r.item_type = 'condition_mapping' AND r.item_key = g.source_value)
    """
)
CURATED_SQL = text(
    """
    SELECT m.source_vocabulary_id || ':' || m.source_code AS key,
           m.valid_start_date::timestamptz AS queued_at, m.source_code,
           m.source_vocabulary_id, m.target_concept_id, c.concept_name
    FROM vocab.source_to_concept_map m
    LEFT JOIN vocab.concept c ON c.concept_id = m.target_concept_id
    WHERE m.source_vocabulary_id = ANY(:vocabularies) AND m.invalid_reason IS NULL
    """
)
DECIDED_SQL = text(
    """
    SELECT DISTINCT ON (item_type, item_key) item_type, item_key, action, decided_at,
           queued_at, qc_sample
    FROM app.review_decision
    WHERE item_type = ANY(:types)
    ORDER BY item_type, item_key, decided_at DESC
    """
)


class ReviewItem(BaseModel):
    """One queue entry. Titles carry no identifiers; `patient_id` is shaped for the viewer."""

    item_type: ItemType
    item_key: str
    title: str
    detail: str | None
    patient_id: str | None
    queued_at: datetime
    tier: int
    tier_label: str
    rejection_related: bool
    high_severity: bool
    qc_sample: bool
    status: Literal["pending", "approve", "edit", "reject", "revert"]
    decided_at: datetime | None = None


class QueuePage(BaseModel):
    """A page of the queue, with the number of pending items per type (all filters but type)."""

    items: list[ReviewItem]
    total: int
    pending_by_type: dict[str, int]


@dataclass(frozen=True)
class Pending:
    """An item before it becomes a ReviewItem."""

    item_type: ItemType
    key: str
    title: str
    detail: str | None
    person_id: int | None
    signals: Signals


def item_of(p: Pending, patient: PatientRef) -> ReviewItem:
    level = tier(p.signals)
    return ReviewItem(
        item_type=p.item_type, item_key=p.key, title=p.title, detail=p.detail,
        patient_id=patient(p.person_id), queued_at=p.signals.queued_at, tier=level,
        tier_label=TIER_LABELS[level], rejection_related=p.signals.rejection_related,
        high_severity=p.signals.high_severity, qc_sample=p.signals.qc_sample, status="pending",
    )  # fmt: skip


def biopsies(conn: Connection) -> Iterable[Pending]:
    for r in conn.execute(BIOPSY_SQL):
        qc = list(r.review_reasons or []) == [QC_REASON]
        signals = biopsy_signals(r.queued_at, r.diagnosis, r.grade, qc)
        reason = "QC sample" if qc else f"{len(r.review_reasons or [])} review reason(s)"
        yield Pending("biopsy_document", r.key,
                      f"{(r.organ or 'unknown organ').capitalize()} biopsy report: "
                      f"{r.diagnosis or 'no diagnosis read'}", reason, r.person_id,
                      signals)  # fmt: skip


def mentions(conn: Connection, rejection: frozenset[int]) -> Iterable[Pending]:
    for r in conn.execute(MENTION_SQL):
        related = (r.concept_id in rejection or r.source_concept_id in rejection
                   or r.review_reason is not None)  # fmt: skip
        why = r.review_reason or ("no concept" if r.concept_id == r.source_concept_id == 0
                                  else f"{r.method} result, low confidence")  # fmt: skip
        yield Pending("note_mention", r.key, f"“{r.lexical_variant}” in a clinic note", why,
                      r.person_id, Signals(r.queued_at, rejection_related=related))  # fmt: skip


def gaps(conn: Connection) -> Iterable[Pending]:
    for r in conn.execute(GAP_SQL):
        detail = f"{r.reason}, {r.rows:,} row(s)"
        yield Pending("condition_mapping", r.key, f"Condition text “{r.key}”", detail, None,
                      Signals(r.queued_at))  # fmt: skip


def curated_rows(conn: Connection):  # type: ignore[no-untyped-def]
    """Every AI-curated mapping row of the loaded vocabulary."""
    return conn.execute(CURATED_SQL, {"vocabularies": sorted(CURATED_PENDING_REVIEW)}).all()


def curated_all(conn: Connection, rejection: frozenset[int]) -> Iterable[tuple[Pending, bool]]:
    """Every curated row and whether a clinician review is in force for it (shared by all
    centers: the history has no tenant)."""
    reviewed = histories(conn)
    for r in curated_rows(conn):
        now = state(r.target_concept_id, reviewed.get((r.source_vocabulary_id, r.source_code), []))
        title = f"{r.source_vocabulary_id} “{r.source_code}” → {r.concept_name or 'no concept'}"
        signals = Signals(r.queued_at, rejection_related=r.target_concept_id in rejection)
        yield Pending("curated_mapping", r.key, title, now.label, None, signals), now.reviewed


def curated(conn: Connection, rejection: frozenset[int]) -> Iterable[Pending]:
    """Curated rows no clinician approval or correction is in force for."""
    return (p for p, reviewed in curated_all(conn, rejection) if not reviewed)


def curated_item(conn: Connection, key: str) -> ReviewItem | None:
    """One curated row as a queue item, reviewed or not (the same for every center)."""
    for p, reviewed in curated_all(conn, rejection_concept_ids(conn)):
        if p.key == key:
            item = item_of(p, lambda _: None)
            return item.model_copy(update={"status": "approve"}) if reviewed else item
    return None


def pending(conn: Connection, types: Iterable[ItemType]) -> list[Pending]:
    """Every pending item of these types, in queue order."""
    wanted = set(types)
    rejection = rejection_concept_ids(conn)
    found: list[Pending] = []
    if "biopsy_document" in wanted:
        found += biopsies(conn)
    if "note_mention" in wanted:
        found += mentions(conn, rejection)
    if "condition_mapping" in wanted:
        found += gaps(conn)
    if "curated_mapping" in wanted:
        found += curated(conn, rejection)
    return sorted(found, key=lambda p: (*sort_key(p.signals), p.key))


def decided(conn: Connection, types: Iterable[ItemType]) -> list[ReviewItem]:
    """The latest decision per item, newest first."""
    items = []
    for r in conn.execute(DECIDED_SQL, {"types": list(types)}):
        signals = Signals(r.queued_at, qc_sample=r.qc_sample)
        level = tier(signals)
        items.append(
            ReviewItem(item_type=r.item_type, item_key=r.item_key,
                       title=r.item_key if r.item_type != "note_mention" else "Note mention",
                       detail=None, patient_id=None, queued_at=r.queued_at, tier=level,
                       tier_label=TIER_LABELS[level], rejection_related=False,
                       high_severity=False, qc_sample=r.qc_sample, status=r.action,
                       decided_at=r.decided_at)
        )  # fmt: skip
    return sorted(items, key=lambda i: i.decided_at or i.queued_at, reverse=True)


def queue_page(
    conn: Connection, patient: PatientRef, item_type: ItemType | None, status: Status,
    limit: int, offset: int,
) -> QueuePage:  # fmt: skip
    """One page of pending (priority order) or decided (newest first) items."""
    types = [item_type] if item_type else list(ITEM_TYPES)
    waiting = pending(conn, ITEM_TYPES)
    counts = Counter(p.item_type for p in waiting)
    if status == "pending":
        chosen = [p for p in waiting if p.item_type in types]
        page = [item_of(p, patient) for p in chosen[offset : offset + limit]]
        total = len(chosen)
    else:
        done = decided(conn, types)
        page, total = done[offset : offset + limit], len(done)
    return QueuePage(items=page, total=total,
                     pending_by_type={t: counts.get(t, 0) for t in ITEM_TYPES})  # fmt: skip
