"""`txlens-demo`: secret checks name problems without values; the status never shows a
password and reports pending AI work."""

import pytest
from api_testutils import PASSWORDS
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import Engine

from txlens_backend.config import Settings
from txlens_backend.demo import TABLES, counts, pending, problems, status_lines

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
