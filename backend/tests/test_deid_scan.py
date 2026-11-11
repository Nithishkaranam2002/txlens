"""De-identification scan: no researcher or data steward response contains an identifier.

Every response a de-identified role can get is collected: all patient pages, every
timeline, cohort preview/create/list/get/members, and (data stewards) the surveillance
worklist and every patient's flags. The scan searches each body for every
MRN, given name, family name, full name and birth date (several formats) in
app.patient_identity, of all centers, plus the real person ids. A clinician run of the same
scanner must find identifiers, which shows that the scanner itself works.
"""

import re
from collections.abc import Iterator

import pytest
from api_testutils import login
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, text

from txlens_backend.api.deps import Role

pytestmark = pytest.mark.db
RULES = {"organ": "kidney",
         "lab_rules": [{"lab": "dd_cfdna", "op": ">=", "value": 1.0}]}  # fmt: skip


@pytest.fixture(scope="module")
def identifiers(api_owner: Engine, api_app: object, api_flags: object) -> re.Pattern[str]:
    """One case-insensitive, whole-word pattern matching any identifier of any patient."""
    with api_owner.connect() as conn:
        rows = conn.execute(
            text("SELECT person_id, mrn, given_name, family_name, birth_date "
                 "FROM app.patient_identity")
        ).all()  # fmt: skip
    values: set[str] = set()
    for person_id, mrn, given, family, birth in rows:
        values |= {str(person_id), mrn, birth.isoformat(), birth.strftime("%m/%d/%Y"),
                   birth.strftime("%d.%m.%Y")}  # fmt: skip
        values |= {n for n in (given, family) if n}
        if given and family:
            values |= {f"{given} {family}", f"{family}, {given}"}
    alternatives = "|".join(re.escape(v) for v in sorted(values, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{alternatives})(?![\w-])", re.IGNORECASE)


def crawl(
    client: TestClient, may_build_cohorts: bool, sees_worklist: bool = False
) -> Iterator[Response]:
    """Every response this (logged-in) caller can get from the patient, cohort and
    surveillance endpoints."""
    offset = 0
    while True:
        page = client.get("/patients", params={"limit": 200, "offset": offset})
        yield page
        items = page.json()["items"]
        for item in items:
            yield client.get(f"/patients/{item['patient_id']}/timeline")
            if sees_worklist:
                yield client.get(f"/patients/{item['patient_id']}/flags")
        offset += len(items)
        if not items:
            break
    if sees_worklist:
        worklist = client.get("/worklist", params={"limit": 200})
        assert worklist.json()["items"], "the worklist must have patients to scan"
        yield worklist
    if may_build_cohorts:
        yield client.post("/cohorts/preview", json=RULES)
        created = client.post("/cohorts", json={"name": "scan", "rules": RULES})
        yield created
        cohort_id = created.json()["cohort_id"]
        yield client.get("/cohorts")
        yield client.get(f"/cohorts/{cohort_id}")
        yield client.get(f"/cohorts/{cohort_id}/members", params={"limit": 200})


def leaks(responses: Iterator[Response], pattern: re.Pattern[str]) -> list[str]:
    """'<path>: <identifier>' for every identifier found in a response body."""
    found = []
    count = 0
    for response in responses:
        assert response.status_code in (200, 201), (response.url, response.text)
        count += 1
        found += [f"{response.url.path}: {m.group(0)}" for m in pattern.finditer(response.text)]
    assert count > 20, "the crawl must cover many responses"
    return found


@pytest.mark.parametrize(
    ("role", "builds_cohorts", "sees_worklist"),
    [(Role.RESEARCHER, True, False), (Role.DATA_STEWARD, False, True)],
    ids=["researcher", "data_steward"],
)
@pytest.mark.parametrize("tenant", ["center_a", "center_b", "center_c"])
def test_no_identifier_in_any_deidentified_response(
    client: TestClient,
    identifiers: re.Pattern[str],
    role: Role,
    builds_cohorts: bool,
    sees_worklist: bool,
    tenant: str,
) -> None:
    login(client, role, tenant)
    assert leaks(crawl(client, builds_cohorts, sees_worklist), identifiers) == []


def test_scanner_finds_identifiers_in_clinician_responses(
    client: TestClient, identifiers: re.Pattern[str]
) -> None:
    login(client, Role.CLINICIAN)
    found = leaks(crawl(client, may_build_cohorts=True, sees_worklist=True), identifiers)
    assert len(found) > 100


def test_note_text_never_reaches_any_role(client: TestClient, api_owner: Engine) -> None:
    with api_owner.connect() as conn:
        texts = conn.execute(text("SELECT note_text FROM cdm.note LIMIT 5")).scalars()
        snippets = [t[:200] for t in texts]
    login(client, Role.CLINICIAN)
    bodies = "".join(r.text for r in crawl(client, may_build_cohorts=False))
    assert not any(snippet in bodies for snippet in snippets)
