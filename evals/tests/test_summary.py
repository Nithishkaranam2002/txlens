"""summary.md is built from the reports' JSON only (no database)."""

import json
from pathlib import Path

from txlens_evals.summary import render

EST = {"value": 0.9, "low": 0.85, "high": 0.95}


def test_summary_rows_come_from_the_json_files(tmp_path: Path) -> None:
    (tmp_path / "extraction_systems.json").write_text(
        json.dumps(
            {
                "reports": 10,
                "systems": {
                    "hybrid": {
                        "f1": EST,
                        "fully_correct": EST,
                        "diagnosis_f1": EST,
                        "review_share": 0.5,
                        "wrong_loaded": 0,
                        "cost_per_report": 0.001,
                        "latency_p50_ms": 4300,
                    }
                },
            }
        )
    )
    report = render(tmp_path)
    assert "| Biopsy reports (10) | hybrid | F1 90.0% (85.0%-95.0%) |" in report
    assert "4.3 s" in report
    assert "Missing (not run yet): notes_nlp, mapping_accuracy." in report


def test_survival_headline_comes_from_survival_json(tmp_path: Path) -> None:
    assert "Not run yet (`make survival-report`)." in render(tmp_path)
    (tmp_path / "survival.json").write_text(json.dumps({
        "parity": {"ran": True, "all_within_tolerance": True, "max_cox_coef_diff": 4e-6},
        "immortal_time": {"simulated_mean_hr": {"naive (from transplant)": 0.56,
                                                "landmark at day 365": 0.99}},
        "rejection_year1": {"abmr_hr": [17.7, 9.3, 33.4]},
        "competing_risks": {"gap_3y_main_pts": 0.1},
    }))  # fmt: skip
    report = render(tmp_path)
    assert "largest Cox coefficient difference 4e-06" in report
    assert "naive 0.56, landmark 0.99" in report
    assert "HR 17.7 (9.3-33.4)" in report


def test_copilot_headline_comes_from_copilot_json(tmp_path: Path) -> None:
    assert "Not run yet (`make copilot-report`" in render(tmp_path)
    headline = {
        "scored": 40, "execution_accuracy": EST, "tool_choice_accuracy": EST,
        "structured_not_sql": EST, "literature_questions": 20,
        "faithfulness": EST, "answer_relevancy": EST, "citation_supported": EST,
        "citation_supported_or_partly": EST, "unanswerable_declined": [3, 3],
        "red_team_passed": [25, 25], "cost_per_question_usd": 0.0012,
        "latency_median_ms": 7000, "judge_model": "openai/judge",
    }  # fmt: skip
    (tmp_path / "copilot.json").write_text(json.dumps(headline))
    report = render(tmp_path)
    assert "- Red team: 25 of 25 attacks refused or neutralized." in report
    assert "judge `openai/judge`" in report and "execution accuracy 90.0% (85.0%-95.0%)" in report


def test_risk_model_headline_comes_from_risk_model_json(tmp_path: Path) -> None:
    assert "Not run yet (`make risk-report`)." in render(tmp_path)
    (tmp_path / "risk_model.json").write_text(json.dumps({
        "champion": "logistic_regression",
        "external": {"n_visits": 4943, "n_events": 78, "prevalence": 0.016,
                     "auroc": EST, "auprc": EST},
        "burden": {"external_alert_rate": 0.077, "external_model_sensitivity": EST,
                   "external_rule_sensitivity": EST, "external_difference": EST},
        "visits": {"for_cause_share": 0.014},
        "sensitivity": {"no_pre_biopsy_external_auroc": 0.744},
    }))  # fmt: skip
    report = render(tmp_path)
    assert "not clinically validated" in report
    assert "AUROC 0.900 (0.850 to 0.950)" in report and "external AUROC is 0.744" in report
