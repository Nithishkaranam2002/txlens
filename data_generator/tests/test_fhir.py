"""FHIR bundles carry deaths and graft losses the way each center writes them."""

from txlens_datagen.config import Center, GeneratorConfig
from txlens_datagen.models import PatientRecord
from txlens_datagen.rand import rng_for
from txlens_datagen.writers.fhir import SNOMED_CODES, make_bundle


def resources(record: PatientRecord, center: Center) -> list[dict[str, object]]:
    return [e["resource"] for e in make_bundle(record, center, rng_for(0, 0))["entry"]]


def test_dead_patient_has_deceased_date_in_center_format(
    cohort: list[PatientRecord], config: GeneratorConfig
) -> None:
    record = next(r for r in cohort if r.outcome.death_date is not None)
    assert record.outcome.death_date is not None
    for center in config.centers:
        patient = resources(record, center)[0]
        expected = record.outcome.death_date.isoformat()
        suffix = "T00:00:00" if center.code == "b" else ""
        assert patient["deceasedDateTime"] == expected + suffix


def test_living_patient_has_no_deceased_date(
    cohort: list[PatientRecord], config: GeneratorConfig
) -> None:
    record = next(r for r in cohort if r.outcome.death_date is None)
    assert "deceasedDateTime" not in resources(record, config.centers[0])[0]


def test_graft_loss_is_a_dated_condition_coded_by_center_a(
    cohort: list[PatientRecord], config: GeneratorConfig
) -> None:
    record = next(r for r in cohort if r.outcome.graft_loss_date is not None)
    by_code = {c.code: c for c in config.centers}
    loss = next(r for r in resources(record, by_code["a"]) if str(r["id"]).endswith("-graft-loss"))
    code, _ = SNOMED_CODES[f"{record.patient.organ}_graft_failure"]
    assert loss["code"]["coding"][0]["code"] == code  # type: ignore[index]
    assert loss["onsetDateTime"] == str(record.outcome.graft_loss_date)
    loss_b = next(
        r for r in resources(record, by_code["b"]) if str(r["id"]).endswith("-graft-loss")
    )
    assert "coding" not in loss_b["code"]  # type: ignore[operator]


def test_no_graft_loss_condition_when_graft_survives(
    cohort: list[PatientRecord], config: GeneratorConfig
) -> None:
    record = next(r for r in cohort if r.outcome.graft_loss_date is None)
    ids = [str(r["id"]) for r in resources(record, config.centers[0])]
    assert not any(i.endswith("-graft-loss") for i in ids)
