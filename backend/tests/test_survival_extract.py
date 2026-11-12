"""The survival extract against the generator's ground truth (API database, RLS on)."""

from datetime import date, datetime

import pandas as pd
import pytest
from api_dataset import REJECTION, Dataset
from sqlalchemy import Engine

from txlens_backend.db import tenant_transaction
from txlens_backend.survival.extract import (
    CENSORED,
    DD_CFDNA_HIGH_PCT,
    DEATH,
    DSA_POSITIVE_MFI,
    DSA_WINDOW_DAYS,
    GRAFT_LOSS,
    extract,
    outcome,
)

pytestmark = pytest.mark.db


def days(day: str, since: date) -> int:
    return (date.fromisoformat(day[:10]) - since).days


def expected(data: Dataset, tenant: str) -> pd.DataFrame:
    """What the extract should hold for one center, computed from the ground truth."""
    patients = {p["patient_id"]: p for p in data.truth("patients") if p["tenant_id"] == tenant}
    tx = {p: date.fromisoformat(r["transplant_date"]) for p, r in patients.items()}
    first: dict[tuple[str, str], int] = {}

    def seen(key: tuple[str, str], day: int) -> None:
        first[key] = min(first.get(key, day), day)

    dsa: dict[str, float] = {}
    for lab in data.truth("labs"):
        p = lab["patient_id"]
        if p not in tx:
            continue
        day = (datetime.fromisoformat(lab["collected_at"]).date() - tx[p]).days
        if lab["lab"] == "dsa_mfi" and 0 <= day <= DSA_WINDOW_DAYS:
            dsa[p] = max(dsa.get(p, 0.0), float(lab["value"]))
        if lab["lab"] == "dd_cfdna" and float(lab["value"]) >= DD_CFDNA_HIGH_PCT and day >= 0:
            seen((p, "dd"), day)
    for e in data.truth("rejection_episodes"):
        if e["patient_id"] in tx and e["rejection_type"] in REJECTION:
            day = days(e["biopsy_date"], tx[e["patient_id"]])
            seen((e["patient_id"], "rejection"), day)
            if e["rejection_type"] in ("ABMR", "mixed"):
                seen((e["patient_id"], "abmr"), day)
    rows = []
    for o in data.truth("outcomes"):
        p = o["patient_id"]
        if p not in tx:
            continue
        loss = days(o["graft_loss_date"], tx[p]) if o["graft_loss_date"] else None
        death = days(o["death_date"], tx[p]) if o["death_date"] else None
        time, status = outcome(loss, death, days(o["last_follow_up_date"], tx[p]))
        kidney = patients[p]["organ"] == "kidney"
        rows.append({
            "time": time, "status": status, "organ": patients[p]["organ"],
            "donor_type": patients[p]["donor_type"],
            "age": tx[p].year - int(patients[p]["birth_date"][:4]),
            "dgf": float(patients[p]["dgf"] == "true") if kidney else None,
            "dsa_positive": float(dsa[p] >= DSA_POSITIVE_MFI) if p in dsa else None,
            "dd_cfdna_high_day": first.get((p, "dd")),
            "rejection_day": first.get((p, "rejection")), "abmr_day": first.get((p, "abmr")),
        })  # fmt: skip
    return pd.DataFrame(rows)


def canonical(frame: pd.DataFrame) -> list[tuple[object, ...]]:
    """Rows as sorted tuples (NaN/None -> None; NaN is the value unequal to itself) so the two
    frames compare exactly."""
    columns = sorted(c for c in frame.columns if c != "person_id")

    def value(v: object) -> object:
        return v if isinstance(v, str) else None if v is None or v != v else float(v)  # type: ignore[arg-type]

    return sorted((tuple(value(v) for v in row) for row in frame[columns].itertuples(index=False)),
                  key=repr)  # fmt: skip


@pytest.mark.parametrize("tenant", ["center_a", "center_b"])
def test_extract_matches_the_ground_truth(
    api_data: Dataset, api_app_engine: Engine, tenant: str
) -> None:
    with tenant_transaction(api_app_engine, tenant) as conn:
        found = extract(conn)
    want = expected(api_data, tenant)
    assert len(found) == len(want) > 0
    assert canonical(found) == canonical(want)
    assert set(found["status"]) <= {CENSORED, GRAFT_LOSS, DEATH}


def test_extract_stays_in_the_callers_center(api_data: Dataset, api_app_engine: Engine) -> None:
    with tenant_transaction(api_app_engine, "center_a") as conn:
        a = set(extract(conn)["person_id"])
    with tenant_transaction(api_app_engine, "center_b") as conn:
        b = set(extract(conn)["person_id"])
    owners = {pid: tenant for tenant, pid in api_data.persons.values()}
    assert a and b and a.isdisjoint(b)
    assert {owners[p] for p in a} == {"center_a"}


@pytest.mark.parametrize(
    ("loss", "death", "end", "want"),
    [
        (100, 100, 900, (100, GRAFT_LOSS)),  # a failed heart graft: death the same day
        (None, 50, 900, (50, DEATH)),
        (300, 200, 900, (200, DEATH)),  # died with a functioning graft
        (None, None, 900, (900, CENSORED)),
        (950, None, 900, (900, CENSORED)),  # after follow-up ended: not seen
    ],
)
def test_outcome_coding(loss: int | None, death: int | None, end: int,
                        want: tuple[int, int]) -> None:  # fmt: skip
    assert outcome(loss, death, end) == want
