"""Patient search (top bar and worklist): role rules, own center only, limits and audit."""

import json
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from api_dataset import Dataset
from api_testutils import login
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response  # what TestClient returns (httpx2 is installed)
from sqlalchemy import Engine, text

from txlens_backend.api.deps import Role

pytestmark = pytest.mark.db


def search(client: TestClient, q: str) -> list[dict[str, Any]]:
    response = client.get("/patients/search", params={"q": q})
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


def center_patients(data: Dataset, tenant: str) -> dict[int, dict[str, str]]:
    """person_id -> ground-truth patient row, for one center."""
    return {data.persons[r["patient_id"]][1]: r for r in data.truth("patients")
            if r["tenant_id"] == tenant}  # fmt: skip


def name_matches(patient: dict[str, Any], q: str) -> bool:
    """The clinician search rule: part of a name, or the start of the MRN."""
    names = (patient["given_name"], patient["family_name"],
             f"{patient['given_name']} {patient['family_name']}",
             f"{patient['family_name']}, {patient['given_name']}")  # fmt: skip
    return patient["mrn"].lower().startswith(q.lower()) or any(
        q.lower() in name.lower() for name in names
    )


def test_clinician_finds_by_name_and_mrn(client: TestClient, api_data: Dataset) -> None:
    login(client, Role.CLINICIAN)
    pid, row = next(iter(center_patients(api_data, "center_a").items()))
    by_mrn = search(client, row["mrn"])
    assert [p["patient_id"] for p in by_mrn] == [pid]
    assert (by_mrn[0]["given_name"], by_mrn[0]["family_name"]) == (
        row["first_name"], row["last_name"]
    )  # fmt: skip
    assert pid in [p["patient_id"] for p in search(client, row["mrn"][:-1])]  # MRN start
    full_name = f"{row['first_name']} {row['last_name']}".upper()
    assert pid in [p["patient_id"] for p in search(client, full_name)]
    assert pid in [p["patient_id"] for p in search(client, row["last_name"][1:4].lower())]


def test_results_are_capped_at_ten_and_sorted(client: TestClient) -> None:
    login(client, Role.CLINICIAN)
    found = search(client, "an")
    assert 0 < len(found) <= 10
    names = [(p["family_name"], p["given_name"]) for p in found]
    assert names == sorted(names)
    assert all(name_matches(p, "an") for p in found)
    login(client, Role.RESEARCHER)
    everyone = search(client, "R-")  # every pseudonym starts with R-
    assert len(everyone) == 10
    assert [p["patient_id"] for p in everyone] == sorted(p["patient_id"] for p in everyone)


@pytest.mark.parametrize("role", [Role.RESEARCHER, Role.DATA_STEWARD])
def test_deidentified_roles_match_pseudonyms_never_names(
    client: TestClient, api_data: Dataset, role: Role
) -> None:
    login(client, role)
    row = next(iter(center_patients(api_data, "center_a").values()))
    for q in (row["last_name"], row["first_name"], row["mrn"], row["mrn"][:4]):
        assert search(client, q) == []
    pseudonym = search(client, "R-")[0]["patient_id"]
    found = search(client, pseudonym[:8].lower())
    assert pseudonym in [p["patient_id"] for p in found]
    for patient in found:
        assert str(patient["patient_id"]).startswith(pseudonym[:8])
        assert not {"mrn", "given_name", "family_name", "birth_date"} & set(patient)


def test_other_centers_patients_are_never_returned(client: TestClient, api_data: Dataset) -> None:
    own = center_patients(api_data, "center_a")
    other_pid, other = next(iter(center_patients(api_data, "center_b").items()))
    login(client, Role.CLINICIAN, "center_a")
    for q in (other["mrn"], other["last_name"], f"{other['first_name']} {other['last_name']}",
              "an", "er"):  # fmt: skip
        ids = {p["patient_id"] for p in search(client, q)}
        assert other_pid not in ids and ids <= set(own)


def test_admin_is_blocked(client: TestClient) -> None:
    login(client, Role.ADMIN)
    assert client.get("/patients/search", params={"q": "an"}).status_code == 403


@pytest.mark.parametrize("q", ["a", " a ", "x" * 101])
def test_query_must_be_two_to_a_hundred_characters(client: TestClient, q: str) -> None:
    login(client, Role.CLINICIAN)
    assert client.get("/patients/search", params={"q": q}).status_code == 422
    assert client.get("/patients/search").status_code == 422


def test_like_wildcards_are_literal(client: TestClient) -> None:
    login(client, Role.CLINICIAN)
    assert search(client, "%%") == search(client, "__") == []


def audit_details(owner: Engine, response: Response) -> dict[str, Any]:
    with owner.connect() as conn:
        details: dict[str, Any] = conn.execute(
            text("SELECT details FROM app.audit_log WHERE request_id = :r"),
            {"r": response.headers["X-Request-ID"]},
        ).scalar_one()
    return details


def test_audit_keeps_a_fingerprint_not_the_search_text(
    client: TestClient, api_owner: Engine, api_data: Dataset
) -> None:
    row = next(iter(center_patients(api_data, "center_a").values()))
    q = f"{row['first_name']} {row['last_name']}"
    login(client, Role.CLINICIAN)
    first = client.get("/patients/search", params={"q": q})
    again = client.get("/patients/search", params={"q": q.upper()})
    other = client.get("/patients/search", params={"q": row["last_name"]})
    details = audit_details(api_owner, first)
    assert "q" not in details and q.lower() not in json.dumps(details).lower()
    assert row["last_name"].lower() not in json.dumps(details).lower()
    assert details["q_length"] == len(q)
    assert details["patients"] == [p["patient_id"] for p in first.json()["items"]]
    assert audit_details(api_owner, again)["q_fingerprint"] == details["q_fingerprint"]
    assert audit_details(api_owner, other)["q_fingerprint"] != details["q_fingerprint"]
    with api_owner.connect() as conn:
        action = conn.execute(
            text("SELECT action || ':' || object_type FROM app.audit_log WHERE request_id = :r"),
            {"r": first.headers["X-Request-ID"]},
        ).scalar_one()
    assert action == "phi_view:patient_search"
    listed = client.get("/patients", params={"q": row["last_name"]})
    assert row["last_name"].lower() not in json.dumps(audit_details(api_owner, listed)).lower()


@pytest.fixture
def search_limit_of_one(api_app: FastAPI) -> Iterator[None]:
    """One search per minute per user, for this test only."""
    settings = api_app.state.ctx.settings
    before, settings.rate_limit_search_per_minute = settings.rate_limit_search_per_minute, 1
    yield
    settings.rate_limit_search_per_minute = before


@pytest.mark.usefixtures("search_limit_of_one")
def test_search_is_rate_limited(client: TestClient) -> None:
    login(client, Role.DATA_STEWARD, "center_c")
    assert client.get("/patients/search", params={"q": "R-"}).status_code == 200
    # Windows are whole clock minutes: if one just ended, the next call opens a new one.
    refused = client.get("/patients/search", params={"q": "R-"})
    if refused.status_code == 200:
        refused = client.get("/patients/search", params={"q": "R-"})
    assert refused.status_code == 429 and refused.headers["Retry-After"]


def worklist(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    response = client.get("/worklist", params={**params, "limit": 200})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == len(body["items"])
    items: list[dict[str, Any]] = body["items"]
    return items


def test_worklist_search_works_with_the_other_filters(
    client: TestClient, api_owner: Engine, api_flags: date
) -> None:
    login(client, Role.CLINICIAN, "center_a")
    everyone = worklist(client)
    target = everyone[-1]["patient"]
    q = target["family_name"][:3]
    found = worklist(client, q=q)
    assert target["patient_id"] in [r["patient"]["patient_id"] for r in found]
    assert found == [r for r in everyone if name_matches(r["patient"], q)]
    by_mrn = worklist(client, q=target["mrn"])
    assert [r["patient"]["patient_id"] for r in by_mrn] == [target["patient_id"]]
    params = {"q": q, "organ": target["organ"], "status": "new"}
    combined = worklist(client, **params)
    assert combined == [
        r for r in everyone if name_matches(r["patient"], q) and r["patient"]["organ"] == target[
            "organ"] and any(f["status"] == "new" for f in r["flags"])
    ]  # fmt: skip
    assert worklist(client, q="zzzz-no-such-patient") == []
    assert client.get("/worklist", params={"q": "a"}).status_code == 422
    response = client.get("/worklist", params={"q": target["family_name"]})
    assert (
        target["family_name"].lower() not in json.dumps(audit_details(api_owner, response)).lower()
    )


def test_data_steward_worklist_search_is_by_pseudonym(client: TestClient, api_flags: date) -> None:
    login(client, Role.CLINICIAN, "center_a")
    name = worklist(client)[0]["patient"]["family_name"]
    login(client, Role.DATA_STEWARD, "center_a")
    rows = worklist(client)
    pseudonym = rows[0]["patient"]["patient_id"]
    assert [r["patient"]["patient_id"] for r in worklist(client, q=pseudonym)] == [pseudonym]
    assert worklist(client, q=name) == []
