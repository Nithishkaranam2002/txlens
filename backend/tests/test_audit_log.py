"""The audit log is tenant-isolated and append-only for the app role."""

from collections.abc import Callable

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import ProgrammingError

pytestmark = pytest.mark.db
AsApp = Callable[[Connection, str | None], None]
INSERT = text("INSERT INTO app.audit_log (action, object_type) VALUES ('load', 'cdm.person')")


def test_entry_gets_tenant_actor_and_time_by_default(db: Connection, as_app: AsApp) -> None:
    as_app(db, "center_a")
    db.execute(INSERT)
    row = db.execute(text("SELECT tenant_id, actor, occurred_at, details FROM app.audit_log")).one()
    assert row.tenant_id == "center_a"
    assert row.actor
    assert row.occurred_at is not None
    assert row.details == {}


def test_other_tenant_cannot_see_entries(db: Connection, as_app: AsApp) -> None:
    as_app(db, "center_a")
    db.execute(INSERT)
    as_app(db, "center_b")
    assert db.execute(text("SELECT count(*) FROM app.audit_log")).scalar() == 0


@pytest.mark.parametrize(
    "statement", ["UPDATE app.audit_log SET action = 'x'", "DELETE FROM app.audit_log"]
)
def test_app_role_cannot_change_history(db: Connection, as_app: AsApp, statement: str) -> None:
    as_app(db, "center_a")
    db.execute(INSERT)
    with pytest.raises(ProgrammingError, match="permission denied"), db.begin_nested():
        db.execute(text(statement))


def test_cannot_write_entry_for_another_tenant(db: Connection, as_app: AsApp) -> None:
    as_app(db, "center_a")
    with pytest.raises(ProgrammingError, match="row-level security"), db.begin_nested():
        db.execute(
            text(
                "INSERT INTO app.audit_log (tenant_id, action, object_type) "
                "VALUES ('center_b', 'load', 'cdm.person')"
            )
        )
