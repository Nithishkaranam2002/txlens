"""Cohort reconciliation: expected members from ground truth, and the report (no database)."""

from pathlib import Path

from txlens_evals.cohort_reconciliation import Result, load_truth, render

PATIENTS = (
    "patient_id,tenant_id,mrn,birth_date,organ,donor_type,transplant_date,diabetes\n"
    "p1,center_a,A1,1970-01-01,kidney,living,2024-01-10,true\n"
    "p2,center_a,A2,1980-01-01,kidney,deceased,2024-02-01,false\n"
)
LABS = (
    "lab_id,patient_id,collected_at,lab,value,unit\n"
    "L1,p1,2024-06-01T08:00:00,dd_cfdna,1.2,%\n"  # counts
    "L2,p2,2025-03-01T08:00:00,dd_cfdna,1.5,%\n"  # after day 365
    "L3,p2,2024-03-01T08:00:00,dd_cfdna,2.0,%\n"  # bad date: quarantined upstream
    "L4,p2,2024-03-02T08:00:00,creatinine,2.0,mg/dL\n"  # other lab
)
ISSUES = "raw_file,raw_row,lab_id,issue\nf,3,L3,bad_date\nf,4,L4,name_variant\n"


def test_expected_high_dd_cfdna_skips_late_and_quarantined_results(tmp_path: Path) -> None:
    (tmp_path / "patients.csv").write_text(PATIENTS)
    (tmp_path / "labs.csv").write_text(LABS)
    (tmp_path / "lab_feed_issues.csv").write_text(ISSUES)
    assert load_truth(tmp_path).high_dd_cfdna_year_one == {"p1"}


def test_report_separates_exact_rules_from_known_gaps() -> None:
    results = [
        Result("exact rule", 3, 3, 0, 0),
        Result("off rule", 3, 2, 1, 0),
        Result("gap rule", 4, 1, 3, 0, "center_a: 1/4", "free text not mapped yet"),
    ]
    report = render(results, 10)
    assert "**1 of 2 rules match the ground truth exactly.**" in report
    assert "| off rule | 3 | 2 | 1 | 0 | FAIL |" in report
    assert "| gap rule | 4 | 1 | center_a: 1/4 | free text not mapped yet |" in report
