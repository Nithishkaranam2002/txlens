"""Per-user rate limits on expensive endpoints: counted in Postgres, 429 with Retry-After."""

from collections.abc import Iterator

import pytest
from api_testutils import login
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from txlens_backend.api.deps import Role
from txlens_backend.api.rate_limit import Bucket, Limit, hit

pytestmark = pytest.mark.db


def user_id(owner: Engine, username: str) -> str:
    with owner.connect() as conn:
        return str(conn.execute(text("SELECT user_id FROM app.app_user WHERE username = :u"),
                                {"u": username}).scalar_one())  # fmt: skip


@pytest.fixture
def analysis_limit_of_one(api_app: FastAPI) -> Iterator[None]:
    """One analysis per minute per user, for this test only."""
    settings = api_app.state.ctx.settings
    before, settings.rate_limit_analysis_per_minute = settings.rate_limit_analysis_per_minute, 1
    yield
    settings.rate_limit_analysis_per_minute = before


def test_hits_within_the_limit_pass_and_the_next_is_refused(
    api_app: FastAPI, api_owner: Engine
) -> None:
    engine = api_app.state.ctx.engine
    user = user_id(api_owner, "researcher.center_b")
    with api_owner.begin() as conn:
        conn.execute(text("DELETE FROM app.rate_limit WHERE user_id = :u"), {"u": user})
    limit = Limit(requests=2, window_seconds=3600)
    assert hit(engine, user, Bucket.COPILOT, limit) is None
    assert hit(engine, user, Bucket.COPILOT, limit) is None
    retry_after = hit(engine, user, Bucket.COPILOT, limit)
    assert retry_after is not None and 1 <= retry_after <= 3600
    assert hit(engine, user, Bucket.ANALYSIS, limit) is None  # buckets count separately


def test_expired_windows_are_deleted_as_the_user_counts(
    api_app: FastAPI, api_owner: Engine
) -> None:
    user = user_id(api_owner, "researcher.center_c")
    with api_owner.begin() as conn:
        conn.execute(text(
            "INSERT INTO app.rate_limit VALUES (:u, 'analysis', now() - interval '1 day', 9)"),
            {"u": user})  # fmt: skip
    hit(api_app.state.ctx.engine, user, Bucket.ANALYSIS, Limit(5, 60))
    with api_owner.connect() as conn:
        hits = conn.execute(text("SELECT hits FROM app.rate_limit WHERE user_id = :u"),
                            {"u": user}).scalars().all()  # fmt: skip
    assert hits == [1]


@pytest.mark.usefixtures("analysis_limit_of_one")
def test_api_answers_429_with_retry_after(client: TestClient) -> None:
    login(client, Role.RESEARCHER)
    body = {"grouping": "donor_type"}
    assert client.post("/survival/analysis", json=body).status_code == 200
    # Windows are whole clock minutes: if one just ended, the next call opens a new one.
    refused = client.post("/survival/analysis", json=body)
    if refused.status_code == 200:
        refused = client.post("/survival/analysis", json=body)
    assert refused.status_code == 429
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    login(client, Role.CLINICIAN)  # limits are per user
    assert client.post("/survival/analysis", json=body).status_code == 200
