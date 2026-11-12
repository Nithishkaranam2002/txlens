"""The differencing guard in the cohort builder and survival APIs (red-team: subtracting counts)."""

from collections import Counter

import pytest
from api_dataset import Dataset
from api_testutils import login
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from txlens_backend.api.deps import Role
from txlens_backend.services.disclosure import WITHHELD

pytestmark = pytest.mark.db
TENANT = "center_a"


def age(p: dict[str, str]) -> int:
    return int(p["transplant_date"][:4]) - int(p["birth_date"][:4])


def small_age(data: Dataset) -> int:
    """An age X shared by 1-10 of center A's patients, with at least 11 patients older."""
    ages = Counter(age(p) for p in data.truth("patients") if p["tenant_id"] == TENANT)
    return next(a for a, n in sorted(ages.items())
                if 1 <= n <= 10 and sum(m for b, m in ages.items() if b > a) >= 11)  # fmt: skip


def preview(client: TestClient, rules: dict[str, object]) -> dict[str, object]:
    response = client.post("/cohorts/preview", json=rules)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_cohort_builder_withholds_a_count_that_differences_an_earlier_one(
    client: TestClient, api_data: Dataset
) -> None:
    x = small_age(api_data)
    login(client, Role.RESEARCHER)
    first = preview(client, {"age_min": x})
    assert isinstance(first["count"], int)
    second = preview(client, {"age_min": x + 1})
    assert second["count"] == WITHHELD
    assert second["notices"] and "withheld" in str(second["notices"])
    assert preview(client, {"age_min": x})["count"] == first["count"]


def test_the_guard_remembers_counts_between_sessions(client: TestClient, api_data: Dataset) -> None:
    x = small_age(api_data)
    login(client, Role.RESEARCHER)
    preview(client, {"age_min": x})
    client.post("/auth/logout")
    login(client, Role.RESEARCHER)  # a new session of the same user
    assert preview(client, {"age_min": x + 1})["count"] == WITHHELD


def test_attrition_steps_are_guarded_within_one_preview(
    client: TestClient, api_data: Dataset
) -> None:
    x = small_age(api_data)
    login(client, Role.RESEARCHER)
    result = preview(client, {"age_min": x, "age_max": 120})
    all_patients = len([p for p in api_data.truth("patients") if p["tenant_id"] == TENANT])
    older = sum(1 for p in api_data.truth("patients") if p["tenant_id"] == TENANT and age(p) >= x)
    steps = result["steps"]
    assert isinstance(steps, list)
    counts = [s["count"] for s in steps]
    assert counts[0] == all_patients
    expected = WITHHELD if 0 < all_patients - older < 11 else older
    assert counts[1] == expected


def test_saving_a_narrower_cohort_does_not_reveal_its_size(
    client: TestClient, api_data: Dataset
) -> None:
    x = small_age(api_data)
    login(client, Role.RESEARCHER)
    preview(client, {"age_min": x})
    saved = client.post("/cohorts", json={"name": "probe", "rules": {"age_min": x + 1}}).json()
    assert saved["member_count"] == WITHHELD
    assert client.get(f"/cohorts/{saved['cohort_id']}").json()["member_count"] == WITHHELD


def test_crossing_filters_are_not_compared(client: TestClient, api_data: Dataset) -> None:
    """Kidney and heart are not nested: only each one's difference to "all patients" counts."""
    organs = Counter(p["organ"] for p in api_data.truth("patients") if p["tenant_id"] == TENANT)
    total = sum(organs.values())

    def expected(organ: str) -> int | str:
        n = organs[organ]
        return "<11" if n < 11 else WITHHELD if 0 < total - n < 11 else n

    login(client, Role.RESEARCHER)
    assert preview(client, {"organ": "kidney"})["count"] == expected("kidney")
    assert preview(client, {"organ": "heart"})["count"] == expected("heart")


def test_clinicians_see_exact_cohort_counts(client: TestClient, api_data: Dataset) -> None:
    x = small_age(api_data)
    login(client, Role.CLINICIAN)
    preview(client, {"age_min": x})
    assert isinstance(preview(client, {"age_min": x + 1})["count"], int)


def test_survival_withholds_differencing_counts_for_every_role(
    client: TestClient, api_data: Dataset
) -> None:
    x = small_age(api_data)
    login(client, Role.CLINICIAN)
    body = {"grouping": "none", "landmark_days": 0, "covariates": []}
    first = client.post("/survival/analysis", json=body | {"rules": {"age_min": x}}).json()
    assert isinstance(first["n"], int)
    second = client.post("/survival/analysis", json=body | {"rules": {"age_min": x + 1}}).json()
    assert second["n"] == WITHHELD
    assert any("withheld" in w for w in second["warnings"])


def test_survival_rejects_two_populations(client: TestClient) -> None:
    login(client, Role.RESEARCHER)
    body = {"cohort_id": "00000000-0000-0000-0000-000000000000", "rules": {"organ": "kidney"}}
    assert client.post("/survival/analysis", json=body).status_code == 422


def test_released_counts_belong_to_their_center(
    client: TestClient, api_data: Dataset, api_owner: Engine
) -> None:
    login(client, Role.RESEARCHER)
    preview(client, {"organ": "kidney"})
    with api_owner.connect() as conn:
        tenants = conn.execute(text("SELECT DISTINCT tenant_id FROM app.released_count")).scalars()
        assert set(tenants) == {TENANT}
