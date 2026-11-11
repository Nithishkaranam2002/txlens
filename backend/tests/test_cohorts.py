"""Cohort rules against ground truth, attrition, saving, suppression and date granularity."""

from collections import Counter
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta

import pytest
from api_dataset import Dataset
from api_testutils import login
from fastapi.testclient import TestClient

from txlens_backend.api.deps import Role
from txlens_backend.deid import suppress

pytestmark = pytest.mark.db
TENANT = "center_a"
Patient = dict[str, str]


def truth_ids(data: Dataset, keep: Callable[[Patient], bool]) -> set[int]:
    """person_ids of center A's ground-truth patients for which `keep` holds."""
    return {
        data.persons[p["patient_id"]][1]
        for p in data.truth("patients")
        if p["tenant_id"] == TENANT and keep(p)
    }


def high_dd_cfdna_in_year_one(data: Dataset) -> set[str]:
    """Ground-truth patient ids with a dd-cfDNA >= 1% within 365 days after transplant."""
    transplant = {p["patient_id"]: date.fromisoformat(p["transplant_date"])
                  for p in data.truth("patients")}  # fmt: skip
    return {
        lab["patient_id"]
        for lab in data.truth("labs")
        if lab["lab"] == "dd_cfdna"
        and float(lab["value"]) >= 1.0
        and timedelta(0)
        <= datetime.fromisoformat(lab["collected_at"]).date() - transplant[lab["patient_id"]]
        <= timedelta(days=365)
    }


def age(p: Patient) -> int:
    return int(p["transplant_date"][:4]) - int(p["birth_date"][:4])


def preview(client: TestClient, rules: Mapping[str, object]) -> dict[str, object]:
    response = client.post("/cohorts/preview", json=rules)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def members(client: TestClient, cohort_id: str) -> list[object]:
    page = client.get(f"/cohorts/{cohort_id}/members", params={"limit": 200}).json()
    return [item["patient_id"] for item in page["items"]]


def test_kidney_with_dd_cfdna_at_least_1pct_in_year_one(
    client: TestClient, api_data: Dataset
) -> None:
    login(client, Role.CLINICIAN)
    high = high_dd_cfdna_in_year_one(api_data)
    expected = truth_ids(api_data, lambda p: p["organ"] == "kidney" and p["patient_id"] in high)
    assert expected, "the test data must contain such patients"
    rules = {"organ": "kidney",
             "lab_rules": [{"lab": "dd_cfdna", "op": ">=", "value": 1.0,
                            "days_from_transplant": [0, 365]}]}  # fmt: skip
    assert preview(client, rules)["count"] == len(expected)
    saved = client.post("/cohorts", json={"name": "High dd-cfDNA year 1", "rules": rules})
    assert saved.status_code == 201
    assert set(members(client, saved.json()["cohort_id"])) == expected


@pytest.mark.parametrize(
    ("rules", "keep"),
    [
        ({"donor_type": "living"}, lambda p: p["organ"] + p["donor_type"] == "kidneyliving"),
        ({"organ": "heart"}, lambda p: p["organ"] == "heart"),
        ({"age_min": 40, "age_max": 59}, lambda p: 40 <= age(p) <= 59),
        ({"transplant_date": {"start": "2024-01-01", "end": "2024-12-31"}},
         lambda p: p["transplant_date"].startswith("2024")),
    ],
)  # fmt: skip
def test_rules_match_ground_truth(
    client: TestClient, api_data: Dataset, rules: dict[str, object], keep: Callable[[Patient], bool]
) -> None:
    login(client, Role.CLINICIAN)
    assert preview(client, rules)["count"] == len(truth_ids(api_data, keep))


def test_condition_rule_includes_descendants(client: TestClient, api_data: Dataset) -> None:
    login(client, Role.CLINICIAN)
    diabetic = len(truth_ids(api_data, lambda p: p["diabetes"] == "true"))
    assert diabetic
    # 201820 "Diabetes mellitus" is only an ancestor; patients carry 201826 (type 2 diabetes).
    assert preview(client, {"condition_concept_ids": [201820]})["count"] == diabetic
    assert preview(client, {"condition_concept_ids": [201826]})["count"] == diabetic


def test_attrition_steps_shrink_in_rule_order(client: TestClient, api_data: Dataset) -> None:
    login(client, Role.CLINICIAN)
    result = preview(client, {"organ": "kidney", "donor_type": "deceased", "age_min": 30})
    steps = result["steps"]
    assert isinstance(steps, list)
    assert [s["rule"] for s in steps] == [
        "all patients", "organ = kidney", "donor type = deceased", "age at transplant >= 30"
    ]  # fmt: skip
    counts = [s["count"] for s in steps]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] == len(truth_ids(api_data, lambda p: True))
    assert counts[-1] == result["count"]


def a_small_age(data: Dataset) -> int:
    """An age at transplant shared by 1-10 patients of center A."""
    ages = Counter(age(p) for p in data.truth("patients") if p["tenant_id"] == TENANT)
    return next(a for a, n in sorted(ages.items()) if 1 <= n <= 10)


def test_researchers_see_small_counts_suppressed(client: TestClient, api_data: Dataset) -> None:
    small = a_small_age(api_data)
    rules = {"age_min": small, "age_max": small}
    login(client, Role.CLINICIAN)
    exact = preview(client, rules)
    assert isinstance(exact["count"], int) and 1 <= exact["count"] <= 10
    login(client, Role.RESEARCHER)
    shown = preview(client, rules)
    assert shown["count"] == "<11"
    assert isinstance(shown["steps"], list) and isinstance(exact["steps"], list)
    assert [s["count"] for s in shown["steps"]] == [
        suppress(s["count"], 11) for s in exact["steps"]
    ]  # every step, not only the last

    saved = client.post("/cohorts", json={"name": "small", "rules": rules}).json()
    assert saved["member_count"] == "<11"
    page = client.get(f"/cohorts/{saved['cohort_id']}/members").json()
    assert page["total"] == "<11"
    assert all(str(i["patient_id"]).startswith("R-") for i in page["items"])
    listed = client.get("/cohorts").json()["items"]
    assert next(c for c in listed if c["cohort_id"] == saved["cohort_id"])["member_count"] == "<11"
    assert client.get(f"/cohorts/{saved['cohort_id']}").json()["member_count"] == "<11"


def test_zero_is_not_suppressed(client: TestClient) -> None:
    login(client, Role.RESEARCHER)
    assert preview(client, {"age_min": 120})["count"] == 0


@pytest.mark.parametrize(
    "bounds",
    [{"start": "2024-03-01"}, {"end": "2024-06-30"}, {"start": "2024-01-01", "end": "2024-12-30"}],
)
def test_researchers_may_filter_dates_by_whole_years_only(
    client: TestClient, bounds: dict[str, str]
) -> None:
    login(client, Role.RESEARCHER)
    rules = {"transplant_date": bounds}
    assert client.post("/cohorts/preview", json=rules).status_code == 422
    assert client.post("/cohorts", json={"name": "x", "rules": rules}).status_code == 422
    whole = {"transplant_date": {"start": "2023-01-01", "end": "2024-12-31"}}
    assert client.post("/cohorts/preview", json=whole).status_code == 200
    login(client, Role.CLINICIAN)
    assert client.post("/cohorts/preview", json=rules).status_code == 200


@pytest.mark.parametrize(
    "rules",
    [
        {
            "lab_rules": [
                {"lab": "dd_cfdna", "op": ">=", "value": 1, "days_from_transplant": [9, 1]}
            ]
        },
        {"lab_rules": [{"lab": "glucose", "op": ">=", "value": 1}]},
        {"lab_rules": [{"lab": "dd_cfdna", "op": "; DROP TABLE x", "value": 1}]},
        {"organ": "liver"},
        {"tenant_id": "center_b"},
    ],
)
def test_invalid_rules_are_rejected(client: TestClient, rules: dict[str, object]) -> None:
    login(client, Role.CLINICIAN)
    assert client.post("/cohorts/preview", json=rules).status_code == 422


def test_saved_cohort_keeps_name_and_rules_and_is_listed(client: TestClient) -> None:
    login(client, Role.CLINICIAN)
    body = {"name": "Heart 'quoted' name", "rules": {"organ": "heart"}}
    cohort = client.post("/cohorts", json=body).json()
    assert (cohort["name"], cohort["rules"]["organ"]) == ("Heart 'quoted' name", "heart")
    listed = client.get("/cohorts").json()["items"]
    assert any(c["cohort_id"] == cohort["cohort_id"] for c in listed)


@pytest.mark.parametrize("role", [Role.DATA_STEWARD, Role.ADMIN])
def test_stewards_and_admins_cannot_use_cohorts(client: TestClient, role: Role) -> None:
    login(client, role)
    assert client.get("/cohorts").status_code == 403
    assert client.post("/cohorts/preview", json={}).status_code == 403


def test_unknown_cohort_ids_are_404(client: TestClient) -> None:
    login(client, Role.CLINICIAN)
    assert client.get("/cohorts/not-a-uuid").status_code == 404
    assert client.get("/cohorts/00000000-0000-0000-0000-000000000000/members").status_code == 404
