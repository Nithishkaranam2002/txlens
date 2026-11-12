"""Role x endpoint permission matrix: the exact status code for every role and endpoint."""

import pytest
from api_dataset import Dataset
from api_testutils import BASE_URL, login, new_flag
from document_testutils import KIDNEY_OCR_LINES, seed_document, write_report_pdf
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

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
    "GET /concepts/conditions?q=73211009": {C: 200, R: 200, S: 403, A: 403, ANON: 401},
    "GET /surveillance/configs/kidney": {C: 200, R: 200, S: 200, A: 403, ANON: 401},
    "GET /worklist":               {C: 200, R: 403, S: 200, A: 403, ANON: 401},
    "GET /patients/{pid}/flags":   {C: 200, R: 403, S: 200, A: 403, ANON: 401},
    "POST /flags/{fid}/acknowledge": {C: 200, R: 403, S: 403, A: 403, ANON: 401},
    "POST /flags/{fid}/resolve":   {C: 200, R: 403, S: 403, A: 403, ANON: 401},
    "GET /documents/{did}/evidence": {C: 200, R: 403, S: 200, A: 403, ANON: 401},
    "GET /documents/{did}/pages/1": {C: 200, R: 403, S: 403, A: 403, ANON: 401},
    "GET /documents/{did}/fields/banff_t/crop": {C: 200, R: 403, S: 200, A: 403, ANON: 401},
    "GET /review/items":           {C: 200, R: 403, S: 200, A: 403, ANON: 401},
    "GET /review/items/biopsy_document/{rid}": {C: 200, R: 403, S: 200, A: 403, ANON: 401},
    "POST /review/items/biopsy_document/{rid}": {C: 200, R: 403, S: 200, A: 403, ANON: 401},
    "GET /review/metrics":         {C: 200, R: 403, S: 200, A: 200, ANON: 401},
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


@pytest.fixture(scope="module")
def document_id(
    api_app: FastAPI, api_owner: Engine, tmp_path_factory: pytest.TempPathFactory
) -> str:
    """A center A document whose scanned file is in the API's document store."""
    root = tmp_path_factory.mktemp("documents")
    (root / "pathology").mkdir()
    write_report_pdf(root / "pathology" / "matrix.pdf")
    api_app.state.ctx.settings.documents_root = root
    return new_document(api_owner, "extracted", "pathology/matrix.pdf")


def new_document(owner: Engine, status: str, source_path: str = "pathology/x.pdf") -> str:
    """A center A document (not linked to a patient) in this status, its text below the
    report header (so a line may be shown as a crop)."""
    with owner.begin() as conn:
        doc = seed_document(conn, "center_a", KIDNEY_OCR_LINES, status, body_top=300)
        conn.execute(text("UPDATE app.document SET source_path = :s WHERE document_id = :d"),
                     {"s": source_path, "d": doc.document_id})  # fmt: skip
    return doc.document_id


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
    document_id: str,
    endpoint: str,
    caller: Role | str,
) -> None:
    if isinstance(caller, Role):
        assert login(client, caller).status_code == 200
    method, path = endpoint.split(" ")
    fid = new_flag(api_owner) if "{fid}" in path else ""
    rid = new_document(api_owner, "in_review") if "{rid}" in path else ""
    path = path.format(pid=patient_id(caller, api_data, api_settings), cid=cohort_id, fid=fid,
                       did=document_id, rid=rid)  # fmt: skip
    if method == "GET":
        response = client.get(path)
    elif path.startswith("/review/"):
        response = client.post(path, json={"action": "approve"})
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
             .replace("{fid}", "{flag_id}").split("?")[0].replace("/kidney", "/{program}")
             .replace("biopsy_document/{rid}", "{item_type}/{item_key}")
             .replace("{did}", "{document_id}").replace("{rid}", "{document_id}")
             .replace("/pages/1", "/pages/{page}").replace("/banff_t/", "/{field}/")
             for e in MATRIX}  # fmt: skip
    assert documented == named | auth
