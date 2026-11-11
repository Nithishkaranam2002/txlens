"""Document processing steps against a real database (RLS on), with OCR and the LLM faked.

OCR is replaced by fixed lines so results do not depend on the Tesseract version; OCR itself
is tested on real PDFs in test_documents_ocr.py.
"""

import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import pytest
from document_testutils import (
    HEART_ANSWER,
    HEART_OCR_LINES,
    KIDNEY_ANSWER,
    KIDNEY_OCR_LINES,
    MODEL,
    MODELS,
    FakeCompletion,
    finding,
)
from sqlalchemy import Engine, text

from txlens_backend.documents import llm, service
from txlens_backend.documents.budget import Budget, BudgetExceededError

pytestmark = pytest.mark.db


def process(
    engine: Engine, path: Path, *answers: dict[str, Any]
) -> tuple[str, str, FakeCompletion]:
    """Register, read, run both passes, assess: (document id, status, fake model)."""
    workflow = f"wf-{uuid.uuid4()}"
    doc = service.register_document(engine, "center_a", path, path.name, workflow)
    service.read_document(engine, "center_a", doc.document_id, path, "tesseract", workflow)
    budget = Budget(engine, f"svc-{uuid.uuid4()}", 1.0)
    budget.open()
    fake = FakeCompletion(*answers)
    extractor = llm.LiteLlmExtractor(MODEL, completion=fake)
    for variant in ("a", "b"):
        service.extraction_pass(
            engine, "center_a", doc.document_id, extractor, budget, variant, workflow
        )
    status = service.assess_document(engine, "center_a", doc.document_id, MODELS, 0.85, workflow)
    return doc.document_id, status, fake


def cdm_rows(owner: Engine, document_id: str) -> dict[str, list[tuple[Any, ...]]]:
    """The document's rows in cdm: conditions and measurements."""
    with owner.connect() as conn:
        ids = [r[0] for r in conn.execute(
            text("SELECT cdm_id FROM app.document_cdm_row WHERE document_id = :d"),
            {"d": document_id})]  # fmt: skip
        conditions = conn.execute(text(
            "SELECT condition_concept_id, condition_source_concept_id, condition_source_value, "
            "condition_type_concept_id, person_id FROM cdm.condition_occurrence "
            "WHERE condition_occurrence_id = ANY(:i) ORDER BY 1"), {"i": ids}).all()  # fmt: skip
        measurements = conn.execute(text(
            "SELECT measurement_source_value, value_as_number, value_source_value, "
            "measurement_event_id FROM cdm.measurement WHERE measurement_id = ANY(:i) "
            "ORDER BY 1"), {"i": ids}).all()  # fmt: skip
    return {
        "conditions": [tuple(r) for r in conditions],
        "measurements": [tuple(r) for r in measurements],
    }


def test_an_agreeing_confident_report_is_extracted_and_loaded(
    clinic: None, report: Any, documents_engine: Engine, documents_owner: Engine
) -> None:
    document_id, status, _ = process(documents_engine, report(KIDNEY_OCR_LINES), KIDNEY_ANSWER)
    assert status == "extracted"
    assert service.load_document(documents_engine, "center_a", document_id, "wf") == 11
    rows = cdm_rows(documents_owner, document_id)
    assert rows["conditions"] == [(900010, 0, "TCMR IA", 32858, 1)]  # grade I, type NLP
    measured = {r[0]: (r[1], r[2], r[3]) for r in rows["measurements"]}
    assert measured["Banff t"] == (2, "2", 101)  # linked to the biopsy procedure
    assert measured["Banff adequacy"] == (None, "adequate", 101)
    assert len(measured) == 10  # i t v g ptc ci ct cv C4d adequacy


def test_the_llm_never_sees_identifiers_from_patient_identity(
    clinic: None, report: Any, documents_engine: Engine, documents_owner: Engine
) -> None:
    _, _, fake = process(documents_engine, report(KIDNEY_OCR_LINES), KIDNEY_ANSWER)
    with documents_owner.connect() as conn:
        identity = conn.execute(text(
            "SELECT mrn, given_name, family_name, birth_date FROM app.patient_identity "
            "WHERE person_id = 1")).one()  # fmt: skip
    payload = json.dumps(fake.calls).lower()
    for value in (identity.mrn, identity.given_name, identity.family_name,
                  f"{identity.birth_date:%m/%d/%Y}", identity.birth_date.isoformat()):  # fmt: skip
        assert value.lower() not in payload


def test_fields_keep_their_evidence_and_provenance(
    clinic: None, report: Any, documents_engine: Engine, documents_owner: Engine
) -> None:
    document_id, _, _ = process(documents_engine, report(KIDNEY_OCR_LINES), KIDNEY_ANSWER)
    with documents_owner.connect() as conn:
        field = conn.execute(text(
            "SELECT value, page, bbox, line_no, source_text, passes_agree FROM "
            "app.extracted_field WHERE document_id = :d AND field = 'banff_t'"),
            {"d": document_id}).one()  # fmt: skip
        passes = conn.execute(text(
            "SELECT pass, model, prompt_version FROM app.extraction WHERE document_id = :d "
            "ORDER BY pass"), {"d": document_id}).all()  # fmt: skip
        actions = conn.execute(text(
            "SELECT action FROM app.audit_log WHERE object_id = :d ORDER BY audit_id"),
            {"d": document_id}).scalars().all()  # fmt: skip
    assert (field.value, field.page, field.line_no, field.source_text) == ("2", 1, 7, "t2")
    assert field.bbox == [40.0, 140.0, 48.0, 149.0] and field.passes_agree
    assert [tuple(p) for p in passes] == [
        ("a", MODEL, llm.PROMPT_VERSION),
        ("b", MODEL, llm.PROMPT_VERSION),
    ]
    assert actions == ["document_register", "document_read", "document_extract",
                       "document_extract", "document_assess"]  # fmt: skip


def test_disagreeing_passes_wait_for_review_and_load_nothing(
    clinic: None, report: Any, documents_engine: Engine, documents_owner: Engine
) -> None:
    other = KIDNEY_ANSWER | {"banff_t": finding(3, 7, "t2")}  # pass B's evidence fails
    document_id, status, _ = process(
        documents_engine, report(KIDNEY_OCR_LINES), KIDNEY_ANSWER, other
    )
    assert status == "in_review"
    assert service.load_document(documents_engine, "center_a", document_id, "wf") == 0
    assert cdm_rows(documents_owner, document_id) == {"conditions": [], "measurements": []}
    with documents_owner.connect() as conn:
        reasons = conn.execute(
            text("SELECT review_reasons FROM app.document WHERE document_id = :d"),
            {"d": document_id},
        ).scalar_one()
    assert any("passes disagree (A: 2, B: None)" in r for r in reasons)


def test_low_ocr_confidence_goes_to_review(
    clinic: None, report: Any, documents_engine: Engine
) -> None:
    _, status, _ = process(
        documents_engine, report(KIDNEY_OCR_LINES, confidence=0.5), KIDNEY_ANSWER
    )
    assert status == "in_review"


def test_an_unknown_mrn_is_not_linked_and_goes_to_review(
    clinic: None, report: Any, documents_engine: Engine
) -> None:
    lines = tuple(t.replace("A1000123", "Z9999999") for t in KIDNEY_OCR_LINES)
    _, status, _ = process(documents_engine, report(lines), KIDNEY_ANSWER)
    assert status == "in_review"


def review(
    engine: Engine,
    document_id: str,
    action: Literal["approve", "edit", "reject"],
    changes: Mapping[str, str | None] | None = None,
) -> str:
    reviewer = "00000000-0000-0000-0000-0000000000a1"
    decision = service.ReviewDecision(action, reviewer, "req-1", dict(changes or {}))
    return service.apply_review(engine, "center_a", document_id, decision, "wf")


def test_an_edited_review_loads_the_corrected_values(
    clinic: None, reviewer: None, report: Any, documents_engine: Engine, documents_owner: Engine
) -> None:
    other = KIDNEY_ANSWER | {"banff_t": finding(3, 7, "t2")}
    document_id, _, _ = process(documents_engine, report(KIDNEY_OCR_LINES), KIDNEY_ANSWER, other)
    changes = {"banff_t": "3", "tcmr_grade": "IB"}
    assert review(documents_engine, document_id, "edit", changes) == "approved"
    rows = cdm_rows(documents_owner, document_id)
    assert rows["conditions"] == [(900010, 0, "TCMR IB", 32858, 1)]
    assert ("Banff t", 3, "3", 101) in rows["measurements"]


def test_a_rejected_review_removes_everything_from_cdm(
    clinic: None, reviewer: None, report: Any, documents_engine: Engine, documents_owner: Engine
) -> None:
    document_id, _, _ = process(documents_engine, report(KIDNEY_OCR_LINES), KIDNEY_ANSWER)
    service.load_document(documents_engine, "center_a", document_id, "wf")
    assert review(documents_engine, document_id, "reject") == "rejected"
    assert cdm_rows(documents_owner, document_id) == {"conditions": [], "measurements": []}


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"banff_t": "7"}, "banff_t"),
        ({"tcmr_grade": "IV"}, "tcmr_grade"),
        ({"nope": "1"}, "unknown field"),
    ],
)
def test_invalid_edits_are_refused(changes: dict[str, str | None], message: str) -> None:
    with pytest.raises(service.InvalidEditError, match=message):
        service.checked_changes("kidney", changes)


def test_heart_diagnosis_is_derived_from_the_grades(
    clinic: None, report: Any, documents_engine: Engine, documents_owner: Engine
) -> None:
    document_id, status, _ = process(documents_engine, report(HEART_OCR_LINES), HEART_ANSWER)
    assert status == "extracted"
    service.load_document(documents_engine, "center_a", document_id, "wf")
    rows = cdm_rows(documents_owner, document_id)
    assert rows["conditions"] == [(900015, 0, "ACR 2R", 32858, 2)]
    assert rows["measurements"] == [
        ("ISHLT ACR grade", 2, "2R", 102),
        ("ISHLT pAMR grade", 0, "pAMR 0", 102),
    ]


def test_the_same_file_is_the_same_document(
    clinic: None, report: Any, documents_engine: Engine
) -> None:
    path = report(KIDNEY_OCR_LINES)
    first = service.register_document(documents_engine, "center_a", path, "x.pdf", "wf-1")
    second = service.register_document(documents_engine, "center_a", path, "x.pdf", "wf-2")
    assert first.document_id == second.document_id


def test_an_exhausted_budget_stops_the_pass(
    clinic: None, report: Any, documents_engine: Engine
) -> None:
    path = report(KIDNEY_OCR_LINES)
    doc = service.register_document(documents_engine, "center_a", path, "x.pdf", "wf")
    service.read_document(documents_engine, "center_a", doc.document_id, path, "tesseract", "wf")
    budget = Budget(documents_engine, f"svc-{uuid.uuid4()}", 0.0001)
    budget.open()
    extractor = llm.LiteLlmExtractor(MODEL, completion=FakeCompletion(KIDNEY_ANSWER))
    with pytest.raises(BudgetExceededError):
        service.extraction_pass(
            documents_engine, "center_a", doc.document_id, extractor, budget, "a", "wf"
        )


def test_an_mrn_damaged_by_ocr_still_links_to_the_only_close_patient(
    clinic: None, report: Any, documents_engine: Engine, documents_owner: Engine
) -> None:
    lines = tuple(t.replace("A1000123", "A1O00123") for t in KIDNEY_OCR_LINES)  # O for 0
    document_id, status, _ = process(documents_engine, report(lines), KIDNEY_ANSWER)
    assert status == "extracted"
    with documents_owner.connect() as conn:
        linked = conn.execute(
            text(
                "SELECT person_id, procedure_occurrence_id FROM app.document WHERE document_id = :d"
            ),
            {"d": document_id},
        ).one()
    assert tuple(linked) == (1, 101)
