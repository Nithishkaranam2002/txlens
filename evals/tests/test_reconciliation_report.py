"""Rendering of the data-quality report (no Spark needed)."""

import pytest

pytest.importorskip("pyspark", reason="the report module imports PySpark")

from txlens_evals.pipeline_reconciliation import Check, render, table


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
