"""Document processing steps against a real database (RLS on), with OCR and the LLM faked.

OCR is replaced by fixed lines so results do not depend on the Tesseract version; OCR itself
is tested on real PDFs in test_documents_ocr.py.
"""

import csv
import json
import uuid
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any, Literal

import pytest
from document_testutils import FakeCompletion, finding
from sqlalchemy import Engine, text

from txlens_backend.documents import llm, service
from txlens_backend.documents.budget import Budget, BudgetExceededError
from txlens_backend.documents.ocr import Line, OcrResult, PageSize, Word
from txlens_omop.mappings import LOCAL_CONCEPT_FILE, mappings_dir

pytestmark = pytest.mark.db
MODEL = "openai/gpt-6-luna"
MODELS = {"a": MODEL, "b": MODEL}
# TEST ONLY concept ids for the standard codes the workflow uses.
SNOMED_IDS = {"1293269004": 900001, "387829002": 900002, "236575009": 900010,
              "236576005": 900011, "236577001": 900012, "236574008": 900013,
              "1381981002": 900014, "432843002": 900015, "713886006": 900016,
              "35455006": 900017, "28944009": 900018}  # fmt: skip
KIDNEY_LINES = (
    "TEST ONLY MEDICAL CENTER",
    "Patient: ELMDALE, BRANNOC",
    "MRN: A1000123 DOB: 02/08/1964 Sex: M",
    "Procedure date: 05/06/2024 Reported: 05/08/2024",
    "Specimen: Kidney allograft, percutaneous needle core biopsy",
    "Specimen adequacy: adequate",
    "Banff lesion scores: i2 t2 v0 g0 ptc0 ci1 ct1 cv0",
    "C4d (IHC, peritubular capillaries): C4d0",
    "- Acute T-cell mediated rejection, Banff grade IA.",
    "Electronically signed: Testa Reviewer, MD",
)
KIDNEY_ANSWER = {
    "biopsy_date": finding("2024-05-06", 4, "05/06/2024"),
    "adequacy": finding("adequate", 6, "adequate"),
    "banff_i": finding(2, 7, "i2"),
    "banff_t": finding(2, 7, "t2"),
    "banff_v": finding(0, 7, "v0"),
    "banff_g": finding(0, 7, "g0"),
    "banff_ptc": finding(0, 7, "ptc0"),
    "banff_ci": finding(1, 7, "ci1"),
    "banff_ct": finding(1, 7, "ct1"),
    "banff_cv": finding(0, 7, "cv0"),
    "c4d": finding(0, 8, "C4d0"),
    "diagnosis": finding("TCMR", 9, "Acute T-cell mediated rejection"),
    "tcmr_grade": finding("IA", 9, "grade IA."),
    "additional_finding": finding(None),
}
HEART_LINES = (
    "Patient: TESTER, HEARTLY",
    "MRN: A1000456 DOB: 01/01/1950",
    "Procedure date: 07/01/2024 Reported: 07/02/2024",
    "Specimen: Endomyocardial biopsy, right ventricle",
    "ACR grade (ISHLT 2004): 2R",
    "pAMR grade (ISHLT 2013): pAMR 0",
)
HEART_ANSWER = {
    "biopsy_date": finding("2024-07-01", 3, "07/01/2024"),
    "acr_grade": finding("2R", 5, "2R"),
    "pamr_grade": finding("pAMR 0", 6, "pAMR 0"),
}


def ocr_of(lines: tuple[str, ...], confidence: float = 0.95) -> OcrResult:
    built = tuple(
        Line(n, 1, tuple(Word(w, (10.0 * i, 20.0 * n, 10.0 * i + 8, 20.0 * n + 9), confidence)
                         for i, w in enumerate(t.split())))
        for n, t in enumerate(lines, start=1)
    )  # fmt: skip
    return OcrResult("tesseract", (PageSize(612, 792),), built)


@pytest.fixture(scope="module")
def clinic(documents_owner: Engine) -> None:
    """Vocabulary rows, and two center_a patients with one biopsy each (committed)."""
    with documents_owner.begin() as conn:
        rows: list[tuple[int, str, str, str | None, str]] = [
            (cid, code, "SNOMED", "S", "Condition") for code, cid in SNOMED_IDS.items()
        ]
        with (mappings_dir() / LOCAL_CONCEPT_FILE).open(encoding="utf-8") as handle:
            rows += [(int(r["concept_id"]), r["concept_code"], "TxLens", None, r["domain_id"])
                     for r in csv.DictReader(handle, delimiter="\t")]  # fmt: skip
        for cid, code, vocabulary, standard, domain in rows:
            conn.execute(
                text(
                    "INSERT INTO vocab.concept VALUES (:i, 'TEST ONLY', :d, :v, 'x', :s, :c, "
                    "'2020-01-01', '2099-12-31', NULL) ON CONFLICT DO NOTHING"
                ),
                {"i": cid, "d": domain, "v": vocabulary, "s": standard, "c": code},
            )
        for person, mrn, names, born, procedure, day, concept in (
            (1, "A1000123", ("Brannoc", "Elmdale"), date(1964, 2, 8), 101, date(2024, 5, 6),
             900001),
            (2, "A1000456", ("Heartly", "Tester"), date(1950, 1, 1), 102, date(2024, 7, 1),
             900002),
        ):  # fmt: skip
            params = {"p": person, "m": mrn, "g": names[0], "f": names[1], "b": born,
                      "r": procedure, "d": day, "c": concept}  # fmt: skip
            for statement in (
                "INSERT INTO cdm.person (tenant_id, person_id, gender_concept_id, year_of_birth, "
                "race_concept_id, ethnicity_concept_id) VALUES ('center_a', :p, 0, 1960, 0, 0)",
                "INSERT INTO app.patient_identity (tenant_id, person_id, mrn, given_name, "
                "family_name, birth_date) VALUES ('center_a', :p, :m, :g, :f, :b)",
                "INSERT INTO cdm.procedure_occurrence (tenant_id, procedure_occurrence_id, "
                "person_id, procedure_concept_id, procedure_date, procedure_type_concept_id) "
                "VALUES ('center_a', :r, :p, :c, :d, 32817)",
            ):
                conn.execute(text(statement), params)


@pytest.fixture
def report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """`report(lines)`: a new file (its own hash) whose OCR reads as those lines."""
    readings: dict[Path, OcrResult] = {}
    monkeypatch.setattr(service, "ocr_pdf", lambda path, _engine: readings[path])

    def make(lines: tuple[str, ...], confidence: float = 0.95) -> Path:
        path = tmp_path / f"{uuid.uuid4()}.pdf"
        path.write_bytes(uuid.uuid4().bytes)
        readings[path] = ocr_of(lines, confidence)
        return path

    yield make


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
    document_id, status, _ = process(documents_engine, report(KIDNEY_LINES), KIDNEY_ANSWER)
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
    _, _, fake = process(documents_engine, report(KIDNEY_LINES), KIDNEY_ANSWER)
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
    document_id, _, _ = process(documents_engine, report(KIDNEY_LINES), KIDNEY_ANSWER)
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
    document_id, status, _ = process(documents_engine, report(KIDNEY_LINES), KIDNEY_ANSWER, other)
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
    _, status, _ = process(documents_engine, report(KIDNEY_LINES, confidence=0.5), KIDNEY_ANSWER)
    assert status == "in_review"


def test_an_unknown_mrn_is_not_linked_and_goes_to_review(
    clinic: None, report: Any, documents_engine: Engine
) -> None:
    lines = tuple(t.replace("A1000123", "Z9999999") for t in KIDNEY_LINES)
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


@pytest.fixture
def reviewer(documents_owner: Engine) -> None:
    with documents_owner.begin() as conn:
        conn.execute(text(
            "INSERT INTO app.app_user (user_id, tenant_id, username, display_name, role, "
            "password_hash) VALUES ('00000000-0000-0000-0000-0000000000a1', 'center_a', "
            "'steward.a', 'Steward', 'data_steward', 'x') ON CONFLICT DO NOTHING"))  # fmt: skip


def test_an_edited_review_loads_the_corrected_values(
    clinic: None, reviewer: None, report: Any, documents_engine: Engine, documents_owner: Engine
) -> None:
    other = KIDNEY_ANSWER | {"banff_t": finding(3, 7, "t2")}
    document_id, _, _ = process(documents_engine, report(KIDNEY_LINES), KIDNEY_ANSWER, other)
    changes = {"banff_t": "3", "tcmr_grade": "IB"}
    assert review(documents_engine, document_id, "edit", changes) == "approved"
    rows = cdm_rows(documents_owner, document_id)
    assert rows["conditions"] == [(900010, 0, "TCMR IB", 32858, 1)]
    assert ("Banff t", 3, "3", 101) in rows["measurements"]


def test_a_rejected_review_removes_everything_from_cdm(
    clinic: None, reviewer: None, report: Any, documents_engine: Engine, documents_owner: Engine
) -> None:
    document_id, _, _ = process(documents_engine, report(KIDNEY_LINES), KIDNEY_ANSWER)
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
    document_id, status, _ = process(documents_engine, report(HEART_LINES), HEART_ANSWER)
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
    path = report(KIDNEY_LINES)
    first = service.register_document(documents_engine, "center_a", path, "x.pdf", "wf-1")
    second = service.register_document(documents_engine, "center_a", path, "x.pdf", "wf-2")
    assert first.document_id == second.document_id


def test_an_exhausted_budget_stops_the_pass(
    clinic: None, report: Any, documents_engine: Engine
) -> None:
    path = report(KIDNEY_LINES)
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
    lines = tuple(t.replace("A1000123", "A1O00123") for t in KIDNEY_LINES)  # O for 0
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
