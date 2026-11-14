"""`txlens-demo`: secret checks name problems without values; the status never shows a
password and reports pending AI work; a session reset forgets every login and is audited;
clearing guest work deletes only the guests' conversations and cohorts."""

from datetime import UTC, date, datetime, timedelta

import pytest
from api_testutils import PASSWORDS
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import Engine, text

from txlens_backend.config import Settings
from txlens_backend.demo import (
    TABLES,
    ReviewedFlagsError,
    clear_guest_work,
    counts,
    drop_surveillance_after,
    pending,
    problems,
    reset_sessions,
    status_lines,
)
from txlens_backend.seed_users import seed_guests

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


def test_drop_surveillance_after_deletes_later_runs_and_flags_and_audits(
    api_owner: Engine, api_flags: date
) -> None:
    later = api_flags + timedelta(days=30)
    with api_owner.connect() as conn, conn.begin() as outer:
        conn.execute(text("SET LOCAL app.tenant_id = 'center_a'"))
        run_id = conn.execute(text(
            "INSERT INTO app.surveillance_run (as_of, configs, patients, flags_opened, "
            "flags_cleared) VALUES (:d, '{}', 1, 1, 0) RETURNING run_id"
        ), {"d": later}).scalar_one()  # fmt: skip
        person = conn.execute(text("SELECT min(person_id) FROM cdm.person")).scalar_one()
        conn.execute(text(
            "INSERT INTO app.surveillance_flag (person_id, run_id, config_id, config_version, "
            "rule_id, rule_group, severity, reason, evidence, evidence_key, as_of) VALUES "
            "(:p, :r, 'kidney', 1, 'missed_test', 'schedule', 'medium', 'late', '[]', 'k', :d)"
        ), {"p": person, "r": run_id, "d": later})  # fmt: skip
        kept = conn.execute(text(
            "SELECT count(*) FROM app.surveillance_flag WHERE as_of <= :c"
        ), {"c": api_flags}).scalar_one()  # fmt: skip
        dropped = drop_surveillance_after(conn, api_flags)
        left = conn.execute(text(
            "SELECT (SELECT count(*) FROM app.surveillance_run WHERE as_of > :c), "
            "(SELECT count(*) FROM app.surveillance_flag WHERE as_of > :c), "
            "(SELECT count(*) FROM app.surveillance_flag WHERE as_of <= :c)"
        ), {"c": api_flags}).one()  # fmt: skip
        audited = conn.execute(text(
            "SELECT tenant_id, details FROM app.audit_log "
            "WHERE action = 'surveillance_dropped_after_cutoff'"
        )).all()  # fmt: skip
        outer.rollback()
    assert dropped == {"flags": 1, "runs": 1}
    assert tuple(left) == (0, 0, kept)
    assert [(a.tenant_id, a.details["runs"], a.details["flags"]) for a in audited] == [
        ("center_a", 1, 1)
    ]


def test_drop_surveillance_after_never_deletes_reviewed_flags(
    api_owner: Engine, api_flags: date
) -> None:
    with api_owner.connect() as conn, conn.begin() as outer:
        conn.execute(text(
            "UPDATE app.surveillance_flag SET status_changed_at = now() "
            "WHERE flag_id = (SELECT min(flag_id::text)::uuid FROM app.surveillance_flag)"
        ))  # fmt: skip
        with pytest.raises(ReviewedFlagsError, match="were reviewed"):
            drop_surveillance_after(conn, api_flags - timedelta(days=3650))
        outer.rollback()


def test_clear_guest_work_deletes_only_guest_conversations_and_cohorts(
    api_app_engine: Engine, api_owner: Engine
) -> None:
    seed_guests(api_app_engine, "guest-demo-password-1", datetime.now(UTC) - timedelta(days=1))
    with api_owner.connect() as conn, conn.begin() as outer:
        ids: dict[str, object] = dict(conn.execute(text(
            "SELECT username, user_id FROM app.app_user "
            "WHERE username IN ('guest.researcher', 'researcher.center_a')"
        )).tuples().all())  # fmt: skip
        for username, user_id in ids.items():
            conn.execute(text(
                "INSERT INTO app.copilot_conversation (tenant_id, user_id, title) "
                "VALUES ('center_a', :u, :t)"
            ), {"u": user_id, "t": username})  # fmt: skip
            conn.execute(text(
                "INSERT INTO app.cohort (tenant_id, name, rules, created_by, member_count) "
                "VALUES ('center_a', :t, '{}', :u, 0)"
            ), {"u": user_id, "t": username})  # fmt: skip
        cleared = clear_guest_work(conn)
        left = conn.execute(text(
            "SELECT (SELECT array_agg(title) FROM app.copilot_conversation WHERE title IN "
            "('guest.researcher', 'researcher.center_a')), (SELECT array_agg(name) FROM "
            "app.cohort WHERE name IN ('guest.researcher', 'researcher.center_a'))"
        )).one()  # fmt: skip
        audited = conn.execute(text(
            "SELECT details FROM app.audit_log WHERE action = 'guest_work_cleared'"
        )).scalars().all()  # fmt: skip
        outer.rollback()
    assert cleared["conversations"] >= 1 and cleared["cohorts"] >= 1
    assert tuple(left) == (["researcher.center_a"], ["researcher.center_a"])
    assert audited and audited[0]["conversations"] >= 1
