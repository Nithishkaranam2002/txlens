"""Condition concept search for the cohort builder."""

import pytest
from api_testutils import login
from fastapi.testclient import TestClient

from txlens_backend.api.deps import Role

pytestmark = pytest.mark.db
DIABETES = 201820  # SNOMED 73211009, parent of type 2 diabetes in the test vocabulary


def test_search_by_code_reports_descendants(client: TestClient, api_data: object) -> None:
    login(client, Role.RESEARCHER)
    [hit] = client.get("/concepts/conditions", params={"q": "73211009"}).json()
    assert (hit["concept_id"], hit["code"], hit["descendants"]) == (DIABETES, "73211009", 1)


def test_search_by_name_finds_only_standard_conditions(
    client: TestClient, api_data: object
) -> None:
    login(client, Role.CLINICIAN)
    hits = client.get("/concepts/conditions", params={"q": "test only"}).json()
    assert len(hits) == 7  # the seven condition concepts; no procedures, labs or drugs
    assert client.get("/concepts/conditions", params={"q": "%_"}).json() == []  # literal


def test_query_must_be_two_characters(client: TestClient) -> None:
    login(client, Role.CLINICIAN)
    assert client.get("/concepts/conditions", params={"q": "a"}).status_code == 422
