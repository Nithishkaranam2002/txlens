"""Surveillance runs in the database: idempotent re-runs, the review workflow, clearing, the
run record, and the same flags from the database path as from the ground-truth CSV path."""

from datetime import date

import pytest
from api_dataset import Dataset
from db_testutils import act_as_app
from sqlalchemy import Connection, text

from txlens_backend.surveillance import csv_source
from txlens_backend.surveillance.config import load_configs
from txlens_backend.surveillance.engine import evaluate
from txlens_backend.surveillance.repository import (
    InvalidTransitionError,
    Status,
    load_series,
    set_status,
)
from txlens_backend.surveillance.run import run_in, transplant_configs

pytestmark = pytest.mark.db
TENANT = "center_a"
AS_OF = date(2024, 9, 30)
LATER = date(2025, 3, 31)


def run(conn: Connection, as_of: date = AS_OF) -> tuple[int, int]:
    """One run for center A as the app role: (opened, cleared)."""
    act_as_app(conn, TENANT)
    summary = run_in(conn, TENANT, as_of, transplant_configs())
    return summary.flags_opened, summary.flags_cleared


def clinician(conn: Connection) -> str:
    """Center A's demo clinician."""
    return str(conn.execute(
        text("SELECT user_id FROM app.app_user WHERE username = 'clinician.center_a'")
    ).scalar_one())  # fmt: skip


def flags(conn: Connection, where: str = "TRUE") -> list[tuple[str, str, str, object]]:
    """(flag_id, evidence_key, status, cleared_at) of center A's flags."""
    return [tuple(r) for r in conn.execute(text(
        "SELECT flag_id::text, evidence_key, status, cleared_at FROM app.surveillance_flag "
        f"WHERE {where} ORDER BY flag_id"
    ))]  # fmt: skip


def test_rerun_is_idempotent(api_tx: Connection) -> None:
    opened, _ = run(api_tx)
    assert opened > 10
    before = flags(api_tx)
    assert run(api_tx) == (0, 0)
    assert flags(api_tx) == before


def test_resolved_flag_is_not_reopened_for_the_same_evidence(api_tx: Connection) -> None:
    run(api_tx)
    flag_id, key, _, _ = flags(api_tx)[0]
    set_status(api_tx, flag_id, Status.RESOLVED, clinician(api_tx), "reviewed, no action")
    run(api_tx)
    assert flags(api_tx, f"evidence_key = '{key}'") == [(flag_id, key, "resolved", None)]


def test_flags_that_stop_firing_are_cleared_and_new_evidence_opens_new_flags(
    api_tx: Connection,
) -> None:
    run(api_tx)
    first = {key for _, key, _, _ in flags(api_tx)}
    opened, cleared = run(api_tx, LATER)
    assert opened > 0 and cleared > 0
    now_open = {key for _, key, _, cleared_at in flags(api_tx) if cleared_at is None}
    assert now_open - first  # new evidence -> new flags
    assert first - now_open  # old evidence no longer firing -> cleared, not deleted
    assert {key for _, key, _, _ in flags(api_tx)} >= first


def test_replaying_an_older_date_does_not_clear_newer_flags(api_tx: Connection) -> None:
    run(api_tx, LATER)
    newer = flags(api_tx, f"as_of = '{LATER}' AND cleared_at IS NULL")
    run(api_tx, AS_OF)
    assert flags(api_tx, f"as_of = '{LATER}' AND cleared_at IS NULL") == newer


def test_review_workflow_transitions(api_tx: Connection) -> None:
    run(api_tx)
    user = clinician(api_tx)
    first, second = (f[0] for f in flags(api_tx)[:2])
    change = set_status(api_tx, first, Status.ACKNOWLEDGED, user, "seen")
    assert change is not None and (change.old, change.new) == (Status.NEW, Status.ACKNOWLEDGED)
    with pytest.raises(InvalidTransitionError):
        set_status(api_tx, first, Status.ACKNOWLEDGED, user, None)
    set_status(api_tx, first, Status.RESOLVED, user, None)
    with pytest.raises(InvalidTransitionError):
        set_status(api_tx, first, Status.ACKNOWLEDGED, user, None)
    set_status(api_tx, second, Status.RESOLVED, user, None)  # new -> resolved directly
    row = api_tx.execute(text(
        "SELECT status, note, status_changed_by::text AS by, status_changed_at "
        "FROM app.surveillance_flag WHERE flag_id = :f"), {"f": first}).one()  # fmt: skip
    assert (row.status, row.note, row.by) == ("resolved", "seen", user)  # note kept
    assert row.status_changed_at is not None
    assert set_status(api_tx, "00000000-0000-0000-0000-000000000000", Status.RESOLVED,
                      user, None) is None  # fmt: skip


def test_run_is_recorded_and_audited(api_tx: Connection) -> None:
    act_as_app(api_tx, TENANT)
    summary = run_in(api_tx, TENANT, AS_OF, transplant_configs())
    recorded = api_tx.execute(text(
        "SELECT as_of, configs, patients, flags_opened FROM app.surveillance_run "
        "WHERE run_id = :r"), {"r": summary.run_id}).one()  # fmt: skip
    assert recorded.as_of == AS_OF and recorded.patients == summary.patients > 0
    assert recorded.configs == {"kidney_transplant": 1, "heart_transplant": 1}
    audit = api_tx.execute(text(
        "SELECT action, details FROM app.audit_log WHERE object_id = :r"),
        {"r": summary.run_id}).one()  # fmt: skip
    assert audit.action == "surveillance_run"
    assert audit.details["flags_opened"] == summary.flags_opened


def test_database_path_and_replay_path_give_the_same_flags(
    api_tx: Connection, api_data: Dataset
) -> None:
    """cdm + engine == ground-truth CSVs + engine, for one center and one date."""
    act_as_app(api_tx, TENANT)
    from_db: set[tuple[int, str, str]] = set()
    from_csv: set[tuple[int, str, str]] = set()
    for config in transplant_configs():
        for person_id, series in load_series(api_tx, config, LATER).items():
            from_db |= {(person_id, f.rule_id, f.evidence_key)
                        for f in evaluate(config, series, LATER)}  # fmt: skip
        patients = csv_source.load(api_data.root / "ground_truth", config, skip_feed_issues=False)
        for patient_id, patient in patients.items():
            if patient.tenant_id == TENANT:
                person_id = api_data.persons[patient_id][1]
                from_csv |= {(person_id, f.rule_id, f.evidence_key)
                             for f in evaluate(config, patient.series, LATER)}  # fmt: skip
    assert len(from_db) > 10
    assert len({rule for _, rule, _ in from_db}) >= 4
    assert from_db == from_csv


def test_oncology_is_not_loaded_from_cdm(api_tx: Connection) -> None:
    act_as_app(api_tx, TENANT)
    with pytest.raises(ValueError, match="only transplant programs"):
        load_series(api_tx, load_configs()["oncology_tumor_dna"], AS_OF)
