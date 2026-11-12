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
