"""`txlens-demo`: secret checks name problems without values; the status never shows a
password and reports pending AI work; a session reset forgets every login and is audited."""

import pytest
from api_testutils import PASSWORDS
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import Engine, text

from txlens_backend.config import Settings
from txlens_backend.demo import TABLES, counts, pending, problems, reset_sessions, status_lines

pytestmark = pytest.mark.db


def test_problems_name_the_setting_and_never_its_value() -> None:
    weak = Settings(jwt_secret=SecretStr("placeholder-change-me"),
                    deid_hmac_key=SecretStr("d" * 40),
                    demo_clinician_password=SecretStr("short"),
                    demo_researcher_password=SecretStr("long-enough-pass"),
                    demo_data_steward_password=SecretStr("long-enough-pass"),
                    demo_admin_password=SecretStr("long-enough-pass"))  # fmt: skip
    found = problems(weak)
    assert [p.split(":")[0] for p in found] == ["JWT_SECRET", "DEMO_CLINICIAN_PASSWORD"]
    assert not any("placeholder-change-me" in p or "short" in p.split(":")[1] for p in found)


def test_status_lists_counts_pending_work_urls_and_users_but_no_password(
    api_app: FastAPI, api_owner: Engine, api_settings: Settings
) -> None:
    offline = api_settings.model_copy(update={"openai_api_key": SecretStr("")})
    with api_owner.connect() as conn:
        loaded = counts(conn)
        waiting = pending(conn)
        lines = status_lines(conn, offline, "http://localhost:3000", "http://localhost:8000",
                             "http://localhost:8233")  # fmt: skip
    text = "\n".join(lines)
    assert set(loaded) == set(TABLES) and loaded["cdm.person"] > 0
    assert set(waiting) == {"biopsy reports", "notes (rules only)"}
    assert "http://localhost:3000" in text and "http://localhost:8000/docs" in text
    assert "clinician.center_a" in text and "DEMO_<ROLE>_PASSWORD" in text
    assert not any(password in text for password in PASSWORDS.values())


def test_reset_sessions_deletes_attempts_and_tokens_and_audits_each_center(
    api_app: FastAPI, api_owner: Engine
) -> None:
    with api_owner.connect() as conn, conn.begin() as outer:
        user = conn.execute(text(
            "SELECT user_id, tenant_id FROM app.app_user WHERE username = 'clinician.center_a'"
        )).one()  # fmt: skip
        conn.execute(text(
            "INSERT INTO app.refresh_token (jti, tenant_id, user_id, expires_at) "
            "VALUES (gen_random_uuid(), :t, :u, now() + interval '1 day')"
        ), {"t": user.tenant_id, "u": user.user_id})  # fmt: skip
        conn.execute(text(
            "INSERT INTO app.login_attempt (username, ip, succeeded) "
            "VALUES ('x', '203.0.113.9', false)"
        ))  # fmt: skip
        deleted = reset_sessions(conn)
        left = conn.execute(text(
            "SELECT (SELECT count(*) FROM app.refresh_token), "
            "(SELECT count(*) FROM app.login_attempt)"
        )).one()  # fmt: skip
        audited = conn.execute(text(
            "SELECT tenant_id, details FROM app.audit_log WHERE action = 'sessions_reset'"
        )).all()  # fmt: skip
        tenants = conn.execute(text("SELECT count(*) FROM app.tenant")).scalar_one()
        outer.rollback()
    assert deleted["refresh_tokens"] >= 1 and deleted["login_attempts"] >= 1
    assert tuple(left) == (0, 0)
    assert len(audited) == tenants
    assert {row.tenant_id: row.details["refresh_tokens"] for row in audited}[user.tenant_id] >= 1
