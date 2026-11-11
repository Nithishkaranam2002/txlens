"""Worklist and flag review through the API: ranking, identified vs de-identified views, the
review workflow, and that de-identified flags carry only shifted dates."""

import re
from datetime import date
from typing import Any

import pytest
from api_testutils import login, new_flag
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from txlens_backend.api.deps import Role
from txlens_backend.config import Settings
from txlens_backend.deid import pseudonym

pytestmark = pytest.mark.db
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def worklist(client: TestClient) -> list[dict[str, Any]]:
    """Every worklist row of the logged-in caller's center."""
    items: list[dict[str, Any]] = client.get("/worklist", params={"limit": 200}).json()["items"]
    return items


def open_flags(client: TestClient) -> dict[str, dict[str, Any]]:
    """flag_id -> flag, for every flag on the caller's worklist."""
    return {f["flag_id"]: f for row in worklist(client) for f in row["flags"]}


def test_clinician_worklist_is_ranked_identified_and_open_only(
    client: TestClient, api_flags: date
) -> None:
    login(client, Role.CLINICIAN, "center_a")
    rows = worklist(client)
    assert len(rows) > 5
    scores = [row["score"] for row in rows]
    assert scores == sorted(scores, reverse=True)
    for row in rows:
        assert isinstance(row["patient"]["patient_id"], int) and row["patient"]["mrn"]
        assert row["flags"]
        for flag in row["flags"]:
            assert flag["status"] != "resolved" and not flag["cleared"]
            assert flag["reason"] and flag["evidence"]


def test_data_steward_sees_pseudonyms_and_only_shifted_dates(
    client: TestClient, api_flags: date
) -> None:
    login(client, Role.CLINICIAN, "center_a")
    true_flags = open_flags(client)
    login(client, Role.DATA_STEWARD, "center_a")
    rows = worklist(client)
    assert {f["flag_id"] for row in rows for f in row["flags"]} == set(true_flags)
    for row in rows:
        assert str(row["patient"]["patient_id"]).startswith("R-")
        assert "mrn" not in row["patient"]
        shifts = set()
        for flag in row["flags"]:
            truth = true_flags[flag["flag_id"]]
            assert flag["note"] is None and flag["status_changed_by"] is None
            for shown, real in zip(flag["evidence"], truth["evidence"], strict=True):
                shifts.add((date.fromisoformat(shown["date"])
                            - date.fromisoformat(real["date"])).days)  # fmt: skip
            real_dates = set(ISO_DATE.findall(truth["reason"]))
            assert real_dates and not real_dates & set(ISO_DATE.findall(flag["reason"]))
        assert len(shifts) == 1 and 0 not in shifts  # one non-zero shift per patient


def test_acknowledge_then_resolve(client: TestClient, api_owner: Engine, api_flags: date) -> None:
    flag_id = new_flag(api_owner)
    login(client, Role.CLINICIAN, "center_a")
    acknowledged = client.post(f"/flags/{flag_id}/acknowledge", json={"note": "calling patient"})
    assert acknowledged.status_code == 200
    body = acknowledged.json()
    assert (body["status"], body["note"]) == ("acknowledged", "calling patient")
    assert body["status_changed_by"] and body["status_changed_at"]
    assert client.post(f"/flags/{flag_id}/acknowledge").status_code == 409
    resolved = client.post(f"/flags/{flag_id}/resolve")  # no body: the note is kept
    assert (resolved.json()["status"], resolved.json()["note"]) == ("resolved", "calling patient")
    conflict = client.post(f"/flags/{flag_id}/resolve")
    assert conflict.status_code == 409 and "resolved" in conflict.json()["detail"]
    assert flag_id not in open_flags(client)


def test_patient_flags_include_resolved_ones(
    client: TestClient, api_owner: Engine, api_flags: date
) -> None:
    flag_id = new_flag(api_owner)
    with api_owner.connect() as conn:
        person_id = conn.execute(
            text("SELECT person_id FROM app.surveillance_flag WHERE flag_id = :f"), {"f": flag_id}
        ).scalar_one()
    login(client, Role.CLINICIAN, "center_a")
    client.post(f"/flags/{flag_id}/resolve", json={"note": "seen in clinic"})
    history = client.get(f"/patients/{person_id}/flags").json()
    assert history["patient"]["patient_id"] == person_id
    [resolved] = [f for f in history["flags"] if f["flag_id"] == flag_id]
    assert (resolved["status"], resolved["note"]) == ("resolved", "seen in clinic")


def test_overlong_note_is_rejected(client: TestClient, api_owner: Engine, api_flags: date) -> None:
    login(client, Role.CLINICIAN, "center_a")
    too_long = client.post(f"/flags/{new_flag(api_owner)}/acknowledge", json={"note": "x" * 2001})
    assert too_long.status_code == 422


@pytest.mark.parametrize(
    ("params", "keep"),
    [
        ({"organ": "heart"}, lambda row: row["patient"]["organ"] == "heart"),
        ({"severity": "high"}, lambda row: any(f["severity"] == "high" for f in row["flags"])),
        ({"status": "new"}, lambda row: any(f["status"] == "new" for f in row["flags"])),
    ],
    ids=["organ", "severity", "status"],
)
def test_worklist_filters(
    client: TestClient, api_flags: date, params: dict[str, str], keep: Any
) -> None:
    login(client, Role.CLINICIAN, "center_a")
    everyone = worklist(client)
    filtered = client.get("/worklist", params=params | {"limit": 200}).json()
    expected = [row["patient"]["patient_id"] for row in everyone if keep(row)]
    assert expected and [r["patient"]["patient_id"] for r in filtered["items"]] == expected
    assert filtered["total"] == len(expected)


def test_patient_flags_carry_review_history(
    client: TestClient, api_owner: Engine, api_settings: Settings, api_flags: date
) -> None:
    flag_id = new_flag(api_owner)
    login(client, Role.CLINICIAN, "center_a")
    client.post(f"/flags/{flag_id}/acknowledge", json={"note": "calling patient"})
    client.post(f"/flags/{flag_id}/resolve", json={"note": "repeat test normal"})
    with api_owner.connect() as conn:
        person = conn.execute(
            text("SELECT person_id FROM app.surveillance_flag WHERE flag_id = :f"), {"f": flag_id}
        ).scalar_one()
    [flag] = [f for f in client.get(f"/patients/{person}/flags").json()["flags"]
              if f["flag_id"] == flag_id]  # fmt: skip
    events = [(e["event"], e["note"]) for e in flag["history"]]
    assert events == [("raised", None), ("acknowledged", "calling patient"),
                      ("resolved", "repeat test normal")]  # fmt: skip
    assert all(e["by"] for e in flag["history"][1:])

    login(client, Role.DATA_STEWARD, "center_a")
    alias = pseudonym(api_settings.deid_hmac_key.get_secret_value(), "center_a", person)
    [seen] = [f for f in client.get(f"/patients/{alias}/flags").json()["flags"]
              if f["flag_id"] == flag_id]  # fmt: skip
    assert [e["event"] for e in seen["history"]] == ["raised", "acknowledged", "resolved"]
    assert all(e["by"] is None and e["note"] is None for e in seen["history"])
    shift = seen["history"][0]["at"][:10] != flag["history"][0]["at"][:10]
    assert shift  # event times move with the patient's dates


def test_surveillance_config_gives_chart_thresholds(client: TestClient) -> None:
    login(client, Role.RESEARCHER, "center_a")
    kidney = client.get("/surveillance/configs/kidney").json()
    threshold = next(r for r in kidney["rules"] if r["kind"] == "threshold")
    assert [level["at_or_above"] for level in threshold["levels"]] == [0.5, 1.0]
    tacrolimus = next(r for r in kidney["rules"] if r["kind"] == "range_by_phase")
    assert tacrolimus["phases"][0] == {"from_month": 0, "to_month": 3, "low": 8.0, "high": 12.0}
    assert client.get("/surveillance/configs/lung").status_code == 404
