"""The risk score on the API: roles, de-identification, on-demand scoring with the model
version, audit, nightly scoring, and isolation from researchers and other centers."""

from datetime import date

import pytest
from api_dataset import Dataset
from api_testutils import login
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from txlens_backend.api.deps import Role
from txlens_backend.config import Settings
from txlens_backend.deid import pseudonym
from txlens_backend.risk import LABEL_TEXT
from txlens_backend.risk.features import FEATURE_NAMES
from txlens_backend.risk.registry import champion_for
from txlens_backend.risk.serve import MIN_FOR_DRIFT, score_tenant

pytestmark = pytest.mark.db


def scored_patient(api_owner: Engine, tenant: str = "center_a") -> int:
    """A patient of this center with a nightly score."""
    with api_owner.connect() as conn:
        return int(conn.execute(
            text("SELECT person_id FROM app.risk_prediction WHERE tenant_id = :t "
                 "AND source = 'nightly' ORDER BY person_id LIMIT 1"), {"t": tenant},
        ).scalar_one())  # fmt: skip


def as_seen_by(role: Role, person_id: int, settings: Settings, tenant: str = "center_a") -> str:
    if role is Role.CLINICIAN:
        return str(person_id)
    return pseudonym(settings.deid_hmac_key.get_secret_value(), tenant, person_id)


def test_nightly_scores_record_the_model_version(
    api_owner: Engine, api_risk_scores: date, api_risk_model: str
) -> None:
    with api_owner.connect() as conn:
        runs = conn.execute(
            text("SELECT tenant_id, model_version, scored, feature_psi FROM app.risk_run")
        ).all()
        versions = conn.execute(
            text("SELECT DISTINCT model_name, model_version FROM app.risk_prediction")
        ).all()
        audited = conn.execute(
            text("SELECT count(*) FROM app.audit_log WHERE action = 'risk_scoring_run'")
        ).scalar_one()
    assert {r.tenant_id for r in runs} >= {"center_a", "center_b"}
    assert all(r.model_version == api_risk_model and r.scored > 0 for r in runs)
    # per-feature drift against the training reference, only with enough patients to judge
    assert all(set(r.feature_psi) == (set(FEATURE_NAMES) if r.scored >= MIN_FOR_DRIFT else set())
               for r in runs)  # fmt: skip
    assert versions == [("txlens-rejection-risk", api_risk_model)]
    assert audited >= len(runs)


def test_rescoring_the_same_night_adds_nothing(
    api_owner: Engine, api_app_engine: Engine, api_settings: Settings, api_risk_scores: date
) -> None:
    champion = champion_for(api_settings.mlflow_tracking_uri)
    assert champion is not None
    with api_owner.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM app.risk_prediction")).scalar_one()
    score_tenant(api_app_engine, "center_a", api_risk_scores, champion)
    with api_owner.connect() as conn:
        after = conn.execute(text("SELECT count(*) FROM app.risk_prediction")).scalar_one()
    assert after == before


def test_clinician_sees_score_contributions_version_and_label(
    client: TestClient, api_owner: Engine, api_settings: Settings, api_risk_scores: date
) -> None:
    person_id = scored_patient(api_owner)
    login(client, Role.CLINICIAN)
    body = client.get(f"/patients/{person_id}/risk").json()
    assert body["available"] and body["label"] == LABEL_TEXT
    assert body["model_name"] == "txlens-rejection-risk" and body["model_version"]
    assert 0 <= body["score"] <= 1 and body["band"] in ("lower", "elevated")
    assert body["band"] == ("elevated" if body["score"] >= body["threshold"] else "lower")
    assert len(body["contributions"]) == 3
    assert all(c["direction"] == ("raises" if c["log_odds"] > 0 else "lowers")
               for c in body["contributions"])  # fmt: skip
    with api_owner.connect() as conn:
        audited = conn.execute(text(
            "SELECT details->>'model_version' FROM app.audit_log WHERE action = 'phi_view' "
            "AND object_type = 'patient_risk' AND object_id = :p ORDER BY occurred_at DESC "
            "LIMIT 1"), {"p": str(person_id)}).scalar_one()  # fmt: skip
    assert audited == body["model_version"]


def test_data_steward_sees_the_same_score_with_shifted_dates(
    client: TestClient, api_owner: Engine, api_settings: Settings, api_risk_scores: date
) -> None:
    person_id = scored_patient(api_owner)
    login(client, Role.CLINICIAN)
    clinician = client.get(f"/patients/{person_id}/risk").json()
    client.post("/auth/logout")
    login(client, Role.DATA_STEWARD)
    steward = client.get(
        f"/patients/{as_seen_by(Role.DATA_STEWARD, person_id, api_settings)}/risk"
    ).json()
    assert steward["score"] == clinician["score"]
    assert steward["visit_date"] != clinician["visit_date"]  # shifted, never by 0 days


def test_researchers_never_see_an_individual_score(
    client: TestClient, api_owner: Engine, api_settings: Settings, api_risk_scores: date
) -> None:
    person_id = scored_patient(api_owner)
    login(client, Role.RESEARCHER)
    patient = as_seen_by(Role.RESEARCHER, person_id, api_settings)
    assert client.get(f"/patients/{patient}/risk").status_code == 403
    assert client.post(f"/patients/{patient}/risk/score").status_code == 403


def test_another_centers_patient_is_not_found(
    client: TestClient, api_owner: Engine, api_risk_scores: date
) -> None:
    login(client, Role.CLINICIAN, tenant="center_b")
    assert client.get(f"/patients/{scored_patient(api_owner)}/risk").status_code == 404


def test_on_demand_score_is_stored_with_version_and_audited(
    client: TestClient, api_owner: Engine, api_data: Dataset, api_risk_model: str
) -> None:
    _, person_id = next(v for v in api_data.persons.values() if v[0] == "center_a")
    login(client, Role.CLINICIAN)
    response = client.post(f"/patients/{person_id}/risk/score")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] and body["model_version"] == api_risk_model
    with api_owner.connect() as conn:
        stored = conn.execute(text(
            "SELECT model_version, scored_by IS NOT NULL AS by_user FROM app.risk_prediction "
            "WHERE person_id = :p AND source = 'on_demand'"), {"p": person_id}).all()  # fmt: skip
        audited = conn.execute(text(
            "SELECT details->>'version' FROM app.audit_log WHERE action = 'risk_score' "
            "AND object_id = :p"), {"p": str(person_id)}).scalars().all()  # fmt: skip
    assert stored and all(r.model_version == api_risk_model and r.by_user for r in stored)
    assert api_risk_model in audited


def test_scoring_without_a_registered_model_is_unavailable(
    client: TestClient, api_app: FastAPI, api_data: Dataset, tmp_path: object
) -> None:
    settings = api_app.state.ctx.settings
    saved = settings.mlflow_tracking_uri
    settings.mlflow_tracking_uri = f"sqlite:///{tmp_path}/empty.db"
    try:
        _, person_id = next(v for v in api_data.persons.values() if v[0] == "center_a")
        login(client, Role.CLINICIAN)
        assert client.post(f"/patients/{person_id}/risk/score").status_code == 503
    finally:
        settings.mlflow_tracking_uri = saved
