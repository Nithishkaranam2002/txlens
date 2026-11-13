"""The risk model's visit dataset on the API database (RLS on), checked against ground truth."""

import dataclasses
import math
import random
from datetime import date, timedelta

import pytest
from api_dataset import REJECTION, Dataset
from sqlalchemy import Engine

from txlens_backend.db import tenant_transaction
from txlens_backend.risk.dataset import COLUMNS, build, load_records
from txlens_backend.risk.features import features_at
from txlens_backend.risk.labels import HORIZON
from txlens_backend.risk.record import PatientRecord
from txlens_backend.surveillance.run import transplant_configs

pytestmark = pytest.mark.db

AS_OF = date(2026, 9, 30)


def iso(value: str) -> date:
    return date.fromisoformat(value[:10])


def expected_label(data: Dataset, patient_id: str, visit: date) -> int | None:
    """The label computed straight from the generator's episodes and outcomes."""
    biopsies = [iso(e["biopsy_date"]) for e in data.truth("rejection_episodes")
                if e["patient_id"] == patient_id and e["rejection_type"] in REJECTION]  # fmt: skip
    if any(visit < b <= visit + HORIZON for b in biopsies):
        return 1
    outcome = next(o for o in data.truth("outcomes") if o["patient_id"] == patient_id)
    ends = [iso(outcome[k]) for k in ("graft_loss_date", "death_date") if outcome[k]]
    if min(iso(outcome["last_follow_up_date"]), AS_OF) < visit + HORIZON:
        return None
    if ends and min(ends) <= visit + HORIZON:
        return None
    return 0


def test_labels_match_the_ground_truth(api_data: Dataset, api_app_engine: Engine) -> None:
    with tenant_transaction(api_app_engine, "center_a") as conn:
        frame = build(conn, "center_a", transplant_configs(), AS_OF)
    assert list(frame.columns) == COLUMNS and len(frame) > 0
    patient = {pid: p for p, (_, pid) in api_data.persons.items()}
    for person_id, visit, value in zip(frame["person_id"], frame["visit"], frame["label"],
                                       strict=True):  # fmt: skip
        want = expected_label(api_data, patient[int(person_id)], visit)
        got = None if math.isnan(value) else int(value)
        assert got == want, (person_id, visit)
    assert frame["label"].sum() > 0


def test_dataset_stays_in_the_callers_center(api_data: Dataset, api_app_engine: Engine) -> None:
    keys = {}
    for tenant in ("center_a", "center_b"):
        with tenant_transaction(api_app_engine, tenant) as conn:
            keys[tenant] = set(build(conn, tenant, transplant_configs(), AS_OF)["person_id"])
    owners = {pid: tenant for tenant, pid in api_data.persons.values()}
    assert keys["center_a"] and keys["center_a"].isdisjoint(keys["center_b"])
    assert {owners[p] for p in keys["center_a"]} == {"center_a"}


def truncated(record: PatientRecord, visit: date) -> PatientRecord:
    """The record as it stood at the end of the visit day."""
    return dataclasses.replace(
        record,
        observations={k: tuple(o for o in v if o.day <= visit)
                      for k, v in record.observations.items()},
        biopsy_days=tuple(b for b in record.biopsy_days if b <= visit),
        results=tuple(r for r in record.results if r.known_day <= visit),
        dgf_known_day=record.dgf_known_day if (record.dgf_known_day or date.max) <= visit else None,
        follow_up_end=visit, end_day=None,
    )  # fmt: skip


def test_features_on_loaded_records_ignore_everything_after_the_visit(
    api_app_engine: Engine,
) -> None:
    with tenant_transaction(api_app_engine, "center_b") as conn:
        records = load_records(conn, "center_b", transplant_configs(), AS_OF)
    rng = random.Random(3)
    checked = 0
    for record, config in rng.sample(records, min(40, len(records))):
        assert config.schedule is not None
        for offset in (35, 95, 200, 400):
            visit = record.transplant_day + timedelta(days=offset)
            full = features_at(record, config.schedule, visit)
            cut = features_at(truncated(record, visit), config.schedule, visit)
            assert all((math.isnan(full[k]) and math.isnan(cut[k])) or full[k] == cut[k]
                       for k in full), (record.key, visit)  # fmt: skip
            checked += 1
    assert checked > 0
