"""The steps of processing one biopsy report, each in its own tenant transaction.

The Temporal workflow calls these as activities; the evaluation script calls them directly.
Every step writes an audit row (request id = the workflow id; user = the reviewer, or none
for automatic steps). Steps only take and return small values: the document itself lives in
the database between steps.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Connection, Engine

from txlens_backend import audit
from txlens_backend.db import tenant_transaction
from txlens_backend.documents import store
from txlens_backend.documents.assess import Assessment, assess
from txlens_backend.documents.budget import Budget
from txlens_backend.documents.concepts import cdm_rows
from txlens_backend.documents.link import link_report
from txlens_backend.documents.llm import PROMPT_VERSION, Extractor, Variant
from txlens_backend.documents.ocr import Engine as OcrEngine
from txlens_backend.documents.ocr import ocr_pdf
from txlens_backend.documents.passes import PassOutcome, run_pass
from txlens_backend.documents.phi import MinimizedText, minimize, parse_header
from txlens_backend.documents.schema import SCHEMAS, Organ, heart_diagnosis
from txlens_backend.documents.validate import validate

QC_REASON = "QC sample: would have been auto-approved; reviewed to measure the error rate"
ORGAN_WORDS: dict[Organ, tuple[str, ...]] = {
    "kidney": ("kidney allograft", "banff"),
    "heart": ("endomyocardial", "heart allograft", "ishlt"),
}


@dataclass(frozen=True)
class ReviewDecision:
    """A reviewer's decision, as sent by the API to the waiting workflow."""

    action: Literal["approve", "edit", "reject"]
    reviewer_id: str
    request_id: str
    changes: dict[str, str | None] = field(default_factory=dict)
    note: str | None = None
    # Added in Step 2.3; the default keeps signals from older API versions valid.
    reason_code: str | None = None


class InvalidEditError(ValueError):
    """A reviewer's edit names an unknown field or a value the field does not allow."""


def info(request_id: str) -> audit.RequestInfo:
    """Audit context for workflow steps (no client address)."""
    return audit.RequestInfo(request_id=request_id, ip="")


def file_sha256(path: Path) -> str:
    """The file's SHA-256: the document's identity."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def register_document(
    engine: Engine, tenant_id: str, path: Path, source_path: str, workflow_id: str
) -> store.DocumentRow:
    """Record the file (same file = same document) and return its current state."""
    with tenant_transaction(engine, tenant_id) as conn:
        row = store.register(conn, file_sha256(path), source_path, workflow_id)
        audit.record(
            conn,
            info(workflow_id),
            None,
            "document_register",
            "document",
            row.document_id,
            {"status": row.status},
        )
    return row


def organ_from_text(text: str) -> Organ | None:
    """The organ named in the report, when the biopsy link could not tell."""
    lowered = text.lower()
    found = [organ for organ, words in ORGAN_WORDS.items() if any(w in lowered for w in words)]
    return found[0] if len(found) == 1 else None


def read_document(
    engine: Engine,
    tenant_id: str,
    document_id: str,
    path: Path,
    ocr_engine: OcrEngine,
    workflow_id: str,
) -> Organ | None:
    """OCR the file, link it to its biopsy, store both; returns the organ (None if unknown)."""
    ocr = ocr_pdf(path, ocr_engine)
    with tenant_transaction(engine, tenant_id) as conn:
        link = link_report(conn, parse_header(ocr))
        organ = link.organ if link else organ_from_text(" ".join(x.text for x in ocr.lines))
        store.save_reading(conn, document_id, ocr, link, organ)
        audit.record(
            conn,
            info(workflow_id),
            None,
            "document_read",
            "document",
            document_id,
            {
                "engine": ocr.engine,
                "words": ocr.word_count,
                "linked": link is not None,
                "organ": organ,
            },
        )
    return organ


def minimized(conn: Connection, document_id: str) -> MinimizedText:
    """The text the LLM may see, rebuilt from the stored OCR and the linked patient."""
    row = store.get(conn, document_id)
    ocr = store.ocr_of(conn, document_id)
    return minimize(ocr, parse_header(ocr), store.identity_of(conn, row.person_id))


def extraction_pass(
    engine: Engine,
    tenant_id: str,
    document_id: str,
    extractor: Extractor,
    budget: Budget,
    variant: Variant,
    workflow_id: str,
) -> PassOutcome:
    """Run (or reuse from cache) one pass and record it. Raises BudgetExceededError."""
    with tenant_transaction(engine, tenant_id) as conn:
        row = store.get(conn, document_id)
        sent = minimized(conn, document_id)
    if row.organ is None:
        raise ValueError(f"document {document_id} has no organ")
    outcome = run_pass(engine, tenant_id, extractor, budget, row.organ, sent, variant, row.sha256)
    with tenant_transaction(engine, tenant_id) as conn:
        store.save_pass(conn, document_id, outcome)
        audit.record(
            conn,
            info(workflow_id),
            None,
            "document_extract",
            "document",
            document_id,
            {
                "pass": variant,
                "model": outcome.model,
                "cached": outcome.cached,
                "cost_usd": outcome.cost_usd,
                "error": outcome.error is not None,
            },
        )
    return outcome


def assess_document(
    engine: Engine,
    tenant_id: str,
    document_id: str,
    models: Mapping[str, str],
    threshold: float,
    workflow_id: str,
    qc_rate: float = 0.0,
) -> str:
    """Combine both passes, validate, map to OMOP rows; status 'extracted' or 'in_review'."""
    with tenant_transaction(engine, tenant_id) as conn:
        row = store.get(conn, document_id)
        if row.organ is None:
            reasons = ["the report's organ could not be determined"]
            store.set_status(conn, document_id, "in_review", reasons, 0.0)
            return "in_review"
        ocr, sent = store.ocr_of(conn, document_id), minimized(conn, document_id)
        answers = store.pass_answers(conn, document_id, PROMPT_VERSION, models)
        errors = tuple(f"pass {v} failed: {e}" for v, (_, e) in sorted(answers.items()) if e)
        result = assess(
            row.organ,
            ocr,
            sent,
            (answers.get("a", (None, None))[0], answers.get("b", (None, None))[0]),
            store.procedure_date(conn, row.procedure_occurrence_id),
            threshold,
            errors,
        )
        status = finish_assessment(conn, document_id, result, qc_sample(row.sha256, qc_rate))
        audit.record(
            conn,
            info(workflow_id),
            None,
            "document_assess",
            "document",
            document_id,
            {"status": status, "confidence": result.decision.confidence},
        )
    return status


def qc_sample(sha256: str, rate: float) -> bool:
    """True for a fixed, content-independent share of files (decided by the file hash)."""
    return int(sha256[:8], 16) % 10_000 < round(rate * 10_000)


def finish_assessment(
    conn: Connection, document_id: str, result: Assessment, qc: bool = False
) -> str:
    """Store fields and OMOP rows; mapping problems also send the document to review.

    A report that would be auto-approved but is in the QC sample goes to review too, so the
    auto-approve error rate keeps being measured.
    """
    rows, problems = cdm_rows(conn, result.organ, result.values)
    reasons = [*result.decision.reasons, *problems]
    if not reasons and qc:
        reasons = [QC_REASON]
    status = "in_review" if reasons else "extracted"
    store.save_fields(conn, document_id, result)
    store.save_cdm_rows(conn, document_id, rows)
    store.set_status(conn, document_id, status, reasons, result.decision.confidence)
    return status


def load_document(engine: Engine, tenant_id: str, document_id: str, workflow_id: str) -> int:
    """Copy an extracted or approved document's rows into cdm; rows written."""
    with tenant_transaction(engine, tenant_id) as conn:
        written = store.load_into_cdm(conn, document_id)
        audit.record(
            conn,
            info(workflow_id),
            None,
            "document_load",
            "document",
            document_id,
            {"rows": written},
        )
    return written


def checked_changes(organ: Organ, changes: Mapping[str, str | None]) -> dict[str, Any]:
    """A reviewer's edits as typed values; refuses unknown fields and disallowed values."""
    schema = SCHEMAS[organ]
    typed: dict[str, Any] = {}
    for name, raw in changes.items():
        if name not in schema.model_fields:
            raise InvalidEditError(f"unknown field: {name}")
        value_type = schema.model_fields[name].annotation.model_fields["value"].annotation  # type: ignore[union-attr]
        try:
            typed[name] = TypeAdapter(value_type).validate_python(raw)
        except ValidationError as error:
            raise InvalidEditError(f"{name}: {error.errors()[0]['msg']}") from error
    return typed


def apply_review(
    engine: Engine, tenant_id: str, document_id: str, decision: ReviewDecision, workflow_id: str
) -> str:
    """Apply a human decision: approve (as is or edited) loads into cdm; reject removes."""
    with tenant_transaction(engine, tenant_id) as conn:
        row = store.get(conn, document_id)
        organ = row.organ or "kidney"
        changes = checked_changes(organ, decision.changes) if decision.action == "edit" else {}
        store.save_review(
            conn, document_id, decision.reviewer_id, decision.action, changes, decision.note
        )
        if decision.action == "reject":
            store.set_status(conn, document_id, "rejected")
            store.unload(conn, document_id)
        else:
            store.set_values(conn, document_id, changes)
            values = store.field_values(conn, document_id)
            if organ == "heart":
                values["diagnosis"] = heart_diagnosis(
                    values.get("acr_grade"), values.get("pamr_grade")
                )
                store.set_values(conn, document_id, {"diagnosis": values["diagnosis"]})
            rows, problems = cdm_rows(conn, organ, values)
            warnings = [
                *validate(organ, values, store.procedure_date(conn, row.procedure_occurrence_id)),
                *problems,
            ]
            store.save_cdm_rows(conn, document_id, rows)
            store.set_status(conn, document_id, "approved", warnings)
            store.load_into_cdm(conn, document_id)
        audit.record(
            conn,
            info(decision.request_id),
            decision.reviewer_id,
            f"document_review_{decision.action}",
            "document",
            document_id,
            {"workflow_id": workflow_id, "fields": sorted(changes),
             "reason_code": decision.reason_code},
        )  # fmt: skip
    return "rejected" if decision.action == "reject" else "approved"


def expire_review(engine: Engine, tenant_id: str, document_id: str, workflow_id: str) -> str:
    """Nobody reviewed in time: nothing is loaded."""
    with tenant_transaction(engine, tenant_id) as conn:
        store.set_status(conn, document_id, "review_expired")
        audit.record(
            conn, info(workflow_id), None, "document_review_expired", "document", document_id
        )
    return "review_expired"


def mark_failed(
    engine: Engine, tenant_id: str, document_id: str, reason: str, workflow_id: str
) -> None:
    """Record that processing stopped (e.g. the batch budget ran out)."""
    with tenant_transaction(engine, tenant_id) as conn:
        store.set_status(conn, document_id, "failed", [reason])
        audit.record(
            conn,
            info(workflow_id),
            None,
            "document_failed",
            "document",
            document_id,
            {"reason": reason},
        )
