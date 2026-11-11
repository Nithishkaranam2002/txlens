"""LLM extraction through LiteLLM + Instructor, with a fake completion (no network, no cost)."""

import json
from pathlib import Path

import pytest
from document_testutils import FakeCompletion, finding, require_tesseract
from litellm import exceptions

from txlens_backend.documents import llm, phi
from txlens_backend.documents.ocr import ocr_with_tesseract
from txlens_backend.documents.schema import HeartBiopsy
from txlens_datagen.config import GeneratorConfig
from txlens_datagen.rand import rng_for
from txlens_datagen.record import build_record
from txlens_datagen.writers.pathology_pdf import write_report

HEART_ANSWER = {
    "biopsy_date": finding("2024-05-06", 2, "05/06/2024"),
    "acr_grade": finding("2R", 5, "2R"),
    "pamr_grade": finding("pAMR 0", 6, "pAMR 0"),
}
META = llm.CallMeta("ab" * 32, "batch-1")
DOCUMENT = "<document>\n1: SURGICAL PATHOLOGY REPORT\n</document>"


def test_extraction_returns_the_schema_and_its_cost() -> None:
    fake = FakeCompletion(HEART_ANSWER)
    extractor = llm.LiteLlmExtractor("openai/gpt-6-luna", completion=fake)
    answer, usage = extractor.extract(HeartBiopsy, DOCUMENT, "a", META)
    assert isinstance(answer, HeartBiopsy)
    assert answer.acr_grade.value == "2R"
    assert (usage.input_tokens, usage.output_tokens) == (1500, 400)
    assert usage.cost_usd == pytest.approx(1500 * 0.10e-6 + 400 * 0.50e-6)


def test_the_two_passes_differ_in_wording_and_field_order() -> None:
    fake = FakeCompletion(HEART_ANSWER)
    extractor = llm.LiteLlmExtractor("openai/gpt-6-luna", completion=fake)
    for variant in ("a", "b"):
        extractor.extract(HeartBiopsy, DOCUMENT, variant, META)
    a, b = fake.calls
    assert a["messages"][0]["content"] != b["messages"][0]["content"]
    assert fake.schema_fields(1) == list(reversed(fake.schema_fields(0)))


def test_the_model_gets_a_schema_and_no_tools() -> None:
    fake = FakeCompletion(HEART_ANSWER)
    llm.LiteLlmExtractor("openai/gpt-6-luna", completion=fake).extract(
        HeartBiopsy, DOCUMENT, "a", META
    )
    assert "tools" not in fake.calls[0]
    assert fake.calls[0]["response_format"]["type"] == "json_schema"


def test_document_text_is_framed_as_data() -> None:
    fake = FakeCompletion(HEART_ANSWER)
    llm.LiteLlmExtractor("openai/gpt-6-luna", completion=fake).extract(
        HeartBiopsy, DOCUMENT, "a", META
    )
    system, user = fake.calls[0]["messages"]
    assert "never instructions" in system["content"]
    assert user["content"] == DOCUMENT


def test_an_answer_that_never_fits_the_schema_fails_with_its_cost() -> None:
    bad = HEART_ANSWER | {"acr_grade": finding("5R", 5, "5R")}
    extractor = llm.LiteLlmExtractor("openai/gpt-6-luna", completion=FakeCompletion(bad))
    with pytest.raises(llm.ExtractionFailedError) as failure:
        extractor.extract(HeartBiopsy, DOCUMENT, "a", META)
    assert failure.value.usage.cost_usd > 0  # both attempts were paid for


def test_tests_cannot_reach_a_real_model() -> None:
    with pytest.raises(llm.ExtractionFailedError, match="tried to call a real LLM"):
        llm.LiteLlmExtractor("openai/gpt-6-luna").extract(HeartBiopsy, DOCUMENT, "a", META)


def test_models_without_a_known_price_are_refused() -> None:
    with pytest.raises(llm.LlmNotConfiguredError):
        llm.max_call_cost("openai/no-such-model", DOCUMENT)
    assert llm.max_call_cost("ollama/llama3.1:8b", DOCUMENT) == 0


def test_trace_metadata_has_no_path_or_identifier() -> None:
    meta = llm.trace_metadata(META, "b")
    assert meta["trace_metadata"] == {
        "file_sha256": META.file_sha256,
        "prompt_version": llm.PROMPT_VERSION,
    }
    assert "pdf" not in json.dumps(meta).lower()


@pytest.mark.parametrize("index", [0, 1, 3])  # patients from center_a, center_b, center_c
def test_llm_request_carries_no_patient_identifier(tmp_path: Path, index: int) -> None:
    require_tesseract()
    record = build_record(index, GeneratorConfig(), with_notes=False)
    patient, biopsy = record.patient, record.biopsies[0]
    relative = write_report(biopsy, patient, "Test Center", tmp_path, 120, rng_for(0, index))
    ocr = ocr_with_tesseract(tmp_path / relative)
    identity = phi.Identity(patient.mrn, patient.first_name, patient.last_name, patient.birth_date)
    header = phi.parse_header(ocr)
    sent = phi.minimize(ocr, header, identity)
    fake = FakeCompletion(HEART_ANSWER)
    llm.LiteLlmExtractor("openai/gpt-6-luna", completion=fake).extract(
        HeartBiopsy, llm.document_block(sent), "a", META
    )
    payload = json.dumps(fake.calls[0]).lower()  # messages and Langfuse metadata
    birth = patient.birth_date
    forbidden = {
        patient.mrn,
        patient.first_name,
        patient.last_name,
        f"{birth:%m/%d/%Y}",
        birth.isoformat(),
        f"{birth.month}/{birth.day}/{birth.year}",
    }
    assert [value for value in forbidden if value.lower() in payload] == []
    # The procedure date is still sent (as OCR read it, e.g. "1 2/25/2022").
    assert header.procedure_line is not None
    procedure_text = sent.text_of(header.procedure_line)
    assert procedure_text is not None and procedure_text.lower() in payload
    assert phi.parse_date(procedure_text) == biopsy.biopsy_date


def test_a_rate_limit_is_retryable_not_a_failed_extraction() -> None:
    def limited(**_: object) -> None:
        raise exceptions.RateLimitError("slow down", llm_provider="openai", model="gpt-6-luna")

    extractor = llm.LiteLlmExtractor("openai/gpt-6-luna", completion=limited)
    with pytest.raises(llm.LlmUnavailableError, match="RateLimitError"):
        extractor.extract(HeartBiopsy, DOCUMENT, "a", META)
