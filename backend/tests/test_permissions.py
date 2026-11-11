"""Role x endpoint permission matrix: the exact status code for every role and endpoint."""

import pytest
from api_dataset import Dataset
from api_testutils import BASE_URL, login, new_flag
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from txlens_backend.api.deps import CSRF_HEADER, Role
from txlens_backend.config import Settings
from txlens_backend.deid import pseudonym

pytestmark = pytest.mark.db
ANON = "anonymous"
C, R, S, A = Role.CLINICIAN, Role.RESEARCHER, Role.DATA_STEWARD, Role.ADMIN
# endpoint -> expected status per caller. {pid} is the caller's own kind of patient id.
MATRIX: dict[str, dict[Role | str, int]] = {
    "GET /health":                 {C: 200, R: 200, S: 200, A: 200, ANON: 200},
    "GET /me":                     {C: 200, R: 200, S: 200, A: 200, ANON: 401},
    "GET /patients":               {C: 200, R: 200, S: 200, A: 403, ANON: 401},
    "GET /patients/{pid}/timeline": {C: 200, R: 200, S: 200, A: 403, ANON: 401},
    "POST /cohorts/preview":       {C: 200, R: 200, S: 403, A: 403, ANON: 401},
    "POST /cohorts":               {C: 201, R: 201, S: 403, A: 403, ANON: 401},
    "GET /cohorts":                {C: 200, R: 200, S: 403, A: 403, ANON: 401},
    "GET /cohorts/{cid}":          {C: 200, R: 200, S: 403, A: 403, ANON: 401},
    "GET /cohorts/{cid}/members":  {C: 200, R: 200, S: 403, A: 403, ANON: 401},
    "GET /worklist":               {C: 200, R: 403, S: 200, A: 403, ANON: 401},
    "GET /patients/{pid}/flags":   {C: 200, R: 403, S: 200, A: 403, ANON: 401},
    "POST /flags/{fid}/acknowledge": {C: 200, R: 403, S: 403, A: 403, ANON: 401},
    "POST /flags/{fid}/resolve":   {C: 200, R: 403, S: 403, A: 403, ANON: 401},
}  # fmt: skip
CALLERS: list[Role | str] = [C, R, S, A, ANON]
BODY = {"name": "matrix", "rules": {"organ": "kidney"}}


@pytest.fixture(scope="module")
def cohort_id(api_app: FastAPI) -> str:
    """A center A cohort that every role is asked about."""
    with TestClient(api_app, base_url=BASE_URL, headers={CSRF_HEADER: "t"}) as client:
        login(client, Role.CLINICIAN)
        created: str = client.post("/cohorts", json=BODY).json()["cohort_id"]
    return created


def patient_id(caller: Role | str, data: Dataset, settings: Settings) -> str:
    """A center A patient as this caller would address it."""
    tenant, person_id = next(v for v in data.persons.values() if v[0] == "center_a")
    if caller == Role.CLINICIAN:
        return str(person_id)
    return pseudonym(settings.deid_hmac_key.get_secret_value(), tenant, person_id)


@pytest.mark.parametrize("caller", CALLERS, ids=str)
@pytest.mark.parametrize("endpoint", list(MATRIX))
def test_permission_matrix(
    client: TestClient,
    api_data: Dataset,
    api_settings: Settings,
    api_owner: Engine,
    api_flags: object,
    cohort_id: str,
    endpoint: str,
    caller: Role | str,
) -> None:
    if isinstance(caller, Role):
        assert login(client, caller).status_code == 200
    method, path = endpoint.split(" ")
    fid = new_flag(api_owner) if "{fid}" in path else ""
    path = path.format(pid=patient_id(caller, api_data, api_settings), cid=cohort_id, fid=fid)
    if method == "GET":
        response = client.get(path)
    elif path.startswith("/flags/"):
        response = client.post(path, json={"note": "permission matrix"})
    else:
        body = BODY["rules"] if path.endswith("preview") else BODY
        response = client.post(path, json=body)
    assert response.status_code == MATRIX[endpoint][caller], response.text


def test_matrix_covers_every_route(api_app: FastAPI) -> None:
    documented = {
        f"{method.upper()} {path}"
        for path, operations in api_app.openapi()["paths"].items()
        for method in operations
    }
    auth = {"POST /auth/login", "POST /auth/refresh", "POST /auth/logout"}  # see test_auth.py
    named = {e.replace("{pid}", "{patient_id}").replace("{cid}", "{cohort_id}")
             .replace("{fid}", "{flag_id}") for e in MATRIX}  # fmt: skip
    assert documented == named | auth
