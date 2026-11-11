"""The synthetic oncology cohort: reproducible, scheduled every 3 months, and with a marker
signal that starts before the clinical recurrence date."""

import csv
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from txlens_datagen.cli import main
from txlens_datagen.oncology import (
    LIMIT_OF_DETECTION,
    OncologyConfig,
    OncologyRecord,
    build,
    generate_oncology,
)
from txlens_datagen.schedule import month_to_day

CONFIG = OncologyConfig()


@pytest.fixture(scope="module")
def records() -> list[OncologyRecord]:
    return [build(i, CONFIG, seed=42) for i in range(CONFIG.n_patients)]


def test_same_seed_same_patients_and_other_seed_differs() -> None:
    assert build(7, CONFIG, seed=42) == build(7, CONFIG, seed=42)
    assert build(7, CONFIG, seed=42) != build(7, CONFIG, seed=43)


def test_tests_every_three_months_with_small_jitter(records: list[OncologyRecord]) -> None:
    for record in records:
        end = record.patient.treatment_end_date
        for test in record.schedule:
            assert test.month % CONFIG.interval_months == 0
            planned = end + timedelta(days=month_to_day(test.month))
            assert abs((test.due_date - planned).days) <= CONFIG.jitter_days
        assert {lab.lab_id for lab in record.labs} == {t.lab_id for t in record.schedule if t.done}


def test_no_tests_after_recurrence_or_death(records: list[OncologyRecord]) -> None:
    for record in records:
        stop = record.outcome.last_follow_up_date
        assert all(lab.collected_at.date() < stop for lab in record.labs)


def test_recurrence_signal_rises_before_the_clinical_date(records: list[OncologyRecord]) -> None:
    recurred = [r for r in records if r.outcome.recurrence_date is not None]
    assert 0.2 <= len(recurred) / len(records) <= 0.4
    for record in recurred:
        onset, recurrence = record.outcome.signal_onset_date, record.outcome.recurrence_date
        assert onset is not None and recurrence is not None
        assert timedelta(days=30) <= recurrence - onset <= timedelta(days=120)
        before = [lab for lab in record.labs if lab.collected_at.date() < onset]
        after = [lab.value for lab in record.labs if lab.collected_at.date() >= onset]
        assert all(lab.value == 0.0 for lab in before)
        assert all(value > 0 for value in after)


def test_false_positives_are_rare_one_off_low_values(records: list[OncologyRecord]) -> None:
    healthy = [lab for r in records if r.outcome.recurrence_date is None for lab in r.labs]
    positives = [lab.value for lab in healthy if lab.value > 0]
    assert 0 < len(positives) / len(healthy) < 0.05
    assert all(LIMIT_OF_DETECTION <= v <= 0.3 for v in positives)


def test_files_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    generate_oncology(replace(CONFIG, n_patients=5), 42, tmp_path)
    with (tmp_path / "oncology" / "patients.csv").open() as f:
        patients = list(csv.DictReader(f))
    assert len(patients) == 5 and {p["program"] for p in patients} == {"oncology"}
    for name in ("labs", "outcomes", "surveillance_schedule"):
        assert (tmp_path / "oncology" / f"{name}.csv").exists()
    assert main(["--n-patients", "2", "--n-oncology", "3", "--out", str(tmp_path),
                 "--no-pdfs"]) == 0  # fmt: skip
    assert "Oncology cohort: 3 patients" in capsys.readouterr().out
    with (tmp_path / "oncology" / "patients.csv").open() as f:
        assert len(list(csv.DictReader(f))) == 3  # an earlier run is replaced
