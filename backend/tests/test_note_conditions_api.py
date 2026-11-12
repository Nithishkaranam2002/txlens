"""Conditions read from clinic notes on the patient timeline: the evidence sentence per role."""

from collections.abc import Iterator

import pytest
from api_testutils import login
from fastapi import FastAPI
from fastapi.testclient import TestClient
from note_testutils import SURROGATE, NoteCondition, remove_note_condition, seed_note_condition
from sqlalchemy import Engine

from txlens_backend.api.deps import Role
from txlens_backend.config import Settings
from txlens_backend.deid import pseudonym

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def note_condition(
    api_owner: Engine, api_app_engine: Engine, api_app: FastAPI
) -> Iterator[NoteCondition]:
    """Two notes of one center_a patient say "<name> reports graft rejection today"."""
    seeded = seed_note_condition(api_owner, api_app_engine)
    yield seeded
    remove_note_condition(api_owner, seeded)


def from_note(client: TestClient, patient: str) -> list[dict[str, object]]:
    timeline = client.get(f"/patients/{patient}/timeline").json()
    return [c for c in timeline["conditions"] if c["source"] == "note"]


def test_clinician_sees_the_sentence_as_written_once_per_concept(
    client: TestClient, note_condition: NoteCondition
) -> None:
    login(client, Role.CLINICIAN)
    (found,) = from_note(client, str(note_condition.person_id))
    assert found["concept_id"] == note_condition.concept_id and found["note_count"] == 2
    evidence = found["evidence"]
    assert isinstance(evidence, dict)
    assert evidence["note_id"] == note_condition.note_ids[0]
    assert (
        evidence["sentence"] == f"HPI: {note_condition.given_name} reports graft rejection today."
    )


def test_steward_sees_the_de_identified_sentence(
    client: TestClient, note_condition: NoteCondition, api_settings: Settings
) -> None:
    login(client, Role.DATA_STEWARD)
    key = api_settings.deid_hmac_key.get_secret_value()
    (found,) = from_note(client, pseudonym(key, "center_a", note_condition.person_id))
    evidence = found["evidence"]
    assert isinstance(evidence, dict)
    assert evidence["sentence"] == f"HPI: {SURROGATE} reports graft rejection today."
    assert evidence["note_id"] is None


def test_researcher_sees_the_source_but_no_sentence(
    client: TestClient, note_condition: NoteCondition, api_settings: Settings
) -> None:
    login(client, Role.RESEARCHER)
    key = api_settings.deid_hmac_key.get_secret_value()
    (found,) = from_note(client, pseudonym(key, "center_a", note_condition.person_id))
    assert found["note_count"] == 2
    assert found["evidence"]["sentence"] is None and found["evidence"]["note_id"] is None  # type: ignore[index]


def test_record_conditions_are_unchanged_and_marked_as_record(
    client: TestClient, note_condition: NoteCondition
) -> None:
    login(client, Role.CLINICIAN)
    timeline = client.get(f"/patients/{note_condition.person_id}/timeline").json()
    record = [c for c in timeline["conditions"] if c["source"] == "record"]
    assert record and all(c["evidence"] is None and c["note_count"] is None for c in record)
