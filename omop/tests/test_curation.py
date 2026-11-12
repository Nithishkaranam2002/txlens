"""Writing review decisions back into the mapping file (no database)."""

from datetime import date
from pathlib import Path

from txlens_omop.curation import (
    PENDING,
    GapMapping,
    apply,
    gap_mappings,
    read_rows,
    reviews,
    write_rows,
)

D1, D2 = date(2026, 11, 12), date(2026, 11, 13)
ROW = {"source_code": "dgf", "source_concept_id": "0", "source_vocabulary_id": "TXLENS_NOTE_ABBREV",
       "source_code_description": f"Delayed graft function {PENDING}",
       "target_concept_id": "4138908", "target_vocabulary_id": "SNOMED",
       "valid_start_date": "20261111", "valid_end_date": "20991231",
       "invalid_reason": ""}  # fmt: skip
LAB = ROW | {"source_code": "cre", "source_vocabulary_id": "TXLENS_LAB_CODE",
             "source_code_description": "local lab code CRE"}  # fmt: skip
KEY = ("TXLENS_NOTE_ABBREV", "dgf")


def test_replaying_history_approve_correct_and_revert() -> None:
    history = [(*KEY, "approve", 4138908, 4138908, 1, D1),
               (*KEY, "correct", 4138908, 99, 2, D1),
               (*KEY, "revert", 99, 4138908, 3, D2)]  # fmt: skip
    assert reviews(history)[KEY] is not None and reviews(history)[KEY].history_id == 1  # type: ignore[union-attr]
    assert reviews(history[:2])[KEY].concept_id == 99  # type: ignore[union-attr]
    assert reviews([*history, (*KEY, "revert", 4138908, 4138908, 4, D2)])[KEY] is None


def test_reviewed_rows_are_relabelled_and_corrected_others_untouched() -> None:
    state = {KEY: reviews([(*KEY, "correct", 4138908, 99, 7, D1)])[KEY]}
    out = apply([ROW, LAB], state, [], {99: "SNOMED"})
    assert out[0]["target_concept_id"] == "99"
    assert out[0]["source_code_description"] == (
        "Delayed graft function [clinician-reviewed 2026-11-12, review h7]"
    )
    assert out[1] == LAB


def test_fully_reverted_row_goes_back_to_pending() -> None:
    reviewed = ROW | {"source_code_description": "Delayed graft function "
                      "[clinician-reviewed 2026-11-12, review h7]"}  # fmt: skip
    out = apply([reviewed], {KEY: None}, [], {})
    assert out[0]["source_code_description"] == f"Delayed graft function {PENDING}"


def test_gap_mappings_need_agreement_between_centers() -> None:
    decisions = [("Hypertensive  crisis", "approve", "316866", "d" * 36, D1),
                 ("odd text", "edit", "1", "e" * 36, D1),
                 ("Odd text", "approve", "2", "f" * 36, D2),
                 ("left alone", "reject", None, "g" * 36, D1)]  # fmt: skip
    agreed, conflicts = gap_mappings(decisions)
    assert [(g.text, g.concept_id) for g in agreed] == [("hypertensive crisis", 316866)]
    assert conflicts == ["odd text"]


def test_gap_rows_are_added_once(tmp_path: Path) -> None:
    gap = GapMapping("hypertensive crisis", 316866, D1, "abcdef12-0000")
    out = apply([LAB], {}, [gap, gap], {316866: "SNOMED"})
    assert len(out) == 2 and out[1]["source_code_description"].endswith(
        "[reviewed 2026-11-12, review dabcdef12]"
    )
    path = tmp_path / "map.csv"
    write_rows(path, list(LAB), out)
    assert read_rows(path)[1] == out
