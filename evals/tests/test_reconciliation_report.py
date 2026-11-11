"""Rendering of the data-quality report (no Spark needed)."""

import pytest

pytest.importorskip("pyspark", reason="the report module imports PySpark")

from datetime import date

from txlens_evals.pipeline_reconciliation import (
    Check,
    MaintenanceGap,
    check_maintenance,
    render,
    table,
    uncovered_days,
)


def test_table_formats_numbers_with_separators() -> None:
    assert table(["A", "B"], [("x", 12345)]) == ["| A | B |", "|---|---|", "| x | 12,345 |"]


def test_report_counts_passed_checks_and_totals_coverage() -> None:
    checks = [Check("one", 1, 1, True), Check("two", 2, 1, False, "1 missing")]
    cover = [("measurement", "unit_concept_id", "center_a", 9, 10),
             ("measurement", "unit_concept_id", "center_b", 1, 10)]  # fmt: skip
    report = render(checks, [("gold", "person", 3)], [], cover, [])
    assert "**Reconciliation: 1 of 2 checks passed.**" in report
    assert "| two | 2 | 1 | FAIL | 1 missing |" in report
    assert "| measurement | unit_concept_id | **all** | 10 | 20 | **50.0%** |" in report


def test_uncovered_days_joins_back_to_back_dose_steps() -> None:
    steps = [(date(2025, 1, 1), date(2025, 1, 31)), (date(2025, 2, 1), date(2025, 3, 31))]
    assert uncovered_days(steps, date(2025, 1, 1), date(2025, 3, 31)) == 0
    assert uncovered_days(steps, date(2025, 1, 1), date(2025, 4, 2)) == 2  # ends too early
    assert uncovered_days([], date(2025, 1, 1), date(2025, 1, 10)) == 10
    gap = [(date(2025, 1, 1), date(2025, 1, 10)), (date(2025, 1, 13), date(2025, 1, 20))]
    assert uncovered_days(gap, date(2025, 1, 1), date(2025, 1, 20)) == 2


def test_maintenance_check_counts_patients_and_names_the_protocol() -> None:
    gaps = [MaintenanceGap("center_a", "A1", "prednisone", 30),
            MaintenanceGap("center_a", "A1", "tacrolimus", 5)]  # fmt: skip
    check = check_maintenance(10, gaps, date(2026, 9, 30))
    assert (check.expected, check.actual, check.passed) == (10, 9, False)
    assert "per the generator's protocol" in check.name
    report = render([check], [], [], [], [], gaps)
    assert "Real-world protocols vary, e.g. steroid-free" in report
    assert "| center_a | A1 | prednisone | 30 |" in report
    assert "Every active patient is covered" in render([], [], [], [], [])


def test_condition_section_compares_codes_only_with_text_matching() -> None:
    from txlens_evals.pipeline_reconciliation import render_conditions

    lines = "\n".join(
        render_conditions(
            [
                ("center_a", {"code": 9, "unmapped": 1}, {"Condition": 9}),
                (
                    "center_b",
                    {"exact": 6, "reviewed": 3, "unmapped": 1},
                    {"Condition": 8, "Observation": 1},
                ),
            ]
        )
    )
    assert "| center_a | 10 | 9 | 90.0% | 9 | 90.0% | 9 | 0 |" in lines
    assert "| center_b | 10 | 0 | 0.0% | 9 | 90.0% | 8 | 1 |" in lines
    assert "| **all** | 20 | 9 | 45.0% | 18 | 90.0% | 17 | 1 |" in lines
    assert "| center_b | 0 | 3 | 6 | 0 | 0 | 1 |" in lines
