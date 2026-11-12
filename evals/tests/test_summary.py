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
