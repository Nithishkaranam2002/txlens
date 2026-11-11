"""Biopsy extraction eval: truth mapping, sampling, scoring and the report (no LLM, no DB)."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from txlens_backend.config import Settings
from txlens_backend.documents.budget import BudgetExceededError
from txlens_backend.documents.service import QC_REASON
from txlens_datagen.config import GeneratorConfig
from txlens_datagen.record import build_record
from txlens_evals import biopsy_extraction as ev

KIDNEY_ROW = {
    "biopsy_id": "BX-000014-01",
    "organ": "kidney",
    "biopsy_date": "2023-12-01",
    "diagnosis": "TCMR",
    "other_finding": "",
    "banff_i": "2",
    "banff_t": "2",
    "banff_v": "1",
    "banff_g": "0",
    "banff_ptc": "0",
    "banff_ci": "1",
    "banff_ct": "1",
    "c4d": "0",
    "banff_cv": "not_reported",
    "adequacy": "inadequate",
    "banff_tcmr_grade": "IIA",
    "ishlt_acr_grade": "",
    "pamr_grade": "",
    "pdf_file": "pathology/center_b/BX-000014-01.pdf",
}


def test_truth_says_which_fields_the_report_leaves_out() -> None:
    values, missing = ev.expected(KIDNEY_ROW)
    assert values["banff_cv"] is None and missing == ("banff_cv",)
    assert (values["adequacy"], values["tcmr_grade"], values["banff_v"]) == ("inadequate", "IIA", 1)
    assert values["biopsy_date"] == date(2023, 12, 1)


def test_truth_maps_findings_and_grades_only_where_they_apply() -> None:
    bk = KIDNEY_ROW | {
        "diagnosis": "no_rejection",
        "banff_tcmr_grade": "",
        "other_finding": "Polyomavirus (BK) nephropathy, SV40 positive",
    }
    values, _ = ev.expected(bk)
    assert (values["additional_finding"], values["tcmr_grade"]) == ("BK_nephropathy", None)
    heart = KIDNEY_ROW | {
        "organ": "heart",
        "diagnosis": "ABMR",
        "ishlt_acr_grade": "1R",
        "pamr_grade": "pAMR 2",
    }
    values, missing = ev.expected(heart)
    assert (values["acr_grade"], values["pamr_grade"], missing) == ("1R", "pAMR 2", ())


def truths() -> list[ev.Truth]:
    made = []
    for i in range(300):
        organ = "kidney" if i % 3 else "heart"
        diagnosis = ev.DIAGNOSES[i % 5] if i % 7 == 0 else "no_rejection"
        made.append(
            ev.Truth(
                f"BX-{i:06d}-01",
                organ,
                f"pathology/center_a/{i}.pdf",
                {"diagnosis": diagnosis},
                (),
                f"{organ}|{diagnosis}",
            )
        )
    return made


def test_the_sample_is_fixed_sized_and_covers_every_stratum() -> None:
    first, again = ev.stratified(truths(), 100, seed=7), ev.stratified(truths(), 100, seed=7)
    assert first == again and len(first) == 100
    assert sum(t.organ == "kidney" for t in first) == 60
    assert {t.stratum for t in first} == {t.stratum for t in truths()}


@pytest.mark.parametrize(
    ("reason", "category"),
    [
        ("banff_t: passes disagree (A: 2, B: None)", "the two passes disagree"),
        ("confidence 0.43 is below 0.85", "low OCR confidence"),
        ("banff_t: quote does not state 2", "evidence check failed"),
        ("not linked to a biopsy procedure", "not linked to a biopsy"),
        ("scores i1 t1 v0 do not meet TCMR grade IA", "Banff consistency rule"),
        ("pass a failed: InstructorRetryException", "model error"),
        (QC_REASON, "QC sample (random check of auto-approval)"),
    ],
)
def test_review_reasons_are_grouped(reason: str, category: str) -> None:
    assert ev.reason_category(reason) == category


def run(
    status: str, diagnosis: str, extracted: dict[str, Any], cost: float = 0.002
) -> ev.DocumentRun:
    values, missing = ev.expected(KIDNEY_ROW | {"diagnosis": diagnosis})
    truth = ev.Truth("BX-000014-01", "kidney", "pathology/center_b/x.pdf", values, missing, "s")
    return ev.DocumentRun(
        ev.Job(truth, Path("center_b/x.pdf")),
        "d",
        status,
        values | extracted,
        cost_usd=cost,
        paid_passes=2,
        seconds=12.0,
        pass_latency_ms=[9000, 11000],
    )


def test_the_report_scores_fields_routes_thresholds_and_injection() -> None:
    runs = [
        run("extracted", "TCMR", {}),
        run("in_review", "TCMR", {"banff_t": None}),
        run("in_review", "TCMR", {"diagnosis": "ABMR"}),
        run("in_review", "TCMR", {}),
    ]
    runs[0].confidence, runs[1].confidence, runs[3].confidence = 0.9, 0.6, 0.95
    runs[1].reasons = ["confidence 0.60 is below 0.70"]
    runs[2].reasons = ["confidence 0.00 is below 0.70"]
    runs[3].reasons = [QC_REASON]
    injected = [run("in_review", "TCMR", {})]
    text = ev.report(runs, injected, Settings(), "eval-test", 1405, 0.01, 0.006, 3.0)
    assert "75.0% sent to human review; 2 of 2 auto-approved reports (QC sample included)" in text
    assert "| kidney | banff_t | 4 | 3 | 75.0% |  |" in text
    assert "| kidney | banff_cv | 4 | 4 | 100.0% | 4/4 empty as expected |" in text
    assert "| **TCMR** |  |  | 3 | 1 |  |  |" in text  # truth TCMR: 3 as TCMR, 1 as ABMR
    assert "| Auto-approved (loaded without review) | 1 | 1 | 100.0% |" in text
    assert "| QC sample (would have been auto-approved) | 1 | 1 | 100.0% |" in text
    assert "| Sent to human review | 2 | 0 | 0.0% |" in text
    # Would-be auto-approvals: confidences 0.9, 0.6 (t wrong), 0.0 (diagnosis wrong), 0.95.
    assert "| 0.85 | 2 | 50.0% | 0 |" in text
    assert "| 0.50 | 3 | 75.0% | 1 |" in text
    assert "about **$2.81**" in text  # 1405 remaining x $0.002
    assert "| BX-000014-01 | TCMR | TCMR | yes | in_review |" in text


def test_the_batch_stops_starting_reports_when_the_budget_runs_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def process(job: ev.Job, *_: object) -> ev.DocumentRun:
        calls.append(job.truth.biopsy_id)
        if len(calls) == 2:
            raise BudgetExceededError("cap")
        return ev.DocumentRun(job, status="extracted")

    monkeypatch.setattr(ev, "process", process)
    jobs = [ev.Job(t, Path(t.pdf_file)) for t in truths()[:5]]
    runs = ev.run_all(jobs, None, Settings(llm_concurrency=1), None)  # type: ignore[arg-type]
    assert [r.status for r in runs] == [
        "extracted",
        "budget_exceeded",
        "not_started",
        "not_started",
        "not_started",
    ]
    assert len(calls) == 2


def test_adversarial_reports_are_written_for_real_biopsies(tmp_path: Path) -> None:
    biopsy = build_record(0, GeneratorConfig(), with_notes=False).biopsies[0]
    values, _ = ev.expected(KIDNEY_ROW)
    truth = ev.Truth(
        biopsy.biopsy_id, "kidney", f"pathology/center_a/{biopsy.biopsy_id}.pdf", values, (), "s"
    )
    path = ev.write_adversarial(truth, tmp_path)
    assert path == tmp_path / "center_a" / f"{biopsy.biopsy_id}-injected.pdf"
    first = path.read_bytes()
    assert first.startswith(b"%PDF")
    assert ev.write_adversarial(truth, tmp_path).read_bytes() == first  # same file every run
