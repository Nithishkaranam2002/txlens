"""The survival API: roles, suppression for every role, the landmark guard, cohorts, exports,
audit and tenant isolation (API database, generated patients)."""

import csv
import io
from collections.abc import Iterator

import pymupdf
import pytest
from api_testutils import login
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from txlens_backend.api.deps import Role
from txlens_backend.db import tenant_transaction
from txlens_backend.services import cohorts
from txlens_backend.survival.export import SYNTHETIC_LABEL
from txlens_backend.survival.service import members_by

pytestmark = pytest.mark.db
REJECTION = 4309320  # TEST ONLY transplant rejection concept (api_dataset)
COUNT_FIELDS = {"n", "events", "n_risk", "n_exposed", "events_exposed"}
HIGH_DD_CFDNA: dict[str, object] = {"lab_rules": [{"lab": "dd_cfdna", "op": ">=", "value": 1.0,
                                "days_from_transplant": [0, 365]}]}  # fmt: skip


def counts(value: object, key: str = "") -> Iterator[object]:
    """Every count in a report (by field name, and the log-rank observed counts)."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from (v.values() if k == "observed" and isinstance(v, dict) else counts(v, k))
    elif isinstance(value, list):
        for item in value:
            yield from counts(item, key)
    elif key in COUNT_FIELDS:
        yield value


def analyse(client: TestClient, status: int = 200, **body: object) -> dict[str, object]:
    response = client.post("/survival/analysis", json=body)
    assert response.status_code == status, response.text
    result: dict[str, object] = response.json()
    return result


def new_cohort(client: TestClient, rules: dict[str, object], name: str = "high dd-cfDNA") -> str:
    response = client.post("/cohorts", json={"name": name, "rules": rules})
    assert response.status_code == 201, response.text
    return str(response.json()["cohort_id"])


@pytest.mark.parametrize("role", [Role.CLINICIAN, Role.RESEARCHER])
@pytest.mark.parametrize("grouping", ["none", "organ", "donor_type", "rejection_year1"])
def test_both_roles_get_a_suppressed_report(client: TestClient, role: Role, grouping: str) -> None:
    login(client, role)
    report = analyse(client, grouping=grouping)
    found = list(counts(report))
    assert found and not [c for c in found if isinstance(c, int) and 0 < c < 11]
    assert report["synthetic_data"] is True
    assert "Synthetic data" in str(report["methods"])


@pytest.mark.parametrize("role", [Role.DATA_STEWARD, Role.ADMIN])
def test_other_roles_are_refused(client: TestClient, role: Role) -> None:
    login(client, role)
    assert client.post("/survival/analysis", json={}).status_code == 403
    assert client.get("/survival/options").status_code == 403


def test_options_list_groupings_with_the_day_they_are_known(client: TestClient) -> None:
    login(client, Role.RESEARCHER)
    options = client.get("/survival/options").json()
    horizons = {g["key"]: g["horizon_days"] for g in options["groupings"]}
    assert horizons["dd_cfdna_year1"] == 365 and horizons["organ"] == 0
    assert {c["key"] for c in options["covariates"]} >= {"dgf", "dsa_positive"}


def test_groups_known_during_follow_up_start_at_their_landmark(client: TestClient) -> None:
    login(client, Role.RESEARCHER)
    assert analyse(client, grouping="dd_cfdna_year1")["landmark_days"] == 365
    assert analyse(client, grouping="dd_cfdna_year1", landmark_days=400)["landmark_days"] == 400
    refused = client.post("/survival/analysis",
                          json={"grouping": "dd_cfdna_year1", "landmark_days": 100})  # fmt: skip
    assert refused.status_code == 422
    assert "immortal time bias" in refused.json()["detail"]


def test_a_cohort_defined_by_year_1_labs_forces_a_1_year_landmark(client: TestClient) -> None:
    login(client, Role.CLINICIAN)
    cohort = new_cohort(client, HIGH_DD_CFDNA)
    report = analyse(client, grouping="cohort", compare_cohort_id=cohort)
    assert report["landmark_days"] == 365
    groups = {c["group"] for c in report["km"]}  # type: ignore[attr-defined]
    assert groups <= {"In 'high dd-cfDNA'", "Not in 'high dd-cfDNA'"}
    assert analyse(client, cohort_id=cohort)["landmark_days"] == 365  # as the population too
    assert client.post("/survival/analysis", json={"grouping": "cohort"}).status_code == 422


def test_unknown_and_other_centers_cohorts_are_not_found(client: TestClient) -> None:
    login(client, Role.CLINICIAN, "center_b")
    other = new_cohort(client, {"organ": "kidney"}, "center b kidneys")
    login(client, Role.CLINICIAN, "center_a")
    for cohort_id in (other, "not-a-cohort"):
        assert analyse(client, 404, cohort_id=cohort_id)["detail"] == "Cohort not found"


def test_analyses_and_exports_are_audited(client: TestClient, api_owner: Engine) -> None:
    login(client, Role.RESEARCHER)
    analyse(client, grouping="donor_type")
    client.post("/survival/report.csv", json={"grouping": "donor_type"})
    with api_owner.connect() as conn:
        actions = conn.execute(text(
            "SELECT action, details->>'grouping' FROM app.audit_log WHERE object_type = "
            "'survival_analysis' ORDER BY audit_id DESC LIMIT 2")).all()  # fmt: skip
    assert [tuple(a) for a in actions] == [("survival_export_csv", "donor_type"),
                                           ("survival_analysis", "donor_type")]  # fmt: skip


def test_csv_export_is_labelled_and_suppressed(client: TestClient) -> None:
    login(client, Role.RESEARCHER)
    response = client.post("/survival/report.csv", json={"grouping": "organ"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert "synthetic" in response.headers["content-disposition"]
    table = list(csv.DictReader(io.StringIO(response.text)))
    assert table[0]["value"] == SYNTHETIC_LABEL
    at_risk = [r["value"] for r in table if r["metric"] in ("at_risk", "graft_losses_in_interval")]
    assert at_risk and not [v for v in at_risk if v.isdigit() and 0 < int(v) < 11]


def test_pdf_export_is_labelled_on_every_page(client: TestClient) -> None:
    login(client, Role.CLINICIAN)
    response = client.post("/survival/report.pdf", json={"grouping": "donor_type"})
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    with pymupdf.open(stream=response.content, filetype="pdf") as pdf:  # type: ignore[no-untyped-call]
        pages = [page.get_text() for page in pdf]
    assert pages and all(SYNTHETIC_LABEL in page.replace("\n", " ") for page in pages)
    assert "Methods" in "".join(pages)


def test_condition_rules_count_only_what_was_known_by_the_landmark(
    client: TestClient, api_app_engine: Engine
) -> None:
    login(client, Role.CLINICIAN)
    cohort_id = new_cohort(client, {"condition_concept_ids": [REJECTION]}, "rejected")
    with tenant_transaction(api_app_engine, "center_a") as conn:
        cohort = cohorts.get(conn, cohort_id)
        assert cohort is not None and cohorts.horizon_days(cohort.rules) == 0
        at_transplant = members_by(conn, cohort, 0)
        by_year_3 = members_by(conn, cohort, 3 * 365)
    assert at_transplant < by_year_3  # rejection is diagnosed after transplant, not at it
    assert cohorts.horizon_days(cohorts.CohortRules.model_validate(HIGH_DD_CFDNA)) == 365
