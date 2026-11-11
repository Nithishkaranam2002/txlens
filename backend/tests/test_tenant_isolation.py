"""Tenant isolation through the API: center A users never get center B data.

Guessing another center's ids gives 404, exactly like an id that does not exist, so a caller
cannot even learn whether an id is in use elsewhere. Tenant hints in the request are ignored.
"""

import pytest
from api_dataset import Dataset
from api_testutils import BASE_URL, login
from fastapi import FastAPI
from fastapi.testclient import TestClient

from txlens_backend.api.deps import CSRF_HEADER, Role
from txlens_backend.config import Settings
from txlens_backend.deid import pseudonym

pytestmark = pytest.mark.db
READERS = [Role.CLINICIAN, Role.RESEARCHER, Role.DATA_STEWARD]


def person_ids(data: Dataset, tenant: str) -> list[int]:
    return sorted(pid for t, pid in data.persons.values() if t == tenant)


def ids_as_seen_by(role: Role, data: Dataset, tenant: str, key: str) -> list[str]:
    """A center's patients as `role` addresses them (person id or pseudonym)."""
    ids = person_ids(data, tenant)
    if role is Role.CLINICIAN:
        return [str(p) for p in ids]
    return [pseudonym(key, tenant, p) for p in ids]


@pytest.fixture(scope="module")
def center_b_cohort(api_app: FastAPI) -> str:
    with TestClient(api_app, base_url=BASE_URL, headers={CSRF_HEADER: "t"}) as client:
        login(client, Role.RESEARCHER, "center_b")
        created: str = client.post(
            "/cohorts", json={"name": "B only", "rules": {"organ": "kidney"}}
        ).json()["cohort_id"]
    return created


@pytest.mark.parametrize("role", READERS, ids=str)
def test_other_centers_patient_ids_are_404(
    client: TestClient, api_data: Dataset, api_settings: Settings, role: Role
) -> None:
    key = api_settings.deid_hmac_key.get_secret_value()
    login(client, role, "center_a")
    for guessed in ids_as_seen_by(role, api_data, "center_b", key)[:10]:
        response = client.get(f"/patients/{guessed}/timeline")
        assert response.status_code == 404, guessed
        assert response.json() == {"detail": "Patient not found"}  # same as a missing id
    nonexistent = "999999999" if role is Role.CLINICIAN else "R-ffffffffffffffff"
    assert client.get(f"/patients/{nonexistent}/timeline").json() == {"detail": "Patient not found"}


@pytest.mark.parametrize("role", READERS, ids=str)
def test_lists_contain_only_own_center(
    client: TestClient, api_data: Dataset, api_settings: Settings, role: Role
) -> None:
    key = api_settings.deid_hmac_key.get_secret_value()
    login(client, role, "center_a")
    seen = {str(i["patient_id"]) for i in client.get("/patients?limit=200").json()["items"]}
    assert seen == set(ids_as_seen_by(role, api_data, "center_a", key))
    for other in ("center_b", "center_c"):
        assert not seen & set(ids_as_seen_by(role, api_data, other, key))


def test_tenant_hints_in_the_request_are_ignored(client: TestClient, api_data: Dataset) -> None:
    login(client, Role.CLINICIAN, "center_a")
    hinted = client.get(
        "/patients",
        params={"tenant_id": "center_b", "limit": 200},
        headers={"X-Tenant-ID": "center_b"},
    ).json()
    assert {i["patient_id"] for i in hinted["items"]} == set(person_ids(api_data, "center_a"))


@pytest.mark.parametrize("role", [Role.CLINICIAN, Role.RESEARCHER], ids=str)
def test_other_centers_cohorts_are_404_and_unlisted(
    client: TestClient, center_b_cohort: str, role: Role
) -> None:
    login(client, role, "center_a")
    assert client.get(f"/cohorts/{center_b_cohort}").status_code == 404
    assert client.get(f"/cohorts/{center_b_cohort}/members").status_code == 404
    listed = {c["cohort_id"] for c in client.get("/cohorts?limit=200").json()["items"]}
    assert center_b_cohort not in listed


def test_center_b_still_sees_its_own_cohort(client: TestClient, center_b_cohort: str) -> None:
    login(client, Role.CLINICIAN, "center_b")
    assert client.get(f"/cohorts/{center_b_cohort}").status_code == 200
