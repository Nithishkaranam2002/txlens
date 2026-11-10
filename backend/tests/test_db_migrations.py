"""Migrations run down to an empty database and back up again."""

import pytest
from db_testutils import create_database, drop_database, migrate
from sqlalchemy import create_engine, pool, text

pytestmark = pytest.mark.db


def test_downgrade_to_base_and_upgrade_again() -> None:
    url = create_database("roundtrip")
    try:
        migrate(url)
        migrate(url, "base", down=True)
        engine = create_engine(url, poolclass=pool.NullPool)
        with engine.connect() as conn:
            schemas = set(conn.execute(text("SELECT nspname FROM pg_namespace")).scalars())
        engine.dispose()
        assert not {"cdm", "vocab", "app"} & schemas
        migrate(url)
    finally:
        drop_database(url)
