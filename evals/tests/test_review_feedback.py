"""Reviewer agreement report and the note-rule regression check (no database)."""

from functools import cache

from txlens_backend.notes.nlp import NoteNlp
from txlens_backend.notes.vocabulary import Term, build
from txlens_evals.review_feedback import Example, agreement, render, rescore


def example(agrees: bool, qc: bool, **kw: object) -> Example:
    values: dict[str, object] = {
        "item_type": "biopsy_document",
        "field": "diagnosis",
        "model_value": "TCMR",
        "reviewer_value": "TCMR",
        "agrees": agrees,
        "qc_sample": qc,
        "evidence": "line",
        "action": "approve",
        "reason_code": None,
        "term": None,
    } | kw
    return Example(**values)  # type: ignore[arg-type]


@cache
def nlp() -> NoteNlp:
    return NoteNlp(build([Term("edema", 433595, "condition", True)], []))


def test_qc_sample_agreement_is_kept_apart() -> None:
    rows = agreement([example(True, True), example(False, True), example(False, False)])
    assert rows == [("biopsy_document", "diagnosis", 2, 0.5, 1, 0.0)]


def test_rules_are_rescored_against_reviewer_answers() -> None:
    examples = [
        example(False, False, item_type="note_mention", field="negated", term="edema",
                evidence="Denies edema.", model_value="false", reviewer_value="true",
                action="edit"),
        example(True, False, item_type="note_mention", field="temporality", term="edema",
                evidence="Edema today.", model_value="current", reviewer_value="current"),
    ]  # fmt: skip
    assert rescore(nlp(), examples) == (2, 2)


def test_report_without_reviews_says_so() -> None:
    report = render([], (0, 0))
    assert "0 reviewed fields" in report and "(no reviews yet)" in report
