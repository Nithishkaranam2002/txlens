"""Reviewer decisions: check, record, learn from, and apply them.

Every decision is one `app.review_decision` row (append-only) plus one `app.labelled_example`
per reviewed field: the model's value next to the reviewer's, with de-identified evidence only.
Agreement rates and new evaluation examples come from those rows. How a decision is applied
depends on the item:
- biopsy document: the API sends it to the document's Temporal workflow (or applies it when
  none is waiting); this module records it and its examples;
- note mention: status and values change here, and the note's OMOP conditions are rebuilt;
- condition mapping gap: the chosen concept is stored with the decision; `txlens-omop
  export-curation` writes it to the mapping file for the next pipeline run;
- AI-curated mapping row: a new version in the shared history (all centers).
Audit rows are written by the caller, in the same transaction.
"""

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import Connection, text

from txlens_backend.documents import store
from txlens_backend.documents.schema import Organ
from txlens_backend.documents.service import checked_changes
from txlens_backend.notes.results import apply_review, rebuild_cdm_rows
from txlens_backend.review import curated
from txlens_backend.review.detail import (
    TEMPORALITIES,
    curated_row,
    mention_row,
    mention_values,
)
from txlens_backend.review.queue import ReviewItem
from txlens_backend.review.reasons import Action, ReasonCode
from txlens_backend.services import biopsies

DECISION_SQL = text(
    """
    INSERT INTO app.review_decision (decision_id, item_type, item_key, action, reason_code,
        changes, note, reviewer_id, qc_sample, queued_at)
    VALUES (:id, :type, :key, :action, :reason, CAST(:changes AS jsonb), :note, :reviewer,
            :qc, :queued)
    """
)
EXAMPLE_SQL = text(
    """
    INSERT INTO app.labelled_example (decision_id, item_type, field, model_value,
        reviewer_value, agrees, qc_sample, evidence_deid)
    VALUES (:id, :type, :field, :model, :reviewer, :agrees, :qc, :evidence)
    """
)
# Dates are compared but never stored in examples: the example set must be de-identified.
DATE_FIELDS = frozenset({"biopsy_date"})
MASK_DATE = "[DATE]"
MENTION_FIELDS = {"concept_id", "negated", "temporality", "experiencer"}
CHOICES = {"negated": ("true", "false"), "temporality": tuple(TEMPORALITIES),
           "experiencer": ("patient", "family")}  # fmt: skip


class InvalidChangeError(ValueError):
    """An edit names an unknown field or a value it does not allow."""


class ConflictError(RuntimeError):
    """The item is not waiting for this decision (already decided, nothing to revert)."""


@dataclass(frozen=True)
class Decision:
    """What the reviewer submitted (already checked against the reason rules)."""

    action: Action
    reason_code: ReasonCode | None
    note: str | None
    changes: Mapping[str, str | None]


def record(
    conn: Connection, item: ReviewItem, decision: Decision, reviewer_id: str,
    model: Mapping[str, str | None], final: Mapping[str, str | None],
    evidence: Mapping[str, str | None],
) -> str:  # fmt: skip
    """The decision row and one labelled example per field; returns the decision id."""
    decision_id = str(uuid.uuid4())
    changes = {"edits": dict(decision.changes), "model": dict(model), "final": dict(final)}
    conn.execute(
        DECISION_SQL,
        {"id": decision_id, "type": item.item_type, "key": item.item_key,
         "action": decision.action, "reason": decision.reason_code,
         "changes": json.dumps(changes), "note": decision.note, "reviewer": reviewer_id,
         "qc": item.qc_sample, "queued": item.queued_at},
    )  # fmt: skip
    for field, model_value in model.items():
        reviewer_value = None if decision.action == "reject" else final.get(field)
        agrees = decision.action != "reject" and reviewer_value == model_value
        if field in DATE_FIELDS:
            model_value, reviewer_value = (None if v is None else MASK_DATE
                                           for v in (model_value, reviewer_value))  # fmt: skip
        conn.execute(
            EXAMPLE_SQL,
            {"id": decision_id, "type": item.item_type, "field": field, "model": model_value,
             "reviewer": reviewer_value, "agrees": agrees, "qc": item.qc_sample,
             "evidence": evidence.get(field)},
        )  # fmt: skip
    return decision_id


def biopsy(conn: Connection, item: ReviewItem, decision: Decision, reviewer_id: str) -> None:
    """Check and record a biopsy decision (the workflow applies it)."""
    row = biopsies.document_row(conn, item.item_key)
    if row is None:
        raise ConflictError("document not found")
    if row.status != "in_review":
        raise ConflictError(f"document is {row.status}")
    organ: Organ = row.organ or "kidney"
    raw = dict(decision.changes) if decision.action == "edit" else {}
    try:
        typed = checked_changes(organ, raw)
    except ValueError as error:
        raise InvalidChangeError(str(error)) from error
    redacted = biopsies.evidence(conn, row, redacted=True, shift=timedelta(0))
    model = {f.field: f.extracted_value for f in redacted.fields}
    final = {f.field: f.value for f in redacted.fields} | {
        k: store.encode(v) for k, v in typed.items()
    }
    lines = {f.field: f.line_text for f in redacted.fields}
    record(conn, item, decision, reviewer_id, model, final, lines)


def concept_exists(conn: Connection, concept_id: int, standard: bool) -> bool:
    """True for a valid concept (and a standard one, when asked)."""
    found = conn.execute(
        text("SELECT standard_concept FROM vocab.concept WHERE concept_id = :c "
             "AND invalid_reason IS NULL"), {"c": concept_id},
    ).first()  # fmt: skip
    return found is not None and (not standard or found[0] == "S")


def concept_change(conn: Connection, changes: Mapping[str, str | None], standard: bool) -> int:
    """The edited concept id, checked; InvalidChangeError if missing, unknown or not allowed."""
    raw = changes.get("concept_id")
    if raw is None or not raw.isdigit() or not concept_exists(conn, int(raw), standard):
        kind = "standard concept" if standard else "concept"
        raise InvalidChangeError(f"concept_id must name a valid {kind}")
    return int(raw)


def mention_final(
    conn: Connection, now: dict[str, str | None], changes: Mapping[str, str | None]
) -> dict[str, str | None]:
    """The mention's values after an edit, every changed value checked."""
    unknown = set(changes) - MENTION_FIELDS
    if unknown:
        raise InvalidChangeError(f"unknown field: {sorted(unknown)[0]}")
    final = dict(now)
    for name, value in changes.items():
        if name == "concept_id":
            final[name] = str(concept_change(conn, changes, standard=False))
        elif value not in CHOICES[name]:
            raise InvalidChangeError(f"{name} must be one of {', '.join(CHOICES[name])}")
        else:
            final[name] = value
    return final


def mention(conn: Connection, item: ReviewItem, decision: Decision, reviewer_id: str) -> None:
    """Approve, edit or reject a note mention, then rebuild the note's conditions."""
    row = mention_row(conn, item.item_key)
    status = conn.execute(
        text("SELECT status FROM app.note_mention WHERE mention_id = :m"), {"m": row.mention_id}
    ).scalar_one()
    if status != "review":
        raise ConflictError(f"mention is {status}")
    now = mention_values(row)
    final = mention_final(conn, now, decision.changes) if decision.action == "edit" else now
    sentence = " ".join((row.sentence or "").split())
    record(conn, item, decision, reviewer_id, now, final, dict.fromkeys(now, sentence))
    note = item.item_key.rsplit(":", 1)[0]
    decided_at = conn.execute(text("SELECT now()")).scalar_one()
    apply_review(conn, item.item_key, decision.action, {k: v for k, v in final.items() if v},
                 reviewer_id, decided_at)  # fmt: skip
    rebuild_cdm_rows(conn, note)


def gap(conn: Connection, item: ReviewItem, decision: Decision, reviewer_id: str) -> None:
    """Map a condition text: approve the suggestion, pick another concept, or leave it
    unmapped (reject)."""
    suggested = conn.execute(
        text("SELECT suggested_concept_id FROM app.mapping_gap WHERE source_value = :k"),
        {"k": item.item_key},
    ).scalar_one_or_none()
    model: dict[str, str | None] = {"concept_id": str(suggested) if suggested else None}
    final: dict[str, str | None]
    if decision.action == "edit":
        final = {"concept_id": str(concept_change(conn, decision.changes, standard=True))}
    elif decision.action == "approve":
        if not suggested or not concept_exists(conn, suggested, standard=True):
            raise InvalidChangeError("there is no valid suggestion to approve: pick a concept")
        final = model
    else:
        final = {"concept_id": None}
    record(conn, item, decision, reviewer_id, model, final, {"concept_id": item.item_key})


def curated_mapping(
    conn: Connection, item: ReviewItem, decision: Decision, reviewer_id: str
) -> None:
    """Approve, correct or revert an AI-curated row: a new version for every center."""
    row = curated_row(conn, item.item_key)
    versions = curated.histories(conn).get((row.source_vocabulary_id, row.source_code), [])
    now = curated.state(row.target_concept_id, versions)
    if decision.action == "revert":
        if not now.in_force:
            raise ConflictError("nothing to revert: no clinician review is in force")
        action: curated.HistoryAction = "revert"
        new = versions[now.in_force[-1]].old_concept_id
    elif decision.action == "edit":
        action, new = "correct", concept_change(conn, decision.changes, standard=True)
    else:
        action, new = "approve", now.concept_id
    curated.add_entry(conn, row.source_vocabulary_id, row.source_code, action, now.concept_id,
                      new, reviewer_id, decision.reason_code, decision.note)  # fmt: skip
    model = {"concept_id": str(row.target_concept_id)}
    record(conn, item, decision, reviewer_id, model, {"concept_id": str(new)},
           {"concept_id": f"{row.source_code}: {row.source_code_description}"})  # fmt: skip
