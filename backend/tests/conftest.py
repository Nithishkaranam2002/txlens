"""Database fixtures: a throwaway database, migrated to head, per test session.

DB tests are skipped when PostgreSQL is not reachable, unless TXLENS_REQUIRE_DB=1 (set in CI)
makes that a failure, so CI can never pass by silently skipping them.
"""

import os
from collections.abc import Callable, Iterator

import psycopg
import pytest
from api_testutils import API_TENANTS, BASE_URL, PASSWORDS
from db_testutils import (
    SEED_ORDER,
    TENANTS,
    admin_connection,
    create_database,
    drop_database,
    migrate,
    seed_row,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, create_engine, pool, text
from sqlalchemy.engine import make_url

from txlens_backend.api.app import create_app
from txlens_backend.api.deps import CSRF_HEADER, Role
from txlens_backend.config import Settings
from txlens_backend.db_setup import ensure_login_role
from txlens_backend.seed_users import demo_passwords, seed_users


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A fresh database migrated to head; dropped after the session."""
    url = create_database("main")
    try:
        migrate(url)
        yield url
    finally:
        drop_database(url)


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    """Engine connected as the owner (a superuser locally and in CI)."""
    eng = create_engine(database_url, poolclass=pool.NullPool)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Connection]:
    """Owner connection in a transaction seeded with one row per clinical table per tenant.

    Everything is rolled back afterwards, so tests never see each other's data.
    """
    with engine.connect() as conn:
        transaction = conn.begin()
        for tenant in TENANTS:
            conn.execute(
                text("INSERT INTO app.tenant (tenant_id, name) VALUES (:t, :n)"),
                {"t": tenant, "n": f"Test {tenant}"},
            )
        for table in SEED_ORDER:
            for tenant, row_id in TENANTS.items():
                seed_row(conn, table, tenant, row_id)
        yield conn
        transaction.rollback()


AsApp = Callable[[Connection, str | None], None]


def act_as_app(conn: Connection, tenant: str | None) -> None:
    """Switch this transaction to the non-superuser app role, scoped to a tenant (or none)."""
    conn.execute(text("SET LOCAL ROLE txlens_app"))
    if tenant is not None:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})


@pytest.fixture
def as_app() -> AsApp:
    """`as_app(conn, tenant)`: continue the transaction as txlens_app for that tenant."""
    return act_as_app


@pytest.fixture(scope="session")
def app_login() -> Iterator[tuple[str, str]]:
    """A test login user in txlens_app (cluster-wide role): (user, password)."""
    user, password = f"txlens_api_test_{os.getpid()}", "test-only-password"
    with admin_connection() as admin:
        ensure_login_role(admin, user, password)
    try:
        yield user, password
    finally:
        with admin_connection() as admin:
            admin.execute(psycopg.sql.SQL("DROP ROLE IF EXISTS {}").format(
                psycopg.sql.Identifier(user)))  # fmt: skip


def login_engine(database_url: str, login: tuple[str, str]) -> Engine:
    """Engine for `database_url`, logged in as the test app user: RLS applies to it."""
    url = make_url(database_url).set(username=login[0], password=login[1])
    return create_engine(url, poolclass=pool.NullPool)


@pytest.fixture(scope="session")
def app_engine(database_url: str, app_login: tuple[str, str]) -> Iterator[Engine]:
    """App-login engine for the main test database."""
    eng = login_engine(database_url, app_login)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def api_settings(database_url: str) -> Settings:
    """Settings with strong test-only secrets."""
    return Settings(
        database_url=SecretStr(database_url),
        jwt_secret=SecretStr("test-jwt-secret-" + "j" * 32),
        deid_hmac_key=SecretStr("test-deid-key-" + "d" * 32),
        demo_clinician_password=SecretStr(PASSWORDS[Role.CLINICIAN]),
        demo_researcher_password=SecretStr(PASSWORDS[Role.RESEARCHER]),
        demo_data_steward_password=SecretStr(PASSWORDS[Role.DATA_STEWARD]),
        demo_admin_password=SecretStr(PASSWORDS[Role.ADMIN]),
    )


@pytest.fixture(scope="session")
def api_database() -> Iterator[str]:
    """A separate migrated database with committed data, for API tests (each request uses
    its own connection, so the rollback-per-test `db` fixture cannot be used)."""
    url = create_database("api")
    try:
        migrate(url)
        owner = create_engine(url, poolclass=pool.NullPool)
        with owner.begin() as conn:
            for tenant in API_TENANTS:
                conn.execute(
                    text("INSERT INTO app.tenant (tenant_id, name) VALUES (:t, :n)"),
                    {"t": tenant, "n": f"Test {tenant}"},
                )
        owner.dispose()
        yield url
    finally:
        drop_database(url)


@pytest.fixture(scope="session")
def api_owner(api_database: str) -> Iterator[Engine]:
    """Owner engine for the API database: for setup and for checking what the API wrote."""
    eng = create_engine(api_database, poolclass=pool.NullPool)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def api_app(
    api_database: str, app_login: tuple[str, str], api_settings: Settings
) -> Iterator[FastAPI]:
    """The app on the API database, with one demo user per role per center."""
    eng = login_engine(api_database, app_login)
    seed_users(eng, list(API_TENANTS), demo_passwords(api_settings))
    yield create_app(api_settings, eng)
    eng.dispose()


@pytest.fixture
def client(api_app: FastAPI, api_owner: Engine) -> Iterator[TestClient]:
    """A fresh client (no cookies) with the CSRF header; login attempts are cleared first."""
    with api_owner.begin() as conn:
        conn.execute(text("DELETE FROM app.login_attempt"))
    with TestClient(api_app, base_url=BASE_URL, headers={CSRF_HEADER: "test"}) as test_client:
        yield test_client
