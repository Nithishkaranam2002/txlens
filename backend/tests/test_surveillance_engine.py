"""Engine: priority scoring, evidence keys, replay, and reuse on the oncology config."""

import csv
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from surveillance_testutils import config, day, series

from txlens_backend.surveillance import csv_source
from txlens_backend.surveillance.config import Severity, load_configs
from txlens_backend.surveillance.engine import change_days, evaluate, priority, replay
from txlens_backend.surveillance.model import Observation, PatientSeries
from txlens_datagen.oncology import OncologyConfig, generate_oncology

WEIGHTS = {Severity.HIGH: 10.0, Severity.MEDIUM: 4.0, Severity.LOW: 1.0}


def test_priority_counts_each_group_once_at_its_highest_severity() -> None:
    flags = [(Severity.MEDIUM, "dd"), (Severity.HIGH, "dd"), (Severity.LOW, "tac"),
             (Severity.MEDIUM, "function"), (Severity.MEDIUM, "function")]  # fmt: skip
    assert priority(flags, WEIGHTS) == 10 + 1 + 4
    assert priority([], WEIGHTS) == 0


def test_flags_carry_config_id_and_version() -> None:
    [flag] = [f for f in evaluate(config(), series(m=[(190, 2.0)]), day(200))
              if f.rule_id == "over"]  # fmt: skip
    assert (flag.config_id, flag.config_version, flag.group) == ("test_program", 3, "m")


def test_evidence_key_is_stable_and_ignores_config_version_and_float_noise() -> None:
    [flag] = [f for f in evaluate(config(), series(m=[(190, 2.0)]), day(200))
              if f.rule_id == "over"]  # fmt: skip
    noisy = series(m=[(190, 2.0000000001)])
    [again] = [f for f in evaluate(config(), noisy, day(210)) if f.rule_id == "over"]
    assert again.evidence_key == flag.evidence_key
    assert replace(flag, config_version=4).evidence_key == flag.evidence_key
    [other] = [f for f in evaluate(config(), series(m=[(195, 2.0)]), day(200))
               if f.rule_id == "over"]  # fmt: skip
    assert other.evidence_key != flag.evidence_key


def test_other_programs_are_ignored() -> None:
    other = replace(series(m=[(190, 3.0)]), program="something_else")
    assert evaluate(config(), other, day(200)) == []


def test_replay_matches_evaluating_every_single_day() -> None:
    patient = series(
        m=[(30, 0.2), (61, 0.6), (130, 0.7), (190, 1.2), (300, 0.3)],
        c=[(35, 1.0), (60, 1.1), (90, 1.4), (200, 1.0)],
        t=[(20, 13.0), (100, 6.0), (250, 9.0)],
        d=[(30, 0.0), (180, 2500.0)],
    )
    end = day(420)
    states = dict(replay(config(), patient, patient.anchor_date, end))
    current: list[str] = []
    for n in range((end - patient.anchor_date).days + 1):
        today = day(n)
        if today in states:
            current = sorted(f.evidence_key for f in states[today])
        daily = sorted(f.evidence_key for f in evaluate(config(), patient, today))
        assert daily == current, today


def test_change_days_cover_due_windows_after_the_last_result() -> None:
    days = change_days(config(), series(m=[(30, 0.2)]), day(400))
    assert day(91 + 15) in days and day(365 + 15) in days
    assert max(days) <= day(400)


def test_same_engine_runs_the_oncology_config() -> None:
    oncology = load_configs()["oncology_tumor_dna"]
    end = date(2024, 1, 1)

    def patient(values: list[float]) -> PatientSeries:
        obs = tuple(Observation(end + timedelta(days=91 * (i + 1)), v)
                    for i, v in enumerate(values))  # fmt: skip
        return PatientSeries("onc1", "oncology", end, None, {"tumor_dna": obs})

    as_of = end + timedelta(days=91 * 3 + 1)
    assert evaluate(oncology, patient([0.0, 0.0, 0.0]), as_of) == []
    detected = evaluate(oncology, patient([0.0, 0.0, 0.4]), as_of)
    assert {f.rule_id for f in detected} == {"tumor_dna_detected"}
    assert detected[0].reason.startswith("Tumor DNA marker detected: 0.4 MTM/mL")
    rising = evaluate(oncology, patient([0.0, 0.03, 0.06]), as_of)
    assert {f.rule_id for f in rising} == {"tumor_dna_rising"}
    missed = evaluate(oncology, patient([0.0]), as_of + timedelta(days=200))
    assert {f.rule_id for f in missed} == {"tumor_dna_overdue"}


def test_oncology_cohort_from_csv_is_flagged_before_recurrence(tmp_path: Path) -> None:
    """End to end: generated oncology CSVs -> CSV source -> the unchanged engine."""
    generate_oncology(replace(OncologyConfig(), n_patients=80), 42, tmp_path)
    oncology = load_configs()["oncology_tumor_dna"]
    patients = csv_source.load(tmp_path / "oncology", oncology, skip_feed_issues=False)
    assert len(patients) == 80
    with (tmp_path / "oncology" / "outcomes.csv").open() as f:
        recurrences = {r["patient_id"]: date.fromisoformat(r["recurrence_date"])
                       for r in csv.DictReader(f) if r["recurrence_date"]}  # fmt: skip
    caught = 0
    for patient_id, recurrence in recurrences.items():
        series = patients[patient_id].series
        assert series.end_date == recurrence
        days = replay(oncology, series, series.anchor_date, recurrence)
        caught += any(f.rule_id == "tumor_dna_detected" for _, flags in days for f in flags)
    assert caught >= len(recurrences) // 3
