from datetime import date, timedelta

from datagen_testutils import fraction

from txlens_datagen.config import GeneratorConfig
from txlens_datagen.models import LabName, PatientRecord


def value_on(record: PatientRecord, lab: LabName, day: date) -> float | None:
    """The lab value drawn on a given date, if any."""
    for result in record.labs:
        if result.lab == lab and result.collected_at.date() == day:
            return result.value
    return None


def test_signal_lead_time_is_two_to_eight_weeks(cohort: list[PatientRecord]) -> None:
    for record in cohort:
        for e in record.rejections:
            assert 14 <= (e.biopsy_date - e.signal_onset_date).days <= 56
        for i in record.injuries:
            assert 14 <= (i.biopsy_date - i.onset_date).days <= 42


def test_dd_cfdna_and_creatinine_rise_before_biopsy(
    cohort: list[PatientRecord], config: GeneratorConfig
) -> None:
    dd_up: list[bool] = []
    cr_up: list[bool] = []
    for record in cohort:
        for e in record.rejections:
            if e.rejection_type == "borderline":
                continue
            threshold = config.organs[record.patient.organ].dd_cfdna.thresholds_pct[-1]
            before = value_on(record, "dd_cfdna", e.biopsy_date - timedelta(days=7))
            at = value_on(record, "dd_cfdna", e.biopsy_date)
            assert before is not None and at is not None
            dd_up.append(at >= threshold and before > 0)
            if record.patient.organ == "kidney":
                baseline = [
                    r.value
                    for r in record.labs
                    if r.lab == "creatinine"
                    and timedelta(days=30) < r.collected_at.date() - record.patient.transplant_date
                    and r.collected_at.date() < e.signal_onset_date
                ]
                cr = value_on(record, "creatinine", e.biopsy_date)
                if baseline and cr is not None:
                    cr_up.append(cr > baseline[-1] * 1.1)
    assert fraction(dd_up) >= 0.95
    assert fraction(cr_up) >= 0.9


def test_dd_cfdna_quiet_before_signal_onset(
    cohort: list[PatientRecord], config: GeneratorConfig
) -> None:
    quiet: list[bool] = []
    for record in cohort:
        if len(record.rejections) != 1 or record.injuries:
            continue
        threshold = config.organs[record.patient.organ].dd_cfdna.thresholds_pct[-1]
        onset = record.rejections[0].signal_onset_date
        quiet += [
            r.value < threshold
            for r in record.labs
            if r.lab == "dd_cfdna" and r.collected_at.date() < onset
        ]
    assert fraction(quiet) >= 0.99


def test_responders_fall_back_and_non_responders_do_not(
    cohort: list[PatientRecord], config: GeneratorConfig
) -> None:
    responders: list[bool] = []
    non_responders: list[bool] = []
    for record in cohort:
        low = config.organs[record.patient.organ].dd_cfdna.thresholds_pct[0]
        for e in record.rejections:
            later = value_on(record, "dd_cfdna", e.biopsy_date + timedelta(days=90))
            if later is None or e.rejection_type == "borderline":
                continue
            (responders if e.responded else non_responders).append(later < low)
    assert fraction(responders) >= 0.9
    assert fraction(non_responders) <= 0.1
