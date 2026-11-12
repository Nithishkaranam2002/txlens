"""Rejection records vs ground truth: support window, late cohorts, report (no database)."""

from datetime import date

from txlens_evals.rejection_records import Record, Truth, late, render, supported

A, B, C = ("center_a", "A1"), ("center_a", "A2"), ("center_b", "B1")
TRUTH = Truth(
    transplant={A: date(2024, 1, 1), B: date(2024, 1, 1), C: date(2024, 1, 1)},
    rejections={A: [date(2024, 9, 1)], B: [date(2024, 2, 1)], C: [date(2024, 10, 1)]},
)


def test_a_note_record_is_supported_only_just_after_a_true_rejection_biopsy() -> None:
    assert supported(Record(A, date(2024, 9, 2), True), TRUTH)
    assert not supported(Record(A, date(2024, 8, 31), True), TRUTH)  # before the biopsy
    assert not supported(Record(B, date(2024, 9, 2), True), TRUTH)  # a past one, re-dated


def test_late_cohort_counts_rejections_after_the_window() -> None:
    assert late(TRUTH.rejections, TRUTH.transplant, 180) == {A, C}


def test_report_flags_extra_patients_and_missing_ones_waiting_for_review() -> None:
    records = [Record(A, date(2024, 9, 2), True), Record(B, date(2024, 9, 2), True)]
    report = render(records, waiting={C}, truth=TRUTH)
    assert "| 2 | 2 | 1 | 1 |" in report  # one unsupported note record
    assert "| rejection > 180 days after transplant | 2 | 2 | 1 | 1 | 1 | FAIL |" in report
    report = render([Record(A, date(2024, 9, 2), True)], waiting={C}, truth=TRUTH)
    assert "| 1 | 1 | 0 | PASS once reviewed |" in report
