"""Schema, validation rules and confidence: pure functions, checked against generator truth."""

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest

from txlens_backend.documents import validate
from txlens_backend.documents.confidence import FieldResult, combine, decide
from txlens_backend.documents.evidence import VerifiedField
from txlens_backend.documents.schema import (
    BANFF_SCORES,
    HeartBiopsy,
    KidneyBiopsy,
    heart_diagnosis,
)
from txlens_datagen.config import GeneratorConfig
from txlens_datagen.models import Biopsy
from txlens_datagen.record import build_record

ADDITIONAL = {"Polyomavirus": "BK_nephropathy", "CMV": "CMV", "Acute tubular": "ATN"}


def generated_biopsies(n_patients: int = 400) -> Iterator[Biopsy]:
    config = GeneratorConfig()
    for index in range(n_patients):
        yield from build_record(index, config, with_notes=False).biopsies


def kidney_values(b: Biopsy) -> dict[str, Any]:
    finding = next((v for k, v in ADDITIONAL.items() if b.other_finding.startswith(k)), None)
    values: dict[str, Any] = {f: getattr(b, f) for f in BANFF_SCORES}
    return values | {
        "biopsy_date": b.biopsy_date,
        "c4d": b.c4d,
        "diagnosis": b.diagnosis,
        "tcmr_grade": b.banff_tcmr_grade or None,
        "additional_finding": finding,
    }


def test_no_rule_fires_on_correct_generator_truth() -> None:
    checked = 0
    for b in generated_biopsies():
        if b.organ == "kidney":
            assert validate.validate("kidney", kidney_values(b), b.biopsy_date) == [], b
            checked += 1
    assert checked > 50


def test_heart_diagnosis_follows_from_the_ishlt_grades() -> None:
    hearts = [b for b in generated_biopsies() if b.organ == "heart"]
    assert {b.diagnosis for b in hearts} >= {"no_rejection", "TCMR"}
    for b in hearts:
        assert heart_diagnosis(b.ishlt_acr_grade, b.pamr_grade) == b.diagnosis  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("acr", "pamr", "expected"),
    [
        ("0R", "pAMR 0", "no_rejection"),
        ("1R", "pAMR 0", "borderline"),
        ("2R", "pAMR 0", "TCMR"),
        ("3R", "pAMR 0", "TCMR"),
        ("1R", "pAMR 1 (I+)", "ABMR"),
        ("2R", "pAMR 2", "mixed"),
        (None, "pAMR 0", None),
    ],
)
def test_heart_diagnosis_rule(acr: Any, pamr: Any, expected: str | None) -> None:
    assert heart_diagnosis(acr, pamr) == expected


def test_schemas_forbid_extra_fields_and_describe_null() -> None:
    for schema in (KidneyBiopsy, HeartBiopsy):
        spec = schema.model_json_schema()
        assert spec["additionalProperties"] is False
    with pytest.raises(ValueError, match="less than or equal to 3"):
        KidneyBiopsy.model_validate(
            {f: {"value": None, "evidence": None} for f in KidneyBiopsy.model_fields}
            | {"banff_t": {"value": 4, "evidence": None}}
        )


TCMR_IA = {
    "biopsy_date": date(2024, 5, 6),
    "diagnosis": "TCMR",
    "tcmr_grade": "IA",
    "banff_i": 2,
    "banff_t": 2,
    "banff_v": 0,
    "banff_g": 0,
    "banff_ptc": 0,
    "banff_ci": 1,
    "banff_ct": 1,
    "c4d": 0,
}


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"banff_t": 3}, "do not meet TCMR grade IA"),
        ({"tcmr_grade": None}, "TCMR without a Banff TCMR grade"),
        ({"diagnosis": "no_rejection", "tcmr_grade": None}, "no rejection reported with"),
        ({"diagnosis": "ABMR", "tcmr_grade": None}, "ABMR with g+ptc = 0 and C4d0"),
        ({"diagnosis": "borderline", "tcmr_grade": None}, "do not fit borderline"),
        ({"diagnosis": "borderline"}, "TCMR grade IA given for diagnosis borderline"),
        ({"banff_g": None}, "banff_g is missing"),
        ({"biopsy_date": date(2024, 6, 1)}, "26 days from the procedure"),
    ],
)
def test_each_rule_catches_its_inconsistency(changes: dict[str, Any], problem: str) -> None:
    assert validate.validate("kidney", TCMR_IA, date(2024, 5, 6)) == []
    problems = validate.validate("kidney", TCMR_IA | changes, date(2024, 5, 6))
    assert any(problem in p for p in problems), problems


def test_bk_nephropathy_explains_tcmr_level_scores() -> None:
    values = TCMR_IA | {"diagnosis": "no_rejection", "tcmr_grade": None}
    assert validate.validate("kidney", values | {"additional_finding": "BK_nephropathy"}, None) == [
        "not linked to a biopsy procedure"
    ]


def test_dates_within_seven_days_pass() -> None:
    day = date(2024, 5, 6)
    assert validate.date_problems(day + timedelta(days=7), day) == []
    assert validate.date_problems(day - timedelta(days=8), day) != []


def field(name: str, value: Any, ocr: float | None = 0.9, *problems: str) -> VerifiedField:
    return VerifiedField(name, value, value, 1, 1, (0, 0, 1, 1), "t", ocr, problems)


def test_agreeing_passes_keep_the_ocr_confidence() -> None:
    result = combine(field("banff_t", 2, 0.8), field("banff_t", 2, 0.95), mean_ocr=0.9)
    assert (result.verified.value, result.passes_agree, result.confidence) == (2, True, 0.8)


def test_disagreeing_passes_get_zero_confidence_and_a_reason() -> None:
    result = combine(field("banff_t", 2), field("banff_t", 3), mean_ocr=0.9)
    assert (result.verified.value, result.confidence) == (2, 0.0)
    assert result.verified.problems == ("passes disagree (A: 2, B: 3)",)


def test_pass_b_is_used_when_pass_a_evidence_failed() -> None:
    a = VerifiedField("banff_t", None, 2, None, None, None, None, None, ("quote not found",))
    result = combine(a, field("banff_t", 2), mean_ocr=0.9)
    assert result.verified.value == 2
    assert result.confidence == 0.0  # A said nothing usable: not an agreement
    assert "quote not found" in result.verified.problems


def test_both_not_reported_take_the_mean_ocr_confidence() -> None:
    result = combine(field("banff_cv", None, None), field("banff_cv", None, None), mean_ocr=0.7)
    assert (result.verified.value, result.confidence) == (None, 0.7)


def results(**confidences: float) -> dict[str, FieldResult]:
    return {k: FieldResult(field(k, 1, c), True, c) for k, c in confidences.items()}


def test_document_confidence_is_the_weakest_key_field() -> None:
    fields = results(biopsy_date=0.95, acr_grade=0.9, pamr_grade=0.6)
    decision = decide("heart", fields, [], threshold=0.85)
    assert decision.confidence == 0.6
    assert decision.needs_review
    assert decision.reasons == ("confidence 0.60 is below 0.85",)


def test_validation_problems_send_a_confident_document_to_review() -> None:
    fields = results(biopsy_date=0.95, acr_grade=0.95, pamr_grade=0.95)
    assert not decide("heart", fields, [], threshold=0.85).needs_review
    decision = decide("heart", fields, ["not linked to a biopsy procedure"], threshold=0.85)
    assert decision.needs_review
    assert decision.reasons == ("not linked to a biopsy procedure",)
