"""From two passes' answers to final values and a review decision (pure, no database).

Each field of each pass is checked against the document (evidence.py), the passes are
combined (confidence.py), heart diagnoses are derived from the grades (schema.py), and the
values are validated (validate.py). A pass that failed outright counts as "said nothing", so
any value from the other pass has no agreement and the document goes to review.
"""

from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from txlens_backend.documents.confidence import Decision, FieldResult, combine, decide
from txlens_backend.documents.evidence import VerifiedField, verify
from txlens_backend.documents.ocr import OcrResult
from txlens_backend.documents.phi import MinimizedText
from txlens_backend.documents.schema import SCHEMAS, Organ, heart_diagnosis
from txlens_backend.documents.validate import validate


@dataclass(frozen=True)
class Assessment:
    """Final field results, their plain values, and the review decision."""

    organ: Organ
    fields: dict[str, FieldResult]
    decision: Decision

    @property
    def values(self) -> dict[str, Any]:
        """Field name -> final value (None when not reported or rejected)."""
        return {name: result.verified.value for name, result in self.fields.items()}


def empty(field: str) -> VerifiedField:
    """A field a failed pass said nothing about."""
    return VerifiedField(field, None, None, None, None, None, None, None, ())


def mean_confidence(ocr: OcrResult, sent: MinimizedText) -> float:
    """Mean OCR confidence of the lines the model saw."""
    lines = [line for line in ocr.lines if sent.text_of(line.line_no) is not None]
    return sum(line.confidence for line in lines) / len(lines) if lines else 0.0


def derived_heart_diagnosis(fields: dict[str, FieldResult]) -> FieldResult:
    """The heart diagnosis from the verified grades, located where the ACR grade is."""
    acr, pamr = fields["acr_grade"], fields["pamr_grade"]
    value = heart_diagnosis(acr.verified.value, pamr.verified.value)  # type: ignore[arg-type]
    verified = replace(acr.verified, field="diagnosis", value=value, extracted_value=value,
                       problems=())  # fmt: skip
    agree = acr.passes_agree and pamr.passes_agree
    return FieldResult(verified, agree, min(acr.confidence, pamr.confidence))


def assess(
    organ: Organ,
    ocr: OcrResult,
    sent: MinimizedText,
    answers: tuple[dict[str, Any] | None, dict[str, Any] | None],
    procedure_date: date | None,
    threshold: float,
    pass_errors: tuple[str, ...] = (),
) -> Assessment:
    """Verify, combine, validate and decide."""
    schema = SCHEMAS[organ]
    parsed = [schema.model_validate(a) if a is not None else None for a in answers]
    mean_ocr = mean_confidence(ocr, sent)
    fields: dict[str, FieldResult] = {}
    for name in schema.model_fields:
        a, b = (verify(name, getattr(p, name), ocr, sent) if p else empty(name) for p in parsed)
        fields[name] = combine(a, b, mean_ocr)
    if organ == "heart":
        fields["diagnosis"] = derived_heart_diagnosis(fields)
    values = {name: result.verified.value for name, result in fields.items()}
    problems = [*pass_errors, *validate(organ, values, procedure_date)]
    return Assessment(organ, fields, decide(organ, fields, problems, threshold))
