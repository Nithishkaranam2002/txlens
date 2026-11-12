"""Review queue rules without a database: priority, reason codes, curated history replay."""

from datetime import datetime

import pytest

from txlens_backend.review.curated import PENDING_LABEL, REVIEWED_LABEL, Version, state
from txlens_backend.review.priority import (
    OTHER,
    QC_SAMPLE,
    REJECTION,
    Signals,
    biopsy_signals,
    sort_key,
    tier,
)
from txlens_backend.review.reasons import DecisionError, check_decision

OLD, NEW = datetime(2026, 1, 1), datetime(2026, 2, 1)


def test_rejection_and_high_severity_come_before_the_qc_sample_then_the_rest() -> None:
    assert tier(biopsy_signals(NEW, "TCMR", "IA", qc_sample=True)) == REJECTION
    assert tier(biopsy_signals(NEW, "no_rejection", "2R", qc_sample=False)) == REJECTION
    assert tier(biopsy_signals(NEW, "no_rejection", None, qc_sample=True)) == QC_SAMPLE
    assert tier(Signals(NEW)) == OTHER
    items = [Signals(OLD), Signals(NEW, qc_sample=True), Signals(NEW, rejection_related=True),
             Signals(OLD, rejection_related=True)]  # fmt: skip
    ordered = sorted(items, key=sort_key)
    assert ordered == [items[3], items[2], items[1], items[0]]  # oldest first within a tier


def test_abmr_and_high_grades_are_high_severity() -> None:
    assert biopsy_signals(NEW, "ABMR", None, False).high_severity
    assert biopsy_signals(NEW, "TCMR", "IIA", False).high_severity
    assert not biopsy_signals(NEW, "TCMR", "IA", False).high_severity


@pytest.mark.parametrize(
    ("item_type", "action", "reason", "note", "error"),
    [
        ("biopsy_document", "approve", None, None, None),
        ("biopsy_document", "edit", None, None, "needs a reason code"),
        ("note_mention", "reject", None, None, "needs a reason code"),
        ("note_mention", "reject", "other", " ", "needs a note"),
        ("note_mention", "reject", "other", "duplicate", None),
        ("biopsy_document", "revert", "wrong_value", None, "not possible"),
        ("curated_mapping", "reject", "wrong_concept", None, "not possible"),
        ("curated_mapping", "revert", None, None, "needs a reason code"),
    ],
)
def test_reason_codes_are_required_for_every_action_but_approve(
    item_type: str, action: str, reason: str | None, note: str | None, error: str | None
) -> None:
    if error is None:
        check_decision(item_type, action, reason, note)  # type: ignore[arg-type]
        return
    with pytest.raises(DecisionError, match=error):
        check_decision(item_type, action, reason, note)  # type: ignore[arg-type]


def test_curated_history_replays_approve_correct_and_revert() -> None:
    assert state(7, []).label == PENDING_LABEL
    approved = [Version("approve", 7, 7)]
    corrected = [*approved, Version("correct", 7, 9)]
    assert (state(7, corrected).concept_id, state(7, corrected).label) == (9, REVIEWED_LABEL)
    reverted = [*corrected, Version("revert", 9, 7)]
    assert (state(7, reverted).concept_id, state(7, reverted).in_force) == (7, (0,))
    all_reverted = [*reverted, Version("revert", 7, 7)]
    assert (state(7, all_reverted).label, state(7, all_reverted).in_force) == (PENDING_LABEL, ())
