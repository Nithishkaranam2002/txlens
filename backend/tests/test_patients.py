"""Patient list and timeline through the API, checked against the generator's ground truth."""

import re
from collections import Counter
from collections.abc import Callable
from datetime import date, timedelta

import pytest
from api_dataset import Dataset
from api_testutils import login
from fastapi.testclient import TestClient

from txlens_backend.api.deps import Role
from txlens_backend.config import Settings
from txlens_backend.deid import date_shift_days, pseudonym

pytestmark = pytest.mark.db
PSEUDONYM = re.compile(r"^R-[0-9a-f]{16}$")
Keep = Callable[[dict[str, str], str], bool]  # (ground-truth patient, status) -> expected?


def truth_patients(data: Dataset, tenant: str) -> dict[int, dict[str, str]]:
    """person_id -> ground-truth patient row, for one center."""
    return {
        data.persons[r["patient_id"]][1]: r
        for r in data.truth("patients")
        if r["tenant_id"] == tenant
    }


def all_items(client: TestClient, **params: str) -> list[dict[str, object]]:
    """Every patient across pages."""
    items: list[dict[str, object]] = []
    while True:
        page = client.get("/patients", params={**params, "limit": 40, "offset": len(items)}).json()
        items += page["items"]
        if len(items) >= page["total"] or not page["items"]:
            return items


def status_of(data: Dataset, patient_id: str) -> str:
    outcome = next(o for o in data.truth("outcomes") if o["patient_id"] == patient_id)
    if outcome["death_date"]:
        return "deceased"
    return "graft_failure" if outcome["graft_loss_date"] else "active"


def test_clinician_sees_identified_patients_of_own_center(
    client: TestClient, api_data: Dataset
) -> None:
    login(client, Role.CLINICIAN)
    truth = truth_patients(api_data, "center_a")
    items = all_items(client)
    assert {i["patient_id"] for i in items} == set(truth)
    for item in items:
        row = truth[int(str(item["patient_id"]))]
        assert (item["mrn"], item["given_name"], item["family_name"]) == (
            row["mrn"], row["first_name"], row["last_name"]
        )  # fmt: skip
        assert item["birth_date"] == row["birth_date"]
        assert item["transplant_date"] == row["transplant_date"]
        assert (item["organ"], item["donor_type"]) == (
            row["organ"], row["donor_type"] if row["organ"] == "kidney" else None
        )  # fmt: skip


@pytest.mark.parametrize(
    ("params", "keep"),
    [
        ({"organ": "heart"}, lambda r, _: r["organ"] == "heart"),
        ({"donor_type": "living"}, lambda r, _: r["organ"] + r["donor_type"] == "kidneyliving"),
        ({"status": "deceased"}, lambda r, s: s == "deceased"),
        ({"status": "active"}, lambda r, s: s == "active"),
    ],
)  # fmt: skip
def test_filters_match_ground_truth(
    client: TestClient, api_data: Dataset, params: dict[str, str], keep: Keep
) -> None:
    login(client, Role.CLINICIAN, "center_b")
    truth = truth_patients(api_data, "center_b")
    expected = {pid for pid, r in truth.items() if keep(r, status_of(api_data, r["patient_id"]))}
    assert {i["patient_id"] for i in all_items(client, **params)} == expected


def test_clinician_search_by_mrn_and_name(client: TestClient, api_data: Dataset) -> None:
    login(client, Role.CLINICIAN)
    pid, row = next(iter(truth_patients(api_data, "center_a").items()))
    by_mrn = client.get("/patients", params={"q": row["mrn"]}).json()
    assert [i["patient_id"] for i in by_mrn["items"]] == [pid]
    by_name = client.get("/patients", params={"q": row["last_name"].upper()}).json()
    assert pid in [i["patient_id"] for i in by_name["items"]]


def test_pagination_and_limits(client: TestClient) -> None:
    login(client, Role.CLINICIAN)
    first = client.get("/patients", params={"limit": 5}).json()
    second = client.get("/patients", params={"limit": 5, "offset": 5}).json()
    assert len(first["items"]) == len(second["items"]) == 5
    assert not {i["patient_id"] for i in first["items"]} & {
        i["patient_id"] for i in second["items"]
    }
    beyond = client.get("/patients", params={"offset": 10_000}).json()
    assert beyond["items"] == [] and beyond["total"] == first["total"]
    assert client.get("/patients", params={"limit": 201}).status_code == 422


def test_researcher_sees_pseudonyms_age_bands_and_shifted_dates(
    client: TestClient, api_data: Dataset
) -> None:
    login(client, Role.RESEARCHER)
    items = all_items(client)
    truth_dates = Counter(
        r["transplant_date"] for r in truth_patients(api_data, "center_a").values()
    )
    assert len(items) == sum(truth_dates.values())
    for item in items:
        assert PSEUDONYM.match(str(item["patient_id"]))
        assert set(item) == {"patient_id", "age_band", "sex", "organ", "donor_type", "status",
                             "transplant_date", "death_date"}  # fmt: skip
    shifted = Counter(str(i["transplant_date"]) for i in items)
    assert shifted != truth_dates  # not the real calendar dates


def test_researcher_searches_by_pseudonym_only(client: TestClient, api_data: Dataset) -> None:
    login(client, Role.RESEARCHER)
    pseudonym = client.get("/patients", params={"limit": 1}).json()["items"][0]["patient_id"]
    found = client.get("/patients", params={"q": pseudonym}).json()
    assert [i["patient_id"] for i in found["items"]] == [pseudonym]
    mrn = next(iter(truth_patients(api_data, "center_a").values()))["mrn"]
    assert client.get("/patients", params={"q": mrn}).json()["total"] == 0


def test_clinician_timeline_has_every_event(client: TestClient, api_data: Dataset) -> None:
    login(client, Role.CLINICIAN)
    pid, row = next((p, r) for p, r in truth_patients(api_data, "center_a").items()
                    if r["organ"] == "kidney")  # fmt: skip
    timeline = client.get(f"/patients/{pid}/timeline").json()
    labs = [x for x in api_data.truth("labs") if x["patient_id"] == row["patient_id"]]
    assert Counter(x["lab"] for x in timeline["labs"]) == Counter(x["lab"] for x in labs)
    transplant = [p for p in timeline["procedures"] if p["kind"] == "transplant"]
    assert [(p["date"], p["concept_id"]) for p in transplant] == [
        (row["transplant_date"], 4022805 if row["donor_type"] == "living" else 4021107)
    ]
    notes = [n for n in api_data.truth("notes") if n["patient_id"] == row["patient_id"]]
    assert len(timeline["notes"]) == len(notes)
    assert all(set(n) == {"date", "note_id", "title"} for n in timeline["notes"])
    assert timeline["drugs"] and timeline["conditions"]


def test_researcher_timeline_shifts_every_date_by_the_same_days(
    client: TestClient, api_data: Dataset, api_settings: Settings
) -> None:
    key = api_settings.deid_hmac_key.get_secret_value()
    pid = next(iter(truth_patients(api_data, "center_a")))
    login(client, Role.CLINICIAN)
    real = client.get(f"/patients/{pid}/timeline").json()
    login(client, Role.RESEARCHER)
    shifted = client.get(f"/patients/{pseudonym(key, 'center_a', pid)}/timeline").json()
    move = timedelta(days=date_shift_days(key, "center_a", pid))
    for section in ("labs", "procedures", "conditions", "notes"):
        assert [date.fromisoformat(e["date"]) for e in shifted[section]] == [
            date.fromisoformat(e["date"]) + move for e in real[section]
        ], section
    assert (
        shifted["patient"]["transplant_date"]
        == (date.fromisoformat(real["patient"]["transplant_date"]) + move).isoformat()
    )
    assert all(n["note_id"] is None and n["title"] is None for n in shifted["notes"])


def test_wrong_kind_of_id_is_404(client: TestClient, api_data: Dataset) -> None:
    pid = next(iter(truth_patients(api_data, "center_a")))
    login(client, Role.RESEARCHER)
    assert client.get(f"/patients/{pid}/timeline").status_code == 404  # real id, de-id role
    login(client, Role.CLINICIAN)
    assert client.get("/patients/R-0123456789abcdef/timeline").status_code == 404


def test_admin_cannot_see_patients(client: TestClient) -> None:
    login(client, Role.ADMIN)
    assert client.get("/patients").status_code == 403
