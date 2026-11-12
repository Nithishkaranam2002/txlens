"""Database reads and writes for documents (all inside the caller's tenant transaction)."""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Connection, text

from txlens_backend.documents.assess import Assessment
from txlens_backend.documents.concepts import CdmRow
from txlens_backend.documents.link import Link
from txlens_backend.documents.ocr import OcrResult, from_json, to_json
from txlens_backend.documents.passes import PassOutcome
from txlens_backend.documents.phi import Identity
from txlens_backend.documents.schema import SCORE_LABELS, Organ

FINAL_STATUSES = ("extracted", "approved", "rejected", "review_expired")


@dataclass(frozen=True)
class DocumentRow:
    """The parts of app.document the workflow needs."""

    document_id: str
    sha256: str
    source_path: str
    organ: Organ | None
    status: str
    person_id: int | None
    procedure_occurrence_id: int | None
    workflow_id: str


def encode(value: Any) -> str | None:
    """A field value as stored text."""
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)


def decode(field: str, stored: str | None) -> Any:
    """A stored field value back in its type (dates, integer scores, or text)."""
    if stored is None:
        return None
    if field == "biopsy_date":
        return date.fromisoformat(stored)
    return int(stored) if field in SCORE_LABELS else stored


def register(conn: Connection, sha256: str, source_path: str, workflow_id: str) -> DocumentRow:
    """The document for this file hash, created on first sight (same file = same document)."""
    conn.execute(
        text(
            "INSERT INTO app.document (sha256, source_path, workflow_id) VALUES (:h, :p, :w) "
            "ON CONFLICT (tenant_id, sha256) DO NOTHING"
        ),
        {"h": sha256, "p": source_path, "w": workflow_id},
    )
    row = conn.execute(
        text("SELECT document_id FROM app.document WHERE sha256 = :h"), {"h": sha256}
    ).scalar_one()
    return get(conn, str(row))


def get(conn: Connection, document_id: str) -> DocumentRow:
    """One document."""
    row = conn.execute(
        text(
            "SELECT document_id, sha256, source_path, organ, status, person_id, "
            "procedure_occurrence_id, workflow_id FROM app.document WHERE document_id = :d"
        ),
        {"d": document_id},
    ).one()
    return DocumentRow(
        str(row.document_id),
        row.sha256,
        row.source_path,
        row.organ,
        row.status,
        row.person_id,
        row.procedure_occurrence_id,
        row.workflow_id,
    )


def save_reading(
    conn: Connection, document_id: str, ocr: OcrResult, link: Link | None, organ: Organ | None
) -> None:
    """Store the OCR result and the biopsy link (or its absence)."""
    conn.execute(
        text(
            "UPDATE app.document SET ocr = CAST(:o AS jsonb), ocr_engine = :e, organ = :g, "
            "person_id = :p, procedure_occurrence_id = :r, status = 'processing', "
            "updated_at = now() WHERE document_id = :d"
        ),
        {
            "o": json.dumps(to_json(ocr)),
            "e": ocr.engine,
            "g": organ,
            "p": link.person_id if link else None,
            "r": link.procedure_occurrence_id if link else None,
            "d": document_id,
        },
    )


def ocr_of(conn: Connection, document_id: str) -> OcrResult:
    """The stored OCR result."""
    data = conn.execute(
        text("SELECT ocr FROM app.document WHERE document_id = :d"), {"d": document_id}
    ).scalar_one()
    return from_json(data)


def identity_of(conn: Connection, person_id: int | None) -> Identity | None:
    """A linked patient's identifiers (for scrubbing them from the LLM input only)."""
    if person_id is None:
        return None
    row = conn.execute(
        text(
            "SELECT mrn, given_name, family_name, birth_date FROM app.patient_identity "
            "WHERE person_id = :p"
        ),
        {"p": person_id},
    ).first()
    return Identity(row.mrn, row.given_name, row.family_name, row.birth_date) if row else None


def procedure_date(conn: Connection, procedure_occurrence_id: int | None) -> date | None:
    """The date of the linked biopsy procedure."""
    if procedure_occurrence_id is None:
        return None
    found = conn.execute(
        text(
            "SELECT procedure_date FROM cdm.procedure_occurrence WHERE procedure_occurrence_id = :p"
        ),
        {"p": procedure_occurrence_id},
    ).scalar_one_or_none()
    return found if isinstance(found, date) else None


def save_pass(conn: Connection, document_id: str, outcome: PassOutcome) -> None:
    """Record one pass (a re-run of the same pass, prompt and model replaces it)."""
    conn.execute(
        text(
            "INSERT INTO app.extraction (document_id, pass, model, prompt_version, output, "
            "error, input_tokens, output_tokens, cost_usd, latency_ms, cached) VALUES "
            "(:d, :v, :m, :pv, CAST(:o AS jsonb), :e, :i, :ot, :c, :l, :ca) "
            "ON CONFLICT (document_id, pass, prompt_version, model) DO UPDATE SET "
            "output = EXCLUDED.output, error = EXCLUDED.error, cached = EXCLUDED.cached, "
            "input_tokens = EXCLUDED.input_tokens, output_tokens = EXCLUDED.output_tokens, "
            "cost_usd = EXCLUDED.cost_usd, latency_ms = EXCLUDED.latency_ms, created_at = now()"
        ),
        {
            "d": document_id,
            "v": outcome.variant,
            "m": outcome.model,
            "pv": outcome.prompt_version,
            "o": json.dumps(outcome.output) if outcome.output is not None else None,
            "e": outcome.error,
            "i": outcome.input_tokens,
            "ot": outcome.output_tokens,
            "c": outcome.cost_usd,
            "l": outcome.latency_ms,
            "ca": outcome.cached,
        },
    )


def pass_answers(
    conn: Connection, document_id: str, prompt_version: str, models: Mapping[str, str]
) -> dict[str, tuple[dict[str, Any] | None, str | None]]:
    """Pass -> (answer, error) for the current prompt version and models."""
    rows = conn.execute(
        text(
            "SELECT pass AS variant, model, output, error FROM app.extraction "
            "WHERE document_id = :d AND prompt_version = :pv"
        ),
        {"d": document_id, "pv": prompt_version},
    ).all()
    return {r.variant: (r.output, r.error) for r in rows if models.get(r.variant) == r.model}


def save_fields(conn: Connection, document_id: str, assessment: Assessment) -> None:
    """Replace the document's final fields with their evidence and confidence."""
    conn.execute(text("DELETE FROM app.extracted_field WHERE document_id = :d"), {"d": document_id})
    for name, result in assessment.fields.items():
        v = result.verified
        conn.execute(
            text(
                "INSERT INTO app.extracted_field (document_id, field, value, extracted_value, "
                "page, bbox, line_no, source_text, ocr_confidence, passes_agree, confidence, "
                "problems) VALUES (:d, :f, :v, :x, :pg, CAST(:b AS jsonb), :ln, :s, :oc, :a, "
                ":c, CAST(:p AS jsonb))"
            ),
            {
                "d": document_id,
                "f": name,
                "v": encode(v.value),
                "x": encode(v.extracted_value),
                "pg": v.page,
                "b": json.dumps(v.bbox) if v.bbox else None,
                "ln": v.line_no,
                "s": v.source_text,
                "oc": round(v.ocr_confidence, 3) if v.ocr_confidence is not None else None,
                "a": result.passes_agree,
                "c": round(result.confidence, 3),
                "p": json.dumps(list(v.problems)),
            },
        )


def field_values(conn: Connection, document_id: str) -> dict[str, Any]:
    """Field name -> current value (after any review edit), in its type."""
    rows = conn.execute(
        text("SELECT field, value FROM app.extracted_field WHERE document_id = :d"),
        {"d": document_id},
    ).all()
    return {r.field: decode(r.field, r.value) for r in rows}


def field_lines(conn: Connection, document_id: str) -> dict[str, int | None]:
    """Field name -> the OCR line its evidence was read from (None if not found)."""
    rows = conn.execute(
        text("SELECT field, line_no FROM app.extracted_field WHERE document_id = :d"),
        {"d": document_id},
    ).all()
    return {r.field: r.line_no for r in rows}


def set_values(conn: Connection, document_id: str, values: Mapping[str, Any]) -> None:
    """Overwrite field values (a reviewer's edit); the extracted value is kept beside it."""
    for name, value in values.items():
        conn.execute(
            text(
                "INSERT INTO app.extracted_field (document_id, field, value, passes_agree, "
                "confidence) VALUES (:d, :f, :v, false, 1) ON CONFLICT (document_id, field) "
                "DO UPDATE SET value = EXCLUDED.value, confidence = 1"
            ),
            {"d": document_id, "f": name, "v": encode(value)},
        )


def unload(conn: Connection, document_id: str) -> None:
    """Remove the document's rows from cdm and forget them (before they are rebuilt)."""
    for table, key in (
        ("condition_occurrence", "condition_occurrence_id"),
        ("measurement", "measurement_id"),
    ):
        conn.execute(
            text(
                f"DELETE FROM cdm.{table} WHERE {key} IN "
                "(SELECT cdm_id FROM app.document_cdm_row WHERE document_id = :d)"
            ),
            {"d": document_id},
        )
    conn.execute(
        text("DELETE FROM app.document_cdm_row WHERE document_id = :d"), {"d": document_id}
    )


def save_cdm_rows(conn: Connection, document_id: str, rows: Iterable[CdmRow]) -> None:
    """Replace the OMOP rows the document produces (not yet loaded into cdm)."""
    unload(conn, document_id)
    for row in rows:
        conn.execute(
            text(
                "INSERT INTO app.document_cdm_row (document_id, field, cdm_table, concept_id, "
                "source_concept_id, source_value, value_as_number, value_source_value) "
                "VALUES (:d, :f, :t, :c, :sc, :sv, :n, :vs)"
            ),
            {
                "d": document_id,
                "f": row.field,
                "t": row.cdm_table,
                "c": row.concept_id,
                "sc": row.source_concept_id,
                "sv": row.source_value,
                "n": row.value_as_number,
                "vs": row.value_source_value,
            },
        )


def set_status(
    conn: Connection,
    document_id: str,
    status: str,
    reasons: Iterable[str] | None = None,
    confidence: float | None = None,
) -> None:
    """Change the document status (and its review reasons and confidence, when given)."""
    conn.execute(
        text(
            "UPDATE app.document SET status = :s, updated_at = now(), "
            "review_reasons = COALESCE(CAST(:r AS jsonb), review_reasons), "
            "confidence = COALESCE(:c, confidence) WHERE document_id = :d"
        ),
        {
            "s": status,
            "r": json.dumps(list(reasons)) if reasons is not None else None,
            "c": confidence,
            "d": document_id,
        },
    )


def load_into_cdm(conn: Connection, document_id: str) -> int:
    """Copy the document's rows into cdm (only if extracted or approved); rows written."""
    return int(
        conn.execute(
            text("SELECT app.load_biopsy_results(CAST(:d AS uuid))"), {"d": document_id}
        ).scalar_one()
    )


def save_review(
    conn: Connection,
    document_id: str,
    reviewer_id: str,
    decision: str,
    changes: Mapping[str, Any],
    note: str | None,
) -> None:
    """Record a human decision."""
    conn.execute(
        text(
            "INSERT INTO app.document_review (document_id, reviewer_id, decision, changes, note) "
            "VALUES (:d, :u, :x, CAST(:c AS jsonb), :n)"
        ),
        {
            "d": document_id,
            "u": reviewer_id,
            "x": decision,
            "c": json.dumps({k: encode(v) for k, v in changes.items()}),
            "n": note,
        },
    )
