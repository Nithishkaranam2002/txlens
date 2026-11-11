"""Biopsy results from pathology reports, shaped for each role.

- Timeline: each biopsy shows its result (diagnosis, grade, key scores) and status: extracted
  (automatic), in review, or approved (by a person). Clinicians and data stewards also get a
  link to the evidence; researchers get the result only.
- Evidence, clinicians: every field with its page, bounding box and the full OCR line.
- Evidence, data stewards (a de-identified role): only the evidence lines, rebuilt from the
  minimized text (identifier lines dropped, the patient's name and MRN scrubbed) with every
  date masked; dates in values and reasons are shifted or masked; no page images, no boxes.
"""

import re
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Connection, text

from txlens_backend.documents import store
from txlens_backend.documents.phi import (
    LONG_NUMBER,
    MASK_DATE,
    MASK_ID,
    Identity,
    mask_dates,
    minimize,
    parse_header,
    scrub_identity,
)

ResultStatus = Literal["extracted", "in_review", "approved"]
SHOWN_STATUSES = ("extracted", "in_review", "approved")
KIDNEY_SCORES = ("banff_i", "banff_t", "banff_v", "banff_g", "banff_ptc", "banff_ci",
                 "banff_ct", "banff_cv", "c4d")  # fmt: skip
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


class BiopsyResult(BaseModel):
    """What a biopsy's pathology report says, and how far it has been checked."""

    status: ResultStatus
    diagnosis: str | None
    grade: str | None
    scores: dict[str, str]
    additional_finding: str | None
    document_id: str | None
    evidence_url: str | None


class FieldEvidence(BaseModel):
    """One extracted field and where it was read. Page and box only in the full view."""

    field: str
    value: str | None
    extracted_value: str | None
    line_text: str | None
    source_text: str | None
    page: int | None
    bbox: list[float] | None
    confidence: float
    passes_agree: bool
    problems: list[str]


class DocumentEvidence(BaseModel):
    """A document's fields with their evidence: full (clinicians) or redacted (stewards)."""

    document_id: str
    view: Literal["full", "redacted"]
    status: str
    organ: str | None
    confidence: float | None
    review_reasons: list[str]
    page_sizes: list[list[float]]
    page_urls: list[str]
    fields: list[FieldEvidence]


def short_name(field: str) -> str:
    """'banff_ptc' -> 'ptc', 'c4d' -> 'C4d'."""
    return "C4d" if field == "c4d" else field.removeprefix("banff_")


def result_of(
    organ: str | None, values: dict[str, str | None]
) -> tuple[str | None, dict[str, str]]:
    """(grade, key scores) for the timeline."""
    if organ == "heart":
        acr, pamr = values.get("acr_grade"), values.get("pamr_grade")
        scores = {"ACR": acr} if acr else {}
        if pamr:
            scores["pAMR"] = pamr.removeprefix("pAMR ")
        return acr, scores
    scores = {short_name(f): v for f in KIDNEY_SCORES if (v := values.get(f)) is not None}
    return values.get("tcmr_grade"), scores


def results_by_procedure(
    conn: Connection, person_id: int, with_links: bool
) -> dict[int, BiopsyResult]:
    """procedure_occurrence_id -> result of its latest shown document."""
    documents = conn.execute(
        text(
            "SELECT DISTINCT ON (procedure_occurrence_id) document_id, procedure_occurrence_id, "
            "status, organ FROM app.document WHERE person_id = :p AND status = ANY(:s) "
            "ORDER BY procedure_occurrence_id, updated_at DESC"
        ),
        {"p": person_id, "s": list(SHOWN_STATUSES)},
    ).all()
    results = {}
    for d in documents:
        rows = conn.execute(
            text("SELECT field, value FROM app.extracted_field WHERE document_id = :d"),
            {"d": d.document_id},
        ).all()
        values = {r.field: r.value for r in rows}
        grade, scores = result_of(d.organ, values)
        document_id = str(d.document_id)
        results[d.procedure_occurrence_id] = BiopsyResult(
            status=d.status,
            diagnosis=values.get("diagnosis"),
            grade=grade,
            scores=scores,
            additional_finding=values.get("additional_finding"),
            document_id=document_id if with_links else None,
            evidence_url=f"/documents/{document_id}/evidence" if with_links else None,
        )
    return results


def document_row(conn: Connection, document_id: str) -> store.DocumentRow | None:
    """The document, or None if it is not in this center (RLS) or the id is malformed."""
    try:
        uuid.UUID(document_id)
    except ValueError:
        return None
    found = conn.execute(
        text("SELECT 1 FROM app.document WHERE document_id = :d"), {"d": document_id}
    ).first()
    return store.get(conn, document_id) if found else None


def redactor(identity: Identity | None) -> Callable[[str], str]:
    """Text as a de-identified role may see it: dates, long numbers and the patient's name
    and MRN masked."""

    def redact(value: str) -> str:
        value = LONG_NUMBER.sub(MASK_ID, ISO_DATE.sub(MASK_DATE, mask_dates(value, False)))
        return scrub_identity(value, identity) if identity else value

    return redact


def shift_value(field: str, value: str | None, move: timedelta) -> str | None:
    """Dates move by the patient's shift in the redacted view; other values are as is."""
    if value is None or field != "biopsy_date":
        return value
    return (date.fromisoformat(value) + move).isoformat()


def evidence(
    conn: Connection, row: store.DocumentRow, redacted: bool, shift: timedelta
) -> DocumentEvidence:
    """A document's fields with their evidence, full or redacted."""
    meta = conn.execute(
        text("SELECT confidence, review_reasons FROM app.document WHERE document_id = :d"),
        {"d": row.document_id},
    ).one()
    ocr = store.ocr_of(conn, row.document_id)
    identity = store.identity_of(conn, row.person_id)
    sent = minimize(ocr, parse_header(ocr), identity)
    redact = redactor(identity)
    fields = conn.execute(
        text(
            "SELECT field, value, extracted_value, page, bbox, line_no, source_text, "
            "confidence, passes_agree, problems FROM app.extracted_field "
            "WHERE document_id = :d ORDER BY field"
        ),
        {"d": row.document_id},
    ).all()

    def line_text(line_no: int | None) -> str | None:
        if line_no is None:
            return None
        if redacted:
            sent_text = sent.text_of(line_no)
            return redact(sent_text) if sent_text else None
        line = ocr.line(line_no)
        return line.text if line else None

    def clean(value: str | None) -> str | None:
        return redact(value) if redacted and value is not None else value

    items = [
        FieldEvidence(
            field=f.field,
            value=shift_value(f.field, f.value, shift) if redacted else f.value,
            extracted_value=shift_value(f.field, f.extracted_value, shift)
            if redacted
            else f.extracted_value,
            line_text=line_text(f.line_no),
            source_text=clean(f.source_text),
            page=None if redacted else f.page,
            bbox=None if redacted else f.bbox,
            confidence=float(f.confidence),
            passes_agree=f.passes_agree,
            problems=[redact(p) if redacted else p for p in f.problems],
        )
        for f in fields
    ]
    pages = [[p.width, p.height] for p in ocr.pages]
    return DocumentEvidence(
        document_id=row.document_id,
        view="redacted" if redacted else "full",
        status=row.status,
        organ=row.organ,
        confidence=float(meta.confidence) if meta.confidence is not None else None,
        review_reasons=[redact(r) if redacted else r for r in meta.review_reasons],
        page_sizes=[] if redacted else pages,
        page_urls=[]
        if redacted
        else [f"/documents/{row.document_id}/pages/{n}" for n in range(1, len(pages) + 1)],
        fields=items,
    )
