"""Seed a throwaway database for the Playwright end-to-end tests and serve the API on it.

Same synthetic data as the API tests (no Spark needed): the generated patients, demo users
with test-only passwords, a surveillance run as of 2025-06-30 and one condition read from
notes. Writes `frontend/e2e/.seed.json` with the ids and names the browser tests look for.
The database is dropped and rebuilt on every run, so tests always start from the same state.

`uv run python backend/tests/e2e_seed.py --serve 8001` (the Playwright config does this) then
serves the API on that database. The owner URL comes from the usual settings (.env or the
environment); JWT_SECRET and DEID_HMAC_KEY are test-only values set by the Playwright config.
"""

import argparse
import json
import tempfile
from datetime import date
from pathlib import Path

import psycopg
import uvicorn
from api_dataset import build, load
from db_testutils import migrate
from note_testutils import NoteCondition, seed_note_condition
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, pool, text
from sqlalchemy.engine import make_url

from txlens_backend.api.app import create_app
from txlens_backend.api.deps import Role
from txlens_backend.config import Settings, get_settings, require_secret
from txlens_backend.db_setup import center_tenants, ensure_login_role, ensure_tenants, libpq_url
from txlens_backend.deid import pseudonym
from txlens_backend.seed_users import seed_users
from txlens_backend.surveillance.run import run_tenant, transplant_configs

DATABASE = "txlens_e2e"
LOGIN = ("txlens_e2e_api", "e2e-only-password")
AS_OF = date(2025, 6, 30)
# Test-only passwords for the throwaway database (never the demo passwords from .env).
PASSWORDS = {
    Role.CLINICIAN: "E2e-clinician-pw-1",
    Role.RESEARCHER: "E2e-researcher-pw-1",
    Role.DATA_STEWARD: "E2e-steward-pw-1",
    Role.ADMIN: "E2e-admin-pw-1",
}
OUT = Path(__file__).parents[2] / "frontend" / "e2e" / ".seed.json"


def recreate_database(owner_url: str) -> str:
    """Drop, create and migrate the e2e database; returns its owner URL."""
    with psycopg.connect(libpq_url(owner_url), autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{DATABASE}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{DATABASE}"')
    url = make_url(owner_url).set(database=DATABASE).render_as_string(hide_password=False)
    migrate(url)  # creates the txlens_app group role on a fresh server
    with psycopg.connect(libpq_url(url), autocommit=True) as conn:
        ensure_login_role(conn, *LOGIN)
        ensure_tenants(conn, center_tenants())
    return url


def facts(owner: Engine, deid_key: str, noted: NoteCondition) -> dict[str, object]:
    """Ids and names the browser tests need."""
    with owner.connect() as conn:
        single = conn.execute(text(
            "SELECT f.person_id, i.given_name, i.family_name FROM app.surveillance_flag f "
            "JOIN app.patient_identity i USING (tenant_id, person_id) "
            "WHERE f.tenant_id = 'center_a' AND f.status <> 'resolved' AND f.cleared_at IS NULL "
            "GROUP BY f.person_id, i.given_name, i.family_name "
            "HAVING count(*) = 1 AND bool_and(f.status = 'new') ORDER BY f.person_id LIMIT 1"
        )).one()  # fmt: skip
        other = conn.execute(text(
            "SELECT min(person_id) FROM cdm.person WHERE tenant_id = 'center_b'"
        )).scalar_one()  # fmt: skip
        identities = conn.execute(
            text("SELECT mrn, given_name, family_name FROM app.patient_identity")
        ).all()
    return {
        "passwords": {role.value: password for role, password in PASSWORDS.items()},
        "singleFlagPatient": {
            "id": single.person_id, "name": f"{single.family_name}, {single.given_name}",
        },
        "otherCenterPatient": {
            "id": other, "pseudonym": pseudonym(deid_key, "center_b", other),
        },
        "noteConditionPatient": {
            "id": noted.person_id, "pseudonym": pseudonym(deid_key, "center_a", noted.person_id),
            "givenName": noted.given_name, "condition": noted.concept_name,
        },
        "names": sorted({n for row in identities for n in (row.given_name, row.family_name) if n}),
        "mrns": sorted(row.mrn for row in identities if row.mrn),
    }  # fmt: skip


def seed(settings: Settings) -> str:
    """Build the e2e database and write the seed file; returns the database's owner URL."""
    url = recreate_database(settings.database_url.get_secret_value())
    owner = create_engine(url, poolclass=pool.NullPool)
    with tempfile.TemporaryDirectory() as folder, owner.begin() as conn:
        load(conn, build(Path(folder)))
    app = create_engine(
        make_url(url).set(username=LOGIN[0], password=LOGIN[1]), poolclass=pool.NullPool
    )
    seed_users(app, list(center_tenants()), PASSWORDS)
    for tenant in center_tenants():
        run_tenant(app, tenant, AS_OF, transplant_configs())
    noted = seed_note_condition(owner, app)
    deid_key = require_secret("deid_hmac_key", settings.deid_hmac_key)
    OUT.write_text(json.dumps(facts(owner, deid_key, noted)))
    print(f"E2E database {DATABASE} ready; seed written to {OUT}")
    return url


def main() -> None:
    """Seed; with --serve PORT, then run the API against the e2e database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=int, metavar="PORT", help="serve the API on this port")
    args = parser.parse_args()
    settings = get_settings()
    url = seed(settings)
    if args.serve:
        app_url = make_url(url).set(username=LOGIN[0], password=LOGIN[1])
        e2e = settings.model_copy(update={
            "database_url": SecretStr(url),
            "app_database_url": SecretStr(app_url.render_as_string(hide_password=False)),
        })  # fmt: skip
        uvicorn.run(create_app(e2e), host="127.0.0.1", port=args.serve, proxy_headers=False)


if __name__ == "__main__":
    main()
