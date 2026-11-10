import statistics
from datetime import timedelta

from datagen_testutils import fraction, is_stable, values

from txlens_datagen.cohort import FIRST_NAMES, SURNAME_ENDS, SURNAME_STARTS, age_on
from txlens_datagen.config import GeneratorConfig
from txlens_datagen.models import PatientRecord
from txlens_datagen.schedule import DAYS_PER_MONTH


def test_demographics(cohort: list[PatientRecord], config: GeneratorConfig) -> None:
    patients = [r.patient for r in cohort]
    for p in patients:
        assert 18 <= age_on(p, p.transplant_date) <= 75
        assert config.transplant_window[0] <= p.transplant_date <= config.transplant_window[1]
    assert 0.82 <= fraction([p.organ == "kidney" for p in patients]) <= 0.88
    kidneys = [p for p in patients if p.organ == "kidney"]
    assert 0.60 <= fraction([p.donor_type == "deceased" for p in kidneys]) <= 0.70
    assert {p.tenant_id for p in patients} == {c.tenant_id for c in config.centers}
    assert len({p.patient_id for p in patients}) == len(patients)
    assert len({p.mrn for p in patients}) == len(patients)


def test_names_are_fictional(cohort: list[PatientRecord]) -> None:
    surnames = {s + e for s in SURNAME_STARTS for e in SURNAME_ENDS}
    for record in cohort:
        p = record.patient
        assert p.first_name in FIRST_NAMES[p.sex]
        assert p.last_name in surnames


def test_medication_doses(cohort: list[PatientRecord]) -> None:
    for record in cohort:
        for order in record.meds:
            if order.drug == "tacrolimus":
                assert 1.0 <= order.dose_value <= 5.0
                assert order.frequency == "BID"
            if order.drug == "mycophenolate mofetil":
                assert 500 <= order.dose_value <= 1000
        pred = [o for o in record.meds if o.drug == "prednisone"]
        assert pred[0].dose_value == 20.0
        if record.outcome.last_follow_up_date > pred[0].start_date + timedelta(days=365):
            assert pred[-1].dose_value == 5.0


def test_tacrolimus_trough_targets(cohort: list[PatientRecord], config: GeneratorConfig) -> None:
    kidney = config.organs["kidney"]
    early: list[float] = []
    late: list[float] = []
    for record in cohort:
        if record.patient.organ != "kidney":
            continue
        for lab in record.labs:
            if lab.lab == "tacrolimus_trough":
                day = (lab.collected_at.date() - record.patient.transplant_date).days
                target_early = day < kidney.tacro_early_months * DAYS_PER_MONTH
                (early if target_early else late).append(lab.value)
    assert 8 <= statistics.median(early) <= 12
    assert 5 <= statistics.median(late) <= 8
    out_of_range = fraction([not 5 <= v <= 8 for v in late])
    assert 0.1 <= out_of_range <= 0.5


def test_stable_kidney_creatinine_and_egfr(cohort: list[PatientRecord]) -> None:
    stable = [r for r in cohort if r.patient.organ == "kidney" and is_stable(r)]
    creatinine = values(stable, "creatinine")
    egfr = values(stable, "egfr")
    assert fraction([0.8 <= v <= 2.0 for v in creatinine]) >= 0.95
    assert fraction([38 <= v <= 85 for v in egfr]) >= 0.90


def test_stable_dd_cfdna_is_organ_specific(cohort: list[PatientRecord]) -> None:
    stable_kidney = [r for r in cohort if r.patient.organ == "kidney" and is_stable(r)]
    stable_heart = [r for r in cohort if r.patient.organ == "heart" and is_stable(r)]
    assert fraction([0.1 <= v <= 0.5 for v in values(stable_kidney, "dd_cfdna")]) >= 0.9
    assert fraction([v < 0.15 for v in values(stable_heart, "dd_cfdna")]) >= 0.9


def test_heart_patients_get_kidney_function_labs_but_no_dsa(cohort: list[PatientRecord]) -> None:
    hearts = [r for r in cohort if r.patient.organ == "heart"]
    assert values(hearts, "creatinine") and values(hearts, "egfr")
    assert not values(hearts, "dsa_mfi")


def test_dsa_positive_in_minority(cohort: list[PatientRecord], config: GeneratorConfig) -> None:
    kidneys = [r for r in cohort if r.patient.organ == "kidney"]
    ever_positive = [
        any(lab.lab == "dsa_mfi" and lab.value >= config.dsa_positive_mfi for lab in r.labs)
        for r in kidneys
    ]
    assert 0.03 <= fraction(ever_positive) <= 0.20
