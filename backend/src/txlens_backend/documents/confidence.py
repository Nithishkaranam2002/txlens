"""Confidence from things we can measure, never from the model's opinion of itself.

- Field confidence = OCR confidence of the evidence words x agreement of the two independent
  extraction passes (1 if both read the same value, else 0). A field both passes leave empty
  ("not reported") has no words to measure, so it takes the document's mean OCR confidence.
- Document confidence = the lowest confidence among the key fields.
- A document goes to human review if its confidence is below the threshold, if any field's
  evidence failed, or if any validation rule failed (including "not linked to a procedure").
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from txlens_backend.documents.evidence import VerifiedField
from txlens_backend.documents.schema import BANFF_SCORES, Organ

KEY_FIELDS: dict[Organ, tuple[str, ...]] = {
    "kidney": ("biopsy_date", "diagnosis", "tcmr_grade", *BANFF_SCORES, "c4d"),
    "heart": ("biopsy_date", "acr_grade", "pamr_grade"),
}


@dataclass(frozen=True)
class FieldResult:
    """The final value of one field, how sure we are, and what went wrong."""

    verified: VerifiedField
    passes_agree: bool
    confidence: float


@dataclass(frozen=True)
class Decision:
    """Document confidence and whether a human must review it, with the reasons."""

    confidence: float
    needs_review: bool
    reasons: tuple[str, ...]


def combine(a: VerifiedField, b: VerifiedField, mean_ocr: float) -> FieldResult:
    """Merge one field from passes A and B.

    Pass A's value is kept when its evidence held; otherwise pass B's. If the two verified
    values differ, the field gets confidence 0 and the disagreement is recorded.
    """
    chosen = a if a.value is not None or b.value is None else b
    agree = a.value == b.value
    problems = (*a.problems, *(f"pass B: {p}" for p in b.problems))
    if not agree:
        problems += (f"passes disagree (A: {a.value}, B: {b.value})",)
    ocr = chosen.ocr_confidence if chosen.ocr_confidence is not None else mean_ocr
    return FieldResult(replace(chosen, problems=problems), agree, ocr if agree else 0.0)


def decide(
    organ: Organ,
    fields: Mapping[str, FieldResult],
    validation_problems: Sequence[str],
    threshold: float,
) -> Decision:
    """Document confidence and the review decision."""
    keys = [fields[f].confidence for f in KEY_FIELDS[organ] if f in fields]
    confidence = min(keys, default=0.0)
    reasons = list(validation_problems)
    reasons += [f"{name}: {p}" for name, r in fields.items() for p in r.verified.problems]
    if confidence < threshold:
        reasons.append(f"confidence {confidence:.2f} is below {threshold:.2f}")
    return Decision(round(confidence, 3), bool(reasons), tuple(reasons))
